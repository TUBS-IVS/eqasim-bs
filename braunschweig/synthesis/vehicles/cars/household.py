"""Per-household vehicle generation (Task F5 / spec component A4).

Replaces the legacy one-car-per-person ``default_car`` fleet with exactly the
household's MiD-H7 ``number_of_cars`` vehicles per household, each owned by a
licensed adult of that household and typed via the KBA/HBEFA per-vehicle
generative chain (:mod:`braunschweig.synthesis.vehicles.fleet_sampling_de`).

Design (spec A4)
----------------
For every household:

1. ``number_of_cars`` is the MiD H7 household car count (broadcast per person in
   the enriched stage). It is aggregated back to ONE value per household via the
   max (every member carries the same household value; max is robust to any
   per-person inconsistency, matching
   :func:`braunschweig.synthesis.population.enriched._derive_car_availability_consistent`).
2. A household with ``number_of_cars == 0`` produces NO vehicle.
3. Otherwise exactly ``number_of_cars`` vehicles are emitted and assigned to the
   household's licensed adults (``has_license`` and ``age >= ADULT_AGE``),
   **highest-need first** -- ordered by descending age (a deterministic,
   reproducible proxy for car need; oldest licensed adult first), tie-broken by
   ascending ``person_id`` so the assignment is fully deterministic. The first
   ``number_of_cars`` licensed adults each receive one car.
4. If ``number_of_cars`` exceeds the number of licensed adults, the surplus cars
   still belong to the household and are assigned round-robin over the licensed
   adults (so a 2-licensed-adult household with 3 cars gives one adult 2 cars).
5. If a household has cars but NO licensed adult (rare; e.g. an elderly couple
   without a recorded licence), the cars are assigned to the OLDEST adult of the
   household and the event is counted and logged (no-silent-fallback rule). No
   car is ever dropped.

"3+" handling
-------------
MiD H7 tops out at "3 or more" (``number_of_cars == 3`` means 3+). A small
empirical tail (4th/5th car) would require the KBA FZ 3 per-Gemeinde mean cars
per household; that table is NOT among the committed derived CSVs of this fleet
work (see ``eqasim-data/data/braunschweig/kba/derived/``), so the 3+ category is
kept at 3 vehicles. This is documented here and is a conservative lower bound:
inflating 3 -> 4+ without the FZ 3 reference would be an undocumented data
assumption (prohibited by CLAUDE.md). When FZ 3 is extracted, expand here.

Car-frame columns for F4
------------------------
:func:`build_household_car_frame` emits one row per household car with the
columns :func:`braunschweig.synthesis.vehicles.fleet_sampling_de.sample_fleet`
requires:

  * ``economic_status`` -- the household's MiD economic status (from the owner's
    enriched ``economic_status``; constant within a household).
  * ``kreis_ags5`` -- ``"03" + Kreis3`` derived from the home commune's 12-digit
    ARS (``ARS[2:5]`` is the Kreis 3-digit, consistent with
    :func:`braunschweig.data.bbsr.regiostar.ars_to_ags8` and the KBA Kennziffer).
  * ``gemeinde`` -- the home Gemeinde NAME, upper-cased, matching the
    ``gemeinde`` key of ``kba_gemeinde_private_bev.csv`` (FZ 27.17). Derived from
    the RegioStaR reference (``commune_id`` -> ``name``) so it agrees with the
    extraction.
  * ``raumtyp`` -- the home RegioStaR-7 code (71..77), from the RegioStaR
    reference (``commune_id`` -> ``regiostar7``) via the AGS-8 join.

plus ``household_id`` and ``owner_id`` (the assigned licensed adult's
``person_id``) carried through to the writer.

Off-equivalence
---------------
The vehicles stage selects this module only for ``vehicles_method:"household"``.
For ``vehicles_method:"default"`` the legacy
``synthesis.vehicles.cars.default`` stage is used unchanged (byte-identical).
:func:`legacy_one_car_per_person` reproduces that exact frame and is used by the
OFF-equivalence test only.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from braunschweig.data.bbsr.regiostar import ars_to_ags8
from braunschweig.data.kba import hsn_tsn
from braunschweig.synthesis.vehicles import fleet_sampling_de as fleet

logger = logging.getLogger(__name__)

#: Minimum age for a person to be a car-owning adult. The legal Pkw driving age
#: in Germany is 18 (Klasse B; matches ``LICENSE_MIN_AGE`` in the enriched
#: stage). ``has_license`` already forces sub-18 persons to no-licence, but the
#: explicit adult floor is kept for clarity and robustness.
ADULT_AGE = 18

#: MiD H7 caps the car count at "3 or more". A value of 3 therefore means 3+.
#: Without the KBA FZ 3 per-Gemeinde mean (not a committed derived CSV) the 3+
#: tail is kept at exactly 3 vehicles (documented in the module docstring).
NUMBER_OF_CARS_CAP = 3


def _kreis_ags5_from_ars(commune_id: str) -> str:
    """Kreis AGS-5 (``"03" + Kreis3`` inside NDS) from a home ARS.

    The 12-digit ARS is Land(2)+RB(1)+Kreis(2)+VG(4)+Gem(3); the AGS-5 Kreis code
    is Land(2)+RB(1)+Kreis(2) = ``ARS[0:5]`` (e.g. ``031010000000`` ->
    ``03101``). For an 8-digit AGS the Kreis-5 is likewise ``AGS[0:5]``. The
    leading ``03`` is the Niedersachsen Land prefix, so this equals
    ``"03" + Kreis3`` and is consistent with the KBA Kennziffer and
    :func:`braunschweig.data.bbsr.regiostar.ars_to_ags8`.
    """
    s = str(commune_id)
    return s[:5]


def build_household_car_frame(df_persons: pd.DataFrame, df_homes: pd.DataFrame,
                              df_regiostar: pd.DataFrame) -> pd.DataFrame:
    """Build the one-row-per-household-car frame F4 consumes.

    Parameters
    ----------
    df_persons : enriched population with at least ``household_id``,
        ``person_id``, ``age``, ``has_license``, ``number_of_cars`` (MiD H7,
        household value broadcast per person) and ``economic_status``.
    df_homes : home zones with ``household_id`` and ``commune_id`` (12-digit ARS).
    df_regiostar : RegioStaR reference with ``commune_id`` (8-digit AGS),
        ``name`` (Gemeinde name) and ``regiostar7`` (RS7 code).

    Returns
    -------
    DataFrame with one row per emitted vehicle carrying ``household_id``,
    ``owner_id`` and the F4 columns ``economic_status, kreis_ags5, gemeinde,
    raumtyp``. 0-car households contribute no row.
    """
    required = {"household_id", "person_id", "age", "has_license",
                "number_of_cars", "economic_status"}
    missing = required - set(df_persons.columns)
    if missing:
        raise ValueError(
            "build_household_car_frame: df_persons is missing required columns "
            f"{sorted(missing)}"
        )

    # Home commune (12-digit ARS) per household -> Kreis AGS-5 + AGS-8 join key.
    homes = df_homes[["household_id", "commune_id"]].drop_duplicates("household_id").copy()
    homes["commune_id"] = homes["commune_id"].astype(str)
    homes["kreis_ags5"] = homes["commune_id"].map(_kreis_ags5_from_ars)
    homes["ags8"] = homes["commune_id"].map(ars_to_ags8)

    # RegioStaR reference -> Gemeinde name (upper-cased, FZ 27.17 key) + RS7.
    rs = df_regiostar[["commune_id", "name", "regiostar7"]].copy()
    rs["commune_id"] = rs["commune_id"].astype(str)
    rs = rs.rename(columns={"commune_id": "ags8", "name": "gemeinde",
                            "regiostar7": "raumtyp"})
    rs["gemeinde"] = rs["gemeinde"].astype(str).str.strip().str.upper()
    homes = homes.merge(rs[["ags8", "gemeinde", "raumtyp"]], on="ags8", how="left")

    # One car-need-ordered record list per household.
    rows: list[dict] = []
    n_zero_licensed_households = 0
    n_zero_licensed_cars = 0

    # Group once; iterate households deterministically by household_id.
    for household_id, group in df_persons.groupby("household_id", sort=True):
        number_of_cars = int(group["number_of_cars"].max())
        if number_of_cars <= 0:
            continue

        home = homes[homes["household_id"] == household_id]
        if home.empty:
            raise ValueError(
                f"build_household_car_frame: no home commune for household "
                f"{household_id}"
            )
        home_row = home.iloc[0]
        economic_status = str(group["economic_status"].iloc[0])

        owners = _assign_owners(group, number_of_cars)
        if owners is None:
            # No licensed adult: assign to the oldest adult (logged fallback).
            n_zero_licensed_households += 1
            n_zero_licensed_cars += number_of_cars
            owners = _fallback_owners(group, number_of_cars)

        for owner_id in owners:
            rows.append({
                "household_id": household_id,
                "owner_id": owner_id,
                "economic_status": economic_status,
                "kreis_ags5": home_row["kreis_ags5"],
                "gemeinde": home_row["gemeinde"],
                "raumtyp": home_row["raumtyp"],
            })

    if n_zero_licensed_households:
        logger.warning(
            "[vehicles.household] %d household(s) with cars but no licensed "
            "adult (%d car(s)): assigned to the oldest adult as a fallback.",
            n_zero_licensed_households, n_zero_licensed_cars,
        )

    df_cars = pd.DataFrame.from_records(
        rows,
        columns=["household_id", "owner_id", "economic_status",
                 "kreis_ags5", "gemeinde", "raumtyp"],
    )
    return df_cars


def _assign_owners(group: pd.DataFrame, number_of_cars: int):
    """Return the owner ``person_id`` list (length ``number_of_cars``) or ``None``.

    Licensed adults are ordered highest-need-first (descending age, tie-broken by
    ascending ``person_id``). The first ``number_of_cars`` cars go one-each to the
    leading adults; any surplus is assigned round-robin over the licensed adults.
    Returns ``None`` when the household has no licensed adult (caller handles the
    fallback).
    """
    licensed = group[group["has_license"].fillna(False) & (group["age"] >= ADULT_AGE)]
    if licensed.empty:
        return None
    ordered = licensed.sort_values(
        ["age", "person_id"], ascending=[False, True])["person_id"].tolist()
    owners = [ordered[i % len(ordered)] for i in range(number_of_cars)]
    return owners


def _fallback_owners(group: pd.DataFrame, number_of_cars: int) -> list:
    """Assign every car to the oldest adult (zero-licensed-adult fallback).

    Picks the oldest adult (``age >= ADULT_AGE``); if the household has no adult
    at all, the oldest person overall is used so a car is never dropped.
    """
    adults = group[group["age"] >= ADULT_AGE]
    pool = adults if not adults.empty else group
    oldest = pool.sort_values(
        ["age", "person_id"], ascending=[False, True])["person_id"].iloc[0]
    return [oldest] * number_of_cars


def assign_vehicle_ids(df_cars: pd.DataFrame) -> pd.DataFrame:
    """Attach a unique ``vehicle_id`` and ``mode`` to each household car.

    Vehicle ids are ``"<owner_id>:car:<k>"`` with ``k`` the per-owner running
    index, so an owner with several household cars gets distinct ids while a
    single-car owner still reads cleanly. The frame is returned in input order.
    """
    df = df_cars.copy()
    df["mode"] = "car"
    per_owner_index = df.groupby("owner_id").cumcount()
    df["vehicle_id"] = (
        df["owner_id"].astype(str) + ":car:" + per_owner_index.astype(str))
    return df


def legacy_one_car_per_person(df_persons: pd.DataFrame) -> pd.DataFrame:
    """Reproduce the legacy one-car-per-person frame (OFF path reference).

    Mirrors ``synthesis/vehicles/cars/default.py`` exactly: one ``car`` vehicle
    per person, ``vehicle_id = "<person_id>:car"``, ``type_id = "default_car"``.
    Used by the OFF-equivalence test; the production OFF path uses the unchanged
    legacy stage directly.
    """
    df = df_persons[["person_id"]].copy().rename(columns={"person_id": "owner_id"})
    df["mode"] = "car"
    df["vehicle_id"] = df["owner_id"].astype(str) + ":car"
    df["type_id"] = "default_car"
    return df


# --------------------------------------------------------------------------- #
# Pure helpers for the 5 km EV grid tilt (T9b)
# --------------------------------------------------------------------------- #

def grid_ev_share_for_homes(homes_gdf, grid_df: pd.DataFrame) -> pd.Series:
    """Assign each household the EV share of the 5 km grid cell it falls in.

    Reprojects home points from EPSG:25832 to EPSG:3857, then assigns each
    point to the grid cell whose ``[minx, maxx] x [miny, maxy]`` bounds
    contain it (a spatial join via geopandas).  Returns a ``pd.Series``
    indexed by ``household_id`` with the ``ev_share`` of the matched cell.

    Households that fall outside all cells, or whose matched cell is
    ``suppressed`` or has a NaN ``ev_share``, receive ``NaN``.  The
    matched/unmatched count is logged so the fallback rate is observable
    (no-silent-fallback rule).

    Parameters
    ----------
    homes_gdf:
        GeoDataFrame with at least ``household_id`` and ``geometry``
        (EPSG:25832 points).
    grid_df:
        DataFrame with columns ``cell_id, ev_share, minx, miny, maxx, maxy,
        suppressed`` (EPSG:3857 cell bounds) as returned by
        :func:`braunschweig.data.kba.fleet_tables.load_ev_grid`.

    Returns
    -------
    pd.Series indexed by ``household_id`` with dtype ``float64``.
    NaN for unmatched/suppressed/invalid cells.
    """
    import geopandas as gpd
    from shapely.geometry import box

    # Reproject home points from EPSG:25832 to EPSG:3857 for spatial join.
    homes = homes_gdf[["household_id", "geometry"]].copy()
    homes = homes.to_crs("EPSG:3857")

    # Build grid cell polygons (box) from bounds.
    if grid_df.empty:
        logger.warning(
            "[vehicles.household] grid_ev_share_for_homes: empty grid DataFrame; "
            "all %d households will get NaN ev_share (fallback to Gemeinde tilt).",
            len(homes),
        )
        return pd.Series(np.nan, index=homes_gdf["household_id"], dtype=float,
                         name="ev_share")

    # Mark suppressed / NaN rows so we can assign NaN after the join.
    grid = grid_df.copy()
    grid["_ev_share_effective"] = grid.apply(
        lambda r: np.nan if (r["suppressed"] or not np.isfinite(r.get("ev_share", np.nan)))
        else float(r["ev_share"]),
        axis=1,
    )
    cell_geoms = [
        box(row["minx"], row["miny"], row["maxx"], row["maxy"])
        for _, row in grid.iterrows()
    ]
    gdf_cells = gpd.GeoDataFrame(
        grid[["cell_id", "_ev_share_effective"]].copy(),
        geometry=cell_geoms,
        crs="EPSG:3857",
    )

    # Spatial join: each home point -> containing cell.
    joined = gpd.sjoin(
        homes.rename(columns={"geometry": "geometry"}),
        gdf_cells,
        how="left",
        predicate="within",
    )

    # Defensive dedup: with predicate="within" a point strictly inside a cell
    # matches exactly one cell, so a well-formed tiling grid yields no duplicates
    # (the real edge case is a boundary/exterior point matching NONE, handled by
    # the left join -> NaN). Keep one row per household as a harmless safeguard
    # against any degenerate overlapping-cell input.
    joined = joined.drop_duplicates(subset=["household_id"], keep="first")

    matched = int(joined["_ev_share_effective"].notna().sum())
    total = len(homes)
    unmatched = total - matched
    (logger.warning if unmatched > total * 0.5 else logger.info)(
        "[vehicles.household] grid_ev_share_for_homes: "
        "matched %d/%d households (%.1f%%); unmatched/suppressed %d (%.1f%%) "
        "-> NaN (fallback to Gemeinde tilt).",
        matched, total, 100.0 * matched / total if total else 0.0,
        unmatched, 100.0 * unmatched / total if total else 0.0,
    )

    result = joined.set_index("household_id")["_ev_share_effective"]
    result.name = "ev_share"
    return result


def gemeinde_grid_mean(df: pd.DataFrame) -> pd.Series:
    """Household-weighted mean ``grid_ev_share`` within each commune.

    For each household in ``df``, returns the mean of ``grid_ev_share`` over
    all households sharing the same ``commune_id``, excluding NaN shares.
    When all shares in a commune are NaN the mean is NaN.

    Because every household in the same commune gets the SAME mean, the ratio
    ``grid_ev_share / gemeinde_grid_mean`` averages to ~1 within a commune
    (household-weighted), so the Gemeinde-level EV aggregate is preserved by
    the T9a tilt.

    Parameters
    ----------
    df:
        DataFrame with at least ``household_id``, ``commune_id`` and
        ``grid_ev_share`` columns.

    Returns
    -------
    pd.Series indexed by ``household_id``.
    """
    # nanmean: NaN shares are excluded (skipna=True is the pandas default).
    # Use a reset-index copy so the transform index matches the household_id
    # assignment and there are no alignment surprises with non-default indices.
    work = df[["household_id", "commune_id", "grid_ev_share"]].reset_index(drop=True)
    commune_mean = (
        work.groupby("commune_id")["grid_ev_share"]
        .transform("mean")  # default skipna=True -> NaN-excluded mean per commune
    )
    result = pd.Series(
        commune_mean.values,
        index=work["household_id"],
        name="gemeinde_grid_mean",
        dtype=float,
    )
    return result


# --------------------------------------------------------------------------- #
# synpp stage: vehicles_method == "household"
# --------------------------------------------------------------------------- #
#: Allowed values for the electric-share calibration mode (``fleet_electric_calibration``).
#: ``"kreis_mix_gemeinde_grid_bev_tilt"`` (default) adds a per-household
#: sub-communal 5 km grid tilt on top of the Gemeinde tilt; the grid CSV
#: ``kba_ev_grid.csv`` must be present.  When the CSV is absent the mode falls
#: back to Gemeinde-only (logged).  ``"kreis_mix_gemeinde_bev_tilt"`` is the
#: legacy Gemeinde-only mode (no grid CSV needed).
FLEET_ELECTRIC_CALIBRATIONS = (
    "kreis_mix_gemeinde_bev_tilt",
    "kreis_mix_gemeinde_grid_bev_tilt",
)


def configure(context):
    context.stage("synthesis.population.enriched")
    context.stage("synthesis.population.spatial.home.zones")
    context.stage("braunschweig.data.bbsr.regiostar")
    # T9b: home locations (with geometry) are needed to assign each household
    # to its 5 km EV grid cell.  The stage is always declared in configure so
    # the synpp cache key includes it; whether it is actually USED at runtime
    # depends on the ``fleet_electric_calibration`` mode and grid CSV availability.
    context.stage("synthesis.population.spatial.home.locations")
    context.config("data_path")
    context.config("random_seed")
    context.config("hbefa_segment_size_map", None)
    # Fleet model switches (Task F7). All flag-gated with the spec defaults; with
    # the German fleet disabled the stage reproduces the legacy one-car-per-person
    # default_car fleet byte-identically (OFF-equivalence).
    context.config("fleet_model_enabled", True)
    context.config("fleet_model_brands", True)
    # Additive HSN/TSN engine attributes (power kW/PS, displacement ccm, a
    # representative HSN/TSN and the dominant fuel) matched onto every vehicle by
    # brand + model family. Default True. OFF -> the five engine columns are
    # absent and the writer emits no engine attributes (byte-identical). Requires
    # fleet_model_brands (the matcher keys on brand/model); with brands off the
    # attach is skipped.
    context.config("fleet_hsn_tsn_attributes", True)
    # Task 9: end-to-end consistency_v2 flag. When True (default) the v2 path in
    # sample_fleet is active (sonstige redistribution + model-feasible powertrain
    # mask + per-Kreis electric rake). When False the legacy path is used, which
    # is byte-identical to the pre-plan output for every existing column.
    context.config("fleet_consistency_v2", True)
    # Task 4 (Plan-2): income->age tilt flag. When True (default) the v2 path
    # applies the economic_status -> age gradient (Feature B); False -> no tilt,
    # byte-identical to pre-Feature-B. Only active when consistency_v2 is also
    # True (the tilt lives inside the v2 block of sample_fleet).
    context.config("fleet_age_income_coupling", True)
    # Task B2: EV-income tilt flag. When True (default) the v2 path applies
    # f_pt(status) = clip(P(pt|status)/P(pt|all), 0.2, 5.0), pt in {bev, phev},
    # from the committed MiD 2023 powertrain-by-economic-status table -- a
    # within-Kreis redistribution toward higher-status households that leaves
    # the per-Kreis electric AGGREGATE (Task 7 rake target) unchanged. False,
    # or an absent mid2023_antrieb_by_status.csv (server-generated, not always
    # present locally), disables the tilt (byte-identical). Only active when
    # consistency_v2 is also True (the tilt lives inside the v2 block of
    # sample_fleet).
    context.config("fleet_ev_income_tilt", True)
    # Task B5: Euro-6 substage (6ab/6d-temp/6d) conditional draw flag. When
    # True (default) the v2 path refines a combustion vehicle's headline
    # "euro6" draw into one of the three real Euro-6 substages via the
    # per-Kreis / national FZ 27.4 fallback chain (see
    # fleet_sampling_de.Euro6SubstageModel). False, consistency_v2=False, or
    # absent substage CSVs (server-generated, not always present locally)
    # leave the plain "euro6" label (byte-identical).
    context.config("fleet_euro6_substage", True)
    # T9b: default is the new grid-tilt mode; falls back gracefully to Gemeinde-
    # only when kba_ev_grid.csv is absent.  The legacy Gemeinde-only mode
    # ("kreis_mix_gemeinde_bev_tilt") remains supported for explicit rollback.
    context.config("fleet_electric_calibration",
                   "kreis_mix_gemeinde_grid_bev_tilt")
    # Optional override for the KBA derived-CSV directory. Default None -> the
    # readers (braunschweig.data.kba.fleet_tables) resolve the tables under
    # ``data_path``; the key is registered so an explicit override invalidates the
    # synpp cache and is documented in one place.
    context.config("kba_fleet_paths", None)


def execute(context):
    df_persons = context.stage("synthesis.population.enriched")
    df_homes = context.stage("synthesis.population.spatial.home.zones")
    df_regiostar = context.stage("braunschweig.data.bbsr.regiostar")

    # All defaults are registered in configure(); the execute context's config()
    # takes the key alone (see tests/test_execute_context_config_contract.py).
    data_path = context.config("data_path")
    random_seed = context.config("random_seed")
    size_map = context.config("hbefa_segment_size_map")
    fleet_model_enabled = bool(context.config("fleet_model_enabled"))
    model_brands = bool(context.config("fleet_model_brands"))
    hsn_tsn_attributes = bool(context.config("fleet_hsn_tsn_attributes"))
    consistency_v2 = bool(context.config("fleet_consistency_v2"))
    age_income_coupling = bool(context.config("fleet_age_income_coupling"))
    ev_income_tilt = bool(context.config("fleet_ev_income_tilt"))
    euro6_substage = bool(context.config("fleet_euro6_substage"))
    electric_calibration = context.config("fleet_electric_calibration")
    # Optional explicit KBA derived-CSV directory; default None -> use data_path.
    kba_fleet_paths = context.config("kba_fleet_paths")
    fleet_data_path = kba_fleet_paths if kba_fleet_paths is not None else data_path

    # OFF switch: the German fleet model disabled -> emit the legacy
    # one-car-per-person default_car fleet (byte-identical to
    # synthesis.vehicles.cars.default), so a single config flag turns the whole
    # fleet feature off even while vehicles_method stays "household".
    if not fleet_model_enabled:
        logger.info(
            "[vehicles.household] fleet_model_enabled=False -> legacy "
            "one-car-per-person default_car fleet (OFF).")
        return _legacy_default_fleet(df_persons)

    if electric_calibration not in FLEET_ELECTRIC_CALIBRATIONS:
        raise ValueError(
            f"unknown fleet_electric_calibration '{electric_calibration}' "
            f"(supported: {FLEET_ELECTRIC_CALIBRATIONS})"
        )

    df_cars = build_household_car_frame(df_persons, df_homes, df_regiostar)
    df_cars = assign_vehicle_ids(df_cars)

    # T9b: grid EV tilt -- attach per-household grid_ev_share + gemeinde_grid_mean
    # columns when the grid-tilt mode is requested and the grid CSV is available.
    # On ANY of {grid CSV absent, home.locations unavailable, empty join}: do NOT
    # add the columns and fall back to Gemeinde-only tilt (no-silent-fallback rule).
    if electric_calibration == "kreis_mix_gemeinde_grid_bev_tilt":
        _grid_cols_added = False
        try:
            from braunschweig.data.kba import fleet_tables as ft
            grid_df = ft.load_ev_grid(fleet_data_path)
            # Home locations stage provides per-household geometry (EPSG:25832)
            # + commune_id.
            df_home_locs = context.stage(
                "synthesis.population.spatial.home.locations")
            # The home locations frame has household_id + commune_id + geometry.
            # Build the per-household grid ev_share via spatial join.
            ev_series = grid_ev_share_for_homes(df_home_locs, grid_df)
            # Attach grid_ev_share to df_cars (join on household_id).
            df_cars = df_cars.join(
                ev_series.rename("grid_ev_share"), on="household_id", how="left")
            # Attach commune_id for the Gemeinde mean computation.
            commune_map = (
                df_home_locs[["household_id", "commune_id"]]
                .drop_duplicates("household_id")
                .set_index("household_id")["commune_id"]
            )
            df_cars["_commune_id_grid"] = df_cars["household_id"].map(commune_map)
            # Compute per-Gemeinde household-weighted mean of grid_ev_share.
            # We need the mean indexed by household_id over the car frame (one row
            # per car, multiple cars per household).  Build a household-level
            # helper frame first so each household contributes exactly once to
            # the mean, then broadcast back.
            hh_grid = (
                df_cars[["household_id", "_commune_id_grid", "grid_ev_share"]]
                .drop_duplicates("household_id")
                .rename(columns={"_commune_id_grid": "commune_id"})
            )
            hh_grid_mean = gemeinde_grid_mean(hh_grid)
            df_cars = df_cars.join(
                hh_grid_mean.rename("gemeinde_grid_mean"),
                on="household_id", how="left",
            )
            df_cars = df_cars.drop(columns=["_commune_id_grid"])
            _grid_cols_added = True
            logger.info(
                "[vehicles.household] fleet_electric_calibration=%s: "
                "grid tilt columns attached (grid_ev_share + gemeinde_grid_mean).",
                electric_calibration,
            )
        except FileNotFoundError:
            logger.info(
                "[vehicles.household] fleet_electric_calibration=%s: "
                "kba_ev_grid.csv absent -> falling back to Gemeinde-only tilt "
                "(no grid columns on df_cars).",
                electric_calibration,
            )
        except Exception as exc:  # noqa: BLE001 -- log (with traceback) and degrade gracefully
            # exc_info=True captures the full traceback so an UNEXPECTED failure
            # here (e.g. a wrong column name, a geopandas/shapely import error, a
            # CRS mismatch) is diagnosable from the run log instead of being
            # silently masked as a graceful fallback (no-silent-fallback rule).
            logger.warning(
                "[vehicles.household] fleet_electric_calibration=%s: "
                "grid tilt setup failed (%s) -> falling back to Gemeinde-only "
                "tilt (no grid columns on df_cars).",
                electric_calibration, exc, exc_info=True,
            )
        if not _grid_cols_added:
            logger.info(
                "[vehicles.household] grid tilt not active for this run; "
                "Gemeinde-only tilt will be used."
            )

    logger.info(
        "[vehicles.household] %d vehicles for %d households (mean %.2f cars/HH "
        "over car-owning households)",
        len(df_cars), df_cars["household_id"].nunique(),
        len(df_cars) / max(df_cars["household_id"].nunique(), 1),
    )

    # Attach the full KBA/HBEFA spec to every household car (F4 chain).
    # Task 3: consistency_v2=True returns a 3-tuple (df_spec, df_types, summary);
    # the legacy path (consistency_v2=False) returns the original 2-tuple.
    _fleet_result = fleet.sample_fleet(
        df_cars, fleet_data_path, random_seed=random_seed, size_map=size_map,
        model_brands=model_brands, consistency_v2=consistency_v2,
        age_income_coupling=age_income_coupling,
        ev_income_tilt=ev_income_tilt,
        euro6_substage=euro6_substage,
        population_label="residents")
    if len(_fleet_result) == 3:
        df_spec, df_vehicle_types, _ = _fleet_result
    else:
        df_spec, df_vehicle_types = _fleet_result
    # The grid-tilt inputs (grid_ev_share, gemeinde_grid_mean) were consumed
    # during the powertrain draw inside sample_fleet; they are NOT vehicle
    # attributes. Drop them here so they do not leak into df_vehicles /
    # vehicles.csv (and do not receive meaningless median fills on the routing
    # default_car rows in _add_default_cars_for_non_owners). ``errors="ignore"``
    # keeps this a no-op when the grid tilt was inactive (columns absent).
    df_spec = df_spec.drop(columns=["grid_ev_share", "gemeinde_grid_mean"], errors="ignore")

    # Additive HSN/TSN engine attributes (power/displacement/fuel + a
    # representative HSN/TSN), matched by brand + model family. Requires the
    # brand/model columns; skipped (with a logged note) when brands are off so
    # the matcher never keys on empty brand/model strings. The match-tier rates
    # are logged by attach_hsn_tsn (no-silent-fallback rule).
    if hsn_tsn_attributes:
        if not model_brands:
            logger.info(
                "[vehicles.household] fleet_hsn_tsn_attributes=True but "
                "fleet_model_brands=False -> brand/model are empty, skipping the "
                "HSN/TSN attach (no engine attributes written).")
        else:
            # Couple keep_tier to whether the consistency-v2 provenance ran.
            # sample_fleet adds ``brand_source`` ONLY in the v2 path, so
            # ``brand_source in df_spec.columns`` is True iff consistency_v2 was
            # active.  The three provenance columns (brand_source,
            # powertrain_feasibility, hsn_tsn_match_tier) must travel together:
            # present on v2, absent on the OFF path so df_vehicles stays
            # byte-identical for existing columns when consistency_v2=False.
            keep_tier = "brand_source" in df_spec.columns
            df_spec = hsn_tsn.attach_hsn_tsn(
                df_spec, data_path=fleet_data_path,
                random_seed=random_seed, keep_tier=keep_tier)

    # The current vehicles writer consumes critair/technology/age/euro per
    # vehicle (synthesis.vehicles.vehicles + matsim.scenario.vehicles). The
    # differentiated writer is Task F6; until then, expose writer-compatible
    # columns derived from the spec so the household frame can be written and
    # concatenated with the passenger frame.
    df_vehicles = _attach_writer_columns(df_spec)

    # Restore eqasim-core per-person car coverage: non-owner members of car-owning
    # households (car_availability "some"/"all") get a routing default_car so any
    # in-loop car-driver choice is routable (see _add_default_cars_for_non_owners).
    df_vehicle_types, df_vehicles = _add_default_cars_for_non_owners(
        df_vehicle_types, df_vehicles, df_persons)

    return df_vehicle_types, df_vehicles


def _legacy_default_fleet(df_persons: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Reproduce the legacy ``synthesis.vehicles.cars.default`` stage output.

    Returns the exact ``(df_vehicle_types, df_vehicles)`` of the upstream eqasim
    default fleet (one ``default_car`` per person, single ``default_car`` type with
    HBEFA "average" attributes, the four legacy per-vehicle attributes), so that
    ``fleet_model_enabled=False`` yields a byte-identical legacy vehicles file even
    while ``vehicles_method`` stays ``"household"``. Mirrors
    ``synthesis/vehicles/cars/default.py`` field-for-field.
    """
    df_vehicle_types = pd.DataFrame.from_records([{
        "type_id": "default_car", "nb_seats": 4, "length": 5.0, "width": 1.0,
        "pce": 1.0, "mode": "car", "hbefa_cat": "PASSENGER_CAR",
        "hbefa_tech": "average", "hbefa_size": "average", "hbefa_emission": "average",
    }])

    df_vehicles = df_persons[["person_id"]].copy()
    df_vehicles = df_vehicles.rename(columns={"person_id": "owner_id"})
    df_vehicles["mode"] = "car"
    df_vehicles["vehicle_id"] = df_vehicles["owner_id"].astype(str) + ":car"
    df_vehicles["type_id"] = "default_car"
    df_vehicles["critair"] = "Crit'air 1"
    df_vehicles["technology"] = "Gazole"
    df_vehicles["age"] = 0
    df_vehicles["euro"] = 6
    return df_vehicle_types, df_vehicles


def _add_default_cars_for_non_owners(df_vehicle_types, df_vehicles, df_persons):
    """Ensure EVERY person owns a ``car`` vehicle by giving non-owners a default_car.

    eqasim core (``RunInsertVehicles`` / ``synthesis.vehicles.cars.default``) gives every
    person a car vehicle; ``car_availability`` gates the in-loop mode CHOICE, not the
    vehicle's existence. The household fleet assigns a real car only to each household
    car's owner, so non-owner members (``car_availability`` "some"/"all") would own no car
    vehicle -- and MATSim aborts routing ("Could not retrieve vehicle id from person ...
    for mode: car") when the in-loop discrete mode choice assigns them car-driver. This
    restores eqasim-core coverage: each person without a fleet car gets a routing
    ``default_car`` (id "<person_id>:car"). The real HBEFA-typed fleet is untouched, so the
    fleet-composition KPIs (which read the HBEFA types) are unaffected; these default cars
    are exactly what the eqasim base ``synthesis.vehicles.cars.default`` stage emits per
    person. The fallback rate is logged (CLAUDE.md no-silent-fallback).
    """
    owner_ids = set(df_vehicles["owner_id"])
    non_owners = df_persons[~df_persons["person_id"].isin(owner_ids)]
    n_owners = len(owner_ids)
    n_default = len(non_owners)
    logger.info(
        "[vehicles.household] car-vehicle coverage: %d owner(s) with a real fleet car, "
        "%d non-owner(s) get a routing default_car (%.1f%% fleet / %.1f%% default).",
        n_owners, n_default,
        100.0 * n_owners / max(n_owners + n_default, 1),
        100.0 * n_default / max(n_owners + n_default, 1),
    )
    if n_default == 0:
        return df_vehicle_types, df_vehicles
    default_types, default_vehicles = _legacy_default_fleet(non_owners)
    # The routing default_cars carry only the legacy writer columns; the German-fleet
    # / engine columns (brand, model, powertrain, engine_power_kw, ...) would otherwise
    # be NaN after the concat. Fill every such column with an explicit, data-backed value
    # so the vehicles frame has NO NaN gaps (CLAUDE.md no-silent-fallback): numeric engine
    # attributes use the typed-fleet MEDIAN (data-derived, not an invented constant);
    # categorical fleet columns are left EMPTY ("") -- the same "no fleet identity"
    # convention used when fleet_model_brands is off -- rather than a fabricated brand, so
    # these placeholder rows stay distinguishable (their ``type_id`` is ``default_car``)
    # without polluting the brand mix. On the legacy path (fleet_model_enabled=False) the
    # two frames already share columns, so ``extra_cols`` is empty and the concat stays
    # byte-identical.
    extra_cols = [c for c in df_vehicles.columns if c not in default_vehicles.columns]
    for col in extra_cols:
        series = df_vehicles[col]
        if pd.api.types.is_numeric_dtype(series):
            default_vehicles[col] = float(series.median()) if series.notna().any() else 0.0
        else:
            default_vehicles[col] = ""
    if extra_cols:
        logger.info(
            "[vehicles.household] filled %d placeholder column(s) on %d routing "
            "default_car(s) (numeric=typed-fleet median, categorical=empty): %s",
            len(extra_cols), n_default, ", ".join(sorted(extra_cols)),
        )
    df_vehicles = pd.concat([df_vehicles, default_vehicles], ignore_index=True)
    df_vehicle_types = pd.concat([df_vehicle_types, default_types], ignore_index=True)
    df_vehicle_types = df_vehicle_types.drop_duplicates(subset="type_id").reset_index(drop=True)
    return df_vehicle_types, df_vehicles


def _attach_writer_columns(df_spec: pd.DataFrame) -> pd.DataFrame:
    """Add legacy writer-compatible columns derived from the KBA spec.

    The legacy MATSim vehicles writer needs ``critair`` and ``technology`` per
    vehicle. ``technology`` is the powertrain label and ``critair`` is left empty
    (the German fleet is described by HBEFA attributes on the VehicleType, not by
    the French Crit'air scheme). ``age`` and ``euro`` already come from the spec.
    """
    df = df_spec.copy()
    df["technology"] = df["powertrain"]
    df["critair"] = ""
    df["euro"] = df["euro_class"]
    return df
