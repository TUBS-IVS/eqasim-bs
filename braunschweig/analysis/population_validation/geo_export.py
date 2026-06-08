"""Config-driven aggregation + multi-layer GeoPackage + per-level CSV export for
free spatial exploration of the synthetic population (down to person/vehicle)."""
from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

LOGGER = logging.getLogger("braunschweig.analysis.geo_export")

# Default aggregation spec. Extend by adding (column, kind) tuples; no code change
# elsewhere is needed. Columns absent in a given run are skipped (logged).
DEFAULT_PERSON_SPEC = [
    ("age", "numeric"), ("sex", "categorical"),
    ("economic_status", "categorical"), ("has_driving_license", "categorical"),
    ("pt_subscription_type", "categorical"), ("employed", "categorical"),
]
DEFAULT_HOUSEHOLD_SPEC = [
    ("household_size", "numeric"), ("household_income_eur", "numeric"),
    ("number_of_cars", "numeric"), ("number_of_bicycles", "numeric"),
    ("housing_tenure", "categorical"),
    # The income EUR-class distribution is exported spatially here (the
    # descriptive-only income_class control was removed from the validation
    # registry because MiD H4 income is HH-size conditional, not a geographic
    # target).
    ("income", "categorical"),
]
DEFAULT_VEHICLE_SPEC = [
    ("brand", "categorical"), ("powertrain", "categorical"),
    ("euro_norm", "categorical"), ("fuel", "categorical"),
    ("power_kw", "numeric"), ("displacement_ccm", "numeric"),
]
GPKG_NAME = "population_explorer.gpkg"


def aggregate(df: pd.DataFrame, group_col: str, spec, count_name: str) -> pd.DataFrame:
    """Aggregate a flat DataFrame by ``group_col``.

    For each entry in ``spec``:
    - ``"numeric"``: mean, median, sum columns are added.
    - ``"categorical"``: share columns (0-1) per category value are added.
    Columns absent from ``df`` are skipped with an INFO log so the caller
    does not need to guard for optional attributes.
    """
    out = df.groupby(group_col).size().rename(count_name).reset_index()
    for col, kind in spec:
        if col not in df.columns:
            LOGGER.info("geo_export: column %r absent; aggregation skipped", col)
            continue
        if kind == "numeric":
            vals = pd.to_numeric(df[col], errors="coerce")
            g = vals.groupby(df[group_col])
            stats = pd.DataFrame({f"{col}_mean": g.mean(), f"{col}_median": g.median(),
                                  f"{col}_sum": g.sum()}).reset_index()
            out = out.merge(stats, on=group_col, how="left")
        elif kind == "categorical":
            ct = pd.crosstab(df[group_col], df[col].astype(str), normalize="index")
            ct.columns = [f"{col}_share_{c}" for c in ct.columns]
            out = out.merge(ct.reset_index(), on=group_col, how="left")
    return out


def _aggregate_level(persons, households, vehicles, geo_col,
                     person_spec, household_spec, vehicle_spec) -> pd.DataFrame:
    """Combine person + household + vehicle aggregates for one geography level
    (keyed by geo_col) into a single wide frame.

    Sources that are empty/None or lack geo_col are skipped (logged). The
    returned frame always contains the count column from the person aggregate
    (``n_persons``) plus any household/vehicle columns joined via outer merge on
    ``geo_col``.
    """
    frames = []
    p = pd.DataFrame(persons.drop(columns="geometry", errors="ignore"))
    frames.append(aggregate(p, geo_col, person_spec, "n_persons"))

    if households is not None and len(households) and geo_col in households.columns:
        h = pd.DataFrame(households.drop(columns="geometry", errors="ignore"))
        frames.append(aggregate(h, geo_col, household_spec, "n_households"))
    elif household_spec:
        LOGGER.info(
            "geo_export: households absent/empty or missing %r; household aggregates skipped",
            geo_col)

    if vehicles is not None and len(vehicles) and geo_col in vehicles.columns:
        v = pd.DataFrame(vehicles.drop(columns="geometry", errors="ignore"))
        frames.append(aggregate(v, geo_col, vehicle_spec, "n_vehicles"))
    elif vehicle_spec:
        LOGGER.info(
            "geo_export: vehicles absent/empty or missing %r; vehicle aggregates skipped",
            geo_col)

    out = frames[0]
    for f in frames[1:]:
        out = out.merge(f, on=geo_col, how="outer")
    return out


def write_geo_package(out_dir, persons, households, vehicles,
                      gemeinde_poly, kreis_poly,
                      person_spec=None, household_spec=None, vehicle_spec=None,
                      deviation_kreis=None, deviation_gemeinde=None,
                      trip_coherence_kreis=None) -> dict:
    """Write the multi-layer GPKG + per-level CSVs.

    Parameters
    ----------
    out_dir:
        Output directory; created if it does not exist.
    persons:
        GeoDataFrame with at minimum ``commune_id``, ``ars5``, ``geometry``.
    households:
        GeoDataFrame (may be empty) with household-level attributes.
    vehicles:
        GeoDataFrame or None (may be empty).
    gemeinde_poly:
        Polygon layer keyed on ``commune_id``; attribute aggregates are joined here.
    kreis_poly:
        Polygon layer keyed on ``ars5``; attribute aggregates are joined here.
    person_spec / household_spec / vehicle_spec:
        List of ``(column, kind)`` pairs overriding the defaults. Absent columns
        are silently skipped (INFO log).
    deviation_kreis / deviation_gemeinde:
        Optional wide DataFrames with columns ``ars5`` / ``commune_id`` plus
        ``'<control>__<category>_delta_pp'`` deviation columns. When provided
        they are merged onto the polygon layers so spatial control deviations
        are visible in QGIS / GIS tools.

    Returns
    -------
    dict mapping layer/file names to absolute Path objects.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    person_spec = DEFAULT_PERSON_SPEC if person_spec is None else person_spec
    household_spec = DEFAULT_HOUSEHOLD_SPEC if household_spec is None else household_spec
    vehicle_spec = DEFAULT_VEHICLE_SPEC if vehicle_spec is None else vehicle_spec

    gpkg = out_dir / GPKG_NAME
    # Remove any stale GPKG so the first to_file always creates a fresh file.
    if gpkg.exists():
        gpkg.unlink()

    paths = {"gpkg": gpkg}

    # --- Point layers (one row per agent / vehicle) ---
    persons.to_file(str(gpkg), layer="persons", driver="GPKG")
    if len(households):
        households.to_file(str(gpkg), layer="households", driver="GPKG", mode="a")
    if vehicles is not None and len(vehicles):
        vehicles.to_file(str(gpkg), layer="vehicles", driver="GPKG", mode="a")

    # --- Gemeinde polygon layer with person + household + vehicle aggregates ---
    agg_gem = _aggregate_level(persons, households, vehicles, "commune_id",
                               person_spec, household_spec, vehicle_spec)
    if deviation_gemeinde is not None:
        agg_gem = agg_gem.merge(deviation_gemeinde, on="commune_id", how="left")
    gem = gemeinde_poly.merge(agg_gem, on="commune_id", how="left")
    gem.to_file(str(gpkg), layer="gemeinde_aggregat", driver="GPKG", mode="a")

    # --- Kreis polygon layer with person + household + vehicle aggregates ---
    agg_kreis = _aggregate_level(persons, households, vehicles, "ars5",
                                 person_spec, household_spec, vehicle_spec)
    if deviation_kreis is not None:
        agg_kreis = agg_kreis.merge(deviation_kreis, on="ars5", how="left")
    # Per-Kreis trip-coherence (realised vs MiD W1/P36 + signed deltas) so the
    # purpose/mobility gaps are mappable alongside the demographic deviations.
    if trip_coherence_kreis is not None:
        agg_kreis = agg_kreis.merge(trip_coherence_kreis, on="ars5", how="left")
    kreis = kreis_poly.merge(agg_kreis, on="ars5", how="left")
    kreis.to_file(str(gpkg), layer="kreis_aggregat", driver="GPKG", mode="a")

    LOGGER.info("geo_export: wrote GPKG %s", gpkg)

    # --- Flat CSV exports (no geometry, for spreadsheet / pandas use) ---
    for name, frame in (("level_persons", persons), ("level_households", households)):
        if frame is not None and len(frame):
            csv_path = out_dir / f"{name}.csv"
            pd.DataFrame(frame.drop(columns="geometry", errors="ignore")).to_csv(
                csv_path, index=False)
            paths[name] = csv_path
            LOGGER.info("geo_export: wrote %s (%d rows)", csv_path.name, len(frame))

    if vehicles is not None and len(vehicles):
        csv_path = out_dir / "level_vehicles.csv"
        pd.DataFrame(vehicles.drop(columns="geometry", errors="ignore")).to_csv(
            csv_path, index=False)
        paths["level_vehicles"] = csv_path
        LOGGER.info("geo_export: wrote %s (%d rows)", csv_path.name, len(vehicles))

    agg_gem_path = out_dir / "agg_gemeinde.csv"
    agg_gem.to_csv(agg_gem_path, index=False)
    paths["agg_gemeinde"] = agg_gem_path

    agg_kreis_path = out_dir / "agg_kreis.csv"
    agg_kreis.to_csv(agg_kreis_path, index=False)
    paths["agg_kreis"] = agg_kreis_path

    LOGGER.info("geo_export: wrote aggregate CSVs agg_gemeinde.csv, agg_kreis.csv")
    return paths
