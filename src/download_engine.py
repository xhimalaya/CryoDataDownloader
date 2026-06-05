import asyncio
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
        """Calculates exponential backoff delay with ±20% random jitter."""
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
        """

        async with self.semaphore:
            attempt = 0
            success = False
            last_error = ""

            os.makedirs(
                os.path.dirname(output_path),
                exist_ok=True
            )

            # --------------------------------------------------
            # Resolve provider once
            # --------------------------------------------------
            try:
                provider = get_provider(
                    source,
                    self.config
                )

                logger.info(
                    f"[DownloadEngine] "
                    f"source={source} "
                    f"provider={provider.__class__.__name__}"
                )

            except ValueError as e:
                logger.error(
                    f"[DownloadEngine] "
                    f"Unknown source '{source}': {e}"
                )

                self._db_update(
                    task_id,
                    "FAILED",
                    last_error=str(e)
                )

                if progress_callback:
                    progress_callback(task_id, -1.0)

                return False

            # --------------------------------------------------
            # Retry loop (UNCHANGED)
            # --------------------------------------------------
            while attempt < self.max_retries and not success:

                attempt += 1

                try:
                    if progress_callback:
                        progress_callback(task_id, 0.05)

                    # --------------------------------------------------
                    # 1. Search
                    # --------------------------------------------------
                    logger.info(
                        f"[DownloadEngine] "
                        f"[{source}] Searching: "
                        f"glacier={glacier} "
                        f"date={date_str}"
                    )

                    product_info = await provider.search(
                        geojson_path=geojson_path,
                        date_str=date_str,
                    )

                    if not product_info:
                        raise IOError(
                            f"No valid product found for "
                            f"{source}/{glacier}/{date_str}"
                        )

                    if progress_callback:
                        progress_callback(task_id, 0.15)

                    # --------------------------------------------------
                    # 2. Download raw data
                    # --------------------------------------------------
                    logger.info(
                        f"[DownloadEngine] "
                        f"[{source}] Downloading: "
                        f"{product_info.get('product_name')}"
                    )

                    downloaded = await provider.download(
                        product_info=product_info,
                        output_path=output_path,
                        progress_callback=(
                            lambda tid, p:
                            progress_callback(
                                task_id,
                                0.15 + p * 0.65
                            )
                            if progress_callback else None
                        ),
                    )

                    if not downloaded:
                        raise IOError(
                            f"Download returned False for "
                            f"{product_info.get('product_name')}"
                        )

                    if progress_callback:
                        progress_callback(task_id, 0.80)

                    # --------------------------------------------------
                    # 3. Provider processing
                    # --------------------------------------------------
                    # IMPORTANT:
                    # Provider already produces final science-ready raster.
                    #
                    # Sentinel-2:
                    #   JP2 → RGB stack → clip → compress
                    #
                    # DEM:
                    #   merge → clip → compress
                    #
                    # ERA5:
                    #   .nc → multiband tif
                    #
                    # DO NOT process again elsewhere.
                    # --------------------------------------------------

                    output_dir = os.path.dirname(output_path)

                    final_path = await provider.process(
                        raw_path=output_path,
                        output_dir=output_dir,
                        geojson_path=geojson_path,
                    )

                    logger.info(
                        f"[DownloadEngine] "
                        f"Provider output ready: "
                        f"{final_path}"
                    )

                    # --------------------------------------------------
                    # Validate provider output
                    # --------------------------------------------------
                    if not final_path:
                        raise RuntimeError(
                            f"Provider returned empty output "
                            f"for {source}"
                        )

                    if not os.path.exists(final_path):
                        raise RuntimeError(
                            f"Provider output missing: "
                            f"{final_path}"
                        )

                    file_size_mb = (
                        os.path.getsize(final_path)
                        / (1024 * 1024)
                    )

                    logger.info(
                        f"[DownloadEngine] "
                        f"Output OK → "
                        f"{os.path.basename(final_path)} "
                        f"({file_size_mb:.2f} MB)"
                    )

                    # --------------------------------------------------
                    # 4. Compute checksum
                    # --------------------------------------------------
                    checksum = compute_md5(final_path)

                    # --------------------------------------------------
                    # 5. Mark DB state
                    # --------------------------------------------------
                    self._db_update(
                        task_id,
                        status="DOWNLOADED",
                        filepath=final_path,
                        checksum=checksum,
                        last_error="",
                    )

                    if progress_callback:
                        progress_callback(task_id, 1.0)

                    logger.info(
                        f"[DownloadEngine] "
                        f"[{source}] Complete: "
                        f"{final_path}"
                    )

                    success = True

                except Exception as e:
                    last_error = str(e)

                    logger.warning(
                        f"[DownloadEngine] "
                        f"[{source}] "
                        f"Attempt {attempt}/"
                        f"{self.max_retries} failed: "
                        f"{last_error}"
                    )

                    if attempt < self.max_retries:

                        delay = self.get_backoff_delay(
                            attempt
                        )

                        self._db_update(
                            task_id,
                            status="FAILED",
                            last_error=(
                                f"Attempt {attempt} failed: "
                                f"{last_error}. "
                                f"Retrying in {delay}s..."
                            ),
                            increment_retry=True,
                        )

                        await asyncio.sleep(delay)

                    else:
                        self._db_update(
                            task_id,
                            status="FAILED",
                            last_error=(
                                f"Max retries exhausted: "
                                f"{last_error}"
                            ),
                            increment_retry=True,
                        )

                        if progress_callback:
                            progress_callback(
                                task_id,
                                -1.0
                            )

            return success

    # --------------------------------------------------
    # Internal helpers
    # --------------------------------------------------

    def _db_update(
        self,
        task_id: int,
        status: str,
        filepath: str = None,
        checksum: str = None,
        last_error: str = None,
        increment_retry: bool = False,
    ) -> None:
        """Delegates DB update to DBManager."""

        if self.db_manager:
            self.db_manager.update_task_status(
                task_id=task_id,
                status=status,
                filepath=filepath,
                checksum=checksum,
                last_error=last_error,
                increment_retry=increment_retry,
            )