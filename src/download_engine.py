"""
download_engine.py

Orchestration layer only.

Responsibilities:
  - Select provider via registry
  - Run search → download → process pipeline
  - Apply retry with backoff (unchanged)
  - Update DB on each state transition (unchanged)
  - Fire progress callbacks (unchanged)
  - Respect async semaphore (unchanged)

This file contains NO provider logic, NO satellite-specific code,
NO S3 logic, NO ERA5 logic, NO GeoTIFF generation.
Max target size: ~200 lines.
"""

import asyncio
import hashlib
import logging
import os
import random
from typing import Any, Callable, Optional

from src.db_manager import DBManager
from src.data.providers import get_provider
from src.data.metadata.checksum import compute_md5

logger = logging.getLogger(__name__)


class DownloadEngine:
    def __init__(
        self,
        async_downloads: int = 100,
        max_retries: int = 8,
        db_manager: DBManager = None,
        config: Any = None,
    ):
        # --- Untouched: semaphore and retry config ---
        self.semaphore = asyncio.Semaphore(async_downloads)
        self.max_retries = max_retries
        self.db_manager = db_manager
        self.config = config
        self.backoff_base = [2, 5, 10, 20, 40, 80, 180, 360]

    def get_backoff_delay(self, attempt: int) -> float:
        """Calculates exponential backoff delay with ±20% random jitter. Unchanged."""
        if attempt < 1:
            attempt = 1
        idx = min(attempt - 1, len(self.backoff_base) - 1)
        base = self.backoff_base[idx]
        return round(base * random.uniform(0.8, 1.2), 2)

    async def download_file(
        self,
        task_id: int,
        source: str,
        glacier: str,
        date_str: str,
        output_path: str,
        progress_callback: Optional[Callable[[int, float], None]] = None,
        geojson_path: str = None,
    ) -> bool:
        """
        Executes the full search → download → process pipeline for one task.

        Uses the provider registry to dispatch to the correct provider.
        Retry logic, semaphore, and DB updates are unchanged.

        Args:
            task_id: DB task ID (for status updates and progress callbacks).
            source: Data source key (e.g. 'sentinel2', 'era5', 'dem').
            glacier: Glacier name (used for output path and logging).
            date_str: Target date string (YYYY-MM-DD).
            output_path: Destination path for the downloaded file.
            progress_callback: Optional callback(task_id, fraction 0.0–1.0 or -1.0 on failure).
            geojson_path: Path to the glacier AOI GeoJSON.

        Returns:
            True if the full pipeline completed successfully.
        """
        async with self.semaphore:
            attempt = 0
            success = False
            last_error = ""

            os.makedirs(os.path.dirname(output_path), exist_ok=True)

            # Resolve provider once — outside retry loop
            try:
                provider = get_provider(source, self.config)
            except ValueError as e:
                logger.error(f"[DownloadEngine] Unknown source '{source}': {e}")
                self._db_update(task_id, "FAILED", last_error=str(e))
                if progress_callback:
                    progress_callback(task_id, -1.0)
                return False

            while attempt < self.max_retries and not success:
                attempt += 1
                try:
                    if progress_callback:
                        progress_callback(task_id, 0.05)

                    # 1. Search for latest valid product
                    logger.info(f"[DownloadEngine] [{source}] Searching: glacier={glacier} date={date_str}")
                    product_info = await provider.search(
                        geojson_path=geojson_path,
                        date_str=date_str,
                    )

                    if not product_info:
                        raise IOError(f"No valid product found for {source}/{glacier}/{date_str}")

                    if progress_callback:
                        progress_callback(task_id, 0.15)

                    # 2. Download raw data
                    logger.info(f"[DownloadEngine] [{source}] Downloading: {product_info.get('product_name')}")
                    downloaded = await provider.download(
                        product_info=product_info,
                        output_path=output_path,
                        progress_callback=lambda tid, p: progress_callback(task_id, 0.15 + p * 0.65)
                        if progress_callback else None,
                    )

                    if not downloaded:
                        raise IOError(f"Download returned False for {product_info.get('product_name')}")

                    if progress_callback:
                        progress_callback(task_id, 0.80)

                    # 3. Post-download processing (reproject, clip, compress)
                    output_dir = os.path.dirname(output_path)
                    final_path = await provider.process(
                        raw_path=output_path,
                        output_dir=output_dir,
                        geojson_path=geojson_path,
                    )

                    # 4. Compute checksum and mark complete
                    checksum = compute_md5(final_path) if os.path.exists(final_path) else ""
                    self._db_update(
                        task_id,
                        status="DOWNLOADED",
                        filepath=final_path,
                        checksum=checksum,
                        last_error="",
                    )

                    if progress_callback:
                        progress_callback(task_id, 1.0)

                    logger.info(f"[DownloadEngine] [{source}] Complete: {final_path}")
                    success = True

                except Exception as e:
                    last_error = str(e)
                    logger.warning(
                        f"[DownloadEngine] [{source}] Attempt {attempt}/{self.max_retries} failed: {last_error}"
                    )

                    if attempt < self.max_retries:
                        delay = self.get_backoff_delay(attempt)
                        self._db_update(
                            task_id,
                            status="FAILED",
                            last_error=f"Attempt {attempt} failed: {last_error}. Retrying in {delay}s...",
                            increment_retry=True,
                        )
                        await asyncio.sleep(delay)
                    else:
                        self._db_update(
                            task_id,
                            status="FAILED",
                            last_error=f"Max retries exhausted: {last_error}",
                            increment_retry=True,
                        )
                        if progress_callback:
                            progress_callback(task_id, -1.0)

            return success

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _db_update(
        self,
        task_id: int,
        status: str,
        filepath: str = None,
        checksum: str = None,
        last_error: str = None,
        increment_retry: bool = False,
    ) -> None:
        """Delegates DB update to DBManager. Unchanged semantics."""
        if self.db_manager:
            self.db_manager.update_task_status(
                task_id=task_id,
                status=status,
                filepath=filepath,
                checksum=checksum,
                last_error=last_error,
                increment_retry=increment_retry,
            )
