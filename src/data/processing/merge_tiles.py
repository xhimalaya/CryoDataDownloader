"""
src/data/processing/merge_tiles.py

Merges multiple raster tiles into a single mosaic GeoTIFF.

Used by:
    dem_provider.py → merge_raster_tiles(tile_paths, output_path)

How it works:
    - Uses rasterio.merge.merge() to mosaic all input tiles
    - Preserves CRS and resolution from the first tile
    - Writes output as GeoTIFF with LZW compression
    - Cleans up temp tiles after merge (caller's responsibility)

Dependencies:
    pip install rasterio
"""

import logging
import os
from typing import List, Optional

logger = logging.getLogger(__name__)


def merge_raster_tiles(
    tile_paths: List[str],
    output_path: str,
    nodata: Optional[float] = None,
    resampling_method: str = "nearest",
) -> bool:
    """
    Merges a list of raster tile paths into a single mosaic GeoTIFF.

    Args:
        tile_paths:        List of local file paths to input raster tiles.
                           All tiles must share the same CRS.
        output_path:       Full path for the merged output GeoTIFF.
        nodata:            NoData value to use. If None, reads from first tile.
        resampling_method: Resampling algorithm for overlapping areas.
                           Options: 'nearest', 'bilinear', 'cubic'.
                           Default: 'nearest' (fastest, lossless for DEMs).

    Returns:
        True  → merge succeeded, file written to output_path.
        False → merge failed (logged); caller should handle retry.

    Example:
        ok = merge_raster_tiles(
            tile_paths=["_tile_N27_E086.tif", "_tile_N27_E087.tif"],
            output_path="./data/raw/dem/khumbu/2023/dem_merged.tif",
        )
    """
    if not tile_paths:
        logger.error("[MergeTiles] No tile paths provided — nothing to merge.")
        return False

    # --- Filter: skip tiles that don't exist ---
    valid_tiles = [p for p in tile_paths if os.path.exists(p)]
    missing = len(tile_paths) - len(valid_tiles)

    if missing > 0:
        logger.warning(f"[MergeTiles] {missing} tile(s) missing on disk — skipping.")

    if not valid_tiles:
        logger.error("[MergeTiles] No valid tiles found on disk — cannot merge.")
        return False

    try:
        import rasterio
        from rasterio.merge import merge as rio_merge
        from rasterio.enums import Resampling
    except ImportError as e:
        raise ImportError(
            "rasterio is required for tile merging. "
            "Install with: pip install rasterio"
        ) from e

    # --- Map resampling string to rasterio enum ---
    resampling_map = {
        "nearest":  Resampling.nearest,
        "bilinear": Resampling.bilinear,
        "cubic":    Resampling.cubic,
    }
    resampling = resampling_map.get(resampling_method, Resampling.nearest)

    logger.info(
        f"[MergeTiles] Merging {len(valid_tiles)} tile(s) → {output_path}"
    )

    datasets = []

    try:
        # --- Open all tiles ---
        for path in valid_tiles:
            ds = rasterio.open(path)
            datasets.append(ds)
            logger.debug(f"[MergeTiles] Opened tile: {os.path.basename(path)}")

        # --- Read nodata from first tile if not supplied ---
        if nodata is None:
            nodata = datasets[0].nodata
            if nodata is not None:
                logger.debug(f"[MergeTiles] Using nodata from tile: {nodata}")

        # --- Merge ---
        merged_array, merged_transform = rio_merge(
            datasets,
            nodata=nodata,
            resampling=resampling,
        )

        # --- Copy metadata from first tile ---
        out_meta = datasets[0].meta.copy()
        out_meta.update(
            {
                "driver":    "GTiff",
                "height":    merged_array.shape[1],
                "width":     merged_array.shape[2],
                "transform": merged_transform,
                "compress":  "lzw",
                "tiled":     True,
                "blockxsize": 256,
                "blockysize": 256,
            }
        )

        if nodata is not None:
            out_meta["nodata"] = nodata

        # --- Ensure output directory exists ---
        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        # --- Write merged output ---
        with rasterio.open(output_path, "w", **out_meta) as dest:
            dest.write(merged_array)

        logger.info(
            f"[MergeTiles] Merge complete → {output_path} "
            f"({merged_array.shape[2]}×{merged_array.shape[1]} px)"
        )
        return True

    except Exception as e:
        logger.error(f"[MergeTiles] Merge failed: {e}")

        # Clean up partial output
        if os.path.exists(output_path):
            try:
                os.remove(output_path)
                logger.debug(f"[MergeTiles] Partial output removed: {output_path}")
            except Exception:
                pass

        return False

    finally:
        # --- Always close all open datasets ---
        for ds in datasets:
            try:
                ds.close()
            except Exception:
                pass


__all__ = ["merge_raster_tiles"]
