"""
auth_factory.py

Auth clients for all providers.
Only two are live today: copernicus_s3 (S3/boto3) and cds (cdsapi).
Rest are stubs ready to activate.
"""

import logging
import pathlib

logger = logging.getLogger(__name__)


def get_copernicus_s3_client(config):
    """
    boto3 S3 client for Copernicus EODATA.
    Used by: DEMProvider, Sentinel2Provider.
    Reads: credentials.copernicus_s3.*
    """
    try:
        import boto3
        from botocore.config import Config
    except ImportError:
        raise ImportError("pip install boto3")

    ep     = config.get("credentials.copernicus_s3.endpoint_url",    "https://eodata.dataspace.copernicus.eu")
    key    = config.get("credentials.copernicus_s3.access_key",      "")
    secret = config.get("credentials.copernicus_s3.secret_key",      "")
    region = config.get("credentials.copernicus_s3.region_name",     "default")
    conns  = int(config.get("credentials.copernicus_s3.max_connections", 100))
    anon   = config.get("credentials.copernicus_s3.use_anonymous",   False)

    boto_cfg = Config(
        max_pool_connections = conns,
        retries              = {"max_attempts": 3, "mode": "standard"},
    )

    if not (key and secret) or anon:
        from botocore import UNSIGNED
        from botocore.config import Config as C
        logger.warning("[Auth:S3] No credentials — anonymous mode")
        return boto3.client(
            "s3",
            endpoint_url = ep,
            config       = C(signature_version=UNSIGNED, max_pool_connections=conns),
        )

    client = boto3.client(
        "s3",
        endpoint_url          = ep,
        aws_access_key_id     = key,
        aws_secret_access_key = secret,
        region_name           = region,
        config                = boto_cfg,
    )
    logger.info(f"[Auth:S3] Client ready → {ep}")
    return client


def get_cds_client(config):
    """
    cdsapi.Client for ERA5 downloads.
    Reads: credentials.cds.url + credentials.cds.api_key
    Writes ~/.cdsapirc once if missing.
    """
    try:
        import cdsapi
    except ImportError:
        raise ImportError("pip install cdsapi")

    url     = config.get("credentials.cds.url",            "https://cds.climate.copernicus.eu/api")
    api_key = config.get("credentials.cds.api_key",        "")
    retries = int(config.get("credentials.cds.retry_attempts", 8))

    if not api_key:
        raise ValueError(
            "[Auth:CDS] api_key missing — "
            "set credentials.cds.api_key in credentials.yaml"
        )

    rc = pathlib.Path.home() / ".cdsapirc"
    if not rc.exists():
        rc.write_text(f"url: {url}\nkey: {api_key}\n")
        logger.info(f"[Auth:CDS] Wrote {rc}")

    client = cdsapi.Client(
        url       = url,
        key       = api_key,
        quiet     = True,
        verify    = True,
        retry_max = retries,
    )
    logger.info("[Auth:CDS] Client ready ✓")
    return client


# ── Future providers (not yet live) ─────────────────────────────

def get_earthdata_session(config):
    import requests
    from requests.auth import HTTPBasicAuth
    u = config.get("credentials.nasa_earthdata.username", "")
    p = config.get("credentials.nasa_earthdata.password", "")
    s = requests.Session()
    if u and p:
        s.auth = HTTPBasicAuth(u, p)
    return s


def get_asf_session(config):
    import requests
    from requests.auth import HTTPBasicAuth
    u = config.get("credentials.asf.username", "")
    p = config.get("credentials.asf.password", "")
    s = requests.Session()
    if u and p:
        s.auth = HTTPBasicAuth(u, p)
    return s


def get_earth_engine(config):
    try:
        import ee
    except ImportError:
        raise ImportError("pip install earthengine-api")
    use_local = config.get("credentials.earth_engine.use_local_auth", True)
    project   = config.get("credentials.earth_engine.project_id",     "")
    if use_local:
        ee.Initialize(project=project or None)
    return ee
