import yaml
import boto3
import geopandas as gpd

# =====================================================
# CONFIG
# =====================================================

GEOJSON_PATH = (
    "/home/himalaya/Projects/"
    "CryoDataDownloader/"
    "inputGeoJson/"
    "jakobshavn_cds_era5.geojson"
)

# =====================================================
# LOAD CREDENTIALS
# =====================================================

with open(
    "/home/himalaya/Projects/CryoDataDownloader/config/credentials.yaml",
    "r"
) as f:
    creds = yaml.safe_load(f)

cfg = creds[
    "credentials"
]["copernicus_s3"]

# =====================================================
# CONNECT S3
# =====================================================

print("=" * 80)
print("CONNECTING TO COPERNICUS S3")
print("=" * 80)

s3 = boto3.client(
    "s3",
    endpoint_url=cfg[
        "endpoint_url"
    ],
    aws_access_key_id=cfg[
        "access_key"
    ],
    aws_secret_access_key=cfg[
        "secret_key"
    ],
    region_name=cfg.get(
        "region_name",
        "default"
    )
)

print("Connected successfully")

# =====================================================
# READ AOI
# =====================================================

print("\n" + "=" * 80)
print("READING JAKOBSHAVN AOI")
print("=" * 80)

gdf = gpd.read_file(
    GEOJSON_PATH
)

if str(gdf.crs) != "EPSG:4326":
    gdf = gdf.to_crs(
        "EPSG:4326"
    )

minx, miny, maxx, maxy = (
    gdf.total_bounds
)

print("Bounds:")
print(
    f"W={minx}, "
    f"S={miny}, "
    f"E={maxx}, "
    f"N={maxy}"
)

print("\nCenter:")
print(
    (
        miny + maxy
    ) / 2,
    (
        minx + maxx
    ) / 2
)

# =====================================================
# TEST SENTINEL ROOTS
# =====================================================

bucket = "eodata"

candidate_prefixes = [
    "Sentinel-2/",
    "Sentinel-2/MSI/",
    "Sentinel-2/MSI/L2A/",
    "Sentinel-2/L2A/",
]

print("\n" + "=" * 80)
print("TESTING SENTINEL PATHS")
print("=" * 80)

for prefix in candidate_prefixes:

    print("\nTrying:")
    print(prefix)

    try:
        response = s3.list_objects_v2(
            Bucket=bucket,
            Prefix=prefix,
            Delimiter="/",
            MaxKeys=10
        )

        folders = response.get(
            "CommonPrefixes",
            []
        )

        files = response.get(
            "Contents",
            []
        )

        print(
            "Folders:",
            len(folders)
        )

        for folder in folders[:10]:
            print(folder["Prefix"])
        print("Files:",len(files))
        for file in files[:5]:
            print(file["Key"])

    except Exception as e:
        print("FAILED:",str(e))

print("\nDONE")
