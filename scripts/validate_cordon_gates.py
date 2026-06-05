"""Standalone validation of the cross-cordon gate derivation on REAL data.

The cross-cordon module's pure functions are unit-tested, but this script exercises
them end to end against the actually built scenario, so we can see -- before wiring
the cordon into the synpp pipeline (Phase 3) -- whether the gates land on the real
motorways / Bundesstrassen and whether ``gates.gpkg`` is written sensibly.

What it does:
  1. Load the in-scope ZGB municipality polygons (VG250, EPSG:25832).
  2. Parse the built MATSim network (``*_network.xml.gz``) into a link
     GeoDataFrame (link_id, capacity, road_class, LineString geometry).
  3. Build the cordon polygon (dissolved ZGB + a fractional buffer) and derive the
     gates (links crossing the cordon boundary), then select the major corridors.
  4. Write ``gates.gpkg`` / ``gates.csv`` and print a thorough sanity report
     (crossing count, road-class breakdown, the strongest gates with coordinates),
     and reload the GeoPackage to confirm it is valid.

This is a diagnostic, not a pipeline stage: it does not synthesize commuter agents
(that is Phase 3). It validates the supply-side gate geometry only.

Usage (run from the repo root so the braunschweig package imports):
    python scripts/validate_cordon_gates.py \
        --network eqasim-data/output_bs_1pct/braunschweig_1pct_network.xml.gz \
        --vg250   eqasim-data/data/germany/vg250-ew_12-31.utm32s.gpkg.ebenen.zip \
        --out     eqasim-data/output_bs_1pct/cordon_validation \
        --buffer-fraction 0.10
"""
from __future__ import annotations

import argparse
import gzip
import os
import sys
import xml.etree.ElementTree as ET

import geopandas as gpd
import pandas as pd
from shapely.geometry import LineString

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from braunschweig.data.spatial.cordon import (  # noqa: E402
    build_cordon_polygon,
    buffer_m_from_fraction,
)
from braunschweig.data.cordon.gates import (  # noqa: E402
    assign_kreise_to_gates,
    derive_road_gates,
    select_major_gates,
)

# The eight Zweckverband Grossraum Braunschweig (ZGB) Kreise, as 5-digit ARS
# prefixes (see braunschweig.analysis.run_mid_validation / political_prefix).
ZGB_KREIS_PREFIXES = ("03101", "03102", "03103", "03151", "03153", "03154", "03157", "03158")

# Road classes where long-distance car commuters cross the cordon. Mirrors the
# CordonConfig default gate_road_classes.
MAJOR_ROAD_CLASSES = ("motorway", "trunk", "primary", "secondary")

# Candidate link attribute names that may carry the OSM road type.
_HIGHWAY_ATTR_NAMES = ("osm:way:highway", "osm_highway", "osm:highway", "highway", "type")


def load_municipalities_from_pickle(pickle_path: str, crs: str = "EPSG:25832") -> gpd.GeoDataFrame:
    """Load the municipalities exactly as the pipeline sees them, from the synpp
    cache pickle of the ``data.spatial.municipalities`` stage.

    This is the most faithful source (same polygons the run used) and avoids any
    VG250 zip/PROJ parsing. Filters to the in-scope ZGB Kreise by the 5-digit
    ``commune_id`` prefix in case the cached frame is wider than the region.
    """
    gdf = pd.read_pickle(pickle_path)
    if not isinstance(gdf, gpd.GeoDataFrame):
        raise ValueError(f"{pickle_path} did not unpickle to a GeoDataFrame "
                         f"(got {type(gdf).__name__})")
    if "commune_id" not in gdf.columns:
        raise ValueError(f"cached municipalities lack a commune_id column; "
                         f"got {list(gdf.columns)}")
    gdf["commune_id"] = gdf["commune_id"].astype(str)
    in_scope = gdf[gdf["commune_id"].str[:5].isin(ZGB_KREIS_PREFIXES)].copy()
    if len(in_scope) == 0:
        # Cached frame may already be region-only with a different id width; keep all.
        in_scope = gdf.copy()
    if in_scope.crs is None:
        in_scope = in_scope.set_crs(crs)
    return in_scope[["commune_id", "geometry"]]


def load_zgb_municipalities(vg250_path: str, crs: str = "EPSG:25832") -> gpd.GeoDataFrame:
    """Load the ZGB municipality polygons from the VG250 GeoPackage (zip)."""
    # The VG250 archive bundles several layers; vg250_gem is the Gemeinde layer.
    layer = "vg250_gem"
    gdf = gpd.read_file(f"zip://{vg250_path}", layer=layer) if vg250_path.endswith(".zip") \
        else gpd.read_file(vg250_path, layer=layer)
    if "ARS" not in gdf.columns:
        raise ValueError(f"VG250 layer {layer!r} has no ARS column; got {list(gdf.columns)[:12]}")
    gdf["ARS"] = gdf["ARS"].astype(str)
    in_scope = gdf[gdf["ARS"].str[:5].isin(ZGB_KREIS_PREFIXES)].copy()
    if len(in_scope) == 0:
        raise ValueError("No ZGB municipalities matched the ARS prefixes")
    in_scope = in_scope.to_crs(crs)
    in_scope = in_scope.rename(columns={"ARS": "commune_id"})
    return in_scope[["commune_id", "geometry"]]


def _normalise_road_class(value):
    """Lower-case and collapse motorway_link/trunk_link/... to their base class."""
    if value is None:
        return None
    v = str(value).strip().lower()
    if v.endswith("_link"):
        v = v[: -len("_link")]
    return v or None


def load_matsim_links(network_path: str, crs: str = "EPSG:25832") -> gpd.GeoDataFrame:
    """Parse a MATSim network XML(.gz) into a link GeoDataFrame.

    Returns columns ``[link_id, capacity, road_class, geometry]`` (LineString) in
    ``crs``. Nodes are read first into a coordinate map; links are streamed to keep
    memory bounded on large networks. The road class is taken from the first
    recognised highway attribute (link ``type`` attribute or an ``<attributes>``
    child), normalised so ``motorway_link`` counts as ``motorway``.
    """
    opener = gzip.open if network_path.endswith(".gz") else open
    nodes: dict[str, tuple[float, float]] = {}
    rows = []
    with opener(network_path, "rb") as handle:
        for event, elem in ET.iterparse(handle, events=("end",)):
            if elem.tag == "node":
                nid = elem.get("id")
                if nid is not None:
                    nodes[nid] = (float(elem.get("x")), float(elem.get("y")))
                elem.clear()
            elif elem.tag == "link":
                src, dst = elem.get("from"), elem.get("to")
                if src in nodes and dst in nodes:
                    road_class = _normalise_road_class(elem.get("type"))
                    if road_class is None:
                        for attr in elem.iter("attribute"):
                            if attr.get("name") in _HIGHWAY_ATTR_NAMES:
                                road_class = _normalise_road_class(attr.text)
                                if road_class is not None:
                                    break
                    cap = elem.get("capacity")
                    rows.append({
                        "link_id": elem.get("id"),
                        "capacity": float(cap) if cap is not None else None,
                        "road_class": road_class,
                        "geometry": LineString([nodes[src], nodes[dst]]),
                    })
                elem.clear()
    if not rows:
        raise ValueError(f"No links parsed from {network_path}")
    return gpd.GeoDataFrame(rows, geometry="geometry", crs=crs)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--network", required=True, help="MATSim *_network.xml(.gz)")
    parser.add_argument("--vg250", default=None, help="VG250 GeoPackage zip with vg250_gem")
    parser.add_argument("--muni-pickle", default=None,
                        help="synpp cache pickle of data.spatial.municipalities (preferred)")
    parser.add_argument("--out", required=True, help="output directory for gates.gpkg/.csv")
    parser.add_argument("--crs", default="EPSG:25832")
    parser.add_argument("--buffer-fraction", type=float, default=0.10,
                        help="cordon buffer as fraction of the region half-diagonal")
    parser.add_argument("--top-n", type=int, default=None,
                        help="optional cap on number of major gates (by capacity)")
    args = parser.parse_args(argv)

    if not args.muni_pickle and not args.vg250:
        parser.error("provide --muni-pickle (preferred) or --vg250")

    print("=== cross-cordon gate validation (real network) ===")
    print(f"network : {args.network}")
    print(f"muni    : {args.muni_pickle or args.vg250}")

    muni = (load_municipalities_from_pickle(args.muni_pickle, args.crs)
            if args.muni_pickle else load_zgb_municipalities(args.vg250, args.crs))
    print(f"\n[1] ZGB municipalities: {len(muni)} communes "
          f"({muni['commune_id'].str[:5].nunique()} Kreise), crs={muni.crs}")

    links = load_matsim_links(args.network, args.crs)
    n_with_class = int(links["road_class"].notna().sum())
    print(f"[2] network links: {len(links):,}  | with road_class: {n_with_class:,} "
          f"({100.0 * n_with_class / len(links):.1f}%)  | crs={links.crs}")
    if n_with_class:
        top_classes = links["road_class"].value_counts().head(8)
        print("    road classes present (top): "
              + ", ".join(f"{k}:{v}" for k, v in top_classes.items()))

    buffer_m = buffer_m_from_fraction(muni, args.buffer_fraction)
    cordon = build_cordon_polygon(muni, buffer_m)
    print(f"[3] cordon polygon: buffer {buffer_m:,.0f} m "
          f"({args.buffer_fraction:.0%} of half-diagonal), area "
          f"{cordon.area / 1e6:,.0f} km^2")

    gates = derive_road_gates(links, cordon)
    print(f"[4] links crossing the cordon boundary (raw gates): {len(gates):,}")
    if len(gates) == 0:
        print("    !! no crossings -- network does not extend beyond the cordon; "
              "check CRS / buffer")
        return 1

    allowed = MAJOR_ROAD_CLASSES if gates["road_class"].notna().any() else None
    major = select_major_gates(gates, allowed_classes=allowed, top_n=args.top_n)
    major = major.reset_index(drop=True)
    major["gate_id"] = [f"gate_{i:04d}" for i in range(len(major))]
    print(f"[5] major gates selected: {len(major):,} "
          f"(allowed_classes={allowed}, top_n={args.top_n})")
    if "road_class" in major.columns and major["road_class"].notna().any():
        print("    by road class: "
              + ", ".join(f"{k}:{v}" for k, v in major["road_class"].value_counts().items()))

    # Map the external (non-ZGB) NDS Kreise to their nearest gate as a smoke test of
    # the entry-corridor assignment (this is what inbound commuters will use).
    if not args.vg250:
        print("[6] Kreis->gate assignment skipped (no --vg250 for external Kreis polygons)")
    else:
        try:
            all_kreise = gpd.read_file(f"zip://{args.vg250}", layer="vg250_krs") \
                if args.vg250.endswith(".zip") else gpd.read_file(args.vg250, layer="vg250_krs")
            all_kreise["ARS"] = all_kreise["ARS"].astype(str)
            ext = all_kreise[(all_kreise["ARS"].str[:2] == "03")
                             & (~all_kreise["ARS"].str[:5].isin(ZGB_KREIS_PREFIXES))].to_crs(args.crs)
            assigned = assign_kreise_to_gates(ext[["ARS", "geometry"]],
                                              major[["gate_id", "geometry"]])
            used = assigned["gate_id"].nunique()
            print(f"[6] external NDS Kreise -> nearest gate: {len(assigned)} Kreise use "
                  f"{used} distinct gates")
        except Exception as exc:  # diagnostic only; never fail gate validation on this
            print(f"[6] Kreis->gate assignment skipped: {exc}")

    os.makedirs(args.out, exist_ok=True)
    gates_csv = os.path.join(args.out, "gates.csv")
    gates_gpkg = os.path.join(args.out, "gates.gpkg")
    out = major.copy()
    out["gate_x"] = out.geometry.x
    out["gate_y"] = out.geometry.y
    out.drop(columns="geometry").to_csv(gates_csv, index=False)
    major.to_file(gates_gpkg, driver="GPKG")

    # Reload to prove the GeoPackage is valid and geometry round-trips.
    reloaded = gpd.read_file(gates_gpkg)
    print(f"\n[7] wrote {gates_gpkg} ({len(reloaded)} features, crs={reloaded.crs}) "
          f"and {gates_csv}")
    print("    reload check: geometry valid="
          f"{bool(reloaded.geometry.notna().all())}, "
          f"x in [{reloaded.geometry.x.min():,.0f}, {reloaded.geometry.x.max():,.0f}], "
          f"y in [{reloaded.geometry.y.min():,.0f}, {reloaded.geometry.y.max():,.0f}]")

    print("\n[8] strongest gates (capacity desc):")
    cols = ["gate_id", "capacity"] + (["road_class"] if "road_class" in out.columns else [])
    show = out.sort_values("capacity", ascending=False).head(12)
    for _, r in show.iterrows():
        rc = f"  {r['road_class']}" if "road_class" in show.columns else ""
        print(f"    {r['gate_id']}  cap={r['capacity']:>7.0f}{rc}  "
              f"@ ({r['gate_x']:,.0f}, {r['gate_y']:,.0f})")

    print("\nDONE: gate geometry validated on the real network.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
