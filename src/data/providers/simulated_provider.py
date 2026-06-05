import os
import asyncio
import logging
import numpy as np
import rasterio
import geopandas as gpd
from rasterio.transform import from_bounds
from typing import Optional, Dict, Any, Callable

from .base_provider import BaseProvider

logger = logging.getLogger(__name__)

class SimulatedProvider(BaseProvider):
    """
    Simulated provider that generates valid, standards-compliant GeoTIFFs.
    Used for Phase 1 testing and as a fallback when credentials are missing.
    """
    NOT_IMPLEMENTED = False

    async def search(self, geojson_path: str, date_str: str) -> Optional[Dict[str, Any]]:
        # Simulate search latency
        await asyncio.sleep(0.1)
        logger.info(f"[{self.provider_name}] Simulated search complete for {date_str}")
        return {
            "product_name": f"SIMULATED_{self.data_type.upper()}_{date_str}",
            "date": date_str,
            "simulated": True
        }

    async def download(self, product_info: Dict[str, Any], output_path: str, progress_callback: Optional[Callable] = None) -> bool:
        # Simulate download latency
        await asyncio.sleep(0.3)
        if progress_callback:
            progress_callback(0, 0.5)
            await asyncio.sleep(0.2)
            progress_callback(0, 1.0)
        
        # Write dummy raw file
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "wb") as f:
            f.write(b"SIMULATED_RAW_DATA")
            
        logger.info(f"[{self.provider_name}] Simulated download complete.")
        return True

    async def process(self, raw_path: str, output_dir: str, geojson_path: str) -> str:
        # Generate the final GeoTIFF directly using the AOI coordinates
        await asyncio.sleep(0.2)
        
        raw_basename = os.path.basename(raw_path)
        base_name, _ = os.path.splitext(raw_basename)
        final_filename = f"{base_name}.tif"
            
        final_path = os.path.join(output_dir, final_filename)
        
        try:
            height, width = 100, 100
            crs = "EPSG:4326"
            minx, miny, maxx, maxy = 86.9, 27.89, 86.91, 27.9 # Default fallback

            if geojson_path and os.path.exists(geojson_path):
                gdf = gpd.read_file(geojson_path)
                bounds = gdf.total_bounds
                minx, miny, maxx, maxy = bounds
                if gdf.crs:
                    crs = str(gdf.crs)

            transform = from_bounds(minx, miny, maxx, maxy, width, height)
            
            # Determine band count: 3 for sentinel2 (RGB), 12 for era5, 1 for others
            if self.data_type == "sentinel2":
                count = 3
            elif self.data_type == "era5":
                count = 12
            else:
                count = 1

            # Generate smooth synthetic terrain data
            y_idx, x_idx = np.mgrid[0:height, 0:width]
            y_norm = 2.0 * y_idx / (height - 1) - 1.0
            x_norm = 2.0 * x_idx / (width - 1) - 1.0
            
            # Base terrain: diagonal slope
            slope = 0.5 * (x_norm - y_norm)
            
            # Glacier valley: a parabolic valley running from top-left to bottom-right
            dist_to_valley = np.abs(x_norm - y_norm) / np.sqrt(2.0)
            valley = 1.0 - np.minimum(1.0, dist_to_valley / 0.8)
            
            # Smooth peaks/hills: low-frequency sine waves
            hills = 0.15 * np.sin(x_norm * np.pi) * np.cos(y_norm * np.pi)
            
            # Combine and normalize base terrain to [0.0, 1.0] range
            grid_base = (slope + 0.6 * valley + hills).astype(np.float32)
            grid_min = grid_base.min()
            grid_max = grid_base.max()
            if grid_max > grid_min:
                grid_base = (grid_base - grid_min) / (grid_max - grid_min)
            else:
                grid_base = np.zeros_like(grid_base)

            if count == 3:
                # Generate Red, Green, Blue bands representing snow/ice/earth
                band_r = (0.3 + 0.7 * (grid_base ** 1.5)).astype(np.float32)
                band_g = (0.35 + 0.65 * (grid_base ** 1.2)).astype(np.float32)
                band_b = (0.25 + 0.75 * (grid_base ** 0.8)).astype(np.float32)
                grid = np.stack([band_r, band_g, band_b], axis=0)
            elif count == 12:
                # Generate 12 distinct bands representing different ERA5 variables
                bands = []
                for b in range(12):
                    factor = 0.5 + 0.5 * (b / 11.0)
                    band = (0.2 * b/11.0 + factor * (grid_base ** (1.0 + 0.05 * b))).astype(np.float32)
                    bands.append(band)
                grid = np.stack(bands, axis=0)
            else:
                grid = grid_base

            with rasterio.open(
                final_path,
                "w",
                driver="GTiff",
                height=height,
                width=width,
                count=count,
                dtype=grid.dtype,
                crs=crs,
                transform=transform,
                compress="LZW"
            ) as dst:
                if count > 1:
                    dst.write(grid)
                    if count == 3:
                        dst.update_tags(1, band="B04_Red")
                        dst.update_tags(2, band="B03_Green")
                        dst.update_tags(3, band="B02_Blue")
                    elif count == 12:
                        variables = [
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
                        for idx, var_name in enumerate(variables, start=1):
                            dst.update_tags(idx, variable=var_name, band_index=idx)
                else:
                    dst.write(grid, 1)
                
            logger.info(f"[{self.provider_name}] Generated simulated GeoTIFF: {final_path}")
            
            # Cleanup raw dummy
            if os.path.exists(raw_path):
                os.remove(raw_path)
                
            return final_path
            
        except Exception as e:
            logger.error(f"[{self.provider_name}] Failed to generate synthetic GeoTIFF: {e}")
            raise

    # Satisfy abstract methods from BaseProvider if necessary
    async def fetch(self, glacier_id: str, year: int, aoi_path: str, output_dir: str):
        pass

    def validate(self, glacier_id: str, year: int, aoi_path: str, output_dir: str):
        return []
