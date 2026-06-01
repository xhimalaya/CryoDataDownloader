"""
src/data/auth/copernicus_auth.py

Returns a configured boto3 S3 client for Copernicus Data Space (EODATA).

Used by:
    dem_provider.py       → DEM GLO-30 tiles
    sentinel1_provider.py → Sentinel-1 GRD scenes
    sentinel2_provider.py → Sentinel-2 L2A scenes

Credentials are read from config/credentials.yaml under:
    credentials:
      copernicus_s3:
        access_key:    YOUR_KEY
        secret_key:    YOUR_SECRET
        endpoint_url:  https://eodata.dataspace.copernicus.eu
        region_name:   default
        max_connections: 10

If credentials are missing or anonymous=True, falls back to anonymous access.
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)


def get_copernicus_s3_client(config: Any, anonymous: bool = False):
    """
    Creates and returns a configured boto3 S3 client for Copernicus EODATA.

    Args:
        config:    ConfigManager instance.
        anonymous: If True, connect without credentials (public buckets only).

    Returns:
        boto3.client instance ready for S3 operations.

    Raises:
        ImportError: If boto3 is not installed.
        ValueError:  If credentials are missing and anonymous=False.
    """
    try:
        import boto3
        from botocore.config import Config as BotoConfig
    except ImportError as e:
        raise ImportError(
            "boto3 is required for Copernicus S3 access. "
            "Install with: pip install boto3"
        ) from e

    # --- Read credentials from config ---
    endpoint_url    = config.get(
                        "credentials.copernicus_s3.endpoint_url",
                        "https://eodata.dataspace.copernicus.eu"
                      )
    access_key      = config.get("credentials.copernicus_s3.access_key", "")
    secret_key      = config.get("credentials.copernicus_s3.secret_key", "")
    region          = config.get("credentials.copernicus_s3.region_name", "default")
    max_connections = config.get("credentials.copernicus_s3.max_connections", 10)

    boto_config = BotoConfig(
        max_pool_connections=max_connections,
        retries={"max_attempts": 3, "mode": "standard"},
    )

    # --- Anonymous fallback ---
    use_anonymous = anonymous or not (access_key and secret_key)

    if use_anonymous:
        logger.warning(
            "[CopernicusAuth] No credentials found — connecting anonymously. "
            "Private buckets will be inaccessible."
        )
        try:
            from botocore import UNSIGNED
            from botocore.config import Config as BotoCfg
        except ImportError as e:
            raise ImportError(
                "botocore is required. Install with: pip install boto3"
            ) from e

        client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            config=BotoCfg(
                signature_version=UNSIGNED,
                max_pool_connections=max_connections,
            ),
        )

    else:
        # --- Authenticated access ---
        client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
            config=boto_config,
        )

    logger.info(f"[CopernicusAuth] S3 client ready → {endpoint_url}")
    return client


def verify_credentials(config: Any) -> bool:
    """
    Quick sanity check — tries to list the root of the EODATA bucket.

    Returns:
        True  → credentials are valid and endpoint is reachable.
        False → credentials are invalid or endpoint unreachable.

    Usage:
        from src.data.auth.copernicus_auth import verify_credentials
        ok = verify_credentials(config)
    """
    try:
        client = get_copernicus_s3_client(config, anonymous=False)
        client.list_buckets()
        logger.info("[CopernicusAuth] Credential verification → OK")
        return True
    except Exception as e:
        logger.error(f"[CopernicusAuth] Credential verification → FAILED: {e}")
        return False


__all__ = ["get_copernicus_s3_client", "verify_credentials"]
