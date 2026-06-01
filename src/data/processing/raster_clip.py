"""
src/data/processing/raster_clip.py

Clips a raster file to the bounds of a glacier AOI GeoJSON polygon.

Used by:
    dem_provider.py       → clip_to_geojson(raw_path, geojson_path, output_dir)
    sentinel1_provider.py → same
    sentinel2_provider.py → same
    era5_provider.py      → same

How it works:
    - Reads glacier polygon from GeoJSON
    - Reprojects polygon to match raster CRS if needed
    - Masks raster outside polygon boundary
    - Writes clipped output as GeoTIFF with LZW compression

Dependencies:
    pip install rasterio shapely fiona
"""

import json
import logging
import os
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)


def clip_to_geojson(
    raster_path: str,
    geojson_path: str,
    output_dir: str,
    output_suffix: str = "_clipped",
    all_touched: bool = False,
) -> str:
    """
    Clips a raster to the glacier AOI defined in a GeoJSON file.

    Args:
        raster_path:   Path to the input raster (GeoTIFF or GRIB).
        geojson_path:  Path to the glacier AOI GeoJSON polygon file.
        output_dir:    Directory to write the clipped output file.
        output_suffix: Suffix appended to input filename for output.
                       Default: '_clipped'  → 'dem_merged_clipped.tif'
        all_touched:   If True, includes all pixels touching the polygon.
                       If False (default), only pixels whose centre is inside.

    Returns:
        Full path to the clipped output GeoTIFF.

    Raises:
        FileNotFoundError: If raster_path or geojson_path don't exist.
        RuntimeError:      If clipping fails.

    Example:
        out = clip_to_geojson(
            raster_path="./data/raw/dem/khumbu/2023/dem_merged.tif",
            geojson_path="./inputGeoJson/khumbu.geojson",
            output_dir="./data/raw/dem/khumbu/2023/",
        )
        # → "./data/raw/dem/khumbu/2023/dem_merged_clipped.tif"
    """
    # --- Validate inputs ---
    if not os.path.exists(raster_path):
        raise FileNotFoundError(f"[RasterClip] Raster not found: {raster_path}")

    if not os.path.exists(geojson_path):
        raise FileNotFoundError(f"[RasterClip] GeoJSON not found: {geojson_path}")

    # --- Build output path ---
    basename   = os.path.splitext(os.path.basename(raster_path))[0]
    output_path = os.path.join(output_dir, f"{basename}{output_suffix}.tif")
    os.makedirs(output_dir, exist_ok=True)

    logger.info(
        f"[RasterClip] Clipping: {os.path.basename(raster_path)} "
        f"→ {os.path.basename(output_path)}"
    )

    try:
        import rasterio
        from rasterio.mask import mask as rio_mask
        from rasterio.crs import CRS
    except ImportError as e:
        raise ImportError(
            "rasterio is required for clipping. "
            "Install with: pip install rasterio"
        ) from e

    # --- Load and reproject GeoJSON shapes to raster CRS ---
    shapes = _load_shapes(geojson_path)

    if not shapes:
        raise RuntimeError(
            f"[RasterClip] No valid geometries found in: {geojson_path}"
        )

    try:
        with rasterio.open(raster_path) as src:
            raster_crs = src.crs

            # Reproject shapes if GeoJSON CRS (EPSG:4326) differs from raster CRS
            projected_shapes = _reproject_shapes(
                shapes=shapes,
                src_crs="EPSG:4326",
                dst_crs=raster_crs,
            )

            # --- Perform mask/clip ---
            clipped_array, clipped_transform = rio_mask(
                src,
                projected_shapes,
                crop=True,
                all_touched=all_touched,
                nodata=src.nodata,
            )

            # --- Build output metadata ---
            out_meta = src.meta.copy()
            out_meta.update(
                {
                    "driver":    "GTiff",
                    "height":    clipped_array.shape[1],
                    "width":     clipped_array.shape[2],
                    "transform": clipped_transform,
                    "compress":  "lzw",
                    "tiled":     True,
                    "blockxsize": 256,
                    "blockysize": 256,
                }
            )

            # --- Write clipped output ---
            with rasterio.open(output_path, "w", **out_meta) as dest:
                dest.write(clipped_array)

        logger.info(
            f"[RasterClip] Clip complete → {output_path} "
            f"({clipped_array.shape[2]}×{clipped_array.shape[1]} px)"
        )
        return output_path

    except Exception as e:
        logger.error(f"[RasterClip] Clip failed for '{raster_path}': {e}")

        # Clean up partial output
        if os.path.exists(output_path):
            try:
                os.remove(output_path)
                logger.debug(f"[RasterClip] Partial output removed: {output_path}")
            except Exception:
                pass

        raise RuntimeError(f"[RasterClip] Clip failed: {e}") from e


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _load_shapes(geojson_path: str) -> List[dict]:
    """
    Loads geometry shapes from a GeoJSON file.

    Returns:
        List of GeoJSON geometry dicts (not Feature dicts).
        Only Polygon and MultiPolygon geometries are included.
    """
    with open(geojson_path, "r") as f:
        geojson = json.load(f)

    shapes = []
    geojson_type = geojson.get("type", "")

    def extract(geometry):
        if geometry is None:
            return
        gtype = geometry.get("type", "")
        if gtype in ("Polygon", "MultiPolygon"):
            shapes.append(geometry)
        elif gtype == "GeometryCollection":
            for geom in geometry.get("geometries", []):
                extract(geom)

    if geojson_type == "FeatureCollection":
        for feature in geojson.get("features", []):
            extract(feature.get("geometry"))
    elif geojson_type == "Feature":
        extract(geojson.get("geometry"))
    else:
        extract(geojson)

    logger.debug(f"[RasterClip] Loaded {len(shapes)} polygon geometry(ies).")
    return shapes


def _reproject_shapes(
    shapes: List[dict],
    src_crs: str,
    dst_crs,
) -> List[dict]:
    """
    Reprojects a list of GeoJSON geometry dicts from src_crs to dst_crs.

    If CRS already matches, returns shapes unchanged.

    Args:
        shapes:  List of GeoJSON geometry dicts.
        src_crs: Source CRS string e.g. 'EPSG:4326'.
        dst_crs: Target rasterio CRS object.

    Returns:
        List of reprojected GeoJSON geometry dicts.
    """
    try:
        from rasterio.crs import CRS
        from rasterio.warp import transform_geom
    except ImportError:
        logger.warning(
            "[RasterClip] rasterio.warp unavailable — skipping reprojection."
        )
        return shapes

    # Compare CRS — skip reprojection if already matching
    try:
        src = CRS.from_string(src_crs)
        if src == dst_crs:
            logger.debug("[RasterClip] CRS match — no reprojection needed.")
            return shapes
    except Exception:
        pass

    reprojected = []
    for geom in shapes:
        try:
            reprojected.append(
                transform_geom(
                    src_crs=src_crs,
                    dst_crs=dst_crs,
                    geom=geom,
                )
            )
        except Exception as e:
            logger.warning(f"[RasterClip] Reprojection failed for geometry: {e}")

    logger.debug(
        f"[RasterClip] Reprojected {len(reprojected)} geometry(ies) "
        f"from {src_crs} → {dst_crs}"
    )
    return reprojected


__all__ = ["clip_to_geojson"]
