"""Content comparison of two GeoPackages whose bytes differ (issue #679, task C7, OFF identity check).

Prints the ``gpkg_contents`` rows (table, last_change, extent) of both files and, per layer, whether
the attribute columns and the geometries (as WKB) are identical. Used for
``<prefix>commutes_pre_portal.gpkg``, the one population file whose bytes differ between the OFF arms
of 66da6ee0 and 0611505f.

Usage (eqasim conda env): python i679_gpkg_content_check.py OLD.gpkg NEW.gpkg
"""
import sqlite3
import sys

import fiona
import geopandas as gpd


def main(old, new):
    for path in (old, new):
        connection = sqlite3.connect(path)
        rows = connection.execute(
            "SELECT table_name, last_change, min_x, min_y, max_x, max_y FROM gpkg_contents").fetchall()
        connection.close()
        print(path.rsplit("/", 1)[-1], rows)
    layers_old, layers_new = fiona.listlayers(old), fiona.listlayers(new)
    print("layers", layers_old, layers_new)
    ok = layers_old == layers_new
    for layer in layers_old:
        a = gpd.read_file(old, layer=layer)
        b = gpd.read_file(new, layer=layer)
        same = (list(a.columns) == list(b.columns)
                and a.drop(columns="geometry").equals(b.drop(columns="geometry"))
                and bool((a.geometry.to_wkb() == b.geometry.to_wkb()).all()))
        print(f"layer {layer}: rows {len(a)} / {len(b)}, content identical: {same}")
        ok &= same
    return 0 if ok else 4


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
