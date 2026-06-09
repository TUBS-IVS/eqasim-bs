"""Clip germany-latest.osm.pbf to the ZGB + source-buffer ring for the cordon network.

The Geofabrik niedersachsen extract is clipped at the Niedersachsen state border, so
the cordon network has no roads east of it (Sachsen-Anhalt) and no eastern gates are
derived. This script clips the Germany-wide pbf to the SAME ring the network build
uses (dissolved ZGB municipalities buffered by cordon_network_source_buffer_m), so the
built network reaches ~30 km beyond ZGB in EVERY direction and the eastern Autobahn
gates (A2 Magdeburg, A39, B188 Wolfsburg) are created by derive_road_gates.

Usage example::

    python scripts/clip_osm_to_cordon_ring.py \\
        --germany-pbf eqasim-data/data/osm/germany-latest.osm.pbf \\
        --vg250 eqasim-data/data/germany/vg250-ew_12-31.utm32s.gpkg.ebenen.zip \\
        --out eqasim-data/data/osm/germany-latest.zgb_ring.osm.pbf

The ``--out`` filename must match ``osm_path_bavaria`` in the cordon-enabled
server configs (``osm/germany-latest.zgb_ring.osm.pbf``).

The .poly file is written next to the output pbf for provenance
(``<out>.poly``). Both input and output file sizes are logged together with
the ring bounding box so the run is fully traceable.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

import geopandas as gpd

from braunschweig.data.cordon.network import (
    read_external_gemeinden,
    ZGB_KREIS_PREFIXES,
)
from braunschweig.data.spatial.cordon import build_cordon_polygon

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# CRS for area/buffer operations — metric (UTM-32N, ZGB region).
_METRIC_CRS = "EPSG:25832"

# CRS required by osmosis bounding-polygon format.
_WGS84_CRS = "EPSG:4326"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _log(msg: str) -> None:
    """Print a timestamped log line to stdout (research traceability)."""
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _write_poly(geom_wgs84, path: str, name: str = "zgb_ring") -> None:
    """Write a geometry to an osmosis .poly file.

    The osmosis bounding-polygon format is::

        <name>
        <ring-index>
          <lon>  <lat>
          ...
        END
        END

    One ring block is written per exterior polygon.  Interior rings (holes) are
    not written; the source buffer is convex enough that holes are irrelevant.

    Args:
        geom_wgs84: Shapely (Multi)Polygon in WGS-84 (EPSG:4326); lon is X,
            lat is Y.
        path: destination file path (created/overwritten).
        name: polygon set name (first line of the file; default ``zgb_ring``).
    """
    polys = (
        list(geom_wgs84.geoms)
        if geom_wgs84.geom_type == "MultiPolygon"
        else [geom_wgs84]
    )
    # newline="\n" forces Unix LF endings on every platform: the osmosis/osmconvert
    # .poly parsers expect LF and silently match NOTHING (empty clip) on CRLF, which
    # the default Windows text mode would produce.
    with open(path, "w", encoding="ascii", newline="\n") as fh:
        fh.write(name + "\n")
        for i, poly in enumerate(polys, 1):
            fh.write(f"{i}\n")
            for x, y in poly.exterior.coords:
                fh.write(f"  {x:.7f}  {y:.7f}\n")
            fh.write("END\n")
        fh.write("END\n")


def _file_size_mb(path: str) -> float:
    """Return file size in MiB, or -1 if the file does not exist."""
    try:
        return os.path.getsize(path) / (1024 * 1024)
    except OSError:
        return -1.0


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

def build_zgb_ring(vg250_path: str, source_buffer_m: float) -> object:
    """Dissolve ZGB municipalities and apply the source buffer.

    Reads ALL Gemeinden from vg250_gem, selects those whose 5-digit ARS prefix
    is in ZGB_KREIS_PREFIXES, dissolves them to a single polygon, and buffers by
    ``source_buffer_m`` in EPSG:25832 (metric).

    Args:
        vg250_path: path to the VG250-EW GeoPackage zip or plain GPKG.
        source_buffer_m: outward buffer in metres (>= 0).

    Returns:
        A Shapely (Multi)Polygon in EPSG:25832.
    """
    _log(f"Ring: reading VG250 Gemeinden from {vg250_path} ...")
    # Pass empty zgb_prefixes so ALL Gemeinden are returned; then filter locally.
    all_gem = read_external_gemeinden(vg250_path, crs=_METRIC_CRS, zgb_prefixes=())
    zgb_gem = all_gem[all_gem["ars5"].isin(set(ZGB_KREIS_PREFIXES))].copy()
    if len(zgb_gem) == 0:
        raise ValueError(
            "No ZGB Gemeinden found in VG250. "
            f"Expected ARS prefixes: {ZGB_KREIS_PREFIXES}. "
            f"Available prefixes (sample): {sorted(all_gem['ars5'].unique())[:10]}"
        )
    _log(f"Ring: {len(zgb_gem)} ZGB Gemeinden selected "
         f"(out of {len(all_gem)} total).")
    ring_m = build_cordon_polygon(zgb_gem, source_buffer_m)
    bbox = ring_m.bounds  # (minx, miny, maxx, maxy) in EPSG:25832 metres
    _log(f"Ring: dissolved + buffered by {source_buffer_m:.0f} m. "
         f"Bbox (EPSG:25832, m): minx={bbox[0]:.0f} miny={bbox[1]:.0f} "
         f"maxx={bbox[2]:.0f} maxy={bbox[3]:.0f}")
    return ring_m


def clip_germany_pbf(
    germany_pbf: str,
    out_pbf: str,
    vg250_path: str,
    source_buffer_m: float,
    osmosis_binary: str,
) -> None:
    """Clip the Germany-wide OSM pbf to the ZGB ring and write the result.

    Steps:
    1. Build ZGB ring (dissolve + buffer) in EPSG:25832.
    2. Reproject ring to WGS-84 and write an osmosis .poly file next to ``out_pbf``.
    3. Run osmosis with ``--bounding-polygon completeWays=yes`` to clip the pbf.
    4. Log input/output sizes and ring bbox for provenance.

    Args:
        germany_pbf: path to germany-latest.osm.pbf (or equivalent).
        out_pbf: output path for the clipped pbf.
        vg250_path: path to VG250-EW zip or GeoPackage.
        source_buffer_m: buffer in metres applied to the dissolved ZGB polygon.
        osmosis_binary: name or full path of the osmosis executable.
    """
    # --- 1. Build ring ---
    ring_m = build_zgb_ring(vg250_path, source_buffer_m)

    # --- 2. Reproject to WGS-84 and write .poly ---
    ring_wgs84 = gpd.GeoSeries([ring_m], crs=_METRIC_CRS).to_crs(_WGS84_CRS).iloc[0]
    bbox_wgs84 = ring_wgs84.bounds
    _log(f"Ring: WGS-84 bbox: "
         f"lon=[{bbox_wgs84[0]:.4f}, {bbox_wgs84[2]:.4f}] "
         f"lat=[{bbox_wgs84[1]:.4f}, {bbox_wgs84[3]:.4f}]")

    poly_path = str(out_pbf) + ".poly"
    _write_poly(ring_wgs84, poly_path)
    _log(f"Ring: .poly written to {poly_path}")

    # --- 3. Run osmosis ---
    in_size_mb = _file_size_mb(germany_pbf)
    _log(f"Input: {germany_pbf} ({in_size_mb:.1f} MiB)")

    Path(out_pbf).parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        osmosis_binary,
        "--read-pbf", germany_pbf,
        "--bounding-polygon", f"file={poly_path}", "completeWays=yes",
        "--write-pbf", out_pbf,
    ]
    _log(f"Osmosis: running: {' '.join(cmd)}")
    t0 = time.time()
    subprocess.run(cmd, check=True)
    elapsed = time.time() - t0

    out_size_mb = _file_size_mb(out_pbf)
    _log(f"Output: {out_pbf} ({out_size_mb:.1f} MiB) in {elapsed:.1f}s")
    _log("Done.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--germany-pbf",
        required=True,
        help="Path to germany-latest.osm.pbf (or equivalent Germany-wide extract).",
    )
    p.add_argument(
        "--out",
        required=True,
        help="Output path for the clipped .osm.pbf.",
    )
    p.add_argument(
        "--vg250",
        default="eqasim-data/data/germany/vg250-ew_12-31.utm32s.gpkg.ebenen.zip",
        help="Path to VG250-EW GeoPackage zip or plain GeoPackage.",
    )
    p.add_argument(
        "--source-buffer-m",
        type=float,
        default=45_000.0,
        metavar="METRES",
        help=(
            "Outward buffer in metres applied to the dissolved ZGB polygon "
            "(default: 45000 = 45 km, so Magdeburg Hbf is captured east). Must match "
            "cordon_network_source_buffer_m in the pipeline config."
        ),
    )
    p.add_argument(
        "--osmosis-binary",
        default="osmosis",
        help="Name or full path of the osmosis executable (default: 'osmosis').",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if not os.path.isfile(args.germany_pbf):
        sys.stderr.write(f"ERROR: germany-pbf not found: {args.germany_pbf}\n")
        return 2
    if not os.path.isfile(args.vg250):
        sys.stderr.write(f"ERROR: vg250 not found: {args.vg250}\n")
        return 2
    if args.source_buffer_m < 0:
        sys.stderr.write("ERROR: --source-buffer-m must be >= 0\n")
        return 2

    clip_germany_pbf(
        germany_pbf=args.germany_pbf,
        out_pbf=args.out,
        vg250_path=args.vg250,
        source_buffer_m=args.source_buffer_m,
        osmosis_binary=args.osmosis_binary,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
