"""OSM PBF chunking by municipality polygon.

Origin: eqasim-bavaria @ b20fbe6, file ``bavaria/data/osm/chunked.py``.
Moved to ``eqasim_common`` in Phase 2.1 of the eqasim-bs refactor;
inherited unchanged apart from the import path of ``osmconvert``.

The stage cuts a national OSM extract into per-municipality PBF files so
downstream loaders can process them in parallel.
"""

import os

import eqasim_common.data.osm.osmconvert


def configure(context):
    context.stage("data.spatial.municipalities")
    context.stage("eqasim_common.data.osm.osmconvert")

    context.config("processes")

    context.config("data_path")
    context.config("osm_network_path", "osm/niedersachsen-latest.osm.pbf")

def process_municipality(context, zone_id):
    input_path = context.data("input_path")
    local_path = context.data("local_path")

    eqasim_common.data.osm.osmconvert.run(context, [input_path,
        "-B={}".format("{}/{}.poly".format(local_path, zone_id)),
        "-o={}".format("{}/{}.osm.pbf".format(local_path, zone_id))], cwd = local_path)
    
    return zone_id
    
def execute(context):
    # Load zones and convert to polyfiles
    df_zones = context.stage("data.spatial.municipalities")[["commune_id", "geometry"]]
    df_zones = df_zones.to_crs("EPSG:4326")

    for zone_id, geometry in df_zones.itertuples(index = False):
        if not hasattr(geometry, "exterior"):
            geometry = geometry.convex_hull

        data = []
        data.append("polyfile")
        data.append("polygon")

        for coordinate in geometry.exterior.coords:
            data.append("    %e    %e" % coordinate[:2])

        data.append("END")
        data.append("END")

        with open("{}/{}.poly".format(context.path(), zone_id), "w+") as f:
            f.write("\n".join(data))
    
    # Cut into chunks
    with context.progress(label = "Chunking OSM data ...", total = len(df_zones)) as progress:
        with context.parallel({
            "input_path": os.path.abspath("{}/{}".format(context.config("data_path"), context.config("osm_network_path"))),
            "local_path": context.path()
        }) as parallel:
            for item in parallel.imap(process_municipality, df_zones["commune_id"].values):
                progress.update()

    return df_zones["commune_id"].values

def validate(context):
    return os.path.getsize("{}/{}".format(context.config("data_path"), context.config("osm_network_path")))