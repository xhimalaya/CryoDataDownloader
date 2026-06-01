"""
era5_provider.py

Downloads ERA5 reanalysis data from Copernicus CDS for a glacier AOI.

Source:   Copernicus Climate Data Store (CDS) — cdsapi
Auth:     credentials.cds.api_key
Dataset:  reanalysis-era5-single-levels (hourly)
Output:   <aoi>_era5_<date>.nc  →  <aoi>_era5_<date>.tif (12-band, daily mean)

Band order matches methodology Section 4.1 energy balance features:
    1  2m_temperature                       K
    2  skin_temperature                     K
    3  snowfall                             m of water equivalent
    4  snow_depth                           m of water equivalent
    5  total_precipitation                  m
    6  surface_pressure                     Pa
    7  surface_solar_radiation_downwards    J/m²
    8  surface_thermal_radiation_downwards  J/m²
    9  surface_latent_heat_flux             J/m²
    10 surface_sensible_heat_flux           J/m²
    11 10m_u_component_of_wind             m/s
    12 10m_v_component_of_wind             m/s
"""

import asyncio
import json
import logging
import os
from datetime import date, timedelta
from typing import Any, Callable, Dict, List, Optional

from .base_provider import BaseProvider, ProviderResult
from ..auth.auth_factory import get_cds_client

logger = logging.getLogger(__name__)

_DATASET   = "reanalysis-era5-single-levels"
_ALL_HOURS = [f"{h:02d}:00" for h in range(24)]
_ERA5_GRID = 0.25   # ERA5 native grid resolution — used as bbox buffer

# Canonical 12-band variable order (Section 4.1 energy balance features).
# process() always writes bands in THIS order regardless of what xarray
# returns from the .nc file, so downstream consumers can rely on band index.
_DEFAULT_VARIABLES = [
    "2m_temperature",
    "skin_temperature",
    "snowfall",
    "snow_depth",
    "total_precipitation",
    "surface_pressure",
    "surface_solar_radiation_downwards",
    "surface_thermal_radiation_downwards",
    "surface_latent_heat_flux",
    "surface_sensible_heat_flux",
    "10m_u_component_of_wind",
    "10m_v_component_of_wind",
]


class ERA5Provider(BaseProvider):
    """
    ERA5 provider via cdsapi (Copernicus CDS).

    Blocking cdsapi calls run in asyncio thread executor —
    never block the event loop used by download_engine.
    """

    provider_name = "ERA5Provider"
    data_type     = "era5"

    # ------------------------------------------------------------------
    # fetch() — full pipeline for one glacier / one year
    # ------------------------------------------------------------------

    async def fetch(
        self,
        glacier_id: str,
        year:       int,
        aoi_path:   str,
        output_dir: str,
    ) -> ProviderResult:
        """
        Full ERA5 pipeline for one glacier / one year.

        Iterates every date in the year, calls search → download → process
        for each. Returns aggregated ProviderResult.
        """
        start_timer = self._start_timer()
        files:  List[str] = []
        errors: List[str] = []

        d     = date(year, 1, 1)
        end_d = date(year, 12, 31)
        dates: List[str] = []
        while d <= end_d:
            dates.append(d.strftime("%Y-%m-%d"))
            d += timedelta(days=1)

        for date_str in dates:
            try:
                product_info = await self.search(
                    geojson_path=aoi_path,
                    date_str=date_str,
                )
                if not product_info:
                    errors.append(f"No ERA5 product found for {glacier_id}/{date_str}")
                    continue

                os.makedirs(output_dir, exist_ok=True)
                raw_path = os.path.join(
                    output_dir,
                    f"{glacier_id}_era5_{date_str.replace('-', '')}.nc",
                )

                ok = await self.download(
                    product_info=product_info,
                    output_path=raw_path,
                )
                if not ok:
                    errors.append(f"Download failed: {glacier_id}/{date_str}")
                    continue

                final_path = await self.process(
                    raw_path=raw_path,
                    output_dir=output_dir,
                    geojson_path=aoi_path,
                )
                files.append(final_path)

            except Exception as e:
                errors.append(f"{date_str}: {e}")
                logger.error(f"[ERA5] fetch error {glacier_id}/{date_str}: {e}")

        return self._make_result(
            glacier_id   = glacier_id,
            year         = year,
            success      = len(files) > 0,
            files        = files,
            errors       = errors,
            duration_sec = self._elapsed(start_timer),
        )

    # ------------------------------------------------------------------
    # validate() — pre-flight checks before fetch() is called
    # ------------------------------------------------------------------

    def validate(
        self,
        glacier_id: str,
        year:       int,
        aoi_path:   str,
        output_dir: str,
    ) -> List[str]:
        """
        Pre-flight checks for ERA5 provider.

        Checks:
            - AOI file exists and is valid GeoJSON
            - Output directory is writable
            - CDS api_key is configured
            - year is within ERA5 availability (1940–present)
        """
        errors = []

        errors += self._validate_aoi(aoi_path)
        errors += self._validate_output_dir(output_dir)

        api_key = self.config.get("credentials.cds.api_key", "")
        if not api_key:
            errors.append(
                "ERA5: credentials.cds.api_key is missing in credentials.yaml"
            )

        if not (1940 <= year <= 2030):
            errors.append(
                f"ERA5: year {year} is outside valid range (1940–2030)"
            )

        if errors:
            logger.warning(
                f"[ERA5] validate() failed for {glacier_id}/{year}: {errors}"
            )

        return errors

    # ------------------------------------------------------------------
    # search() — validates AOI + date, builds CDS request parameters
    # ------------------------------------------------------------------

    async def search(
        self,
        geojson_path: str,
        date_str:     str,
    ) -> Optional[Dict[str, Any]]:
        """
        Validates AOI + date, builds CDS request parameters.

        CDS has no catalogue search endpoint — every request IS the
        retrieval. This step only validates inputs and returns a
        product_info dict consumed by download().

        Returns None if AOI extraction fails (triggers retry in engine).
        """
        bbox = self._bbox_from_geojson(geojson_path)
        if bbox is None:
            logger.error(f"[ERA5] Failed to extract bbox from: {geojson_path}")
            return None

        variables: List[str] = self.config.get(
            "sources.era5.variables",
            _DEFAULT_VARIABLES,
        )

        year, month, day = date_str.split("-")

        # CDS area order: [North, West, South, East]
        # Add one ERA5 grid cell buffer so the glacier AOI is fully covered
        area = [
            round(bbox["north"] + _ERA5_GRID, 4),
            round(bbox["west"]  - _ERA5_GRID, 4),
            round(bbox["south"] - _ERA5_GRID, 4),
            round(bbox["east"]  + _ERA5_GRID, 4),
        ]

        aoi_name = (
            os.path.basename(geojson_path)
            .replace(".geojson", "")
            .replace("_cds_era5", "")   # strip suffix added by scanner
        )

        logger.info(
            f"[ERA5] Ready: {aoi_name} | {date_str} | "
            f"area={area} | {len(variables)} vars"
        )

        return {
            "product_name": f"ERA5_{aoi_name}_{date_str}",
            "dataset":      _DATASET,
            "variables":    variables,
            "year":         year,
            "month":        month,
            "day":          day,
            "time":         _ALL_HOURS,
            "area":         area,
            "bbox":         bbox,
            "date_str":     date_str,
            "aoi_name":     aoi_name,
        }

    # ------------------------------------------------------------------
    # download() — submits CDS job and downloads resulting NetCDF
    # ------------------------------------------------------------------

    async def download(
        self,
        product_info:      Dict[str, Any],
        output_path:       str,
        progress_callback: Optional[Callable] = None,
    ) -> bool:
        """
        Submits CDS retrieval job and downloads resulting NetCDF.

        cdsapi handles server-side job queuing and polling internally.
        Runs in thread executor — does NOT block asyncio event loop.
        """
        # Always work with .nc path regardless of what output_path extension is
        nc_path = os.path.splitext(output_path)[0] + ".nc"
        os.makedirs(os.path.dirname(nc_path), exist_ok=True)

        loop = asyncio.get_event_loop()

        def _cds_retrieve():
            client = get_cds_client(self.config)

            request = {
                "product_type":    "reanalysis",
                "variable":        product_info["variables"],
                "year":            product_info["year"],
                "month":           product_info["month"],
                "day":             product_info["day"],
                "time":            product_info["time"],
                "area":            product_info["area"],
                "data_format":     "netcdf",
                "download_format": "unarchived",
            }

            logger.info(
                f"[ERA5] Submitting CDS job: {product_info['dataset']} | "
                f"{product_info['date_str']} | {len(product_info['variables'])} variables"
            )

            if progress_callback:
                progress_callback(0, 0.05)

            client.retrieve(product_info["dataset"], request, nc_path)

            if progress_callback:
                progress_callback(0, 1.0)

            ok = os.path.exists(nc_path) and os.path.getsize(nc_path) > 0
            if ok:
                logger.info(
                    f"[ERA5] Downloaded: {nc_path} "
                    f"({os.path.getsize(nc_path) / 1e6:.1f} MB)"
                )
            else:
                logger.error(f"[ERA5] File missing/empty after retrieve: {nc_path}")
            return ok

        try:
            return await loop.run_in_executor(None, _cds_retrieve)
        except Exception as e:
            logger.error(f"[ERA5] CDS retrieve failed: {e}")
            raise

    # ------------------------------------------------------------------
    # process() — converts ERA5 .nc → 12-band daily-mean GeoTIFF
    # ------------------------------------------------------------------

    async def process(
        self,
        raw_path:     str,
        output_dir:   str,
        geojson_path: str,
    ) -> str:
        """
        Converts ERA5 .nc → 12-band daily-mean GeoTIFF (LZW, EPSG:4326).

        Steps:
            1. Open NetCDF with xarray (engine='netcdf4' — explicit, never guessed)
            2. Reorder variables to match canonical _DEFAULT_VARIABLES band order
            3. Each variable: hourly stack → daily mean (axis=time)
            4. Flip latitude N→S to S→N for rasterio convention
            5. Stack all variables as separate bands
            6. Write LZW-compressed GeoTIFF in EPSG:4326
            7. Tag each band with variable name + band index
            8. Clip to glacier AOI polygon
            9. Delete raw .nc if download.delete_temp_files=True (default)

        Falls back to returning .nc path if xarray/rasterio not available.
        """
        nc_path  = (
            raw_path if raw_path.endswith(".nc")
            else os.path.splitext(raw_path)[0] + ".nc"
        )
        tif_path     = nc_path.replace(".nc", ".tif")
        clipped_path = nc_path.replace(".nc", "_clipped.tif")

        loop = asyncio.get_event_loop()

        def _convert():
            if not os.path.exists(nc_path):
                logger.warning(f"[ERA5] .nc not found at {nc_path} — returning raw path")
                return raw_path

            try:
                import numpy  as np
                import xarray as xr
                import rasterio
                from rasterio.crs       import CRS
                from rasterio.transform import from_bounds
                from rasterio.mask      import mask as rio_mask
                import json as _json

                logger.info(f"[ERA5] Converting {nc_path} → {tif_path}")

                # ── Fix 1: always force engine='netcdf4', never let xarray guess ──
                ds = xr.open_dataset(nc_path, engine="netcdf4")

                # ── Fix 2: use canonical variable order; skip any missing vars ──
                requested_vars: List[str] = self.config.get(
                    "sources.era5.variables", _DEFAULT_VARIABLES
                )
                available_vars = list(ds.data_vars)
                ordered_vars   = [v for v in requested_vars if v in available_vars]

                # Warn if any requested variable is absent in the file
                missing = set(requested_vars) - set(available_vars)
                if missing:
                    logger.warning(
                        f"[ERA5] Variables missing from .nc (will be skipped): {missing}"
                    )

                if not ordered_vars:
                    logger.error(
                        f"[ERA5] No usable variables in {nc_path} — returning .nc"
                    )
                    ds.close()
                    return nc_path

                arrays:     List = []
                band_names: List = []

                for var in ordered_vars:
                    da = ds[var].squeeze(drop=True)

                    # Compute daily mean over time dimension if present
                    if "time" in da.dims:
                        daily = da.mean(dim="time").values.astype("float32")
                    else:
                        daily = da.values.astype("float32")

                    # ERA5 latitude runs North→South — flip to South→North for rasterio
                    lat_vals = da.coords.get(
                        "latitude",
                        da.coords.get("lat", None)
                    )
                    if lat_vals is not None and lat_vals.values[0] > lat_vals.values[-1]:
                        daily = np.flipud(daily)

                    arrays.append(daily)
                    band_names.append(str(var))
                    logger.debug(f"[ERA5]   band '{var}' shape={daily.shape}")

                # Derive spatial extent from dataset coordinates
                lat_coord = ds.get("latitude", ds.get("lat"))
                lon_coord = ds.get("longitude", ds.get("lon"))

                if lat_coord is None or lon_coord is None:
                    logger.error("[ERA5] Cannot find lat/lon coords — returning .nc")
                    ds.close()
                    return nc_path

                lats  = lat_coord.values
                lons  = lon_coord.values
                south = float(lats.min())
                north = float(lats.max())
                west  = float(lons.min())
                east  = float(lons.max())

                h, w      = arrays[0].shape
                transform = from_bounds(west, south, east, north, w, h)

                # ── Step 1: write full-extent GeoTIFF ──
                with rasterio.open(
                    tif_path, "w",
                    driver    = "GTiff",
                    height    = h,
                    width     = w,
                    count     = len(arrays),
                    dtype     = "float32",
                    crs       = CRS.from_epsg(4326),
                    transform = transform,
                    compress  = "lzw",
                ) as dst:
                    for i, (arr, name) in enumerate(zip(arrays, band_names), start=1):
                        dst.write(arr, i)
                        dst.update_tags(i, variable=name, band_index=i)

                ds.close()
                logger.info(
                    f"[ERA5] GeoTIFF written: {tif_path} "
                    f"({len(arrays)} bands)"
                )

                # ── Step 2: clip to glacier AOI polygon ──
                final_path = tif_path   # default: return unclipped if clip fails
                if geojson_path and os.path.exists(geojson_path):
                    try:
                        with open(geojson_path) as gf:
                            gj = _json.load(gf)

                        from shapely.geometry import shape, mapping
                        geometries = [
                            mapping(shape(feat["geometry"]))
                            for feat in gj.get("features", [])
                        ]

                        with rasterio.open(tif_path) as src:
                            clipped_data, clipped_transform = rio_mask(
                                src,
                                geometries,
                                crop    = True,
                                nodata  = float("nan"),
                                filled  = True,
                            )
                            clipped_meta = src.meta.copy()

                        clipped_meta.update({
                            "height":    clipped_data.shape[1],
                            "width":     clipped_data.shape[2],
                            "transform": clipped_transform,
                            "compress":  "lzw",
                            "nodata":    float("nan"),
                        })

                        with rasterio.open(clipped_path, "w", **clipped_meta) as cdst:
                            cdst.write(clipped_data)
                            for i, name in enumerate(band_names, start=1):
                                cdst.update_tags(i, variable=name, band_index=i)

                        # Remove intermediate unclipped tif
                        os.remove(tif_path)
                        final_path = clipped_path
                        logger.info(
                            f"[ERA5] Clipped GeoTIFF → {clipped_path}"
                        )

                    except ImportError:
                        logger.warning(
                            "[ERA5] shapely not installed — skipping clip, "
                            "returning full-extent GeoTIFF"
                        )
                    except Exception as clip_err:
                        logger.warning(
                            f"[ERA5] Clip failed ({clip_err}) — "
                            f"returning full-extent GeoTIFF"
                        )

                # ── Step 3: delete raw .nc if configured ──
                if self.config.get("download.delete_temp_files", True):
                    try:
                        os.remove(nc_path)
                        logger.debug(f"[ERA5] Removed raw .nc: {nc_path}")
                    except Exception:
                        pass

                return final_path

            except ImportError as e:
                logger.warning(
                    f"[ERA5] Missing dependency ({e}) — returning .nc path"
                )
                return nc_path
            except Exception as e:
                logger.error(f"[ERA5] NetCDF→GeoTIFF failed: {e}")
                return nc_path

        return await loop.run_in_executor(None, _convert)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _bbox_from_geojson(self, geojson_path: str) -> Optional[Dict[str, float]]:
        """Extracts N/S/E/W bounding box from GeoJSON polygon."""
        try:
            with open(geojson_path) as f:
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
            logger.error(f"[ERA5] bbox extraction failed: {e}")
            return None
