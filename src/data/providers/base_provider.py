"""
src/data/providers/base_provider.py

Abstract base class for all data providers.

Implemented by:
    dem_provider.py       → DEMProvider
    sentinel1_provider.py → Sentinel1Provider
    sentinel2_provider.py → Sentinel2Provider
    era5_provider.py      → ERA5Provider

Design contract:
    Every provider MUST implement:
        - fetch()     → full pipeline: search → download → process → record
        - validate()  → pre-flight checks before fetch() is called

    Every provider receives at __init__:
        - config      → ConfigManager instance
        - db          → DBManager instance
        - auth        → CopernicusAuth instance (or None for ERA5)

    Every provider exposes:
        - provider_name  (str)  → used in logs and DB records
        - data_type      (str)  → 'dem' | 'sentinel1' | 'sentinel2' | 'era5'
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared result dataclass — returned by every provider's fetch()
# ---------------------------------------------------------------------------

@dataclass
class ProviderResult:
    """
    Standardised result object returned by provider.fetch().

    Attributes:
        success:       True if at least one file was downloaded and processed.
        provider:      Provider name e.g. 'DEMProvider'.
        data_type:     Data type e.g. 'dem', 'sentinel1', 'era5'.
        glacier_id:    Glacier ID processed e.g. 'khumbu'.
        year:          Year processed e.g. 2023.
        files:         List of absolute paths to final output files.
        record_ids:    List of DB row IDs inserted into downloads table.
        errors:        List of error messages encountered (non-fatal).
        duration_sec:  Total time taken for fetch() in seconds.
        meta:          Provider-specific extra metadata dict.
    """
    success:      bool
    provider:     str
    data_type:    str
    glacier_id:   str
    year:         int
    files:        List[str]       = field(default_factory=list)
    record_ids:   List[int]       = field(default_factory=list)
    errors:       List[str]       = field(default_factory=list)
    duration_sec: float           = 0.0
    meta:         Dict[str, Any]  = field(default_factory=dict)

    def summary(self) -> str:
        """
        Returns a one-line human-readable summary of the result.

        Example:
            '[DEMProvider] khumbu/2023 → 3 file(s) in 12.4s ✓'
            '[DEMProvider] khumbu/2023 → FAILED (2 error(s))'
        """
        if self.success:
            return (
                f"[{self.provider}] {self.glacier_id}/{self.year} → "
                f"{len(self.files)} file(s) in {self.duration_sec:.1f}s ✓"
            )
        else:
            return (
                f"[{self.provider}] {self.glacier_id}/{self.year} → "
                f"FAILED ({len(self.errors)} error(s))"
            )


# ---------------------------------------------------------------------------
# Abstract base provider
# ---------------------------------------------------------------------------

class BaseProvider(ABC):
    """
    Abstract base class that all data providers must inherit from.

    Enforces a common interface across DEM, Sentinel-1, Sentinel-2,
    and ERA5 providers so the download engine can call them uniformly.

    Subclass usage:
        class DEMProvider(BaseProvider):
            provider_name = "DEMProvider"
            data_type     = "dem"

            async def fetch(self, glacier_id, year, aoi_path, output_dir):
                ...

            def validate(self, glacier_id, year, aoi_path, output_dir):
                ...

    Args:
        config: ConfigManager instance — access to all config values.
        db:     DBManager instance — for recording downloads.
        auth:   CopernicusAuth instance — for S3/STAC credentials.
                Pass None for providers that don't use Copernicus auth (ERA5).
    """

    # --- Subclasses MUST set these class-level attributes ---
    provider_name: str = "BaseProvider"
    data_type:     str = "unknown"

    def __init__(self, config, db, auth=None):
        self.config = config
        self.db     = db
        self.auth   = auth
        self._log   = logging.getLogger(
            f"{__name__}.{self.__class__.__name__}"
        )

    # -----------------------------------------------------------------------
    # Abstract interface — subclasses must implement both
    # -----------------------------------------------------------------------

    @abstractmethod
    async def fetch(
        self,
        glacier_id: str,
        year:       int,
        aoi_path:   str,
        output_dir: str,
    ) -> ProviderResult:
        """
        Full data pipeline for one glacier / one year.

        Steps (provider-specific):
            1. Search for available data (STAC / CDS API / S3 listing)
            2. Download raw files (S3 stream / HTTPS / CDS)
            3. Process files (merge tiles, clip to AOI, compress)
            4. Record results to DB (downloads table)
            5. Return ProviderResult

        Args:
            glacier_id: Glacier identifier e.g. 'khumbu'.
            year:       Target year e.g. 2023.
            aoi_path:   Path to glacier AOI GeoJSON file.
            output_dir: Root output directory for processed files.

        Returns:
            ProviderResult with success flag, file paths, and errors.
        """
        ...

    @abstractmethod
    def validate(
        self,
        glacier_id: str,
        year:       int,
        aoi_path:   str,
        output_dir: str,
    ) -> List[str]:
        """
        Pre-flight validation before fetch() is called.

        Checks:
            - AOI file exists and is valid GeoJSON
            - Output directory is writable
            - Required config keys are present
            - Auth credentials are available (if needed)

        Args:
            glacier_id: Glacier identifier.
            year:       Target year.
            aoi_path:   Path to glacier AOI GeoJSON file.
            output_dir: Root output directory.

        Returns:
            List of validation error strings.
            Empty list [] means all checks passed.

        Example:
            errors = provider.validate('khumbu', 2023, aoi, outdir)
            if errors:
                for e in errors:
                    print(f"Validation error: {e}")
            else:
                result = await provider.fetch(...)
        """
        ...

    # -----------------------------------------------------------------------
    # Shared helpers — available to all subclasses
    # -----------------------------------------------------------------------

    def _start_timer(self) -> datetime:
        """Records fetch start time. Call at top of fetch()."""
        return datetime.utcnow()

    def _elapsed(self, start: datetime) -> float:
        """
        Returns seconds elapsed since start.

        Args:
            start: datetime returned by _start_timer().

        Returns:
            Float seconds elapsed.
        """
        return (datetime.utcnow() - start).total_seconds()

    def _make_result(
        self,
        glacier_id:   str,
        year:         int,
        success:      bool,
        files:        Optional[List[str]] = None,
        record_ids:   Optional[List[int]] = None,
        errors:       Optional[List[str]] = None,
        duration_sec: float = 0.0,
        meta:         Optional[Dict[str, Any]] = None,
    ) -> ProviderResult:
        """
        Convenience factory for building a ProviderResult.

        Args:
            glacier_id:   Glacier ID.
            year:         Year processed.
            success:      True if pipeline succeeded.
            files:        List of output file paths.
            record_ids:   List of DB row IDs.
            errors:       List of non-fatal error strings.
            duration_sec: Time taken in seconds.
            meta:         Extra provider-specific metadata.

        Returns:
            Populated ProviderResult instance.
        """
        return ProviderResult(
            success      = success,
            provider     = self.provider_name,
            data_type    = self.data_type,
            glacier_id   = glacier_id,
            year         = year,
            files        = files        or [],
            record_ids   = record_ids   or [],
            errors       = errors       or [],
            duration_sec = duration_sec,
            meta         = meta         or {},
        )

    def _validate_aoi(self, aoi_path: str) -> List[str]:
        """
        Shared AOI validation — checks file exists and is valid GeoJSON.

        Args:
            aoi_path: Path to AOI GeoJSON file.

        Returns:
            List of error strings (empty = valid).
        """
        import json
        import os

        errors = []

        if not aoi_path:
            errors.append("aoi_path is empty or None.")
            return errors

        if not os.path.exists(aoi_path):
            errors.append(f"AOI file not found: {aoi_path}")
            return errors

        try:
            with open(aoi_path, "r") as f:
                data = json.load(f)

            if data.get("type") not in (
                "FeatureCollection", "Feature", "Polygon", "MultiPolygon"
            ):
                errors.append(
                    f"AOI GeoJSON has unsupported type: {data.get('type')}"
                )
        except json.JSONDecodeError as e:
            errors.append(f"AOI file is not valid JSON: {e}")
        except Exception as e:
            errors.append(f"AOI file read error: {e}")

        return errors

    def _validate_output_dir(self, output_dir: str) -> List[str]:
        """
        Shared output directory validation — creates dir and checks writeable.

        Args:
            output_dir: Path to output directory.

        Returns:
            List of error strings (empty = valid).
        """
        import os

        errors = []

        if not output_dir:
            errors.append("output_dir is empty or None.")
            return errors

        try:
            os.makedirs(output_dir, exist_ok=True)
        except Exception as e:
            errors.append(f"Cannot create output_dir '{output_dir}': {e}")
            return errors

        if not os.access(output_dir, os.W_OK):
            errors.append(f"output_dir is not writeable: {output_dir}")

        return errors

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} provider='{self.provider_name}' type='{self.data_type}'>"


__all__ = ["BaseProvider", "ProviderResult"]
