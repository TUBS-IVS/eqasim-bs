"""Compare two releases of the TUBS-IVS building activity potentials (ADR-0190).

Read-only measurement: the superseded parquet release (data record
``building_activity_potentials``) against the redistribution GeoPackage
(data record ``building_potentials_redistribution``, layer ``buildings``).
It writes one plain-text report with

* per potential: buildings with a positive value, sum, and the 50/90/99/100 %
  quantiles of the positive values (the scale the secondary scorer sees);
* the old ``other`` potential as derived before ADR-0190 is NOT recomputed
  here (its code was removed); its scale is taken from ``potential_generic``;
* the spatial overlap: share of each old potential that lay on old buildings
  whose representative point falls inside a building of the new release;
* the plausibility of ``value_errands`` as the ``other`` potential: agreement
  with the ``errands`` activity label, value by Bosserhof class, share of the
  value resting on rates the producer marks as assumed, zones with no errand
  building.

Usage::

    python scripts/compare_building_potential_releases.py --old <parquet> --new <gpkg> --out <report.txt>
"""
from __future__ import annotations

import argparse
import io

import geopandas as gpd
import pandas as pd
import pyogrio

#: Old internal column -> new source column carrying the same zonal total.
SAME_TOTAL_COLUMNS = {
    "potential_work": "assigned_Workers",
    "potential_school": "assigned_School",
    "potential_university": "assigned_University",
    "potential_kindergarten": "assigned_Kindergarten",
    "potential_retail_daily": "assigned_Retail_Daily",
    "potential_retail_non_daily": "assigned_Retail_Non_Daily",
    "potential_leisure": "assigned_Leisure",
}
VALUE_COLUMNS = ["value_errands", "value_business", "value_sports", "value_meetup", "value_lessons"]
QUANTILES = [0.5, 0.9, 0.99, 1.0]


def describe_positive(series: pd.Series) -> str:
    positive = series[series > 0]
    quantiles = positive.quantile(QUANTILES).round(2).tolist() if len(positive) else []
    return "n_positive=%d sum=%.1f q50/q90/q99/max=%s" % (len(positive), float(series.sum()), quantiles)


def has_label(labels: pd.Series, label: str) -> pd.Series:
    return labels.fillna("").str.split(";").apply(lambda xs: label in [x.strip() for x in xs])


def build_report(old: gpd.GeoDataFrame, new: gpd.GeoDataFrame, zones: pd.DataFrame) -> str:
    out = io.StringIO()
    w = lambda text="": out.write(text + "\n")  # noqa: E731
    w("buildings: old %d, new %d" % (len(old), len(new)))
    w()
    w("== Scale per potential (old internal column -> new source column)")
    for old_col, new_col in SAME_TOTAL_COLUMNS.items():
        w("%s: OLD %s" % (old_col, describe_positive(old[old_col])))
        w("%s: NEW %s" % (new_col, describe_positive(new[new_col])))
    w("potential_generic (old base of the derived other potential): OLD %s"
      % describe_positive(old["potential_generic"]))
    for col in VALUE_COLUMNS:
        w("%s: NEW %s" % (col, describe_positive(new[col])))
    w()
    w("== Spatial overlap (old representative point inside a new building)")
    points = old[["building_id"] + list(SAME_TOTAL_COLUMNS) + ["potential_generic", "geometry"]].copy()
    points["geometry"] = points.geometry.representative_point()
    joined = gpd.sjoin(points, new[["geometry"]], how="left", predicate="within")
    joined = joined[~joined.index.duplicated(keep="first")]
    inside = joined["index_right"].notna()
    w("old buildings inside a new building: %d of %d" % (int(inside.sum()), len(joined)))
    for col in list(SAME_TOTAL_COLUMNS) + ["potential_generic"]:
        total = float(joined[col].sum())
        share = float(joined.loc[inside, col].sum()) / total if total else float("nan")
        w("%s: share of old value on matched buildings %.3f" % (col, share))
    w()
    w("== value_errands as the other potential")
    errand_label = has_label(new["labels_used"], "errands")
    positive = new["value_errands"] > 0
    w("errands label %d, value_errands > 0 %d, label without value %d, value without label %d"
      % (int(errand_label.sum()), int(positive.sum()), int((errand_label & ~positive).sum()),
         int((positive & ~errand_label).sum())))
    by_class = new[positive].groupby("bosserhof_class")["value_errands"].agg(["count", "sum"])
    by_class["share"] = (by_class["sum"] / by_class["sum"].sum()).round(3)
    by_class["sum"] = by_class["sum"].round(1)
    w(by_class.sort_values("sum", ascending=False).to_string())
    assumed = positive & new["value_assumed"].fillna("").str.contains("errands")
    w("share of value_errands on rates marked assumed by the producer: %.3f"
      % (float(new.loc[assumed, "value_errands"].sum()) / float(new.loc[positive, "value_errands"].sum())))
    per_zone = new[positive].groupby("zone_no").size()
    with_errand = zones["NO"].isin(per_zone.index)
    w("zones with at least one errand building: %d of %d" % (int(with_errand.sum()), len(zones)))
    return out.getvalue()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--old", required=True, help="superseded building_activity_potentials.parquet")
    parser.add_argument("--new", required=True, help="08_redistribution.gpkg")
    parser.add_argument("--out", required=True, help="report path (.txt)")
    args = parser.parse_args(argv)
    old = gpd.read_parquet(args.old)
    new = pyogrio.read_dataframe(args.new, layer="buildings")
    if old.crs is None or new.crs is None or old.crs.to_epsg() != 25832 or new.crs.to_epsg() != 25832:
        raise ValueError("both releases must be in EPSG:25832 (old %s, new %s)" % (old.crs, new.crs))
    zones = pyogrio.read_dataframe(args.new, layer="zones", read_geometry=False)
    report = build_report(old, new, zones)
    with open(args.out, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(report)
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
