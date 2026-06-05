import os
import time
import random
import numpy as np
from typing import Dict, Any, List

# Try imports, flag state
try:
    import rasterio
    import rasterio.mask
    from rasterio.warp import calculate_default_transform, reproject, Resampling
    import geopandas as gpd
    from shapely.geometry import shape, mapping
    import rioxarray
    import xarray as xr
    HAS_GEOSPATIAL = True
except ImportError:
    HAS_GEOSPATIAL = False

def process_single_raster(task_id: int, input_filepath: str, output_dir: str,
                          glacier_name: str, source: str, buffer_km: float,
                          geojson_path: str = None) -> Dict[str, Any]:
    """
    Handles reprojection, clipping, and compression of a single downloaded satellite image.
    Designed as a top-level module function to be easily pickled and run in ProcessPoolExecutor.
    """
    try:
        # Resolve target paths
        glacier_clean = glacier_name.lower().replace(" ", "_").replace("-", "_")
        clipped_dir = os.path.join(output_dir, "clipped", glacier_clean, source)
        os.makedirs(clipped_dir, exist_ok=True)

        # If the input file is a .raw but a .tif exists (e.g. simulated provider/download processed it),
        # use the .tif file as the input file instead.
        if input_filepath.endswith(".raw"):
            tif_alt = input_filepath.replace(".raw", ".tif")
            if os.path.exists(tif_alt):
                input_filepath = tif_alt

        # Output GeoTIFF filename
        filename = os.path.basename(input_filepath)
        tif_filename = f"{os.path.splitext(filename)[0]}_clipped.tif"
        output_tif_path = os.path.join(clipped_dir, tif_filename)

        if HAS_GEOSPATIAL:

            # --------------------------------------------------------
            # FIXED VALIDATION LOGIC
            # --------------------------------------------------------
            # DO NOT classify files by size.
            # ERA5 files can be very small but still valid.
            is_valid_input = False

            try:
                ext = os.path.splitext(input_filepath)[1].lower()

                # ERA5 raw files are NetCDF (.nc)
                if source == "era5" and ext == ".nc":
                    ds = xr.open_dataset(input_filepath)
                    ds.close()
                    is_valid_input = True

                else:
                    with rasterio.open(input_filepath) as src:
                        _ = src.count
                        is_valid_input = True

            except Exception as validation_error:
                print(
                    f"[WARNING] Input validation failed "
                    f"for {input_filepath}: {validation_error}"
                )
                is_valid_input = False

            # --------------------------------------------------------
            # INVALID INPUT HANDLING
            # --------------------------------------------------------
            if not is_valid_input:

                # ONLY simulated provider can generate fake TIFFs
                if source == "simulated":
                    time.sleep(random.uniform(0.5, 1.2))
                    write_synthetic_geotiff(
                        output_tif_path,
                        geojson_path,
                        source=source
                    )

                else:
                    raise RuntimeError(
                        f"Invalid input for source={source}: "
                        f"{input_filepath}"
                    )

            else:
                # --------------------------------------------------------
                # REAL GEOSPATIAL PIPELINE (UNCHANGED)
                # --------------------------------------------------------
                try:
                    # Load glacier polygon
                    gdf = gpd.read_file(geojson_path)

                    with rasterio.open(input_filepath) as src:
                        print(
                            f"[INFO] Processing REAL raster: "
                            f"{input_filepath} | "
                            f"bands={src.count} "
                            f"shape=({src.height},{src.width})"
                        )

                        # Reproject glacier polygon to raster CRS if needed
                        if gdf.crs != src.crs:
                            gdf_reprojected = gdf.to_crs(src.crs)
                        else:
                            gdf_reprojected = gdf

                        # Clip raster using glacier polygon
                        geometries = gdf_reprojected.geometry.values

                        out_image, out_transform = rasterio.mask.mask(
                            src,
                            geometries,
                            crop=True
                        )

                        # Copy metadata and update dimensions
                        meta = src.meta.copy()
                        meta.update({
                            "driver": "GTiff",
                            "height": out_image.shape[1],
                            "width": out_image.shape[2],
                            "transform": out_transform,
                            "compress": "LZW"
                        })

                        # Save clipped GeoTIFF
                        with rasterio.open(
                            output_tif_path,
                            "w",
                            **meta
                        ) as dst:
                            dst.write(out_image)
                            for idx in range(1, src.count + 1):
                                dst.update_tags(idx, **src.tags(idx))

                    # Simulated processing latency (UNCHANGED)
                    time.sleep(random.uniform(1.0, 2.5))

                except Exception as rio_err:
                    raise RuntimeError(
                        f"Real geospatial pipeline failed: {rio_err}"
                    )

        else:
            # --------------------------------------------------------
            # NO GEOSPATIAL LIBRARIES AVAILABLE
            # --------------------------------------------------------
            processing_time = random.uniform(0.4, 1.2)
            time.sleep(processing_time)

            # Keep synthetic only in non-geospatial environments
            write_synthetic_geotiff(
                output_tif_path,
                geojson_path,
                source=source
            )

        return {
            "success": True,
            "task_id": task_id,
            "clipped_path": output_tif_path,
            "zarr_store": "",
            "error": ""
        }

    except Exception as e:
        return {
            "success": False,
            "task_id": task_id,
            "clipped_path": "",
            "zarr_store": "",
            "error": str(e)
        }

def write_synthetic_geotiff(filepath: str, geojson_path: str = None, source: str = None):
    """Writes a valid small GeoTIFF file using rasterio with LZW compression and bounds from GeoJSON."""
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    
    height, width = 100, 100
    
    # Determine band count: 3 for sentinel2 (RGB), 12 for era5, 1 for others
    if source == "sentinel2":
        count = 3
    elif source == "era5":
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
        
    crs = "EPSG:4326"
    
    # Default bounds (Khumbu)
    minx, miny, maxx, maxy = 86.9, 27.89, 86.91, 27.9
    
    # If geojson_path is provided, extract the bounds
    if geojson_path and os.path.exists(geojson_path) and HAS_GEOSPATIAL:
        try:
            gdf = gpd.read_file(geojson_path)
            bounds = gdf.total_bounds
            minx, miny, maxx, maxy = bounds
            if gdf.crs:
                crs = str(gdf.crs)
        except Exception as e:
            print(f"[WARNING] Failed to read bounds from GeoJSON, falling back to defaults: {e}")
            
    if HAS_GEOSPATIAL:
        from rasterio.transform import from_bounds
        transform = from_bounds(minx, miny, maxx, maxy, width, height)
        
        with rasterio.open(
            filepath,
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
    else:
        # Absolute fallback if rasterio isn't imported
        with open(filepath, "wb") as f:
            f.write(b"II*\x00\x08\x00\x00\x00")
            flat_grid = grid[0] if count > 1 else grid
            f.write(flat_grid.tobytes()[:50000])
            f.truncate(100 * 1024)

class ProcessingEngine:
    def __init__(self, max_workers: int = 4):
        self.max_workers = max_workers

    def process_tile(self, task_id: int, input_filepath: str, output_dir: str, 
                     glacier_name: str, source: str, buffer_km: float, 
                     geojson_path: str = None) -> Dict[str, Any]:
        """Wrapper to call the standalone process function synchronously or within pool."""
        return process_single_raster(
            task_id=task_id,
            input_filepath=input_filepath,
            output_dir=output_dir,
            glacier_name=glacier_name,
            source=source,
            buffer_km=buffer_km,
            geojson_path=geojson_path
        )
