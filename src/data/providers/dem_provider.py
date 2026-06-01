"""
src/data/providers/dem_provider.py

Copernicus GLO-30 Digital Elevation Model provider.

Source:    Copernicus S3 (EODATA) — Copernicus DEM GLO-30
Pipeline:  search() → download() → process()
Output:    Clipped, LZW-compressed GeoTIFF elevation raster

Called by:
    download_engine.py → provider.search() → provider.download() → provider.process()

Config keys read (from config.yaml / credentials.yaml):
    sources.dem.s3_bucket              → S3 bucket name (default: 'eodata')
    sources.dem.s3_prefix              → S3 path prefix to GLO-30 tiles
    credentials.copernicus_s3.*        → S3 auth (via CopernicusAuth)
    project.output_dir                 → root output dir (default: './data')

Dependencies:
    pip install boto3 rasterio shapely fiona
"""

import logging
import os
from typing import Any, Callable, Dict, List, Optional

from .base_provider import BaseProvider, ProviderResult
from ..auth.copernicus_auth import get_copernicus_s3_client
from ..search.dem_search import find_intersecting_dem_tiles
from ..downloader.s3_stream_downloader import S3StreamDownloader
from ..processing.merge_tiles import merge_raster_tiles
from ..processing.raster_clip import clip_to_geojson
from ..processing.compression import compress_lzw

logger = logging.getLogger(__name__)


class DEMProvider(BaseProvider):
    """
    Copernicus GLO-30 Digital Elevation Model provider.

    Inherits: BaseProvider (fetch, validate interface)

    Responsibilities:
        - Find all GLO-30 DEM tiles intersecting the glacier AOI
        - Download tiles from Copernicus S3 (EODATA)
        - Merge tiles into a single mosaic GeoTIFF
        - Clip mosaic to glacier polygon boundary
        - LZW compress the clipped output
        - Return final file path to download_engine.py

    Note:
        GLO-30 DEM is a static product — no date filtering is applied.
        The same tiles are returned regardless of date_str.
        date_str is stored in DB for consistency with other providers.
    """

    provider_name = "DEMProvider"
    data_type     = "dem"

    # -----------------------------------------------------------------------
    # search()
    # -----------------------------------------------------------------------

    async def search(
        self,
        geojson_path: str,
        date_str: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Finds all GLO-30 DEM tile S3 keys intersecting the glacier AOI.

        DEM is static — no date filtering. date_str stored in product_info
        for DB record consistency only.

        Args:
            geojson_path: Path to glacier AOI GeoJSON file.
            date_str:     Target date string (YYYY-MM-DD) — used for naming only.

        Returns:
            product_info dict:
                {
                    'product_name': 'DEM_GLO30_2023-06-15',
                    'date':         '2023-06-15',
                    'provider':     'copernicus_s3',
                    'tiles':        ['Copernicus/DEM/...N27_E086.tif', ...],
                    'metadata':     {'tile_count': 2},
                }
            None if no tiles found.
        """
        self._log.info(
            f"[DEMProvider] Searching GLO-30 tiles for AOI: "
            f"{os.path.basename(geojson_path)}"
        )

        try:
            tiles = await find_intersecting_dem_tiles(geojson_path)
        except Exception as e:
            self._log.error(f"[DEMProvider] Tile search failed: {e}")
            return None

        if not tiles:
            self._log.warning(
                f"[DEMProvider] No intersecting GLO-30 tiles found for: "
                f"{geojson_path}"
            )
            return None

        self._log.info(
            f"[DEMProvider] Found {len(tiles)} intersecting tile(s)."
        )

        return {
            "product_name": f"DEM_GLO30_{date_str}",
            "date":         date_str,
            "provider":     "copernicus_s3",
            "tiles":        tiles,
            "metadata":     {"tile_count": len(tiles)},
        }

    # -----------------------------------------------------------------------
    # download()
    # -----------------------------------------------------------------------

    async def download(
        self,
        product_info: Dict[str, Any],
        output_path: str,
        progress_callback: Optional[Callable[[int, float], None]] = None,
    ) -> bool:
        """
        Downloads all intersecting DEM tiles from Copernicus S3,
        then merges them into a single mosaic GeoTIFF at output_path.

        Steps:
            1. Initialise S3 client via CopernicusAuth
            2. Download each tile to a temp _tile_*.tif file
            3. Merge all tiles → output_path
            4. Delete temp tile files

        Args:
            product_info:      Dict returned by search() containing 'tiles' list.
            output_path:       Full path for merged output GeoTIFF.
            progress_callback: Optional callback(task_id, fraction).
                               Not used at tile level — fired at merge completion.

        Returns:
            True  → merge GeoTIFF written to output_path.
            False → no tiles downloaded or merge failed.
        """
        tile_s3_keys: List[str] = product_info.get("tiles", [])

        if not tile_s3_keys:
            self._log.error("[DEMProvider] product_info contains no tile keys.")
            return False

        # --- Build S3 client and downloader ---
        try:
            s3_client = get_copernicus_s3_client(self.config)
        except Exception as e:
            self._log.error(f"[DEMProvider] S3 client init failed: {e}")
            return False

        bucket     = self.config.get("sources.dem.s3_bucket", "eodata")
        downloader = S3StreamDownloader(s3_client=s3_client, bucket=bucket)
        output_dir = os.path.dirname(output_path)
        os.makedirs(output_dir, exist_ok=True)

        # --- Download each tile ---
        tile_locals: List[str] = []
        total = len(tile_s3_keys)

        for i, s3_key in enumerate(tile_s3_keys):
            tile_filename = os.path.basename(s3_key)
            tile_local    = os.path.join(output_dir, f"_tile_{tile_filename}")

            self._log.info(
                f"[DEMProvider] Downloading tile {i + 1}/{total}: "
                f"{tile_filename}"
            )

            ok = await downloader.download_key(
                s3_key=s3_key,
                output_path=tile_local,
                progress_callback=None,
            )

            if ok:
                tile_locals.append(tile_local)
                self._log.debug(
                    f"[DEMProvider] Tile saved: {tile_local}"
                )
            else:
                self._log.warning(
                    f"[DEMProvider] Failed to download tile: {s3_key}"
                )

        if not tile_locals:
            self._log.error(
                "[DEMProvider] No tiles downloaded successfully — "
                "cannot produce merged DEM."
            )
            return False

        # --- Merge tiles → output_path ---
        self._log.info(
            f"[DEMProvider] Merging {len(tile_locals)}/{total} tile(s) "
            f"→ {os.path.basename(output_path)}"
        )

        merge_ok = merge_raster_tiles(
            tile_paths=tile_locals,
            output_path=output_path,
        )

        # --- Clean up temp tile files ---
        for tile_path in tile_locals:
            try:
                os.remove(tile_path)
                self._log.debug(
                    f"[DEMProvider] Temp tile removed: "
                    f"{os.path.basename(tile_path)}"
                )
            except Exception as e:
                self._log.warning(
                    f"[DEMProvider] Could not remove temp tile "
                    f"'{tile_path}': {e}"
                )

        if merge_ok and progress_callback:
            progress_callback(None, 1.0)

        return merge_ok

    # -----------------------------------------------------------------------
    # process()
    # -----------------------------------------------------------------------

    async def process(
        self,
        raw_path: str,
        output_dir: str,
        geojson_path: str,
    ) -> str:
        """
        Clips merged DEM to glacier polygon and compresses in-place.

        Steps:
            1. clip_to_geojson() → dem_merged_clipped.tif
            2. compress_lzw()    → in-place LZW compression
            3. Remove unclipped merged file (raw_path) to save space

        Args:
            raw_path:     Path to merged (unclipped) DEM GeoTIFF.
            output_dir:   Directory for clipped output.
            geojson_path: Path to glacier AOI GeoJSON.

        Returns:
            Path to the final clipped + compressed GeoTIFF.

        Raises:
            RuntimeError: If clip or compression fails.
        """
        self._log.info(
            f"[DEMProvider] Clipping DEM → AOI: "
            f"{os.path.basename(geojson_path)}"
        )

        # --- Step 1: Clip to glacier AOI ---
        clipped_path = clip_to_geojson(
            raster_path=raw_path,
            geojson_path=geojson_path,
            output_dir=output_dir,
            output_suffix="_clipped",
        )

        # --- Step 2: Compress in-place ---
        self._log.info(
            f"[DEMProvider] Compressing (LZW): "
            f"{os.path.basename(clipped_path)}"
        )
        final_path = compress_lzw(clipped_path)

        # --- Step 3: Remove unclipped merged raster ---
        if os.path.exists(raw_path) and raw_path != clipped_path:
            try:
                os.remove(raw_path)
                self._log.debug(
                    f"[DEMProvider] Unclipped raster removed: "
                    f"{os.path.basename(raw_path)}"
                )
            except Exception as e:
                self._log.warning(
                    f"[DEMProvider] Could not remove raw merged file "
                    f"'{raw_path}': {e}"
                )

        self._log.info(
            f"[DEMProvider] Processing complete → {final_path}"
        )
        return final_path

    # -----------------------------------------------------------------------
    # fetch() — full pipeline (BaseProvider abstract implementation)
    # -----------------------------------------------------------------------

    async def fetch(
        self,
        glacier_id: str,
        year:       int,
        aoi_path:   str,
        output_dir: str,
    ) -> ProviderResult:
        """
        Full DEM pipeline for one glacier.

        Steps:
            1. validate()  — pre-flight checks
            2. search()    — find intersecting GLO-30 tile S3 keys
            3. download()  — download + merge tiles
            4. process()   — clip + compress
            5. Returns ProviderResult

        Args:
            glacier_id: Glacier identifier e.g. 'khumbu'.
            year:       Target year (used for date_str and output path).
            aoi_path:   Path to glacier AOI GeoJSON.
            output_dir: Root output directory.

        Returns:
            ProviderResult with success flag, file paths, and errors.
        """
        start    = self._start_timer()
        errors:  List[str] = []
        files:   List[str] = []
        date_str = f"{year}-01-01"   # GLO-30 is static; date used for naming only

        # --- Step 1: Validate ---
        validation_errors = self.validate(glacier_id, year, aoi_path, output_dir)
        if validation_errors:
            return self._make_result(
                glacier_id   = glacier_id,
                year         = year,
                success      = False,
                errors       = validation_errors,
                duration_sec = self._elapsed(start),
            )

        # Build provider-specific output dir:
        # ./data/raw/dem/<glacier_id>/<year>/
        dem_output_dir = os.path.join(output_dir, "raw", "dem", glacier_id, str(year))
        os.makedirs(dem_output_dir, exist_ok=True)

        merged_path = os.path.join(dem_output_dir, f"dem_merged_{glacier_id}_{year}.tif")

        # --- Step 2: Search ---
        self._log.info(
            f"[DEMProvider] fetch() → glacier={glacier_id} year={year}"
        )
        product_info = await self.search(
            geojson_path=aoi_path,
            date_str=date_str,
        )

        if not product_info:
            return self._make_result(
                glacier_id   = glacier_id,
                year         = year,
                success      = False,
                errors       = [f"No GLO-30 tiles found for AOI: {aoi_path}"],
                duration_sec = self._elapsed(start),
            )

        # --- Step 3: Download + Merge ---
        try:
            download_ok = await self.download(
                product_info=product_info,
                output_path=merged_path,
            )
        except Exception as e:
            download_ok = False
            errors.append(f"Download error: {e}")
            self._log.error(f"[DEMProvider] Download exception: {e}")

        if not download_ok:
            errors.append("Download/merge step returned False.")
            return self._make_result(
                glacier_id   = glacier_id,
                year         = year,
                success      = False,
                errors       = errors,
                duration_sec = self._elapsed(start),
            )

        # --- Step 4: Process (clip + compress) ---
        try:
            final_path = await self.process(
                raw_path=merged_path,
                output_dir=dem_output_dir,
                geojson_path=aoi_path,
            )
            files.append(final_path)
        except Exception as e:
            errors.append(f"Processing error: {e}")
            self._log.error(f"[DEMProvider] Process exception: {e}")
            return self._make_result(
                glacier_id   = glacier_id,
                year         = year,
                success      = False,
                files        = files,
                errors       = errors,
                duration_sec = self._elapsed(start),
            )

        # --- Step 5: Return result ---
        result = self._make_result(
            glacier_id   = glacier_id,
            year         = year,
            success      = True,
            files        = files,
            errors       = errors,
            duration_sec = self._elapsed(start),
            meta         = {
                "tile_count":    product_info["metadata"]["tile_count"],
                "product_name":  product_info["product_name"],
                "merged_path":   merged_path,
                "final_path":    final_path,
            },
        )

        self._log.info(result.summary())
        return result

    # -----------------------------------------------------------------------
    # validate() — BaseProvider abstract implementation
    # -----------------------------------------------------------------------

    def validate(
        self,
        glacier_id: str,
        year:       int,
        aoi_path:   str,
        output_dir: str,
    ) -> List[str]:
        """
        Pre-flight validation for DEM provider.

        Checks:
            - glacier_id is a non-empty string
            - year is a plausible integer (2000–2100)
            - AOI GeoJSON file exists and is valid
            - Output directory is writable
            - S3 bucket config key is present

        Returns:
            List of error strings. Empty list = all checks passed.
        """
        errors: List[str] = []

        # glacier_id
        if not glacier_id or not isinstance(glacier_id, str):
            errors.append("glacier_id must be a non-empty string.")

        # year
        if not isinstance(year, int) or not (2000 <= year <= 2100):
            errors.append(f"year must be an integer between 2000–2100. Got: {year}")

        # AOI file
        errors.extend(self._validate_aoi(aoi_path))

        # Output dir
        errors.extend(self._validate_output_dir(output_dir))

        # S3 bucket config
        bucket = self.config.get("sources.dem.s3_bucket", None)
        if not bucket:
            self._log.warning(
                "[DEMProvider] 'sources.dem.s3_bucket' not set — "
                "will use default 'eodata'."
            )

        if errors:
            self._log.warning(
                f"[DEMProvider] Validation failed ({len(errors)} error(s)): "
                f"{errors}"
            )

        return errors


__all__ = ["DEMProvider"]
