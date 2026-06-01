"""
src/data/downloader/s3_stream_downloader.py

Streams files from S3-compatible storage to local disk.

Used by:
    dem_provider.py       → downloader.download_key(s3_key, output_path)
    sentinel1_provider.py → same
    sentinel2_provider.py → same

Design:
    - Streams in chunks — never loads full file into memory
    - Fires progress_callback(fraction 0.0–1.0) periodically
    - Returns True/False — never raises on download failure
    - Thread-safe — one instance can be shared across async tasks
"""

import asyncio
import logging
import os
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# Default chunk size: 8 MB
_DEFAULT_CHUNK_SIZE = 8 * 1024 * 1024


class S3StreamDownloader:
    """
    Streams S3 objects to local disk using a boto3 S3 client.

    Args:
        s3_client: A boto3 S3 client (from get_copernicus_s3_client()).
        bucket:    S3 bucket name e.g. 'eodata'.
        chunk_size: Read buffer size in bytes. Default 8 MB.

    Example:
        downloader = S3StreamDownloader(s3_client=client, bucket="eodata")
        ok = await downloader.download_key(
            s3_key="Copernicus-DEM/.../N27_00_E086_00_DEM.tif",
            output_path="./data/raw/dem/khumbu/2023/_tile_N27.tif",
        )
    """

    def __init__(
        self,
        s3_client,
        bucket: str,
        chunk_size: int = _DEFAULT_CHUNK_SIZE,
    ):
        self.s3_client  = s3_client
        self.bucket     = bucket
        self.chunk_size = chunk_size

    async def download_key(
        self,
        s3_key: str,
        output_path: str,
        progress_callback: Optional[Callable[[float], None]] = None,
    ) -> bool:
        """
        Downloads a single S3 object to output_path.

        Streams in chunks — safe for large GeoTIFF and GRIB files.

        Args:
            s3_key:            Full S3 key for the object.
            output_path:       Local file path to write to.
            progress_callback: Optional callable(fraction 0.0–1.0).
                               Called every chunk. Pass None to skip.

        Returns:
            True  → file downloaded and written successfully.
            False → any failure (logged); caller should handle retry.
        """
        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        try:
            # --- Get object size for progress calculation ---
            total_bytes = await self._get_object_size(s3_key)

            logger.info(
                f"[S3Downloader] Downloading s3://{self.bucket}/{s3_key} "
                f"({self._human_size(total_bytes)}) → {output_path}"
            )

            # --- Stream download in executor (boto3 is sync) ---
            loop = asyncio.get_event_loop()
            success = await loop.run_in_executor(
                None,
                self._stream_to_disk,
                s3_key,
                output_path,
                total_bytes,
                progress_callback,
            )

            if success:
                logger.info(f"[S3Downloader] Done → {os.path.basename(output_path)}")

            return success

        except Exception as e:
            logger.error(
                f"[S3Downloader] Failed: s3://{self.bucket}/{s3_key} — {e}"
            )
            return False

    def _stream_to_disk(
        self,
        s3_key: str,
        output_path: str,
        total_bytes: int,
        progress_callback: Optional[Callable[[float], None]],
    ) -> bool:
        """
        Synchronous streaming worker — runs inside executor.

        Writes chunks directly to disk.
        Fires progress_callback every chunk if provided.

        Returns True on success, False on any error.
        """
        try:
            response = self.s3_client.get_object(
                Bucket=self.bucket,
                Key=s3_key,
            )
            body = response["Body"]

            bytes_written = 0

            with open(output_path, "wb") as f:
                while True:
                    chunk = body.read(self.chunk_size)
                    if not chunk:
                        break

                    f.write(chunk)
                    bytes_written += len(chunk)

                    # Fire progress callback
                    if progress_callback and total_bytes > 0:
                        fraction = min(bytes_written / total_bytes, 1.0)
                        try:
                            progress_callback(fraction)
                        except Exception:
                            pass  # Never let callback crash the download

            logger.debug(
                f"[S3Downloader] Written {self._human_size(bytes_written)} → {output_path}"
            )
            return True

        except Exception as e:
            logger.error(
                f"[S3Downloader] Stream error for '{s3_key}': {e}"
            )
            # Clean up partial file
            if os.path.exists(output_path):
                try:
                    os.remove(output_path)
                    logger.debug(f"[S3Downloader] Partial file removed: {output_path}")
                except Exception:
                    pass
            return False

    async def _get_object_size(self, s3_key: str) -> int:
        """
        HEAD request to get object size before streaming.

        Returns:
            File size in bytes, or 0 if HEAD fails.
        """
        try:
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: self.s3_client.head_object(
                    Bucket=self.bucket,
                    Key=s3_key,
                ),
            )
            return response.get("ContentLength", 0)
        except Exception as e:
            logger.warning(
                f"[S3Downloader] HEAD failed for '{s3_key}' — "
                f"progress will be unavailable: {e}"
            )
            return 0

    @staticmethod
    def _human_size(size_bytes: int) -> str:
        """
        Converts bytes to a human-readable string.

        Examples:
            1024        → '1.0 KB'
            1048576     → '1.0 MB'
            1073741824  → '1.0 GB'
        """
        if size_bytes <= 0:
            return "unknown size"
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if size_bytes < 1024:
                return f"{size_bytes:.1f} {unit}"
            size_bytes /= 1024
        return f"{size_bytes:.1f} PB"


__all__ = ["S3StreamDownloader"]
