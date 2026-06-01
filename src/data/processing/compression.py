"""
src/data/processing/compression.py

Compresses a GeoTIFF file using LZW compression in-place.

Used by:
    dem_provider.py       → compress_lzw(clipped_path)
    sentinel1_provider.py → same
    sentinel2_provider.py → same

How it works:
    - Reads the input GeoTIFF
    - Rewrites it with LZW + tiling + predictor=2 (optimal for elevation/float data)
    - Replaces the original file (in-place compression)
    - Returns the path to the compressed file

Design notes:
    - If file is already LZW compressed → skips rewrite (no-op)
    - Temp file used during rewrite → atomic swap → original replaced
    - Never deletes input if compression fails

Dependencies:
    pip install rasterio
"""

import logging
import os
import shutil
import tempfile

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_COMPRESS_METHOD  = "lzw"
_TILE_SIZE        = 256        # pixels — standard Cloud-Optimized GeoTIFF block
_PREDICTOR_INT    = 2          # horizontal differencing — best for integer DEMs
_PREDICTOR_FLOAT  = 3          # floating-point predictor — best for float32 bands


def compress_lzw(
    input_path: str,
    output_path: str = None,
    tile_size: int = _TILE_SIZE,
) -> str:
    """
    Compresses a GeoTIFF with LZW encoding and tiling.

    If output_path is None, compresses in-place (replaces input file).
    If output_path is provided, writes compressed copy to that path.

    Args:
        input_path:  Path to the input GeoTIFF.
        output_path: Optional path for output. None = in-place compression.
        tile_size:   Tile block size in pixels. Default 256.

    Returns:
        Path to the compressed GeoTIFF (same as input_path if in-place).

    Raises:
        FileNotFoundError: If input_path doesn't exist.
        RuntimeError:      If compression fails.

    Example:
        # In-place:
        final = compress_lzw("./data/raw/dem/khumbu/dem_merged_clipped.tif")

        # To new path:
        final = compress_lzw(
            "./data/raw/dem/khumbu/dem_merged_clipped.tif",
            output_path="./data/processed/dem/khumbu/dem_final.tif"
        )
    """
    if not os.path.exists(input_path):
        raise FileNotFoundError(f"[Compression] Input not found: {input_path}")

    try:
        import rasterio
    except ImportError as e:
        raise ImportError(
            "rasterio is required for compression. "
            "Install with: pip install rasterio"
        ) from e

    # --- Check if already compressed ---
    if _is_already_lzw(input_path):
        logger.info(
            f"[Compression] Already LZW compressed — skipping: "
            f"{os.path.basename(input_path)}"
        )
        return output_path if output_path else input_path

    in_place = output_path is None
    final_path = input_path if in_place else output_path

    logger.info(
        f"[Compression] Compressing (LZW) → "
        f"{os.path.basename(final_path)}"
    )

    # --- Use a temp file for atomic swap ---
    tmp_fd, tmp_path = tempfile.mkstemp(
        suffix=".tif",
        dir=os.path.dirname(input_path),
    )
    os.close(tmp_fd)

    try:
        with rasterio.open(input_path) as src:
            # Choose predictor based on dtype
            predictor = _choose_predictor(src)

            out_meta = src.meta.copy()
            out_meta.update(
                {
                    "driver":    "GTiff",
                    "compress":  _COMPRESS_METHOD,
                    "tiled":     True,
                    "blockxsize": tile_size,
                    "blockysize": tile_size,
                    "predictor": predictor,
                }
            )

            with rasterio.open(tmp_path, "w", **out_meta) as dst:
                # Band-by-band copy — memory efficient for multi-band rasters
                for band_idx in range(1, src.count + 1):
                    data = src.read(band_idx)
                    dst.write(data, band_idx)

                    logger.debug(
                        f"[Compression] Band {band_idx}/{src.count} written."
                    )

        # --- Atomic replace ---
        if in_place:
            os.replace(tmp_path, input_path)
            logger.info(
                f"[Compression] In-place compression complete → "
                f"{os.path.basename(input_path)}"
            )
        else:
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            shutil.move(tmp_path, output_path)
            logger.info(
                f"[Compression] Compressed copy written → "
                f"{os.path.basename(output_path)}"
            )

        return final_path

    except Exception as e:
        logger.error(f"[Compression] Failed for '{input_path}': {e}")

        # Clean up temp file on failure
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass

        raise RuntimeError(f"[Compression] LZW compression failed: {e}") from e


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _is_already_lzw(path: str) -> bool:
    """
    Checks if a GeoTIFF is already LZW compressed.

    Returns True if compression == 'lzw', False otherwise.
    """
    try:
        import rasterio
        with rasterio.open(path) as src:
            profile = src.profile
            compress = profile.get("compress", "").lower()
            return compress == "lzw"
    except Exception:
        return False


def _choose_predictor(src) -> int:
    """
    Picks the optimal LZW predictor based on raster dtype.

    Predictor 2 (horizontal differencing) → integer dtypes (uint8, int16, uint16)
    Predictor 3 (floating-point)          → float32, float64

    Returns:
        2 or 3 as integer.
    """
    import numpy as np

    dtype = src.dtypes[0] if src.count > 0 else "uint8"

    float_dtypes = {"float32", "float64", "float16"}

    if dtype in float_dtypes:
        logger.debug(f"[Compression] dtype={dtype} → predictor=3 (float)")
        return _PREDICTOR_FLOAT

    logger.debug(f"[Compression] dtype={dtype} → predictor=2 (int)")
    return _PREDICTOR_INT


__all__ = ["compress_lzw"]
