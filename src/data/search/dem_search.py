"""
src/data/search/dem_search.py

Finds all Copernicus GLO-30 DEM tile S3 keys that intersect a glacier AOI.

Called by:
    dem_provider.py → tiles = await find_intersecting_dem_tiles(geojson_path)

How GLO-30 tiles are structured on S3 (EODATA):
    Copernicus DEM tiles follow a strict naming grid:
    Each tile covers exactly 1° × 1° in WGS84 (EPSG:4326).
    Tile name encodes its SW corner:

    Pattern:
        Copernicus_DSM_COG_10_N{lat:02d}_00_E{lon:03d}_00_DEM/
        └── Copernicus_DSM_COG_10_N{lat:02d}_00_E{lon:03d}_00_DEM.tif

    Examples:
        N27_E086 → covers lat 27–28°N, lon 86–87°E  (Khumbu / Everest)
        N46_E007 → covers lat 46–47°N, lon 7–8°E    (Swiss Alps)
        N63_W019 → covers lat 63–64°N, lon 19–20°W  (Iceland)

Algorithm:
    1. Load glacier AOI polygon from GeoJSON
    2. Compute bounding box (min_lon, min_lat, max_lon, max_lat)
    3. Generate integer degree grid covering the bounding box
    4. Build S3 key for each grid cell
    5. Return list of S3 keys

No S3 calls are made here — tile names are computed deterministically
from the bounding box. S3StreamDownloader handles actual existence checks
during download.

Dependencies:
    stdlib + json only (no rasterio / shapely needed here)
"""

import json
import logging
import math
import os
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# GLO-30 S3 path template
# ---------------------------------------------------------------------------

# Base prefix in the Copernicus EODATA S3 bucket
_GLO30_BASE = "Copernicus/DEM/release/2021_1/DEM1_SAR_DEM_1deg_v2"

# Tile key template — uses SW corner lat/lon integers
# Example: N27_E086 → lat=27, lon=86
_TILE_KEY_TEMPLATE = (
    "{base}/"
    "Copernicus_DSM_COG_10_{lat_pfx}{lat:02d}_00_{lon_pfx}{lon:03d}_00_DEM/"
    "Copernicus_DSM_COG_10_{lat_pfx}{lat:02d}_00_{lon_pfx}{lon:03d}_00_DEM.tif"
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def find_intersecting_dem_tiles(geojson_path: str) -> List[str]:
    """
    Returns a list of GLO-30 S3 tile keys that cover the glacier AOI.

    Tile names are computed deterministically from the AOI bounding box —
    no S3 listing or network calls are made.

    Args:
        geojson_path: Path to glacier AOI GeoJSON file (EPSG:4326).

    Returns:
        List of S3 key strings for all intersecting GLO-30 tiles.
        Empty list if AOI cannot be parsed or no tiles computed.

    Example:
        tiles = await find_intersecting_dem_tiles("./inputGeoJson/khumbu.geojson")
        # → [
        #     "Copernicus/DEM/.../Copernicus_DSM_COG_10_N27_00_E086_00_DEM/...tif",
        #     "Copernicus/DEM/.../Copernicus_DSM_COG_10_N27_00_E087_00_DEM/...tif",
        # ]
    """
    if not geojson_path or not os.path.exists(geojson_path):
        logger.error(f"[DEMSearch] GeoJSON not found: {geojson_path}")
        return []

    # --- Load bounding box from GeoJSON ---
    bbox = _extract_bbox(geojson_path)
    if bbox is None:
        logger.error(
            f"[DEMSearch] Could not extract bounding box from: {geojson_path}"
        )
        return []

    min_lon, min_lat, max_lon, max_lat = bbox

    logger.info(
        f"[DEMSearch] AOI bbox → "
        f"lat [{min_lat:.4f} → {max_lat:.4f}], "
        f"lon [{min_lon:.4f} → {max_lon:.4f}]"
    )

    # --- Generate tile S3 keys from degree grid ---
    tiles = _build_tile_keys(min_lon, min_lat, max_lon, max_lat)

    logger.info(
        f"[DEMSearch] {len(tiles)} GLO-30 tile(s) computed for AOI."
    )

    for t in tiles:
        logger.debug(f"[DEMSearch] Tile key: {t}")

    return tiles


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _extract_bbox(
    geojson_path: str,
) -> Optional[Tuple[float, float, float, float]]:
    """
    Extracts the bounding box of all coordinates in a GeoJSON file.

    Supports:
        - FeatureCollection
        - Feature
        - Polygon
        - MultiPolygon

    Returns:
        Tuple (min_lon, min_lat, max_lon, max_lat) in EPSG:4326 degrees.
        None if the file cannot be parsed or contains no coordinates.
    """
    try:
        with open(geojson_path, "r") as f:
            geojson = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.error(f"[DEMSearch] Failed to read GeoJSON '{geojson_path}': {e}")
        return None

    coords: List[Tuple[float, float]] = []
    _collect_coords(geojson, coords)

    if not coords:
        logger.warning(f"[DEMSearch] No coordinates found in: {geojson_path}")
        return None

    lons = [c[0] for c in coords]
    lats = [c[1] for c in coords]

    bbox = (min(lons), min(lats), max(lons), max(lats))
    logger.debug(f"[DEMSearch] Extracted bbox: {bbox}")
    return bbox


def _collect_coords(
    obj: dict,
    coords: List[Tuple[float, float]],
) -> None:
    """
    Recursively collects all [lon, lat] coordinate pairs from any
    GeoJSON object into the coords list.

    Handles:
        FeatureCollection → Feature → Geometry → coordinates[]
    """
    if not isinstance(obj, dict):
        return

    obj_type = obj.get("type", "")

    if obj_type == "FeatureCollection":
        for feature in obj.get("features", []):
            _collect_coords(feature, coords)

    elif obj_type == "Feature":
        geometry = obj.get("geometry")
        if geometry:
            _collect_coords(geometry, coords)

    elif obj_type == "Polygon":
        for ring in obj.get("coordinates", []):
            for point in ring:
                if len(point) >= 2:
                    coords.append((point[0], point[1]))

    elif obj_type == "MultiPolygon":
        for polygon in obj.get("coordinates", []):
            for ring in polygon:
                for point in ring:
                    if len(point) >= 2:
                        coords.append((point[0], point[1]))

    elif obj_type == "GeometryCollection":
        for geom in obj.get("geometries", []):
            _collect_coords(geom, coords)


def _build_tile_keys(
    min_lon: float,
    min_lat: float,
    max_lon: float,
    max_lat: float,
) -> List[str]:
    """
    Generates GLO-30 S3 tile keys for all 1°×1° cells covering the bbox.

    GLO-30 tiles are named by their SW corner integer degree.
    For bbox spanning lat 27.3–28.1, lon 86.7–87.4:
        → tiles at (lat=27, lon=86), (lat=27, lon=87), (lat=28, lon=86), (lat=28, lon=87)

    Args:
        min_lon, min_lat: SW corner of AOI bounding box.
        max_lon, max_lat: NE corner of AOI bounding box.

    Returns:
        List of S3 key strings.
    """
    # Floor to integer degree for SW tile corner
    lat_start = int(math.floor(min_lat))
    lon_start = int(math.floor(min_lon))

    # Ceil for NE corner — tiles whose SW corner is < max value
    lat_end   = int(math.floor(max_lat))
    lon_end   = int(math.floor(max_lon))

    tile_keys: List[str] = []

    for lat in range(lat_start, lat_end + 1):
        for lon in range(lon_start, lon_end + 1):

            # Build N/S and E/W prefix
            lat_pfx = "N" if lat >= 0 else "S"
            lon_pfx = "E" if lon >= 0 else "W"

            # Absolute values for zero-padded formatting
            abs_lat = abs(lat)
            abs_lon = abs(lon)

            key = _TILE_KEY_TEMPLATE.format(
                base    = _GLO30_BASE,
                lat_pfx = lat_pfx,
                lat     = abs_lat,
                lon_pfx = lon_pfx,
                lon     = abs_lon,
            )

            tile_keys.append(key)
            logger.debug(
                f"[DEMSearch] Grid cell → "
                f"{lat_pfx}{abs_lat:02d} {lon_pfx}{abs_lon:03d} : {key}"
            )

    return tile_keys


def _bbox_from_geojson_bbox_field(geojson: dict) -> Optional[Tuple[float, float, float, float]]:
    """
    Reads the optional top-level 'bbox' field from a GeoJSON object
    if present, as a faster alternative to coordinate scanning.

    GeoJSON spec: bbox = [min_lon, min_lat, max_lon, max_lat]

    Returns:
        Tuple (min_lon, min_lat, max_lon, max_lat) or None.
    """
    bbox = geojson.get("bbox")
    if bbox and len(bbox) >= 4:
        return (
            float(bbox[0]),
            float(bbox[1]),
            float(bbox[2]),
            float(bbox[3]),
        )
    return None


__all__ = ["find_intersecting_dem_tiles"]
