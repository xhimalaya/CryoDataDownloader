# src/data/providers/__init__.py

import logging
from typing import Any

from .base_provider      import BaseProvider, ProviderResult
from .dem_provider       import DEMProvider
from .era5_provider      import ERA5Provider
from .sentinel2_provider import Sentinel2Provider

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Stub provider — placeholder for unbuilt providers
# ---------------------------------------------------------------------------

class _StubProvider(BaseProvider):
    """
    Temporary stub for sources not yet implemented.
    Returns None / False / raw_path immediately — never retried.
    Flag NOT_IMPLEMENTED = True causes download_engine to SKIP instantly.
    """
    NOT_IMPLEMENTED = True

    async def search(self, geojson_path: str, date_str: str):
        logger.warning(
            f"[{self.__class__.__name__}] STUB — search() not implemented."
        )
        return None

    async def download(self, product_info, output_path, progress_callback=None):
        return False

    async def process(self, raw_path, output_dir, geojson_path):
        return raw_path

    async def fetch(self, glacier_id, year, aoi_path, output_dir):
        return self._make_result(
            glacier_id = glacier_id,
            year       = year,
            success    = False,
            errors     = [f"{self.__class__.__name__} is a stub — not implemented."],
        )

    def validate(self, glacier_id, year, aoi_path, output_dir):
        return []


# ---------------------------------------------------------------------------
# Stub factory
# ---------------------------------------------------------------------------

def _make_stub(name: str, dtype: str) -> type:
    """Creates a named stub provider class dynamically."""
    return type(
        name,
        (_StubProvider,),
        {
            "provider_name": name,
            "data_type":     dtype,
        },
    )


# Stub classes for unbuilt providers
Sentinel1Provider = _make_stub("Sentinel1Provider", "sentinel1")
MODISProvider     = _make_stub("MODISProvider",     "modis_lst")
LandsatProvider   = _make_stub("LandsatProvider",   "landsat_thermal")
ALOS2Provider     = _make_stub("ALOS2Provider",     "alos2")
GRACEProvider     = _make_stub("GRACEProvider",     "grace")


# ---------------------------------------------------------------------------
# Provider registry
# ---------------------------------------------------------------------------

_REGISTRY: dict = {
    # ── Live providers ──────────────────────────────
    "dem":             DEMProvider,
    "era5":            ERA5Provider,
    "sentinel2":       Sentinel2Provider,

    # ── Stubs (SKIPPED instantly, no retries) ───────
    "sentinel1":       Sentinel1Provider,
    "modis_lst":       MODISProvider,
    "landsat_thermal": LandsatProvider,
    "alos2":           ALOS2Provider,
    "grace":           GRACEProvider,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_provider(source: str, config: Any) -> BaseProvider:
    """
    Maps source key → instantiated provider.

    Stubs flagged with NOT_IMPLEMENTED=True are caught by download_engine
    and marked SKIPPED immediately — zero retries wasted.
    """
    key = source.strip().lower()
    cls = _REGISTRY.get(key)

    if cls is None:
        raise ValueError(
            f"Unknown source '{source}'. "
            f"Registered: {list(_REGISTRY.keys())}"
        )

    instance = cls(config=config, db=None, auth=None)

    if getattr(instance, "NOT_IMPLEMENTED", False):
        logger.warning(
            f"[ProviderRegistry] '{source}' → STUB "
            f"(will be SKIPPED, not retried)"
        )

    return instance


def register_provider(source: str, cls: type) -> None:
    """Swap a stub for a real provider at runtime without editing this file."""
    _REGISTRY[source.strip().lower()] = cls
    logger.info(f"[ProviderRegistry] Registered: '{source}' → {cls.__name__}")


__all__ = [
    "BaseProvider",
    "ProviderResult",
    "DEMProvider",
    "ERA5Provider",
    "Sentinel2Provider",
    "get_provider",
    "register_provider",
]
