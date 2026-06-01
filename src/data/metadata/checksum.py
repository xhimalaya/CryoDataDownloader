"""
src/data/metadata/checksum.py

Computes file checksums for download validation.

Used by:
    download_engine.py → compute_md5(final_path)

Import:
    from src.data.metadata.checksum import compute_md5
"""

import hashlib
import logging
import os

logger = logging.getLogger(__name__)


def compute_md5(filepath: str, chunk_size: int = 8192) -> str:
    """
    Computes MD5 checksum of a local file.

    Args:
        filepath:   Absolute or relative path to the file.
        chunk_size: Read buffer size in bytes. Default 8192.

    Returns:
        Hex digest string e.g. 'a3f1d9...'
        Empty string if file does not exist or read fails.

    Example:
        checksum = compute_md5("/data/raw/era5/khumbu_era5.grib")
    """
    if not os.path.exists(filepath):
        logger.warning(f"[Checksum] File not found — cannot compute MD5: {filepath}")
        return ""

    if not os.path.isfile(filepath):
        logger.warning(f"[Checksum] Path is not a file: {filepath}")
        return ""

    hasher = hashlib.md5()

    try:
        with open(filepath, "rb") as f:
            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                hasher.update(chunk)

        digest = hasher.hexdigest()
        logger.debug(f"[Checksum] MD5({os.path.basename(filepath)}) = {digest}")
        return digest

    except OSError as e:
        logger.error(f"[Checksum] Failed to read file for MD5: {filepath} — {e}")
        return ""


def compute_sha256(filepath: str, chunk_size: int = 8192) -> str:
    """
    Computes SHA256 checksum of a local file.

    Same signature as compute_md5.
    Available for future use when providers supply SHA256 manifests.

    Returns:
        Hex digest string, or empty string on failure.
    """
    if not os.path.exists(filepath):
        logger.warning(f"[Checksum] File not found — cannot compute SHA256: {filepath}")
        return ""

    hasher = hashlib.sha256()

    try:
        with open(filepath, "rb") as f:
            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                hasher.update(chunk)

        digest = hasher.hexdigest()
        logger.debug(f"[Checksum] SHA256({os.path.basename(filepath)}) = {digest}")
        return digest

    except OSError as e:
        logger.error(f"[Checksum] Failed to read file for SHA256: {filepath} — {e}")
        return ""


__all__ = ["compute_md5", "compute_sha256"]
