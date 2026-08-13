"""SimWrapper spatial dashboard export -- facade module.

Emits the SimWrapper spatial layers of a run: fleet map, trip-origin/
destination hexagons ("spatial demand"), household socio-economic points,
purpose-to-mode behaviour (sankey + scatter), and commuter (Pendler) flows.
``export_spatial`` (near the bottom of this file) is the registry-based
driver wired into :func:`braunschweig.analysis.simwrapper.export.main`.

Module layout (issue #267 split; formerly one ~1600-line module): this file
is being extracted, one layer at a time, into SIBLING modules inside the
already-existing ``braunschweig.analysis.simwrapper`` package -- a sibling
split, not a package conversion (this file keeps its module path, so no
consumer import changes are needed). Every name a sibling defines is
re-exported here (``# noqa: F401  (re-exports)`` blocks below) so external
imports of ``braunschweig.analysis.simwrapper.spatial_export`` keep working
unchanged. Submodules extracted so far:

    fleet        Fleet map tab: vehicle geolocation, BEV/brand/powertrain mix
                 by Kreis (``load_fleet``, ``fleet_by_kreis``,
                 ``_brand_mix_by_kreis``, ``_powertrain_mix_by_kreis``,
                 ``emit_fleet``).

    geo_layers   Generic geometry-aware writers shared across tabs:
                 ``write_xyt_csv`` (xytime point-cloud CSV) and
                 ``write_kreis_choropleth_geojson`` (Kreis choropleth
                 GeoJSON). Task 1 had to place these temporarily inside
                 ``fleet`` (no dedicated sibling existed yet and leaving them
                 here would have forced a facade-import cycle); Task 2
                 relocated them into this dedicated sibling. ``emit_socio``
                 below still calls ``write_xyt_csv`` via this facade's
                 re-export.

    trip_demand  Spatial demand tab: ``_trips_xy`` (hexagon-map OD
                 coordinates), ``_purpose_to_mode`` (purpose->mode trip
                 counts), and ``emit_fleet``-style tab emitter
                 ``emit_spatial_demand``. ``_purpose_to_mode`` is consumed by
                 ``emit_behaviour`` below (still defined in this facade) via
                 this sibling's re-export.

The remaining tabs (socio, behaviour, commuters, student commuters) and
``export_spatial`` itself are still defined directly below; later tasks of
the same split will extract them into further siblings.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pandas as pd

if TYPE_CHECKING:
    import geopandas as gpd

from braunschweig.analysis.freight_filter import drop_freight_agents
from braunschweig.analysis.simwrapper import writers as w

LOGGER = logging.getLogger("braunschweig.analysis.simwrapper.spatial")

# ---------------------------------------------------------------------------
# Package submodules (extracted layer sections). Every name is re-exported
# here so external consumers (export.main(), tests) keep importing from this
# facade module path unchanged. This split is incremental (issue #267);
# further sibling modules will be added here by later tasks.
# ---------------------------------------------------------------------------

from . import fleet  # noqa: F401  (submodule re-export)
from .fleet import (  # noqa: F401  (re-exports)
    BEV_POWERTRAIN_VALUE,
    _MIN_BRAND_COVERAGE,
    _REQUIRED_FLEET_COLS,
    _brand_mix_by_kreis,
    _powertrain_mix_by_kreis,
    emit_fleet,
    fleet_by_kreis,
    load_fleet,
)
from . import geo_layers  # noqa: F401  (submodule re-export)
from .geo_layers import (  # noqa: F401  (re-exports)
    MAX_XYT_POINTS,
    _XYT_SAMPLE_SEED,
    write_kreis_choropleth_geojson,
    write_xyt_csv,
)
from . import trip_demand  # noqa: F401  (submodule re-export)
from .trip_demand import (  # noqa: F401  (re-exports)
    _purpose_to_mode,
    _trips_xy,
    emit_spatial_demand,
)


# ---------------------------------------------------------------------------
# Pure helper functions (testable without disk I/O)
# ---------------------------------------------------------------------------

# Ordered mapping of economic_status category labels to ordinal codes 1..5.
# These are the exact values written to the population CSVs by the synthesis
# pipeline (braunschweig.synthesis.population.enriched).
ECONOMIC_STATUS_ORDER = ("very_low", "low", "medium", "high", "very_high")
_ECONOMIC_STATUS_CODE: dict[str, int] = {
    cat: i + 1 for i, cat in enumerate(ECONOMIC_STATUS_ORDER)
}


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


# ---------------------------------------------------------------------------
# Tab 3: Behaviour (sankey + scatter)
# ---------------------------------------------------------------------------

def emit_behaviour(
    sim_output_dir: Path | None,
    record: "dict[str, Any] | None",
    folder: Path,
) -> "dict[str, Any] | None":
    """Write purpose-to-mode sankey and per-Kreis car-share scatter.

    Sankey:
        Reads ``eqasim_trips.csv`` from ``sim_output_dir``; aggregates
        trip counts by (following_purpose, mode); writes
        ``purpose_to_mode.csv`` (SEMICOLON-delimited).  Skipped when
        trips file is absent.

    Scatter:
        Reads ``record["matsim"]["per_kreis_sim"]`` (sim car-share %) and
        ``record["mid_reference"]["p12_per_kreis"]`` (MiD P12 car-share %
        as ``auto`` column); joins on ``ars5``; writes
        ``kreis_car_sim_vs_mid.csv``.  Skipped when either side absent.

    Returns ``None`` if neither part produced output.

    Args:
        sim_output_dir: Resolved path to the MATSim ``simulation_output/``
            directory, or ``None``.
        record: Run record dict from ``assemble_run_record``.
        folder: SimWrapper dashboard output folder.

    Returns:
        Dashboard dict or ``None``.
    """
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    rows: dict[str, list[dict[str, Any]]] = {}

    # --- Sankey: purpose -> mode ---
    trips_path: Path | None = None
    if sim_output_dir is not None:
        trips_path = Path(sim_output_dir) / "eqasim_trips.csv"
        if not trips_path.exists():
            trips_path = None

    if trips_path is not None:
        df = pd.read_csv(trips_path, sep=";")
        df = drop_freight_agents(df, label="behaviour")
        ptm = _purpose_to_mode(df)
        LOGGER.info(
            "[behaviour] sankey: %d (purpose, mode) pairs from %d trips",
            len(ptm), len(df),
        )
        # SimWrapper sankey expects SEMICOLON-delimited CSV.
        ptm.to_csv(folder / "purpose_to_mode.csv", sep=";", index=False, encoding="utf-8")
        LOGGER.info("[behaviour] wrote purpose_to_mode.csv")
        rows["sankey"] = [
            w.card_sankey(
                "Trip purpose to mode transitions",
                "purpose_to_mode.csv",
                sort=True,
                width=2,
                description=(
                    "Flow: following_purpose (from) -> mode (to). "
                    "Width = trip count. Cordon 'outside' trips excluded."
                ),
            )
        ]
    else:
        LOGGER.info(
            "[behaviour] eqasim_trips.csv not found in %s -- sankey skipped",
            sim_output_dir,
        )

    # --- Scatter: sim car-share vs MiD P12 car-share per Kreis ---
    per_kreis_sim = (record or {}).get("matsim", {}).get("per_kreis_sim") or {}
    p12_per_kreis = (record or {}).get("mid_reference", {}).get("p12_per_kreis") or []

    if per_kreis_sim and p12_per_kreis:
        sim_rows = []
        for ars5, d in per_kreis_sim.items():
            ms = d.get("mode_share_pct", {})
            sim_rows.append({"ars5": ars5, "sim_car_pct": ms.get("car", 0.0)})
        sim_df = pd.DataFrame(sim_rows)

        mid_rows = [
            {"ars5": str(entry["ars5"]), "mid_car_pct": float(entry["auto"])}
            for entry in p12_per_kreis
            if "ars5" in entry and "auto" in entry
        ]
        mid_df = pd.DataFrame(mid_rows)

        scatter_df = sim_df.merge(mid_df, on="ars5", how="inner")
        LOGGER.info(
            "[behaviour] scatter: %d Kreise matched (sim per_kreis_sim=%d, MiD p12=%d)",
            len(scatter_df), len(sim_df), len(mid_df),
        )
        if not scatter_df.empty:
            w.write_csv(folder, "kreis_car_sim_vs_mid.csv", scatter_df)
            LOGGER.info("[behaviour] wrote kreis_car_sim_vs_mid.csv (%d rows)", len(scatter_df))
            rows["scatter"] = [
                w.card_scatter(
                    "Car share per Kreis: Sim vs MiD P12.1",
                    "kreis_car_sim_vs_mid.csv",
                    x="mid_car_pct",
                    y="sim_car_pct",
                    x_axis_name="MiD 2023 P12.1 car share (%)",
                    y_axis_name="Simulation car share (%)",
                    width=2,
                    description=(
                        "Scatter of commute car mode share per Kreis: "
                        "x = MiD 2023 reference (auto %), y = simulation. "
                        "Points on the diagonal indicate a good fit."
                    ),
                )
            ]
        else:
            LOGGER.warning("[behaviour] scatter: no Kreise matched between sim and MiD -- scatter skipped")
    else:
        missing = []
        if not per_kreis_sim:
            missing.append("matsim.per_kreis_sim")
        if not p12_per_kreis:
            missing.append("mid_reference.p12_per_kreis")
        LOGGER.info("[behaviour] scatter skipped: missing %s", ", ".join(missing))

    if not rows:
        LOGGER.warning("[behaviour] neither sankey nor scatter produced output -- behaviour tab skipped")
        return None

    return w.dashboard(
        "Behaviour",
        "Mode transitions & per-Kreis fit",
        rows,
        description=(
            "Left: sankey of trip purpose -> mode (all trips, excl. cordon). "
            "Right: per-Kreis car share scatter (sim vs MiD P12.1 reference)."
        ),
    )


# ---------------------------------------------------------------------------
# export_spatial -- registry-based driver, wired into export.main()
# ---------------------------------------------------------------------------

def _load_commutes(run_output_dir: "str | None") -> "gpd.GeoDataFrame | None":
    """Load the synthesis home->work commute LineStrings (``*commutes.gpkg``).

    Returns None (logged) when the run dir or the file is absent, so the
    commuter tab can fall back / skip without a silent failure.
    """
    if run_output_dir is None:
        return None
    import geopandas as gpd
    path = next(Path(run_output_dir).glob("*commutes.gpkg"), None)
    if path is None:
        LOGGER.info("[commuters] no *commutes.gpkg in %s", run_output_dir)
        return None
    return gpd.read_file(path)


def emit_commuters(
    run_output_dir: "str | None",
    record: "dict[str, Any] | None",
    folder: Path,
) -> "dict[str, Any] | None":
    """Commuter (Pendler) tab: in-/out-/internal commuters per Kreis + top relations.

    Source of the work commute Kreis x Kreis matrix, in order of preference:
    1. **MATSim realised** work trips (``record["matsim"]["od_matrix"]``).
    2. **Synthesis** home->work assignment (``*commutes.gpkg``) -- works even
       without a MATSim run.
    The active source is named in the tab title so the two are never confused.
    Returns None (logged) when neither source is available.
    """
    from braunschweig.analysis.simwrapper import commuters as cm
    from braunschweig.analysis import spatial

    zm = cm.commute_matrix_from_record(record, "work") if record else None
    source = "MATSim realised work trips"
    if zm is None:
        commutes = _load_commutes(run_output_dir)
        if commutes is None:
            LOGGER.warning(
                "[commuters] neither MATSim work OD nor synthesis commutes.gpkg "
                "available -- commuter tab skipped")
            return None
        # commutes.gpkg loses its CRS metadata after clean_gpkg(); the synthesis
        # always writes in EPSG:25832, so set it explicitly for the spatial join.
        if commutes.crs is None:
            commutes = commutes.set_crs("EPSG:25832")
        kreise = spatial.load_kreise(commutes.crs)
        zm = cm.commute_matrix_from_synthesis(commutes, kreise)
        source = "synthesis home->work assignment (pre-MATSim)"
    zones, matrix = zm

    balance = cm.commuter_balance(zones, matrix)
    top = cm.top_relations(zones, matrix, n=12)
    LOGGER.info("[commuters] %d Kreise; source: %s", len(balance), source)

    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    w.write_csv(folder, "commuter_balance.csv", balance)
    w.write_csv(folder, "commuter_top_relations.csv", top)

    rows: dict[str, list[dict[str, Any]]] = {
        "bars": [w.card_bar(
            "In- / out- / internal commuters by Kreis",
            "commuter_balance.csv", x="ars5",
            columns=["einpendler_gesamt", "auspendler", "binnen"],
            legend_titles=["Einpendler (in)", "Auspendler (out)", "Binnen (internal)"],
            y_axis_name="commuters", width=2,
            description=f"Work commuters per Kreis. Source: {source}.")],
        "table": [w.card_table(
            "Top commuter relations (Kreis -> Kreis)",
            "commuter_top_relations.csv", width=2)],
    }

    # Net-balance choropleth (Einpendler - Auspendler), VG250 polygons in 4326.
    try:
        kreise4326 = spatial.load_kreise("EPSG:25832").to_crs(4326)[["ars5", "geometry"]]
        geo = kreise4326.merge(balance, on="ars5", how="left")
        geo.to_file(folder / "kreis_commuters.geojson", driver="GeoJSON")
        # Own full-width row so the map renders large.
        rows["choropleth"] = [w.card_choropleth(
            "Net commuter balance by Kreis (Einpendler - Auspendler)",
            "kreis_commuters.geojson",
            value_col="netto", join="ars5", color_ramp="RdYlGn",
            height=13,
            description=f"Positive = net in-commuting Kreis. Source: {source}.")]
        LOGGER.info("[commuters] wrote kreis_commuters.geojson (%d Kreise)", len(geo))
    except Exception as exc:
        LOGGER.warning("[commuters] net-balance choropleth skipped: %s", exc)

    return w.dashboard("Commuters", f"Commuters / Pendler ({source})", rows)


def emit_student_commuters(
    persons: "pd.DataFrame | None",
    locations: "gpd.GeoDataFrame | None",
    folder: Path,
) -> "dict[str, Any] | None":
    """Student in-commuter (#140) OD-flow + distance tab.

    Unlike :func:`emit_commuters` (which reads a POST-HOC disk artifact, since
    the SvB in-commuters never carry a fine external-Kreis breakdown on disk),
    the student in-commuter frames are passed in directly by the caller from
    the LIVE ``braunschweig.synthesis.student_incommuters`` stage output (see
    ``braunschweig.analysis.simwrapper_export``): that stage is the only place
    the per-agent ``orig_ars5`` / ``dest_commune`` columns exist, because
    in-commuters bypass ``synthesis.output`` (which only exports the resident
    population) and the MATSim-realised OD (``metrics_od_matrix``) collapses
    every external origin into one coarse ``"external"`` zone.

    Args:
        persons: The student stage's ``persons`` frame (must carry
            ``orig_ars5`` / ``dest_commune``, attached by
            ``student_incommuters._inject``), or ``None``/empty when the
            feature is off, skipped, or the caller did not pass it.
        locations: The student stage's ``locations`` GeoDataFrame (3 rows per
            agent: activity_index 0/2 = home, 1 = education), used to derive
            the per-agent straight-line origin->campus distance.
        folder: SimWrapper dashboard output folder.

    Returns:
        Dashboard dict, or ``None`` (logged) when there are no student
        in-commuters to report -- writes nothing in that case.
    """
    from braunschweig.analysis.simwrapper import student_commuters as sc
    from braunschweig.data.cordon.plans import straight_line_distance_km

    if persons is None or len(persons) == 0:
        LOGGER.info(
            "[student_commuters] no student in-commuters (feature off, "
            "zero-count run, or frames not supplied) -- tab skipped"
        )
        return None
    if locations is None or len(locations) == 0:
        LOGGER.warning(
            "[student_commuters] %d student in-commuter persons but an empty "
            "locations frame -- tab skipped (should never happen for an "
            "active student in-commuter stage)", len(persons),
        )
        return None

    persons = persons[["person_id", "orig_ars5", "dest_commune"]].copy()
    home = (locations[locations["activity_index"] == 0]
            .set_index("person_id").loc[persons["person_id"]])
    education = (locations[locations["activity_index"] == 1]
                .set_index("person_id").loc[persons["person_id"]])
    straight_line_km = pd.Series(
        straight_line_distance_km(
            home.geometry.x.to_numpy(), home.geometry.y.to_numpy(),
            education.geometry.x.to_numpy(), education.geometry.y.to_numpy(),
        ),
        index=persons.index, name="straight_line_km",
    )

    folder = Path(folder)
    sc.write_outputs(persons[["orig_ars5", "dest_commune"]], straight_line_km, str(folder))
    LOGGER.info(
        "[student_commuters] wrote student_commuter_od.csv / "
        "_top_relations.csv / _distance.csv for %d student in-commuters "
        "(mean straight-line distance %.1f km)",
        len(persons), float(straight_line_km.mean()),
    )

    return w.dashboard(
        "Student commuters",
        "Student in-commuters (#140): origin-Kreis -> campus OD + distance",
        {
            "table": [w.card_table(
                "Student OD flows (origin Kreis -> destination university commune)",
                "student_commuter_od.csv", width=2,
            )],
            "top": [w.card_table(
                "Top student in-commuter relations",
                "student_commuter_top_relations.csv", width=1,
            )],
            "distance": [w.card_bar(
                "Student in-commuter distance distribution",
                "student_commute_distance.csv",
                x="band", columns=["count"],
                x_axis_name="straight-line distance band (km)",
                y_axis_name="students", width=1,
            )],
        },
        description=(
            "Cross-cordon student in-commuter OD flows and origin->campus "
            "straight-line distances (braunschweig.synthesis.student_incommuters). "
            "Model output, not compared to a committed reference."
        ),
    )


def export_spatial(
    target_dir: str | Path,
    run_output_dir: str | None = None,
    sim_cache: str | None = None,
    record: "dict[str, Any] | None" = None,
    start_index: int = 9,
    student_frames: "dict[str, Any] | None" = None,
) -> list[Path]:
    """Write spatial dashboard tabs (fleet, spatial-demand, socio, behaviour).

    Iterates over a registry of ``(name, emit_fn)`` pairs, writes each
    returned dashboard dict as ``dashboard-{idx}-{name}.yaml``, and skips
    boards that return ``None`` (always logging the skip so there are no
    silent fallbacks).

    Tab order (sequential indices starting at ``start_index``):
    1. fleet -- vehicle fleet map (all-features runs only).
    2. spatial-demand -- trip origin/destination hexagons.
    3. socio -- home points coloured by income / economic status.
    4. behaviour -- purpose->mode sankey + per-Kreis car-share scatter.
    5. commuters -- SvB in/out/internal commuters per Kreis + top relations.
    6. student-commuters -- student in-commuter OD flows + distance (#140);
       only produced when ``student_frames`` is supplied and non-empty (the
       caller must pull it from the LIVE
       ``braunschweig.synthesis.student_incommuters`` stage -- see
       :func:`emit_student_commuters` for why this differs from the other,
       disk-based tabs).

    Args:
        target_dir: SimWrapper dashboard output folder (created if absent).
        run_output_dir: eqasim run output directory.
        sim_cache: synpp cache directory.
        record: Run record dict from ``assemble_run_record`` (needed for the
            behaviour scatter; skipped when ``None``).
        start_index: Starting dashboard index (default 9 so it follows the 8
            core tabs produced by ``export_run``).
        student_frames: The ``braunschweig.synthesis.student_incommuters``
            stage output dict (keys ``persons``, ``locations``, ...), or
            ``None`` when the caller has no live pipeline context (e.g. the
            standalone CLI) or the feature is off -- the student-commuters tab
            is then skipped (logged, not silently ignored).

    Returns:
        List of written YAML :class:`pathlib.Path` objects.
    """
    if run_output_dir is None and sim_cache is None and not student_frames:
        LOGGER.warning(
            "[spatial_export] neither run_output_dir, sim_cache, nor "
            "student_frames provided -- all spatial tabs skipped"
        )
        return []

    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    # Resolve the fleet / socio source directory (run_output preferred).
    source_dir = run_output_dir if run_output_dir is not None else sim_cache

    # Resolve the MATSim simulation_output/ for trips-based tabs.
    sim_output_dir: Path | None = None
    if sim_cache is not None:
        from braunschweig.analysis.dashboard.build_dashboard import _find_sim_output
        sim_output_dir = _find_sim_output(Path(sim_cache))
        if sim_output_dir is None:
            LOGGER.info(
                "[spatial_export] no matsim.simulation.run__*.cache found in %s "
                "-- trips-based tabs (spatial-demand, behaviour-sankey) will be skipped",
                sim_cache,
            )

    _SPATIAL_REGISTRY: list[tuple[str, Any]] = [
        # Tab: fleet
        ("fleet", lambda f: emit_fleet(source_dir, f)),
        # Tab: spatial-demand (hexagons from eqasim_trips.csv)
        ("spatial-demand", lambda f: emit_spatial_demand(sim_output_dir, f)),
        # Tab: socio (home xytime from population)
        ("socio", lambda f: emit_socio(source_dir, f) if source_dir else None),
        # Tab: behaviour (sankey + scatter)
        ("behaviour", lambda f: emit_behaviour(sim_output_dir, record, f)),
        # Tab: commuters (Pendler in/out/internal + top relations); works in
        # both modes (MATSim work OD, else synthesis commutes.gpkg).
        ("commuters", lambda f: emit_commuters(source_dir, record, f)),
        # Tab: student-commuters (#140 OD flows + distance); requires the
        # LIVE student_incommuters stage frames (see emit_student_commuters),
        # so it is a no-op (None) unless the caller supplied student_frames.
        ("student-commuters", lambda f: emit_student_commuters(
            (student_frames or {}).get("persons"),
            (student_frames or {}).get("locations"), f)),
    ]

    written: list[Path] = []
    for idx, (name, fn) in enumerate(_SPATIAL_REGISTRY, start=start_index):
        try:
            board = fn(target_dir)
        except Exception as exc:
            LOGGER.warning(
                "[spatial_export] tab '%s' skipped due to error: %s", name, exc
            )
            board = None
        if board is None:
            LOGGER.info(
                "[spatial_export] tab '%s' has no data, skipped", name
            )
            continue
        path = w.write_yaml(target_dir, f"dashboard-{idx}-{name}.yaml", board)
        written.append(path)
        LOGGER.info("[spatial_export] wrote %s", path.name)

    LOGGER.info(
        "[spatial_export] wrote %d spatial dashboard tab(s) to %s",
        len(written), target_dir,
    )
    return written
