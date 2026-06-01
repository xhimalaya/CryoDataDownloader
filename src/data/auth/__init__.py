# Authentication package initialization
# src/data/auth/__init__.py

from .copernicus_auth import get_copernicus_s3_client, verify_credentials

__all__ = ["get_copernicus_s3_client", "verify_credentials"]
