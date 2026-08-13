"""Socio tab: household socio-economic home points + Kreis choropleth (issue #267 split).

This module holds the pure per-Kreis aggregation helper (:func:`_socio_by_kreis`),
the economic-status ordinal mapping helper (:func:`_economic_status_ordinal`),
and the tab emitter (:func:`emit_socio`) that writes the xytime point CSVs and
the per-Kreis choropleth for household income / economic status / car
ownership.

``emit_socio`` calls :func:`braunschweig.analysis.simwrapper.geo_layers.write_xyt_csv`
directly (imported from that sibling, not via the ``spatial_export`` facade,
per this split's no-back-import-cycle rule).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pandas as pd

from braunschweig.analysis.simwrapper import writers as w
from braunschweig.analysis.simwrapper.geo_layers import write_xyt_csv

LOGGER = logging.getLogger("braunschweig.analysis.simwrapper.spatial")

# Ordered mapping of economic_status category labels to ordinal codes 1..5.
# These are the exact values written to the population CSVs by the synthesis
# pipeline (braunschweig.synthesis.population.enriched).
ECONOMIC_STATUS_ORDER = ("very_low", "low", "medium", "high", "very_high")
_ECONOMIC_STATUS_CODE: dict[str, int] = {
    cat: i + 1 for i, cat in enumerate(ECONOMIC_STATUS_ORDER)
}


# ---------------------------------------------------------------------------
# Pure helper functions (testable without disk I/O)
# ---------------------------------------------------------------------------

def _socio_by_kreis(homes_df: "pd.DataFrame") -> pd.DataFrame:
    """Aggregate socio-economic metrics per Kreis (5-digit ars5 code).

    Computes per-Kreis summary statistics from a flat DataFrame of homes
    that already has ``ars5`` attached (from
    :func:`braunschweig.analysis.spatial.assign_geographies`).

    Columns used (all optional -- missing ones are silently skipped):

    - ``household_income_eur``: float, averaged as ``mean_income_eur``.
    - ``high_income``: bool/int (1=True), averaged as ``high_income_share_pct``
      (0-100).
    - ``number_of_cars``: int, averaged as ``mean_cars``.
    - ``economic_status_ord``: ordinal 1..5, averaged as
      ``mean_economic_status`` (only over rows where the value is non-null;
      carless households have no status and are excluded -- this is logged
      in the calling code, not here).

    Args:
        homes_df: Flat DataFrame with ``ars5`` plus the optional attribute
            columns listed above.  One row per household.

    Returns:
        DataFrame with ``ars5`` and whatever metric columns could be computed
        (those whose source column was present and had at least one non-null
        value per Kreis).  Empty DataFrame when ``ars5`` is absent.
    """
    if "ars5" not in homes_df.columns:
        return pd.DataFrame()

    agg_spec: dict[str, tuple[str, Any]] = {}
    if "household_income_eur" in homes_df.columns:
        agg_spec["mean_income_eur"] = ("household_income_eur", "mean")
    if "high_income" in homes_df.columns:
        agg_spec["high_income_share_pct"] = (
            "high_income",
            lambda s: round(100.0 * pd.to_numeric(s, errors="coerce").mean(), 2),
        )
    if "number_of_cars" in homes_df.columns:
        agg_spec["mean_cars"] = ("number_of_cars", "mean")
    if "economic_status_ord" in homes_df.columns:
        agg_spec["mean_economic_status"] = ("economic_status_ord", "mean")

    if not agg_spec:
        return pd.DataFrame()

    result = (
        homes_df.groupby("ars5", sort=True)
        .agg(**agg_spec)
        .reset_index()
    )
    return result


def _economic_status_ordinal(series: pd.Series) -> pd.Series:
    """Map economic_status category strings to ordinal codes 1..5.

    Mapping (per CLAUDE.md BMDV classes):
        very_low -> 1, low -> 2, medium -> 3, high -> 4, very_high -> 5

    Unknown or null values map to NaN.

    Args:
        series: String series of economic_status values.

    Returns:
        Float series of ordinal codes (NaN for unknowns).
    """
    return series.map(_ECONOMIC_STATUS_CODE).astype(float)


# ---------------------------------------------------------------------------
# Tab 2: Socio home points (xytime)
# ---------------------------------------------------------------------------

def emit_socio(run_output_dir: str, folder: Path) -> "dict[str, Any] | None":
    """Write xytime CSVs and per-Kreis choropleth for household socio-economic attributes.

    Loads the synthetic population from ``run_output_dir`` via
    :func:`braunschweig.analysis.population_validation.population_source.load_population`.

    For each available numeric attribute one xytime CSV is written:

    - ``homes_income.xyt.csv`` (value = ``household_income_eur``).
    - ``homes_economic_status.xyt.csv`` (value = ordinal code 1..5 for
      ``economic_status``; sourced from vehicles.csv; only written for the
      ~84% of households that own a car -- carless households have no vehicle
      row and therefore no status).
    - ``homes_high_income.xyt.csv`` (value = ``high_income`` as 0/1).
    - ``homes_number_of_cars.xyt.csv`` (value = ``number_of_cars``).

    Also writes a per-Kreis choropleth when VG250 is available:

    - ``kreis_socio.geojson`` -- Kreis polygons (EPSG:4326) with aggregated
      metrics.
    - ``kreis_socio.csv`` -- per-Kreis metrics joined by ``ars5``.

    Coverage note: ``economic_status`` is sourced from ``vehicles.csv``
    (one row per vehicle, ``mode == car``; drop_duplicates on household_id).
    Carless households have no vehicle row and therefore no status -- they
    are excluded from the status point map and the ``mean_economic_status``
    Kreis aggregate.  The coverage is always logged explicitly.

    Returns ``None`` when no attribute can be produced (always logged).

    Primary/fallback rates are logged per CLAUDE.md requirement.

    Args:
        run_output_dir: eqasim run output directory.
        folder: SimWrapper dashboard output folder.

    Returns:
        Dashboard dict with xytime point maps + Kreis choropleth rows, or ``None``.
    """
    import geopandas as gpd
    from braunschweig.analysis.population_validation.population_source import (
        load_population,
    )
    from braunschweig.analysis import spatial

    try:
        pop = load_population(run_output_dir=run_output_dir)
    except Exception as exc:
        LOGGER.warning("[socio] could not load population from %s: %s -- tab skipped",
                       run_output_dir, exc)
        return None

    homes = pop.homes[["household_id", "geometry"]].copy()
    if homes.crs is None or homes.crs.to_epsg() != 25832:
        LOGGER.warning(
            "[socio] homes CRS is %s, expected EPSG:25832 -- tab skipped", homes.crs
        )
        return None

    households = pop.households.copy()
    households["household_id"] = households["household_id"].astype(str)
    homes["household_id"] = homes["household_id"].astype(str)

    # Build one wide household-attributes frame by joining all available
    # household-level attributes onto the homes geometry.
    hh_attrs = households[["household_id"]].copy()

    for col in ("household_income_eur", "high_income", "number_of_cars"):
        if col in households.columns:
            hh_attrs[col] = households[col].values
        else:
            LOGGER.info(
                "[socio] '%s' absent in households.csv -- attribute skipped", col
            )

    # --- Source economic_status (per-household synthesised attribute) ---------
    # economic_status is assigned to EVERY household by the synthesis
    # (status_from_hhtype), so its natural coverage is 100%. We therefore prefer
    # the PRIMARY full-coverage source persons.csv (synthesis.output writes it via
    # PERSON_OPTIONAL_OUTPUT_COLUMNS) and only FALL BACK to vehicles.csv (which
    # covers car-owning HHs only, ~84%) for legacy outputs that predate that
    # column -- the fallback + its partial coverage are logged loudly, never
    # silent (CLAUDE.md no-silent-fallback).
    n_total_hh = len(homes)
    status_col_available = False
    status_per_hh = None

    persons = pop.persons
    if (persons is not None and "economic_status" in persons.columns
            and "household_id" in persons.columns):
        sp = persons[["household_id", "economic_status"]].dropna(subset=["economic_status"]).copy()
        sp["household_id"] = sp["household_id"].astype(str)
        status_per_hh = sp.drop_duplicates("household_id", keep="first")
        LOGGER.info(
            "[socio] economic_status: PRIMARY source persons.csv -- %d / %d "
            "households (%.1f%%, full per-household coverage)",
            len(status_per_hh), n_total_hh,
            100.0 * len(status_per_hh) / max(n_total_hh, 1),
        )
    else:
        vehicles = pop.vehicles
        if (vehicles is not None and "economic_status" in vehicles.columns
                and "household_id" in vehicles.columns):
            car_vehicles = vehicles[vehicles["mode"] == "car"].copy() if "mode" in vehicles.columns else vehicles.copy()
            car_vehicles["household_id"] = (
                pd.to_numeric(car_vehicles["household_id"], errors="coerce")
                .astype("Int64")
                .apply(lambda v: str(int(v)) if pd.notna(v) else None)
            )
            status_per_hh = (
                car_vehicles[["household_id", "economic_status"]]
                .dropna(subset=["household_id"])
                .drop_duplicates("household_id", keep="first")
                .copy()
            )
            LOGGER.warning(
                "[socio] economic_status NOT in persons.csv -- FALLBACK to "
                "vehicles.csv (car-owning HHs only): %d / %d households (%.1f%%); "
                "carless HHs are excluded. A fresh run writes economic_status to "
                "persons.csv for full coverage.",
                len(status_per_hh), n_total_hh,
                100.0 * len(status_per_hh) / max(n_total_hh, 1),
            )
        elif vehicles is not None:
            LOGGER.warning(
                "[socio] economic_status absent in persons.csv and vehicles.csv "
                "(lean-run schema) -- economic status xyt skipped"
            )
        else:
            LOGGER.warning(
                "[socio] economic_status absent in persons.csv and no vehicles.csv "
                "in %s -- economic status xyt skipped", run_output_dir,
            )

    if status_per_hh is not None:
        status_per_hh = status_per_hh.copy()
        status_per_hh["economic_status_ord"] = _economic_status_ordinal(
            status_per_hh["economic_status"]
        )
        LOGGER.info(
            "[socio] economic_status ordinal mapping: %s",
            ", ".join(f"{k}={v}" for k, v in _ECONOMIC_STATUS_CODE.items()),
        )
        hh_attrs = hh_attrs.merge(
            status_per_hh[["household_id", "economic_status_ord"]],
            on="household_id", how="left",
        )
        status_col_available = True

    # Build GeoDataFrame: homes + all attribute columns.
    gdf_full = gpd.GeoDataFrame(
        homes.merge(hh_attrs, on="household_id", how="left"),
        geometry="geometry", crs=pop.homes.crs,
    )

    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    written_files: list[str] = []

    # --- Write individual xytime point CSVs ---
    def _write_attr_xyt(col: str, filename: str, label: str) -> bool:
        """Write one xytime CSV for ``col``; return True if written."""
        if col not in gdf_full.columns:
            return False
        n_notna = int(gdf_full[col].notna().sum())
        n_total = len(gdf_full)
        LOGGER.info(
            "[socio] %s: %d / %d rows non-null (%.1f%%)",
            col, n_notna, n_total, 100.0 * n_notna / max(n_total, 1),
        )
        if n_notna == 0:
            LOGGER.warning("[socio] %s: all null -- xyt skipped", col)
            return False
        # Cast high_income bool -> int so the xytime CSV is numeric.
        if gdf_full[col].dtype == bool or str(gdf_full[col].dtype) == "object":
            gdf_full[col] = pd.to_numeric(gdf_full[col], errors="coerce")
        write_xyt_csv(gdf_full, folder, filename, col)
        return True

    if _write_attr_xyt("household_income_eur", "homes_income.xyt.csv", "EUR"):
        written_files.append("homes_income.xyt.csv")
    if status_col_available and _write_attr_xyt(
        "economic_status_ord", "homes_economic_status.xyt.csv", "status (1-5)"
    ):
        written_files.append("homes_economic_status.xyt.csv")
    if _write_attr_xyt("high_income", "homes_high_income.xyt.csv", "high income (0/1)"):
        written_files.append("homes_high_income.xyt.csv")
    if _write_attr_xyt("number_of_cars", "homes_number_of_cars.xyt.csv", "cars"):
        written_files.append("homes_number_of_cars.xyt.csv")

    if not written_files:
        LOGGER.warning(
            "[socio] no attribute produced any output -- socio tab skipped"
        )
        return None

    # --- Per-Kreis choropleth (requires VG250; skipped gracefully if absent) ---
    choropleth_available = False
    try:
        kreise = spatial.load_kreise(pop.homes.crs)
        homes_geo = spatial.assign_geographies(
            pop.homes[["household_id", "geometry"]].copy().assign(
                household_id=pop.homes["household_id"].astype(str)
            ),
            kreise=kreise,
        )
        # Attach ars5 to the wide attribute frame.
        ars5_map = homes_geo[["household_id", "ars5"]].copy()
        ars5_map["household_id"] = ars5_map["household_id"].astype(str)
        homes_with_ars5 = gdf_full.drop(columns=["geometry"], errors="ignore").merge(
            ars5_map, on="household_id", how="left"
        )
        # Include economic_status_ord only if it was produced (for the Kreis mean).
        agg_df = _socio_by_kreis(homes_with_ars5)
        if not agg_df.empty:
            n_kreise_with_status = int(agg_df["mean_economic_status"].notna().sum()) if "mean_economic_status" in agg_df.columns else 0
            LOGGER.info(
                "[socio] per-Kreis aggregation: %d Kreise; mean_economic_status "
                "available for %d Kreise (excludes carless HHs per column).",
                len(agg_df), n_kreise_with_status,
            )
            # Write CSV with ars5 key for SimWrapper.
            w.write_csv(folder, "kreis_socio.csv", agg_df)
            # Write GeoJSON (EPSG:4326).
            kreise_4326 = kreise[["ars5", "geometry"]].to_crs(epsg=4326).copy()
            merged_geo = kreise_4326.merge(agg_df, on="ars5", how="left")
            merged_geo.to_file(folder / "kreis_socio.geojson", driver="GeoJSON")
            LOGGER.info(
                "[socio] wrote kreis_socio.geojson and kreis_socio.csv (%d Kreise)",
                len(merged_geo),
            )
            choropleth_available = True
        else:
            LOGGER.warning("[socio] per-Kreis aggregation produced no rows -- choropleth skipped")
    except Exception as exc:
        LOGGER.warning(
            "[socio] per-Kreis choropleth skipped: %s (VG250 may be absent)", exc
        )

    # --- Assemble dashboard rows ---
    rows: dict[str, list[dict[str, Any]]] = {}

    # Row 1: xytime point maps (only include cards for written files).
    point_cards: list[dict[str, Any]] = []
    if "homes_income.xyt.csv" in written_files:
        point_cards.append(w.card_xytime(
            "Household income by home location (EUR)",
            "homes_income.xyt.csv",
            value_label="EUR",
            radius=6,
            description=(
                "Each point = one household at its home location. "
                "Value = household_income_eur (synthesised, descriptive)."
            ),
        ))
    if "homes_economic_status.xyt.csv" in written_files:
        point_cards.append(w.card_xytime(
            "Economic status by home location (1=very low .. 5=very high)",
            "homes_economic_status.xyt.csv",
            value_label="economic status (1=very low .. 5=very high)",
            radius=6,
            description=(
                "Each point = one household at its home location. "
                "Ordinal: 1=very_low, 2=low, 3=medium, 4=high, 5=very_high "
                "(synthesised per household, descriptive). Primary source "
                "persons.csv = full coverage; legacy outputs fall back to "
                "vehicles.csv (car-owning HHs only) -- see the run log for the "
                "actual coverage."
            ),
        ))
    if "homes_high_income.xyt.csv" in written_files:
        point_cards.append(w.card_xytime(
            "High-income households by home location",
            "homes_high_income.xyt.csv",
            value_label="high income (1=yes, 0=no)",
            radius=6,
            description=(
                "Each point = one household. Value = 1 if high_income, 0 otherwise "
                "(synthesised, descriptive)."
            ),
        ))
    if "homes_number_of_cars.xyt.csv" in written_files:
        point_cards.append(w.card_xytime(
            "Number of cars by home location",
            "homes_number_of_cars.xyt.csv",
            value_label="number of cars",
            radius=6,
            description=(
                "Each point = one household. Value = number_of_cars (0, 1, 2, ...) "
                "(synthesised, descriptive)."
            ),
        ))
    if point_cards:
        rows["point_maps"] = point_cards

    # Row 2: Kreis choropleths.
    if choropleth_available:
        # Each big map card gets its own full-width row so it renders large.
        if "mean_income_eur" in agg_df.columns:
            rows["choropleth_income"] = [w.card_choropleth(
                "Mean household income by Kreis (EUR)",
                "kreis_socio.geojson",
                value_col="mean_income_eur",
                join="ars5",
                color_ramp="Viridis",
                height=13,
                description="Mean synthesised household_income_eur per Kreis (descriptive).",
            )]
        if "high_income_share_pct" in agg_df.columns:
            rows["choropleth_high_income"] = [w.card_choropleth(
                "High-income share by Kreis (%)",
                "kreis_socio.geojson",
                value_col="high_income_share_pct",
                join="ars5",
                color_ramp="Plasma",
                height=13,
                description="Share of high-income households per Kreis (descriptive).",
            )]
        if "mean_economic_status" in agg_df.columns:
            rows["choropleth_status"] = [w.card_choropleth(
                "Mean economic status by Kreis (1=very low .. 5=very high)",
                "kreis_socio.geojson",
                value_col="mean_economic_status",
                join="ars5",
                color_ramp="RdYlGn",
                height=13,
                description=(
                    "Mean ordinal economic status per Kreis (1=very_low .. 5=very_high), "
                    "synthesised per household (descriptive). Full coverage when sourced "
                    "from persons.csv; legacy vehicles.csv fallback excludes carless HHs "
                    "(see run log)."
                ),
            )]
        rows["kreis_table"] = [
            w.card_table(
                "Per-Kreis socio-economic summary",
                "kreis_socio.csv",
                width=2,
                description=(
                    "mean_income_eur, high_income_share_pct, mean_cars, "
                    "mean_economic_status per Kreis (descriptive). "
                    "mean_economic_status excludes carless HHs."
                ),
            )
        ]

    if not rows:
        LOGGER.warning("[socio] no rows assembled -- socio tab skipped")
        return None

    return w.dashboard(
        "Socio",
        "Household socio-economic structure (descriptive)",
        rows,
        description=(
            "Spatial distribution of synthesised socio-economic attributes at home locations "
            "(EPSG:25832). Descriptive output only -- no committed reference target."
        ),
    )
