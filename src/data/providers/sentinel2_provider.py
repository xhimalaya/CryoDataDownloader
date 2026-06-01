"""
sentinel2_provider.py

Downloads Sentinel-2 L2A RGB (B04/B03/B02) from Copernicus EODATA S3.

Source:   Copernicus S3  (bucket: eodata)  — boto3, same auth as DEM
Auth:     credentials.copernicus_s3.access_key / secret_key
Product:  S2MSI2A — Level-2A surface reflectance
Bands:    B04 (Red), B03 (Green), B02 (Blue) at 10m resolution

S3 path structure:
    /Sentinel-2/MSI/L2A/YYYY/MM/DD/
        <product_name>.SAFE/
            GRANULE/<tile_id>/IMG_DATA/R10m/
                *_B04_10m.jp2
                *_B03_10m.jp2
                *_B02_10m.jp2

Pipeline:
    search()   → list S3 prefix, find .SAFE folder matching date+AOI tile
    download() → download B04, B03, B02 .jp2 files via S3 streaming
    process()  → stack B04/B03/B02 → RGB GeoTIFF, clip to AOI, LZW compress
"""

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional

from .base_provider import BaseProvider
from ..auth.auth_factory import get_copernicus_s3_client

logger = logging.getLogger(__name__)

_BUCKET = "eodata"

# Sentinel-2 Military Grid Reference System tiles that cover
# Himalayan + Polar study regions — used to narrow S3 listing
# Full list resolved from AOI bbox at runtime via MGRS
_S2_L2A_PREFIX = "Sentinel-2/MSI/L2A"


class Sentinel2Provider(BaseProvider):
    """
    Sentinel-2 L2A RGB provider via Copernicus EODATA S3 (boto3).

    No REST API, no OData — pure S3 list + get_object.
    Same auth as DEMProvider.
    """

    # ──────────────────────────────────────────────────────────────
    # search()
    # ──────────────────────────────────────────────────────────────

    async def search(
        self,
        geojson_path: str,
        date_str:     str,
    ) -> Optional[Dict[str, Any]]:
        """
        Lists Copernicus S3 to find S2 L2A products covering the AOI.

        Strategy:
            1. Build S3 date prefix: Sentinel-2/MSI/L2A/YYYY/MM/DD/
            2. List all .SAFE folders under that prefix
            3. Filter by AOI intersection using tile ID from product name
               (tile ID encodes UTM zone + lat band + grid square)
            4. Within matching .SAFE, list GRANULE/.../IMG_DATA/R10m/
            5. Return S3 keys for B04, B03, B02

        Searches ±3 days around date_str for best available scene.
        """
        bbox = self._bbox_from_geojson(geojson_path)
        if bbox is None:
            logger.error(f"[S2] Cannot read bbox from: {geojson_path}")
            return None

        loop = asyncio.get_event_loop()

        def _s3_search():
            s3 = get_copernicus_s3_client(self.config)

            # Search ±3 days
            dt_target = datetime.strptime(date_str, "%Y-%m-%d")
            candidates = []

            for delta in range(-3, 4):
                dt_check = dt_target + timedelta(days=delta)
                prefix = (
                    f"{_S2_L2A_PREFIX}/"
                    f"{dt_check.year:04d}/"
                    f"{dt_check.month:02d}/"
                    f"{dt_check.day:02d}/"
                )

                logger.debug(f"[S2] Listing S3 prefix: {prefix}")

                paginator = s3.get_paginator("list_objects_v2")
                pages = paginator.paginate(
                    Bucket    = _BUCKET,
                    Prefix    = prefix,
                    Delimiter = "/",
                )

                for page in pages:
                    for obj in page.get("CommonPrefixes", []):
                        safe_prefix = obj["Prefix"]
                        product_name = safe_prefix.rstrip("/").split("/")[-1]

                        # Filter: must be .SAFE folder
                        if not product_name.endswith(".SAFE"):
                            continue

                        # Filter: must intersect AOI via tile ID
                        tile_id = self._extract_tile_id(product_name)
                        if tile_id and not self._tile_intersects_bbox(
                            tile_id, bbox
                        ):
                            logger.debug(
                                f"[S2] Tile {tile_id} does not intersect "
                                f"AOI — skip"
                            )
                            continue

                        candidates.append({
                            "product_name": product_name,
                            "safe_prefix":  safe_prefix,
                            "date":         dt_check.strftime("%Y-%m-%d"),
                            "delta_days":   abs(delta),
                        })

            if not candidates:
                logger.warning(
                    f"[S2] No L2A products found ±3d of {date_str} "
                    f"for bbox {bbox}"
                )
                return None

            # Pick closest date
            candidates.sort(key=lambda x: x["delta_days"])
            best = candidates[0]
            logger.info(
                f"[S2] Found: {best['product_name']} "
                f"(date={best['date']}, Δ={best['delta_days']}d)"
            )

            # Now find the RGB band keys inside this .SAFE
            rgb_keys = self._find_rgb_keys(s3, best["safe_prefix"])
            if not rgb_keys:
                logger.warning(
                    f"[S2] No RGB bands found in: {best['safe_prefix']}"
                )
                return None

            best["rgb_keys"] = rgb_keys
            return best

        try:
            return await loop.run_in_executor(None, _s3_search)
        except Exception as e:
            logger.error(f"[S2] Search failed: {e}")
            raise

    # ──────────────────────────────────────────────────────────────
    # download()
    # ──────────────────────────────────────────────────────────────

    async def download(
        self,
        product_info:      Dict[str, Any],
        output_path:       str,
        progress_callback: Optional[Callable] = None,
    ) -> bool:
        """
        Downloads B04, B03, B02 .jp2 files from Copernicus S3.

        Files saved as:
            <output_dir>/_s2_B04.jp2
            <output_dir>/_s2_B03.jp2
            <output_dir>/_s2_B02.jp2

        output_path is used only for its directory — process() builds
        the final merged GeoTIFF path.
        """
        output_dir = os.path.dirname(output_path)
        os.makedirs(output_dir, exist_ok=True)

        rgb_keys: Dict[str, str] = product_info["rgb_keys"]
        # {band: s3_key}  e.g. {"B04": "Sentinel-2/.../B04_10m.jp2"}

        loop = asyncio.get_event_loop()

        def _download_bands():
            s3 = get_copernicus_s3_client(self.config)
            total_bands = len(rgb_keys)
            downloaded  = {}

            for i, (band, s3_key) in enumerate(rgb_keys.items()):
                local_path = os.path.join(output_dir, f"_s2_{band}.jp2")

                logger.info(
                    f"[S2] Downloading band {band} "
                    f"({i+1}/{total_bands}): {s3_key}"
                )

                s3.download_file(
                    Bucket   = _BUCKET,
                    Key      = s3_key,
                    Filename = local_path,
                )

                if os.path.exists(local_path) and \
                   os.path.getsize(local_path) > 0:
                    downloaded[band] = local_path
                    logger.info(
                        f"[S2] Band {band} OK "
                        f"({os.path.getsize(local_path)/1e6:.1f} MB)"
                    )
                else:
                    logger.error(f"[S2] Band {band} download failed")

                if progress_callback:
                    progress_callback(0, (i + 1) / total_bands)

            # Store local paths back into product_info for process()
            product_info["local_bands"] = downloaded
            return len(downloaded) == len(rgb_keys)

        try:
            return await loop.run_in_executor(None, _download_bands)
        except Exception as e:
            logger.error(f"[S2] Band download failed: {e}")
            raise

    # ──────────────────────────────────────────────────────────────
    # process()
    # ──────────────────────────────────────────────────────────────

    async def process(
        self,
        raw_path:     str,
        output_dir:   str,
        geojson_path: str,
    ) -> str:
        """
        Stacks B04/B03/B02 → RGB GeoTIFF, clips to AOI, LZW compresses.

        Output band order:
            Band 1 → B04 (Red)
            Band 2 → B03 (Green)
            Band 3 → B02 (Blue)

        Sentinel-2 DN values are uint16 (0–10000 = 100% reflectance).
        Output stored as uint16 to preserve full precision.
        Clip applied to AOI polygon from geojson_path.
        """
        loop = asyncio.get_event_loop()

        def _build_rgb():
            try:
                import rasterio
                from rasterio.merge   import merge
                from rasterio.mask    import mask
                from rasterio.enums   import Resampling
                import numpy as np
                import shapely.geometry as sg
                import fiona

                local_bands: Dict[str, str] = \
                    self._recover_band_paths(output_dir)

                if not local_bands:
                    logger.error("[S2] No local band files found in process()")
                    return raw_path

                # Band order: R G B
                band_order = ["B04", "B03", "B02"]
                missing = [b for b in band_order if b not in local_bands]
                if missing:
                    logger.error(f"[S2] Missing bands: {missing}")
                    return raw_path

                # Read each band
                arrays   = []
                profile  = None

                for band in band_order:
                    with rasterio.open(local_bands[band]) as src:
                        arrays.append(src.read(1))
                        if profile is None:
                            profile = src.profile.copy()
                            profile.update(
                                count    = 3,
                                driver   = "GTiff",
                                compress = "lzw",
                                dtype    = "uint16",
                            )

                rgb_stack = np.stack(arrays, axis=0)  # (3, H, W)

                # Write stacked RGB
                rgb_path = os.path.join(
                    output_dir,
                    os.path.basename(output_dir) + "_RGB.tif",
                )
                with rasterio.open(rgb_path, "w", **profile) as dst:
                    dst.write(rgb_stack)
                    dst.update_tags(1, band="B04_Red")
                    dst.update_tags(2, band="B03_Green")
                    dst.update_tags(3, band="B02_Blue")

                # Clip to AOI polygon
                clipped_path = rgb_path.replace("_RGB.tif", "_RGB_clipped.tif")
                with fiona.open(geojson_path) as gj:
                    shapes = [feature["geometry"] for feature in gj]

                with rasterio.open(rgb_path) as src:
                    out_image, out_transform = mask(
                        src, shapes,
                        crop       = True,
                        nodata     = 0,
                        all_touched= True,
                    )
                    out_profile = src.profile.copy()
                    out_profile.update(
                        height    = out_image.shape[1],
                        width     = out_image.shape[2],
                        transform = out_transform,
                        compress  = "lzw",
                        nodata    = 0,
                    )

                with rasterio.open(clipped_path, "w", **out_profile) as dst:
                    dst.write(out_image)

                logger.info(f"[S2] RGB GeoTIFF: {clipped_path}")

                # Cleanup intermediates
                if self.config.get("download.delete_temp_files", True):
                    for band, path in local_bands.items():
                        if os.path.exists(path):
                            os.remove(path)
                            logger.debug(f"[S2] Removed temp: {path}")
                    if os.path.exists(rgb_path):
                        os.remove(rgb_path)

                return clipped_path

            except ImportError as e:
                logger.warning(
                    f"[S2] rasterio/fiona not available ({e}) "
                    f"— returning raw"
                )
                return raw_path
            except Exception as e:
                logger.error(f"[S2] RGB processing failed: {e}")
                return raw_path

        return await loop.run_in_executor(None, _build_rgb)

    # ──────────────────────────────────────────────────────────────
    # Private helpers
    # ──────────────────────────────────────────────────────────────

    def _bbox_from_geojson(self, path: str) -> Optional[Dict[str, float]]:
        try:
            with open(path) as f:
                gj = json.load(f)
            coords = gj["features"][0]["geometry"]["coordinates"][0]
            lons = [c[0] for c in coords]
            lats = [c[1] for c in coords]
            return {
                "north": max(lats),
                "south": min(lats),
                "west":  min(lons),
                "east":  max(lons),
            }
        except Exception as e:
            logger.error(f"[S2] bbox error: {e}")
            return None

    def _extract_tile_id(self, product_name: str) -> Optional[str]:
        """
        Extracts MGRS tile ID from S2 product name.

        Product naming convention (ESA):
            S2B_MSIL2A_20240115T053649_N0510_R048_T43RGP_20240115T084935.SAFE
                                                          ^^^^^^ tile ID = T43RGP
        Returns the 5-char tile code e.g. '43RGP'.
        """
        parts = product_name.replace(".SAFE", "").split("_")
        for part in parts:
            if part.startswith("T") and len(part) == 6:
                return part[1:]   # strip leading 'T'
        return None

    def _tile_intersects_bbox(
        self, tile_id: str, bbox: Dict[str, float]
    ) -> bool:
        """
        Checks if an MGRS tile code intersects the AOI bounding box.

        Uses mgrs library if available for precise check.
        Falls back to accepting all tiles if mgrs not installed
        (download_engine will just download more scenes than needed,
         process() clips to exact AOI anyway).
        """
        try:
            import mgrs
            m = mgrs.MGRS()
            # Convert tile centre to lat/lon
            # MGRS grid square is 100km — approximate centre
            lat, lon = m.toLatLon(
                (tile_id[:2] + tile_id[2] + tile_id[3:]).encode()
            )
            return (
                bbox["south"] - 1.5 <= lat <= bbox["north"] + 1.5 and
                bbox["west"]  - 1.5 <= lon <= bbox["east"]  + 1.5
            )
        except Exception:
            # mgrs not installed or parse error — accept all tiles
            # process() will clip; only a few extra tiles at worst
            return True

    def _find_rgb_keys(
        self, s3, safe_prefix: str
    ) -> Optional[Dict[str, str]]:
        """
        Lists S3 inside a .SAFE folder to find B04, B03, B02 at 10m.

        S3 path: .SAFE/GRANULE/<tile>/IMG_DATA/R10m/*_B04_10m.jp2
        Returns dict: {"B04": "full/s3/key.jp2", "B03": ..., "B02": ...}
        """
        granule_prefix = safe_prefix + "GRANULE/"
        logger.debug(f"[S2] Listing granule prefix: {granule_prefix}")

        rgb_keys: Dict[str, str] = {}
        target_bands = {"B04", "B03", "B02"}

        paginator = s3.get_paginator("list_objects_v2")
        pages = paginator.paginate(
            Bucket = _BUCKET,
            Prefix = granule_prefix,
        )

        for page in pages:
            for obj in page.get("Contents", []):
                key = obj["Key"]
                # Only 10m resolution .jp2 files
                if "R10m" not in key or not key.endswith(".jp2"):
                    continue
                filename = key.split("/")[-1]
                for band in target_bands:
                    if f"_{band}_10m" in filename:
                        rgb_keys[band] = key
                        logger.debug(f"[S2] Found {band}: {key}")

        found = set(rgb_keys.keys())
        missing = target_bands - found
        if missing:
            logger.warning(f"[S2] Missing bands in .SAFE: {missing}")

        return rgb_keys if rgb_keys else None

    def _recover_band_paths(
        self, output_dir: str
    ) -> Dict[str, str]:
        """
        Recovers local band file paths written by download().
        Looks for _s2_B04.jp2, _s2_B03.jp2, _s2_B02.jp2 in output_dir.
        """
        bands = {}
        for band in ["B04", "B03", "B02"]:
            p = os.path.join(output_dir, f"_s2_{band}.jp2")
            if os.path.exists(p):
                bands[band] = p
        return bands
