import openeo

# connect with the backend
eoconn = openeo.connect(
        "openeo.dataspace.copernicus.eu"
        ).authenticate_oidc()

# Setup process parameters
aoi = {
        "type": "Polygon",
        "coordinates": [
          [
            [
              5.179324150085449,
              51.2498689148547
            ],
            [
              5.178744792938232,
              51.24672597710759
            ],
            [
              5.185289382934569,
              51.24504696935156
            ],
            [
              5.18676996231079,
              51.245342479161295
            ],
            [
              5.187370777130127,
              51.24918393390799
            ],
            [
              5.179324150085449,
              51.2498689148547
            ]
          ]
        ]
      }
date = ["2020-05-06", "2020-05-30"]

# Create a processing graph from the BIOPAR process using an active openEO connection
biopar = eoconn.datacube_from_process(
        "biopar", 
        namespace = "https://raw.githubusercontent.com/ESA-APEx/apex_algorithms/refs/heads/main/algorithm_catalog/vito/biopar/openeo_udp/biopar.json",
        temporal_extent = date,
        spatial_extent= aoi,
        biopar_type = 'FCOVER'
        )
