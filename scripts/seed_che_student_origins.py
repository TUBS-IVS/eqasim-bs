"""Seed the committed CHE table of student origins (Kreis of the school leaving certificate).

Hand-read from the CHE DatenCHECK 2/2024 pages (data: Statistisches Bundesamt),
winter semester 2022/23, in two views that are pinned in the constants below:

* ``INSTITUTION_ORIGINS``: per institution the (up to) ten largest origin Kreise, plus
  the abroad, other and total rows.
* ``KREIS_DESTINATIONS``: per origin Kreis the (up to) ten institutions with most
  students from there, plus the total.

Cells below the tenth value are censored by the source. The origin is the Kreis of the
school leaving certificate, not the place of residence.

``--list-required-kreise`` prints the origin Kreise that must be hand-read (every Kreis
within ``--max-distance-km`` of a university site) and exits without writing.

Usage: python scripts/seed_che_student_origins.py
       python scripts/seed_che_student_origins.py --list-required-kreise \
           --vg250 <vg250 gpkg zip> --sites <lsn_university_sites.csv>
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

SEMESTER = "WS 2022/23"
SOURCE = "CHE DatenCHECK 2/2024 (data: Statistisches Bundesamt)"
OUTPUT_PATH = Path("eqasim-data/data/braunschweig/schools/che_student_origins_ws2022_23.csv")
METRIC_CRS = "EPSG:25832"
MAX_LISTED_RANKS = 10

# Filled together with the two lists below; main() refuses to run while any is empty.
ACCESS_DATE = ""

# (institution, rank_or_kind, origin_ars5, origin_name, students_persons)
# rank_or_kind is 1..10 (listed), "abroad", "other" or "total"; abroad/other/total rows
# carry an empty origin_ars5.
INSTITUTION_ORIGINS: list[tuple[str, int | str, str, str, int]] = []

# (origin_ars5, origin_name, rank_or_kind, institution, students_persons)
# rank_or_kind is 1..10 (listed) or "total"; the total row carries an empty institution.
KREIS_DESTINATIONS: list[tuple[str, str, int | str, str, int]] = []

_COLUMNS = ["view", "institution", "origin_ars5", "origin_name", "rank", "row_kind",
            "students_persons"]
_SPECIAL_KINDS = ("abroad", "other", "total")


def _check_count(value, where: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"students_persons must be an integer, got {value!r} for {where}")
    if value < 0:
        raise ValueError(f"negative students_persons {value} for {where}")


def _row_kind(rank_or_kind, where: str) -> str:
    if rank_or_kind in _SPECIAL_KINDS:
        return rank_or_kind
    if isinstance(rank_or_kind, int) and not isinstance(rank_or_kind, bool) \
            and 1 <= rank_or_kind <= MAX_LISTED_RANKS:
        return "listed"
    raise ValueError(f"rank_or_kind must be 1..{MAX_LISTED_RANKS}, 'abroad', 'other' or "
                     f"'total', got {rank_or_kind!r} for {where}")


def _check_ranked_listed(listed: list[tuple[int, int]], where: str) -> None:
    """listed = (rank, count) pairs: at most 10, ranks 1..n contiguous, counts non-increasing."""
    ranks = sorted(rank for rank, _ in listed)
    if ranks != list(range(1, len(ranks) + 1)):
        raise ValueError(f"listed ranks must be unique and contiguous from 1 for {where}, "
                         f"got {ranks}")
    counts = [count for _, count in sorted(listed)]
    if any(later > earlier for earlier, later in zip(counts, counts[1:])):
        raise ValueError(f"listed counts must be non-increasing by rank for {where}, got {counts}")


def build_origins_frame(institution_origins, kreis_destinations) -> pd.DataFrame:
    """Validate both views and return them stacked in one frame.

    Raises ValueError naming the institution or Kreis for every violated invariant
    (see the module docstring and the checks below).
    """
    records = []
    listed_origin_cells: dict[tuple[str, str], int] = {}

    by_institution: dict[str, list] = {}
    for institution, rank_or_kind, ars5, name, students in institution_origins:
        where = f"institution {institution!r}"
        _check_count(students, where)
        kind = _row_kind(rank_or_kind, where)
        by_institution.setdefault(institution, []).append((rank_or_kind, kind, ars5, students))
        records.append({"view": "institution_origins", "institution": institution,
                        "origin_ars5": ars5, "origin_name": name,
                        "rank": rank_or_kind if kind == "listed" else pd.NA,
                        "row_kind": kind, "students_persons": students})
        if kind == "listed":
            listed_origin_cells[(institution, ars5)] = students
    for institution, items in by_institution.items():
        where = f"institution {institution!r}"
        listed = [(rank, n) for rank, kind, _, n in items if kind == "listed"]
        if len(listed) > MAX_LISTED_RANKS:
            raise ValueError(f"more than {MAX_LISTED_RANKS} listed rows for {where}")
        _check_ranked_listed(listed, where)
        singles = {}
        for kind in _SPECIAL_KINDS:
            values = [n for _, k, _, n in items if k == kind]
            if len(values) != 1:
                raise ValueError(f"expected exactly one {kind!r} row for {where}, "
                                 f"got {len(values)}")
            singles[kind] = values[0]
        closed = sum(n for _, n in listed) + singles["abroad"] + singles["other"]
        if closed != singles["total"]:
            raise ValueError(f"rows of {where} -- sum does not close: listed + abroad + other = "
                             f"{closed}, total = {singles['total']}")

    by_kreis: dict[str, list] = {}
    for ars5, name, rank_or_kind, institution, students in kreis_destinations:
        where = f"Kreis {ars5!r} ({name})"
        _check_count(students, where)
        kind = _row_kind(rank_or_kind, where)
        if kind in ("abroad", "other"):
            raise ValueError(f"kreis_destinations has no {kind!r} row, got one for {where}")
        by_kreis.setdefault(ars5, []).append((rank_or_kind, kind, institution, students))
        records.append({"view": "kreis_destinations", "institution": institution,
                        "origin_ars5": ars5, "origin_name": name,
                        "rank": rank_or_kind if kind == "listed" else pd.NA,
                        "row_kind": kind, "students_persons": students})
    for ars5, items in by_kreis.items():
        where = f"Kreis {ars5!r}"
        listed = [(rank, n) for rank, kind, _, n in items if kind == "listed"]
        if len(listed) > MAX_LISTED_RANKS:
            raise ValueError(f"more than {MAX_LISTED_RANKS} listed rows for {where}")
        _check_ranked_listed(listed, where)
        totals = [n for _, kind, _, n in items if kind == "total"]
        if len(totals) != 1:
            raise ValueError(f"expected exactly one 'total' row for {where}, got {len(totals)}")
        if sum(n for _, n in listed) > totals[0]:
            raise ValueError(f"listed rows of {where} exceed the total {totals[0]}")
        for rank, kind, institution, n in items:
            if kind != "listed":
                continue
            other = listed_origin_cells.get((institution, ars5))
            if other is not None and other != n:
                raise ValueError(f"views disagree for institution {institution!r} x Kreis "
                                 f"{ars5!r}: kreis_destinations {n}, institution_origins {other}")

    frame = pd.DataFrame(records, columns=_COLUMNS)
    frame["rank"] = frame["rank"].astype("Int64")
    return frame


def _require_metric(frame, label: str) -> None:
    if frame.crs is None or not frame.crs.is_projected:
        raise ValueError(f"{label} must be in a metric (projected) CRS such as {METRIC_CRS}, "
                         f"got {frame.crs}")


def required_origin_kreise(kreis_polygons, site_points, max_distance_km: float = 100.0) -> list[str]:
    """Sorted ARS-5 codes of Kreise whose polygon lies within max_distance_km of any site.

    kreis_polygons needs an ``ars5`` column; both frames must share one metric CRS.
    Polygon (not centroid) distance makes this a superset of the population-weighted
    centroid rule of the design, so the pinned list over-covers rather than misses.
    """
    from shapely.ops import unary_union

    _require_metric(kreis_polygons, "kreis_polygons")
    _require_metric(site_points, "site_points")
    if kreis_polygons.crs != site_points.crs:
        raise ValueError(f"kreis_polygons ({kreis_polygons.crs}) and site_points "
                         f"({site_points.crs}) must share one CRS")
    if "ars5" not in kreis_polygons.columns:
        raise ValueError("kreis_polygons needs an 'ars5' column")
    if len(site_points) == 0:
        raise ValueError("site_points is empty")
    sites = unary_union(list(site_points.geometry))
    distance_m = kreis_polygons.geometry.distance(sites)
    return sorted(set(kreis_polygons.loc[distance_m <= max_distance_km * 1000.0, "ars5"]))


def _list_required(vg250: Path, sites_csv: Path, max_distance_km: float) -> list[str]:
    import geopandas as gpd

    for path in (vg250, sites_csv):
        if not Path(path).exists():
            raise FileNotFoundError(f"input not found: {path}")
    kreise = gpd.read_file(f"zip://{vg250}", layer="vg250_krs")
    if "GF" in kreise.columns:
        kreise = kreise[kreise["GF"] == 4]  # land area only, not the water polygons
    kreise = kreise.assign(ars5=kreise["ARS"].astype(str).str[:5]).to_crs(METRIC_CRS)
    communes = gpd.read_file(f"zip://{vg250}", layer="vg250_gem")
    sites = pd.read_csv(sites_csv, comment="#", dtype={"site_ags8": str})
    wanted = set(sites["site_ags8"])
    communes = communes[communes["AGS"].astype(str).isin(wanted)].to_crs(METRIC_CRS)
    missing = wanted - set(communes["AGS"].astype(str))
    if missing:
        raise ValueError(f"site communes not found in vg250_gem: {sorted(missing)}")
    points = communes.assign(geometry=communes.geometry.representative_point())
    return required_origin_kreise(kreise, points, max_distance_km)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--list-required-kreise", action="store_true")
    parser.add_argument("--vg250", type=Path, help="VG250 GeoPackage zip")
    parser.add_argument("--sites", type=Path, help="lsn_university_sites.csv")
    parser.add_argument("--max-distance-km", type=float, default=100.0)
    args = parser.parse_args(argv)

    if args.list_required_kreise:
        if args.vg250 is None or args.sites is None:
            parser.error("--list-required-kreise needs --vg250 and --sites")
        for ars5 in _list_required(args.vg250, args.sites, args.max_distance_km):
            print(ars5)
        return 0

    if not INSTITUTION_ORIGINS or not KREIS_DESTINATIONS or not ACCESS_DATE:
        raise ValueError("INSTITUTION_ORIGINS, KREIS_DESTINATIONS and ACCESS_DATE are empty: "
                         "fill them from the CHE DatenCHECK pages before running this script")
    frame = build_origins_frame(INSTITUTION_ORIGINS, KREIS_DESTINATIONS)
    counts = frame["view"].value_counts()
    header = [
        f"# Source: {SOURCE}, {SEMESTER}, accessed {ACCESS_DATE}",
        "# Script: scripts/seed_che_student_origins.py (values pinned in INSTITUTION_ORIGINS "
        "and KREIS_DESTINATIONS)",
        "# Licence: the CHE page names no licence; committed by owner decision 2026-11-25",
        "# Origin = Kreis of the school leaving certificate, not residence; cells below the "
        "tenth value are censored",
        f"# Rows: institution_origins {int(counts.get('institution_origins', 0))}, "
        f"kreis_destinations {int(counts.get('kreis_destinations', 0))}",
    ]
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(header) + "\n")
        frame.to_csv(handle, index=False, lineterminator="\n")
    print(f"[seed_che_student_origins] wrote {OUTPUT_PATH} ({len(frame)} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
