"""Enriched population stage - Braunschweig overrides + Bavaria base.

This module is the merged successor of:
- ``bavaria/synthesis/population/enriched.py`` (Origin: eqasim-bavaria @ b20fbe6).
- ``braunschweig/synthesis/population/enriched.py`` (BS-specific MiD-2023
  vehicle counts, INKAR income scaling, BS-resident flag).

Phase 2.8 of the eqasim-bs refactor merged both into a single module so the
BS pipeline no longer delegates through ``bavaria.synthesis.population.enriched``.

Per Decision D-5 the merge is behaviour-preserving: the wrapper-around-wrapper
structure is intentionally preserved (BUG-001 stays untouched; cleanup is
deferred to a separate post-refactor pass).

Pipeline order: ``synthesis.population.enriched`` (eqasim core) -> base
augmentation (car/bike/PT IPF, household size + income sampling) -> BS overlay
(MiD-2023 vehicle counts, INKAR income, BS resident flag).

Package layout (issue #267 split; formerly one ~2900-line module, itself the
rename of the legacy ``enriched.py``): this ``__init__`` is the synpp stage
(``configure``/``execute``/``validate``) and re-exports every submodule name,
so external imports of the stage module path keep working unchanged. The
submodules:

    availability        PT-subscription conditioning (A6) and consistent
                        car_availability (A5)
    economic_status    5-class MiD economic status (income-class-derived and
                        MiD hhtype x region Bayes-derived variants)
    housing_tenure      housing_tenure completeness attribute sampled from MiD
                        P(tenure | income_bracket, raumtyp) (Bayes-inverted)
    income_distribution MiD income-bracket distribution draw (with the INKAR
                        Kreis fine tilt) and the legacy INKAR class-midpoint
                        income scaling
    vehicle_ownership  MiD H7/H12.3 vehicle-count sampling, income-aware
                        number_of_cars, and the shared Kreis-ARS / binarisation
                        helpers

``validate()`` hashes all submodule sources into the synpp validation token
because ``get_stage_hash`` only covers this file -- a helper-only change
devalidates the cached stage output exactly like an edit here.
"""

import hashlib
import inspect

import synthesis.population.enriched as delegate

import pandas as pd
import geopandas as gpd
import numpy as np

from braunschweig.data.mid.reference_tables import load_class_midpoint_eur

# ---------------------------------------------------------------------------
# Package submodules (extracted stage sections). Every name is re-exported
# here so external consumers (calibration scripts, tests) keep importing from
# the stage module path unchanged. Each submodule MUST also be listed in
# _HELPER_MODULES below so its source participates in the synpp cache-
# validation token.
# ---------------------------------------------------------------------------

from . import availability, economic_status, housing_tenure, income_distribution, vehicle_ownership
from .availability import (  # noqa: F401  (re-exports)
    CAR_AVAILABILITY_CATEGORIES,
    CAR_AVAILABILITY_RAKE_FALLBACK_WARN_RATE,
    _PT_CAR_MARGIN_RAKE_ITERATIONS,
    _apply_car_availability_pt_margin,
    _condition_pt_subscription_probs,
    _derive_car_availability_consistent,
)
from .economic_status import (  # noqa: F401  (re-exports)
    ECONOMIC_STATUS_BY_INCOME_CLASS,
    ECONOMIC_STATUS_CATEGORIES,
    INCOME_CLASS_BY_ECONOMIC_STATUS,
    STATUS_HHTYPE_FALLBACK_WARN_RATE,
    _build_economic_status_by_income_class,
    _build_income_class_by_status,
    _derive_economic_status,
    _derive_economic_status_from_hhtype,
)
from .housing_tenure import (  # noqa: F401  (re-exports)
    HOUSING_TENURE_FALLBACK_WARN_RATE,
    _apply_housing_tenure,
    _eur_to_bracket_index,
)
from .income_distribution import (  # noqa: F401  (re-exports)
    INCOME_DISTRIBUTION_FALLBACK_WARN_RATE,
    INCOME_MIDPOINT_FALLBACK_EUR,
    INCOME_MIDPOINT_FALLBACK_WARN_RATE,
    INCOME_MIN_EUR,
    INCOME_OPEN_TOP_EXP_MEAN_EUR_FRACTION,
    INCOME_OPEN_TOP_MAX_EUR,
    _apply_distribution_income,
    _apply_inkar_income_scale,
    _income_class_from_eur,
)
from .vehicle_ownership import (  # noqa: F401  (re-exports)
    CARS_INCOME_AWARE_FALLBACK_WARN_RATE,
    INSIDE_FLAG_TO_ARS5,
    KREIS_SHARE_FALLBACK_WARN_RATE,
    _assign_to_column_targets,
    _binarise_availability,
    _derive_kreis_ars5,
    _largest_remainder,
    _sample_cars_income_aware,
    _sample_counts,
    _sample_vehicle_counts,
    load_kreis_share_table,
    rake_2d,
)


# --- Inherited from eqasim-bavaria -----------------------------------------
# Helper: map IPF hh_size values onto the bins present in df_income.

def _compute_zone_membership(df_homes, df_zones):
    """Add ``inside_<zone>`` boolean columns to ``df_homes`` (FIX 3.8).

    Replaces the per-zone loop of ``gpd.sjoin(df_homes, df_zones[zone])`` (one
    spatial join per zone) with a SINGLE ``gpd.sjoin(df_homes, df_zones,
    predicate="within")`` followed by a membership reduction. A home counts as
    inside a zone iff its geometry is ``within`` that zone's polygon -- exactly
    the predicate of the legacy per-zone join.

    Output-identity: the legacy code derived membership purely from
    ``df_homes["household_id"].isin(df_query["household_id"])``, i.e. a home is
    "inside zone Z" iff its ``household_id`` appears at least once in the join
    against zone Z. A home whose geometry lies within several zones (boundary
    tie / overlapping polygons) is therefore counted inside EVERY matching zone,
    independently per zone. The single join reproduces this: every (home, zone)
    match becomes one row; reducing the join to the set of distinct
    (household_id, zone) pairs and testing membership per zone yields the same
    per-zone boolean, regardless of duplicate matches (a household_id that
    matches a zone in one or several rows is "inside" exactly when it appears at
    least once). ``df_zones["name"].unique()`` is iterated in the same order so
    the column order is preserved.

    Note: ``inside_external`` (= not covered by any zone) is intentionally NOT
    added here; the caller derives it from the OR of the per-zone columns, as in
    the legacy code.
    """
    zone_names = df_zones["name"].unique()

    # Single spatial join: one row per (home, matching zone). ``predicate=
    # "within"`` matches the legacy per-zone join predicate exactly.
    df_query = gpd.sjoin(df_homes, df_zones[["name", "geometry"]], predicate="within")

    # Distinct (household_id, zone-name) pairs. The legacy ``isin`` membership
    # test is insensitive to duplicate matches, so deduping here is safe and
    # makes the per-zone membership sets identical to the per-zone joins.
    matched = df_query[["household_id", "name"]].drop_duplicates()

    household_ids = df_homes["household_id"]
    for zone in zone_names:
        ids_in_zone = matched.loc[matched["name"] == zone, "household_id"]
        df_homes["inside_{}".format(zone)] = household_ids.isin(ids_in_zone)

    return df_homes


def _build_income_size_map(income_bins):
    """Map IPF hh_size values onto the bins present in df_income.

    Returns (mapping_dict, scheme_name). ``scheme_name`` is "6-bin" if the
    reference table separates 5 from 6+, else "5-bin" (collapses 5/6/5+/6+
    onto "5+"). Raises ValueError if neither scheme is recognised.
    """
    bins = set(income_bins)
    if {"1", "2", "3", "4", "5", "6+"}.issubset(bins):
        return (
            {"1": "1", "2": "2", "3": "3", "4": "4",
             "5": "5", "6": "6+", "5+": "5", "6+": "6+"},
            "6-bin",
        )
    if {"1", "2", "3", "4", "5+"}.issubset(bins):
        return (
            {"1": "1", "2": "2", "3": "3", "4": "4",
             "5": "5+", "6": "5+", "5+": "5+", "6+": "5+"},
            "5-bin",
        )
    raise ValueError(
        f"household_income reference has unrecognised hh_size bins: {sorted(bins)}"
    )


def _income_bin_for_size(size_str, income_size_map, scheme):
    """Resolve an IPF household_size value onto an income reference bin.

    ``income_size_map`` covers the explicit sizes of the reference scheme (1..6
    for "6-bin", 1..5 for "5-bin"). The IPF, however, forms real households of
    any size (7, 8, ... up to ~11). The income reference's largest category is
    open-ended ("6+" = six *or more*, "5+" = five or more), so a household
    larger than that category belongs to it: any unmapped numeric size collapses
    onto the scheme's top bin. Non-numeric, non-mapped values are returned
    unchanged so the caller's downstream validation can surface a real mismatch.
    """
    if size_str in income_size_map:
        return income_size_map[size_str]
    if size_str.isdigit():
        return "6+" if scheme == "6-bin" else "5+"
    return size_str


def _configure_base(context):
    """Inherited configure() from bavaria.synthesis.population.enriched."""
    delegate.configure(context)

    context.stage("synthesis.population.spatial.home.locations")

    context.stage("braunschweig.data.mid.data")
    context.stage("braunschweig.data.mid.zones")

    context.stage("braunschweig.data.census.household_size")
    context.stage("braunschweig.data.census.household_income")

    context.config("random_seed")
    context.config("data_path")

    context.config("braunschweig.minimum_age.car_availability", 0)
    context.config("braunschweig.minimum_age.bicycle_availability", 0)
    context.config("braunschweig.minimum_age.pt_subscription", 0)

    context.config("braunschweig.minimum_age.one_person_household", 16)
    context.config("braunschweig.ipf.use_household_size_margin", False)

    # A5: consistent car_availability (causal IPF chain). Default ON. OFF -> the
    # legacy free P19 IPF + all/none binarisation (byte-identical). When ON,
    # car_availability is derived conditionally from the MiD-P17.1 licence and
    # the MiD-H7 household car count and then raked to the P19 marginal; this
    # requires licence + number_of_cars to be computed BEFORE car_availability
    # (the A-REORDER), which the stage now always does (the vehicle-count
    # sampling moved earlier is output-identical with the flag OFF).
    context.config("consistent_car_availability", True)

    # A6: condition pt_subscription on student / employment status (+ optional
    # carless<->PT car-availability margin). Default ON. OFF -> the exact legacy
    # 3-margin {Kreis,sex,age} P24.1 IPF (byte-identical). When ON, the combined
    # Job-/Semesterticket category is zeroed for persons who are neither employed
    # nor studying and the per-person vector re-normalised before sampling; this
    # requires employed/studies (attributed stage) computed before PT (already the
    # case in the causal IPF order). The car-availability margin only activates if
    # the MiD P24.1 x Pkw-Verfuegbarkeit cross-tab CSV is present (documented
    # logged fallback otherwise).
    context.config("pt_subscription_conditioned", True)

    # Economic status from MiD household-type x region (Bayes). Default ON.
    # OFF -> exact legacy income-class-derived status (commit c65399d), so the
    # pipeline is byte-identical. The RegioStaR-7 stage (raumtyp tilt) is only a
    # dependency when the feature is on, so the OFF path keeps the legacy
    # dependency graph.
    if context.config("status_from_hhtype", True):
        context.stage("braunschweig.data.bbsr.regiostar")

    # Income/household-type-aware household car count (number_of_cars). Default
    # ON. OFF -> the exact legacy per-Kreis MiD-H7 draw (byte-identical). When
    # ON, number_of_cars is drawn per household from the MiD-coupled pmf
    # P(num_cars | hhtype, status, raumtyp) and then raked back to the per-Kreis
    # MiD-H7 marginal, so each Kreis's 0/1/2/3+ totals stay exactly the H7
    # control -- only the WITHIN-Kreis allocation by income/hhtype changes. The
    # draw needs economic_status + Haushaltstyp + the home RegioStaR-7 raumtyp,
    # so it depends on the regiostar stage and on commune_id being carried (both
    # already required by status_from_hhtype; ensured here too for the OFF-status
    # / ON-cars combination).
    if context.config("cars_income_aware", True):
        context.stage("braunschweig.data.bbsr.regiostar")

    # household_income_eur from the real MiD net-income distribution (size x
    # region), rank-aligned to economic_status. Default ON. OFF -> the legacy
    # class-midpoint x INKAR-scale path (byte-identical household_income_eur).
    # When ON the EUR value is drawn from P(bracket | hh_size, raumtyp) (MiD,
    # NDS base + raumtyp tilt) and the regiostar stage is required to resolve the
    # home raumtyp. The actual EUR draw happens in the OUTER execute() (after the
    # INKAR stage is available); the dependency is registered here so the OFF
    # path keeps the legacy dependency graph.
    if context.config("income_eur_from_distribution", True):
        context.stage("braunschweig.data.bbsr.regiostar")

    # housing_tenure completeness attribute (synthesise_housing_tenure). Default
    # ON. Per household a tenure in {rent, own, other} is sampled from
    # P(tenure | income_bracket, raumtyp) (MiD income x Wohnen, NDS base + raumtyp
    # tilt, Bayes-inverted). This is a COMPLETENESS attribute: written to the
    # MATSim population (attribute ``housingTenure``) but NOT consumed by the
    # simulation, like the HSN/TSN vehicle engine attributes. OFF -> the attribute
    # is absent and the output schema is byte-identical. The draw needs the home
    # raumtyp, so it depends on the regiostar stage (already a dependency when the
    # distribution income is on; ensured here too for the income-OFF combination).
    if context.config("synthesise_housing_tenure", True):
        context.stage("braunschweig.data.bbsr.regiostar")


def _execute_base(context):
    """Inherited execute() from bavaria.synthesis.population.enriched.

    Overrides car availability, bike availability and transit subscription
    based on MiD data, then samples household_size and household_income from
    the German census/MiD reference tables.
    """
    df_persons = delegate.execute(context)

    # ``commune_id`` is carried alongside the geometry so the MiD
    # household-type x region economic-status derivation (status_from_hhtype)
    # can resolve each home's RegioStaR-7 raumtyp. It is dropped again before
    # returning so the output schema is unchanged when the flag is off.
    _home_cols = ["household_id", "geometry"]
    _df_home_src = context.stage("synthesis.population.spatial.home.locations")
    _needs_commune = (
        context.config("status_from_hhtype")
        or context.config("cars_income_aware")
        or context.config("income_eur_from_distribution")
        or context.config("synthesise_housing_tenure")
    )
    if _needs_commune and "commune_id" in _df_home_src.columns:
        _home_cols.append("commune_id")
    df_homes = _df_home_src[_home_cols].copy()

    df_zones = context.stage("braunschweig.data.mid.zones")
    mid = context.stage("braunschweig.data.mid.data")

    # Per-home zone membership (FIX 3.8): a single spatial join produces the
    # same ``inside_<zone>`` boolean columns as the former per-zone sjoin loop.
    df_homes = _compute_zone_membership(df_homes, df_zones)

    f_covered = np.zeros(len(df_homes), dtype=bool)
    for zone in df_zones["name"].unique():
        f_covered |= df_homes["inside_{}".format(zone)]

    df_homes["inside_external"] = ~f_covered

    df_persons = gpd.GeoDataFrame(
        pd.merge(df_persons, df_homes, on="household_id"),
        crs=df_homes.crs,
    )

    iterations = 1000

    # CAR AVAILABILITY
    df_persons["car_availability"] = 1.0
    # Copy the cached constraint list before appending: ``mid`` is the cached
    # ``braunschweig.data.mid.data`` stage object, so appending to the original
    # list would mutate the cache in place (and on a re-run within the same
    # process the extra age constraint would be appended again). ``list(...)``
    # takes a shallow copy so the cached stage object is never mutated.
    constraints = list(mid["car_availability_constraints"])
    constraints.append({
        "age": (-np.inf, context.config("braunschweig.minimum_age.car_availability") - 1),
        "target": 0.0,
    })
    filters = []
    targets = []
    for constraint in constraints:
        f = np.ones((len(df_persons),), dtype=bool)
        if "zone" in constraint:
            f &= df_persons["inside_{}".format(constraint["zone"])]
        if "sex" in constraint:
            f &= df_persons["sex"] == constraint["sex"]
        if "age" in constraint:
            f &= df_persons["age"].between(*constraint["age"])
        targets.append(constraint["target"] * np.count_nonzero(f))
        filters.append(f)
    for iteration in context.progress(range(iterations), label="imputing car availability"):
        factors = []
        for f, target in zip(filters, targets):
            current = df_persons.loc[f, "car_availability"].sum()
            factor = target / current if current > 0 else 1.0
            df_persons.loc[f, "car_availability"] *= factor
            factors.append(factor)
    print("Factors", "min:", min(factors), "max:", max(factors), "mean:", np.mean(factors))
    print(df_persons["car_availability"].min(), df_persons["car_availability"].max())

    # BIKE AVAILABILITY
    df_persons["bicycle_availability"] = 1.0
    # Copy the cached constraint list before appending (see car-availability
    # block above): never mutate the cached ``braunschweig.data.mid.data``
    # stage object.
    constraints = list(mid["bicycle_availability_constraints"])
    constraints.append({
        "age": (-np.inf, context.config("braunschweig.minimum_age.bicycle_availability") - 1),
        "target": 0.0,
    })
    filters = []
    targets = []
    for constraint in constraints:
        f = np.ones((len(df_persons),), dtype=bool)
        if "zone" in constraint:
            if constraint["zone"].startswith("!"):
                f &= ~df_persons["inside_{}".format(constraint["zone"][1:])]
            else:
                f &= df_persons["inside_{}".format(constraint["zone"])]
        if "sex" in constraint:
            f &= df_persons["sex"] == constraint["sex"]
        if "age" in constraint:
            f &= df_persons["age"].between(*constraint["age"])
        targets.append(constraint["target"] * np.count_nonzero(f))
        filters.append(f)
    for iteration in context.progress(range(iterations), label="imputing bike availability"):
        factors = []
        for f, target in zip(filters, targets):
            current = df_persons.loc[f, "bicycle_availability"].sum()
            factor = target / current if current > 0 else 1.0
            df_persons.loc[f, "bicycle_availability"] *= factor
            factors.append(factor)
    print("Factors", "min:", min(factors), "max:", max(factors), "mean:", np.mean(factors))

    # DRIVING LICENCE (categorical, MiD 2023 P17.1).
    #
    # Three-margin IPF (raking) on the 4-way contingency table
    #   Xl[kreis, sex, age_bin, license_category]
    # with target marginals from MiD P17.1 (per-Kreis page 87 + sex/age
    # margins also page 87, Tabelle A).  Mirrors the PT-subscription block
    # below but for the {ja, nein, keine_angabe} licence categories.
    #
    # The boolean ``has_license`` (later renamed to ``has_driving_license``
    # by the eqasim output writer) is then derived as
    #   has_license = pt_subscription_type ∈ LICENSE_TRUE  (= {"ja"})
    # and overwrites the HTS-matched value coming from
    # ``synthesis.population.enriched``.  ``keine_angabe`` is conservatively
    # mapped to ``False`` (see ``LICENSE_TRUE``); persons below
    # ``LICENSE_MIN_AGE`` are forced to ``"nein"`` deterministically.
    from braunschweig.data.mid.reference_tables import (
        load_license_breakdown,
        load_license_margins,
        LICENSE_CATEGORIES,
        LICENSE_TRUE,
        LICENSE_MIN_AGE,
    )
    from braunschweig.data.mid.zones import ZONE_NAMES as _LIC_ZONE_NAMES

    lic_data_path = context.config("data_path")
    lic_by_kreis, lic_region = load_license_breakdown(lic_data_path)
    lic_by_sex, lic_by_age = load_license_margins(lic_data_path)
    lic_name_to_ars5 = {v: k for k, v in _LIC_ZONE_NAMES.items()}

    n_persons = len(df_persons)
    n_lic_cats = len(LICENSE_CATEGORIES)
    lic_categories_arr = np.asarray(LICENSE_CATEGORIES)
    idx_nein = LICENSE_CATEGORIES.index("nein")

    # Map persons -> (kreis_idx, sex_idx, age_idx).
    lic_ars5_list = list(lic_by_kreis.keys())
    lic_ars5_to_idx = {ars: i for i, ars in enumerate(lic_ars5_list)}
    lic_person_kreis = np.full(n_persons, -1, dtype=np.int64)
    for zone_name, ars5 in lic_name_to_ars5.items():
        col = "inside_{}".format(zone_name)
        if col not in df_persons.columns or ars5 not in lic_ars5_to_idx:
            continue
        f_zone = df_persons[col].to_numpy()
        if f_zone.any():
            lic_person_kreis[f_zone] = lic_ars5_to_idx[ars5]

    lic_sex_arr = df_persons["sex"].astype(str).to_numpy()
    lic_person_sex = np.where(lic_sex_arr == "male", 0,
                              np.where(lic_sex_arr == "female", 1, -1))
    lic_age_arr = df_persons["age"].to_numpy()
    lic_person_age = np.full(n_persons, -1, dtype=np.int64)
    for ai, (lo, hi, _vec) in enumerate(lic_by_age):
        f_band = (lic_age_arr >= lo) & (lic_age_arr <= hi)
        if f_band.any():
            lic_person_age[f_band] = ai

    # Eligible = has Kreis, sex, age band, AND age >= legal minimum.
    lic_eligible = (
        (lic_person_kreis >= 0)
        & (lic_person_sex >= 0)
        & (lic_person_age >= 0)
        & (lic_age_arr >= LICENSE_MIN_AGE)
    )
    n_lic_kreise = len(lic_ars5_list)
    n_lic_sex = 2
    n_lic_ages = len(lic_by_age)

    # Build target matrices and the count table T[k, s, a].
    Mlic_K = np.zeros((n_lic_kreise, n_lic_cats))
    for ars, vec in lic_by_kreis.items():
        Mlic_K[lic_ars5_to_idx[ars], :] = vec
    Mlic_S = np.zeros((n_lic_sex, n_lic_cats))
    for sex, vec in lic_by_sex.items():
        si = 0 if sex == "male" else 1
        Mlic_S[si, :] = vec
    Mlic_A = np.zeros((n_lic_ages, n_lic_cats))
    for ai, (_lo, _hi, vec) in enumerate(lic_by_age):
        Mlic_A[ai, :] = vec

    Tlic = np.zeros((n_lic_kreise, n_lic_sex, n_lic_ages), dtype=float)
    if lic_eligible.any():
        flat = (
            lic_person_kreis[lic_eligible] * (n_lic_sex * n_lic_ages)
            + lic_person_sex[lic_eligible] * n_lic_ages
            + lic_person_age[lic_eligible]
        )
        counts = np.bincount(flat, minlength=n_lic_kreise * n_lic_sex * n_lic_ages)
        Tlic = counts.reshape(
            (n_lic_kreise, n_lic_sex, n_lic_ages)
        ).astype(float)

    Tlic_K = Tlic.sum(axis=(1, 2))
    Tlic_S = Tlic.sum(axis=(0, 2))
    Tlic_A = Tlic.sum(axis=(0, 1))

    target_lic_kc = Mlic_K * Tlic_K[:, None]
    target_lic_sc = Mlic_S * Tlic_S[:, None]
    target_lic_ac = Mlic_A * Tlic_A[:, None]

    Xlic = np.broadcast_to(
        Tlic[..., None] / n_lic_cats,
        (n_lic_kreise, n_lic_sex, n_lic_ages, n_lic_cats),
    ).copy()

    lic_iterations = 200
    lic_eps = 1e-9
    for _ in range(lic_iterations):
        cur = Xlic.sum(axis=(1, 2))
        Xlic *= np.where(cur > lic_eps, target_lic_kc / np.maximum(cur, lic_eps), 1.0)[:, None, None, :]
        cur = Xlic.sum(axis=(0, 2))
        Xlic *= np.where(cur > lic_eps, target_lic_sc / np.maximum(cur, lic_eps), 1.0)[None, :, None, :]
        cur = Xlic.sum(axis=(0, 1))
        Xlic *= np.where(cur > lic_eps, target_lic_ac / np.maximum(cur, lic_eps), 1.0)[None, None, :, :]
        cur = Xlic.sum(axis=3)
        Xlic *= np.where(cur > lic_eps, Tlic / np.maximum(cur, lic_eps), 0.0)[..., None]

    # Convergence diagnostics.
    cur_kc = Xlic.sum(axis=(1, 2))
    cur_sc = Xlic.sum(axis=(0, 2))
    cur_ac = Xlic.sum(axis=(0, 1))
    with np.errstate(invalid="ignore", divide="ignore"):
        dev_k = np.nanmax(np.abs(cur_kc / np.maximum(Tlic_K[:, None], lic_eps) - Mlic_K))
        dev_s = np.nanmax(np.abs(cur_sc / np.maximum(Tlic_S[:, None], lic_eps) - Mlic_S))
        dev_a = np.nanmax(np.abs(cur_ac / np.maximum(Tlic_A[:, None], lic_eps) - Mlic_A))
    print(
        "[braunschweig.enriched] LICENSE IPF converged after "
        f"{lic_iterations} iter; max |Δ| (pp): "
        f"kreis={dev_k * 100:.2f}, sex={dev_s * 100:.2f}, age={dev_a * 100:.2f}"
    )

    cell_sum = Xlic.sum(axis=3, keepdims=True)
    Plic_cell = np.where(cell_sum > lic_eps, Xlic / np.maximum(cell_sum, lic_eps), 0.0)
    lic_probs = np.zeros((n_persons, n_lic_cats))
    if lic_eligible.any():
        lic_probs[lic_eligible] = Plic_cell[
            lic_person_kreis[lic_eligible],
            lic_person_sex[lic_eligible],
            lic_person_age[lic_eligible], :
        ]
    # Persons below 18 (or with no Kreis/sex) → "nein" deterministically
    # (BF17 / begleitetes Fahren ab 17 in NDS is intentionally ignored).

    lic_ineligible = ~lic_eligible
    if lic_ineligible.any():
        lic_probs[lic_ineligible, :] = 0.0
        lic_probs[lic_ineligible, idx_nein] = 1.0

    row_sums = lic_probs.sum(axis=1, keepdims=True)
    row_sums[row_sums == 0] = 1.0
    lic_probs = lic_probs / row_sums

    # Use a dedicated RNG seed (independent of the PT one below).
    lic_rng = np.random.RandomState(context.config("random_seed") + 5417)
    lic_cdf = np.cumsum(lic_probs, axis=1)
    u_lic = lic_rng.random_sample(n_persons)
    lic_indices = (u_lic[:, None] < lic_cdf).argmax(axis=1)
    df_persons["license_type"] = pd.Categorical(
        lic_categories_arr[lic_indices], categories=list(LICENSE_CATEGORIES)
    )
    # Overwrite the HTS-matched ``has_license`` boolean with the MiD-derived
    # value so the downstream ``number_of_licenses`` / ``car_availability``
    # logic in the eqasim core uses the new attribute.
    df_persons["has_license"] = df_persons["license_type"].isin(list(LICENSE_TRUE))
    print(
        "[braunschweig.enriched] license_type share = "
        + ", ".join(
            f"{k}={v:.1%}"
            for k, v in df_persons["license_type"]
            .value_counts(normalize=True)
            .sort_index()
            .items()
        )
    )
    print(
        "[braunschweig.enriched] derived has_license = "
        f"{df_persons['has_license'].mean():.1%}"
    )

    # VEHICLE COUNTS + CAR / BIKE AVAILABILITY (A-REORDER causal chain).
    #
    # The MiD-H7 household car count (number_of_cars) and MiD-H12.3 bike count
    # (number_of_bicycles) are sampled HERE -- after the licence IPF and before
    # car_availability -- so the consistent-car_availability feature (A5) can
    # condition car_availability on both the per-person licence and the household
    # car count. This sampling was historically done in the OUTER execute() after
    # this stage returned; it uses its own RNG stream (+91731), independent of the
    # licence (+5417) / PT (+8572) / binarisation (+23761) streams, so moving it
    # earlier is output-identical.
    _vehicle_seed = context.config("random_seed")
    _vehicle_data_path = context.config("data_path")
    _sample_vehicle_counts(df_persons, _vehicle_data_path, _vehicle_seed)

    if context.config("consistent_car_availability"):
        # A5: the consistent car_availability derivation is DEFERRED to AFTER the
        # income-aware number_of_cars draw (see the A5/A6 block further down), so
        # it conditions on the FINAL household car count rather than the legacy
        # H7 draw. Here only the car Bernoulli uniform is still consumed
        # (apply_car=False) so the BICYCLE binarisation stays byte-identical to
        # the legacy path; the fractional car_availability weights are left in
        # place for the deferred A5 rake (which overwrites car_availability).
        _binarise_availability(df_persons, _vehicle_seed, apply_car=False)
    else:
        # OFF: legacy free P19 IPF weights -> {none, all} Bernoulli (and the
        # bicycle binarisation), byte-identical to the historical in-line block.
        # The legacy car_availability does NOT depend on number_of_cars, so it is
        # set HERE (independent of the later income-aware cars draw).
        _binarise_availability(df_persons, _vehicle_seed, apply_car=True)

    # NOTE: the A5 consistent car_availability derivation and the A6 PT
    # subscription block (which conditions on car_availability) are computed
    # FURTHER DOWN, after the income-aware number_of_cars draw, so they see the
    # FINAL car count. See the "A5/A6 (deferred)" block after _sample_cars_income_aware.

    # Household size: keep IPF-balanced values when the margin was active,
    # otherwise sample from the German census reference table.
    if context.config("braunschweig.ipf.use_household_size_margin"):
        df_persons["household_size"] = df_persons["household_size"].astype("category")
        print(
            "[braunschweig.enriched] using IPF-balanced hh_size; share by bin = "
            + ", ".join(
                f"{k}={v:.1%}"
                for k, v in df_persons["household_size"]
                .value_counts(normalize=True)
                .sort_index()
                .items()
            )
        )
    else:
        df_household_size = context.stage("braunschweig.data.census.household_size")

        minimum_age = context.config("braunschweig.minimum_age.one_person_household")
        df_household_size["lower_age"] = df_household_size["lower_age"].replace({0: minimum_age})

        df_young = df_household_size[df_household_size["lower_age"] == minimum_age].copy()
        df_young["lower_age"] = 0
        df_young["upper_age"] = minimum_age
        df_young.loc[df_young["household_size"] == "1", "weight"] = 0

        df_household_size = pd.concat([df_household_size, df_young])

        for (lower_age, upper_age, sex), df in df_household_size.groupby(["lower_age", "upper_age", "sex"]):
            f = df_persons["age"].between(lower_age, upper_age, inclusive="left")
            f &= df_persons["sex"] == sex  # TODO

            df = df.copy()
            df["weight"] /= df["weight"].sum()
            df = df.sample(n=np.count_nonzero(f), weights="weight", replace=True)
            df_persons.loc[f, "household_size"] = df["household_size"].values

        df_persons["household_size"] = df_persons["household_size"].astype("category")

    # Household income (overwrite). The reference table can be 5-bin
    # (Bavaria GENESIS) or 6-bin (Braunschweig MiD H4); pick adaptively.
    df_income = context.stage("braunschweig.data.census.household_income")
    income_bins = set(df_income["household_size"].astype(str).unique())
    income_size_map, scheme = _build_income_size_map(income_bins)
    income_lookup_size = df_persons["household_size"].astype(str).map(
        lambda s: _income_bin_for_size(s, income_size_map, scheme)
    )
    unresolved = set(income_lookup_size.unique()) - income_bins
    if unresolved:
        raise RuntimeError(
            "income_size_map produced bins not present in df_income: "
            f"{sorted(unresolved)} (reference has {sorted(income_bins)}, "
            f"scheme={scheme})"
        )

    for household_size, df in df_income.groupby("household_size"):
        f = (income_lookup_size == household_size).values
        df = df.copy()
        df["weight"] /= df["weight"].sum()
        df = df.sample(n=np.count_nonzero(f), weights="weight", replace=True)
        df_persons.loc[f, "household_income"] = df["income_class"].values

    n_missing_income = int(df_persons["household_income"].isna().sum())
    if n_missing_income > 0:
        raise RuntimeError(
            f"{n_missing_income} persons have no household_income after "
            f"reference sampling (scheme={scheme}, reference bins "
            f"{sorted(income_bins)}, lookup bins "
            f"{sorted(income_lookup_size.unique())})"
        )

    df_persons["high_income"] = df_persons["household_income"] == "5000+"

    # 5-class MiD economic status.
    #
    # ON (status_from_hhtype, default): sample economic_status from the MiD
    # P(status | hhtype, region) (Bayes; NDS base tilted by RegioStaR-7 raumtyp)
    # and RE-DERIVE household_income / high_income from the sampled status, so the
    # much stronger household-type predictor drives both. household_income_eur is
    # computed downstream (INKAR scaling) from the re-derived class.
    #
    # OFF: exact legacy path (commit c65399d) -- economic_status mapped 1:1 from
    # the already-sampled income EUR-class; income untouched -> byte-identical.
    if context.config("status_from_hhtype"):
        df_regiostar = context.stage("braunschweig.data.bbsr.regiostar")
        df_persons = _derive_economic_status_from_hhtype(
            df_persons,
            context.config("data_path"),
            df_regiostar,
            context.config("random_seed"),
        )
    else:
        df_persons = _derive_economic_status(df_persons)

    # INCOME/HOUSEHOLD-TYPE-AWARE number_of_cars (cars_income_aware). Default ON.
    #
    # The legacy per-Kreis MiD-H7 number_of_cars was already sampled in the
    # VEHICLE COUNTS block (above, before car_availability). Here -- now that
    # economic_status, the Haushaltstyp inputs (age / hh_type) and commune_id
    # (raumtyp) are all available -- it is OVERWRITTEN by a draw from the
    # MiD-coupled pmf P(num_cars | hhtype, status, raumtyp), raked per Kreis back
    # to the MiD-H7 marginal so each Kreis's 0/1/2/3+ totals stay EXACTLY the H7
    # control (only the within-Kreis allocation by income/hhtype/raumtyp changes).
    # OFF -> the legacy H7 draw is left untouched (byte-identical).
    #
    # CAUSAL ORDER (A5/A6 consistency): the A5 consistent car_availability
    # derivation and the A6 PT-subscription block are run AFTER this income-aware
    # draw (see the "A5/A6 (deferred)" block immediately below), so they condition
    # on the FINAL income-aware number_of_cars rather than the legacy H7 draw.
    # Otherwise a household could end up with car_availability != "none" while its
    # final number_of_cars == 0 (the bug this order fixes). The downstream fleet
    # stage (F5, braunschweig.synthesis.vehicles.cars.household) reads this final,
    # income-aware number_of_cars, so vehicle generation is income-coupled.
    if context.config("cars_income_aware"):
        df_regiostar_cars = context.stage("braunschweig.data.bbsr.regiostar")
        df_persons = _sample_cars_income_aware(
            df_persons,
            context.config("data_path"),
            context.config("random_seed"),
            df_regiostar_cars,
        )

    # A5/A6 (deferred): now that number_of_cars is FINAL (income-aware draw above,
    # or the legacy H7 draw if cars_income_aware is OFF), derive the consistent
    # car_availability (A5) and then the PT subscription (A6, which conditions on
    # car_availability). Moving these here -- rather than next to the vehicle-count
    # block -- guarantees A5 sees the final household car count, so no household
    # gets car_availability != "none" with a final number_of_cars == 0.
    #
    # RNG: A5 consumes its own seeded stream (random_seed + 41719) exactly once;
    # PT consumes (random_seed + 8572) exactly once. Both are independent of the
    # income-aware cars stream (+47629) and the vehicle-count / binarisation
    # streams, so the OFF paths stay byte-identical -- only the call ORDER moved.
    if context.config("consistent_car_availability"):
        # A5: derive car_availability conditionally (licence + FINAL household
        # cars) and rake to the P19 marginal. The car Bernoulli uniform was
        # already consumed (apply_car=False) in the vehicle block to keep the
        # BICYCLE binarisation byte-identical; here car_availability is overwritten.
        _derive_car_availability_consistent(
            df_persons, mid, _vehicle_seed,
            context.config("braunschweig.minimum_age.car_availability"),
        )

    # PT SUBSCRIPTION (categorical, MiD 2023 P24.1).
    #
    # Three-margin IPF (raking) on the 4-way contingency table
    #   X[kreis, sex, age_bin, ticket_type]
    # with target marginals:
    #   M_K[kreis, ticket]  = T_K[kreis]  * by_kreis[kreis][ticket]
    #   M_S[sex, ticket]    = T_S[sex]    * by_sex[sex][ticket]
    #   M_A[age, ticket]    = T_A[age]    * by_age[age][ticket]
    # and row totals T[kreis, sex, age] preserved (i.e. sum_c X = T).
    #
    # After convergence each person in cell (k, s, a) gets the probability
    # vector P[k, s, a, :] = X[k, s, a, :] / sum_c X[k, s, a, :], which is
    # then sampled categorically.  ``has_pt_subscription`` is finally
    # derived as the union of the flatrate categories.
    from braunschweig.data.mid.reference_tables import (
        load_pt_subscription_breakdown,
        load_pt_subscription_margins,
        PT_TICKET_CATEGORIES,
        PT_TICKET_FLATRATE,
    )
    from braunschweig.data.mid.zones import ZONE_NAMES as _PT_ZONE_NAMES

    pt_data_path = context.config("data_path")
    pt_by_kreis, pt_region = load_pt_subscription_breakdown(pt_data_path)
    pt_by_sex, pt_by_age = load_pt_subscription_margins(pt_data_path)
    pt_name_to_ars5 = {v: k for k, v in _PT_ZONE_NAMES.items()}
    pt_min_age = context.config("braunschweig.minimum_age.pt_subscription")

    n_persons = len(df_persons)
    n_cats = len(PT_TICKET_CATEGORIES)
    pt_categories_arr = np.asarray(PT_TICKET_CATEGORIES)
    idx_fahre_nie = PT_TICKET_CATEGORIES.index("fahre_nie")

    # Assign each person to a Kreis index, sex index and age-bin index.
    # Persons with no zone (external) or age below the MiD basis are
    # excluded from the IPF and assigned ``fahre_nie`` deterministically.
    ars5_list = list(pt_by_kreis.keys())
    ars5_to_idx = {ars: i for i, ars in enumerate(ars5_list)}
    person_kreis = np.full(n_persons, -1, dtype=np.int64)
    for zone_name, ars5 in pt_name_to_ars5.items():
        col = "inside_{}".format(zone_name)
        if col not in df_persons.columns or ars5 not in ars5_to_idx:
            continue
        f_zone = df_persons[col].to_numpy()
        if f_zone.any():
            person_kreis[f_zone] = ars5_to_idx[ars5]

    sex_arr = df_persons["sex"].astype(str).to_numpy()
    person_sex = np.where(sex_arr == "male", 0, np.where(sex_arr == "female", 1, -1))

    age_arr = df_persons["age"].to_numpy()
    person_age = np.full(n_persons, -1, dtype=np.int64)
    for ai, (lo, hi, _vec) in enumerate(pt_by_age):
        f_band = (age_arr >= lo) & (age_arr <= hi)
        if f_band.any():
            person_age[f_band] = ai

    # Persons eligible for IPF: have valid Kreis, sex and age band, and are
    # at or above the MiD survey age cut-off.
    f_eligible = (
        (person_kreis >= 0)
        & (person_sex >= 0)
        & (person_age >= 0)
        & (age_arr >= max(pt_min_age, 14))
    )
    n_kreise = len(ars5_list)
    n_sex = 2
    n_ages = len(pt_by_age)

    # Build target matrices and the count table T[k, s, a].
    M_K = np.zeros((n_kreise, n_cats))
    for ars, vec in pt_by_kreis.items():
        M_K[ars5_to_idx[ars], :] = vec
    M_S = np.zeros((n_sex, n_cats))
    for sex, vec in pt_by_sex.items():
        si = 0 if sex == "male" else 1
        M_S[si, :] = vec
    M_A = np.zeros((n_ages, n_cats))
    for ai, (_lo, _hi, vec) in enumerate(pt_by_age):
        M_A[ai, :] = vec

    T = np.zeros((n_kreise, n_sex, n_ages), dtype=float)
    if f_eligible.any():
        # Vectorised count via flat-index histogram.
        flat = (
            person_kreis[f_eligible] * (n_sex * n_ages)
            + person_sex[f_eligible] * n_ages
            + person_age[f_eligible]
        )
        counts = np.bincount(flat, minlength=n_kreise * n_sex * n_ages)
        T = counts.reshape((n_kreise, n_sex, n_ages)).astype(float)

    T_K = T.sum(axis=(1, 2))                         # shape (n_kreise,)
    T_S = T.sum(axis=(0, 2))                         # shape (n_sex,)
    T_A = T.sum(axis=(0, 1))                         # shape (n_ages,)

    # Convert per-cell shares to absolute targets (only meaningful where T>0).
    target_kc = M_K * T_K[:, None]                   # shape (n_kreise, n_cats)
    target_sc = M_S * T_S[:, None]                   # shape (n_sex, n_cats)
    target_ac = M_A * T_A[:, None]                   # shape (n_ages, n_cats)

    # Initialise X uniformly within each (k, s, a) cell.
    X = np.broadcast_to(T[..., None] / n_cats, (n_kreise, n_sex, n_ages, n_cats)).copy()

    pt_iterations = 200
    pt_eps = 1e-9
    for _ in range(pt_iterations):
        # 1) Match Kreis × ticket margin.
        cur_kc = X.sum(axis=(1, 2))                  # (n_kreise, n_cats)
        scale_kc = np.where(cur_kc > pt_eps, target_kc / np.maximum(cur_kc, pt_eps), 1.0)
        X *= scale_kc[:, None, None, :]
        # 2) Match Sex × ticket margin.
        cur_sc = X.sum(axis=(0, 2))                  # (n_sex, n_cats)
        scale_sc = np.where(cur_sc > pt_eps, target_sc / np.maximum(cur_sc, pt_eps), 1.0)
        X *= scale_sc[None, :, None, :]
        # 3) Match Age × ticket margin.
        cur_ac = X.sum(axis=(0, 1))                  # (n_ages, n_cats)
        scale_ac = np.where(cur_ac > pt_eps, target_ac / np.maximum(cur_ac, pt_eps), 1.0)
        X *= scale_ac[None, None, :, :]
        # 4) Restore per-cell totals (row-sum = T[k,s,a]).
        cur_cell = X.sum(axis=3)                     # (n_kreise, n_sex, n_ages)
        scale_cell = np.where(cur_cell > pt_eps, T / np.maximum(cur_cell, pt_eps), 0.0)
        X *= scale_cell[..., None]

    # Convergence diagnostics (max abs deviation in pp on each margin).
    cur_kc = X.sum(axis=(1, 2))
    cur_sc = X.sum(axis=(0, 2))
    cur_ac = X.sum(axis=(0, 1))
    with np.errstate(invalid="ignore", divide="ignore"):
        dev_k = np.nanmax(np.abs(cur_kc / np.maximum(T_K[:, None], pt_eps) - M_K))
        dev_s = np.nanmax(np.abs(cur_sc / np.maximum(T_S[:, None], pt_eps) - M_S))
        dev_a = np.nanmax(np.abs(cur_ac / np.maximum(T_A[:, None], pt_eps) - M_A))
    print(
        "[braunschweig.enriched] PT IPF converged after "
        f"{pt_iterations} iter; max |Δ| (pp): "
        f"kreis={dev_k * 100:.2f}, sex={dev_s * 100:.2f}, age={dev_a * 100:.2f}"
    )

    # Convert per-cell counts back to probability vectors and sample.
    cell_sum = X.sum(axis=3, keepdims=True)
    P_cell = np.where(cell_sum > pt_eps, X / np.maximum(cell_sum, pt_eps), 0.0)

    # Build per-person probability matrix.
    pt_probs = np.zeros((n_persons, n_cats))
    if f_eligible.any():
        pt_probs[f_eligible] = P_cell[
            person_kreis[f_eligible], person_sex[f_eligible], person_age[f_eligible], :
        ]
    # Persons not eligible (no Kreis / no sex / age below cutoff) → fahre_nie.
    f_ineligible = ~f_eligible
    if f_ineligible.any():
        pt_probs[f_ineligible, :] = 0.0
        pt_probs[f_ineligible, idx_fahre_nie] = 1.0

    # Defensive renormalise.
    row_sums = pt_probs.sum(axis=1, keepdims=True)
    row_sums[row_sums == 0] = 1.0
    pt_probs = pt_probs / row_sums

    # A6: condition pt_subscription on student / employment status (+car hook).
    # Default ON. OFF -> the exact legacy 3-margin {Kreis,sex,age} P24.1 IPF above
    # is used unchanged (byte-identical sampling, since the same pt_probs feed the
    # same +8572 RNG stream).
    if context.config("pt_subscription_conditioned"):
        # (1) DATA-FREE logical constraint: the work/study-bound combined ticket
        # category requires employed OR studies; zero it for everyone else and
        # re-normalise. employed/studies are produced upstream (attributed stage,
        # reactivate_person_attributes); fall back to all-False if the columns are
        # absent (e.g. flag OFF in attributed) -- which then zeroes the work/study
        # ticket for everyone, a documented but loud limitation.
        for _col in ("employed", "studies"):
            if _col not in df_persons.columns:
                print(
                    f"[braunschweig.enriched] WARNING: pt_subscription_conditioned "
                    f"is ON but column '{_col}' is missing; treating it as all-False "
                    f"(work/study ticket will be unavailable). Enable "
                    f"reactivate_person_attributes to fix."
                )
                df_persons[_col] = False
        pt_probs = _condition_pt_subscription_probs(
            pt_probs, df_persons, PT_TICKET_CATEGORIES
        )
        print(
            "[braunschweig.enriched] PT A6 conditioning: work/study ticket zeroed "
            f"for {df_persons.attrs.get('pt_subscription_workstudy_zeroed_count', 0)} "
            "non-working/non-studying persons; degenerate->fahre_nie fallback "
            f"{df_persons.attrs.get('pt_subscription_degenerate_fallback_count', 0)}"
        )

        # (2) DATA-DEPENDENT carless<->PT correlation. Only an extra margin when
        # the MiD P24.1 x Pkw-Verfuegbarkeit cross-tab is present; otherwise the
        # loader returns None and logs an INFO fallback (documented, not silent).
        from braunschweig.data.mid.reference_tables import (
            load_pt_subscription_by_car_availability,
        )
        pt_by_car = load_pt_subscription_by_car_availability(
            context.config("data_path")
        )
        if pt_by_car is None:
            print(
                "[braunschweig.enriched] PT A6: carless<->PT car-availability "
                "margin NOT applied (cross-tab CSV absent; coupling uncalibrated)."
            )
        else:
            # Re-weight each person's PT vector toward P(ticket | their
            # car_availability) from the MiD cross-tab, then lightly rake back to
            # the P24.1 marginal so the aggregate target stays matched (the
            # carless<->PT-pass coupling is imposed WITHIN persons; the column
            # levels are preserved). car_availability is already categorical at
            # this point ({none, some, all} with A5 ON, {none, all} with A5 OFF)
            # and reflects the FINAL income-aware number_of_cars (A5 ran above).
            if "car_availability" not in df_persons.columns:
                print(
                    "[braunschweig.enriched] WARNING: PT A6 car-availability "
                    "cross-tab present but 'car_availability' column missing; "
                    "carless<->PT coupling skipped."
                )
            else:
                pt_probs = _apply_car_availability_pt_margin(
                    pt_probs, df_persons, PT_TICKET_CATEGORIES, pt_by_car,
                )
                _car_primary = df_persons.attrs.get(
                    "pt_subscription_car_margin_primary_count", 0
                )
                _car_fallback = df_persons.attrs.get(
                    "pt_subscription_car_margin_fallback_count", 0
                )
                _car_rate = df_persons.attrs.get(
                    "pt_subscription_car_margin_fallback_rate", 0.0
                )
                _car_dev = df_persons.attrs.get(
                    "pt_subscription_car_margin_max_dev_pp", float("nan")
                )
                _level = "WARNING: " if _car_rate > 0.5 else ""
                print(
                    f"[braunschweig.enriched] {_level}PT A6 carless<->PT margin "
                    f"applied: primary {_car_primary}, fallback {_car_fallback} "
                    f"({_car_rate:.2%}) persons without a usable car_availability; "
                    f"P24.1 marginal restored to max |Δ| {_car_dev:.2f} pp "
                    f"(cross-tab keys {sorted(pt_by_car.keys())})."
                )

    # Sample categorical via inverse-CDF with a dedicated RNG seed.
    random = np.random.RandomState(context.config("random_seed") + 8572)
    pt_cdf = np.cumsum(pt_probs, axis=1)
    u_pt = random.random_sample(n_persons)
    pt_indices = (u_pt[:, None] < pt_cdf).argmax(axis=1)
    df_persons["pt_subscription_type"] = pd.Categorical(
        pt_categories_arr[pt_indices], categories=list(PT_TICKET_CATEGORIES)
    )
    df_persons["has_pt_subscription"] = df_persons["pt_subscription_type"].isin(
        list(PT_TICKET_FLATRATE)
    )
    print(
        "[braunschweig.enriched] pt_subscription_type share = "
        + ", ".join(
            f"{k}={v:.1%}"
            for k, v in df_persons["pt_subscription_type"]
            .value_counts(normalize=True)
            .sort_index()
            .items()
        )
    )
    print(
        "[braunschweig.enriched] derived has_pt_subscription = "
        f"{df_persons['has_pt_subscription'].mean():.1%}"
    )

    # Urban-area resident flag (legacy Munich/Paris name kept for downstream
    # compatibility). Region-neutral default: only set when the regional
    # enricher attaches an "inside_<region>" column. Braunschweig overrides
    # this via the post-execute hook below (is_bs_resident → is_urban_resident).
    df_persons["is_munich_resident"] = (
        df_persons["inside_munich"]
        if "inside_munich" in df_persons.columns
        else False
    )

    # ``commune_id`` was merged in to resolve the home RegioStaR-7 raumtyp for the
    # status_from_hhtype / cars_income_aware derivations. When the distribution
    # income (income_eur_from_distribution) is ON it is ALSO needed by the OUTER
    # execute() (the EUR draw runs there, after the INKAR stage is available), so
    # it is kept and dropped by the outer execute() instead. Otherwise drop it
    # here so the returned schema is unchanged vs the legacy path (OFF never reads
    # it).
    if (
        "commune_id" in df_persons.columns
        and not context.config("income_eur_from_distribution")
        and not context.config("synthesise_housing_tenure")
    ):
        df_persons = df_persons.drop(columns=["commune_id"])

    return df_persons


# ---------------------------------------------------------------------------
# synpp cache validation
# ---------------------------------------------------------------------------

# Extracted helper submodules of this stage package. synpp's get_stage_hash
# only hashes THIS file's source (inspect.getsource of the stage module), so
# without the validate() hook below a change confined to a helper submodule
# would silently reuse the stale cached stage output on a partial rerun.
# Every submodule extracted from this package MUST be listed here.
_HELPER_MODULES = (
    availability,
    economic_status,
    housing_tenure,
    income_distribution,
    vehicle_ownership,
)


def validate(context):
    """synpp validation token: md5 over the helper submodules' sources.

    synpp compares this token against the one stored with the cached stage
    output and devalidates the cache on mismatch, so helper-only source
    changes recompute the stage just like changes to this file itself.
    """
    digest = hashlib.md5()
    for module in _HELPER_MODULES:
        digest.update(inspect.getsource(module).encode("utf-8"))
    return digest.hexdigest()


def configure(context):
    _configure_base(context)
    context.stage("braunschweig.data.inkar.household_income")
    context.config("random_seed")
    context.config("data_path")


def execute(context):
    df_persons = _execute_base(context)

    data_path = context.config("data_path")

    # Derive the per-person Kreis AGS-5 once for the INKAR income scaling below.
    # NOTE: the MiD H7 / H12.3 vehicle counts (number_of_cars /
    # number_of_bicycles) are NO LONGER sampled here. They were moved into
    # _execute_base (the VEHICLE COUNTS block, before car_availability) so the A5
    # consistent-car_availability feature can condition on the household car count
    # and the licence. The +91731 RNG stream and the cars-then-bikes consumption
    # order are preserved there, so the OFF-path draws are byte-identical to the
    # legacy outer-execute sampling.
    kreis = _derive_kreis_ars5(df_persons)

    # household_income_eur.
    #
    # ON (income_eur_from_distribution, default): draw the EUR value from the real
    # MiD monthly net-income distribution P(bracket | hh_size, raumtyp) (NDS base +
    # raumtyp tilt), rank-aligned to economic_status so income is monotone in the
    # SES anchor; INKAR is applied only as a FINE within-region Kreis tilt. The
    # categorical household_income / high_income are RE-DERIVED from the drawn EUR.
    #
    # OFF: the legacy class-midpoint x INKAR-scale path (byte-identical
    # household_income_eur).
    df_inkar = context.stage("braunschweig.data.inkar.household_income")
    class_midpoint_eur = load_class_midpoint_eur(data_path)
    if context.config("income_eur_from_distribution"):
        from braunschweig.data.mid.income_by_size import (
            load_income_by_size_bundesland,
            load_income_by_size_raumtyp,
        )
        from braunschweig.data.mid.income_by_status import (
            load_income_by_status_bundesland,
            load_income_by_status_raumtyp,
        )
        df_regiostar_income = context.stage("braunschweig.data.bbsr.regiostar")
        df_income_bund = load_income_by_size_bundesland(data_path)
        df_income_raum = load_income_by_size_raumtyp(data_path)
        # Empirical income x economic-status conditional (replaces the legacy
        # rank-alignment heuristic; see _apply_distribution_income).
        df_income_status_bund = load_income_by_status_bundesland(data_path)
        df_income_status_raum = load_income_by_status_raumtyp(data_path)
        df_persons = _apply_distribution_income(
            df_persons, df_inkar, df_income_bund, df_income_raum,
            df_regiostar_income, class_midpoint_eur,
            context.config("random_seed"),
            df_status_bundesland=df_income_status_bund,
            df_status_raumtyp=df_income_status_raum,
            kreis=kreis,
        )
    else:
        df_persons = _apply_inkar_income_scale(df_persons, df_inkar,
                                               class_midpoint_eur, kreis=kreis)

    # housing_tenure COMPLETENESS attribute (synthesise_housing_tenure, default
    # ON). Sampled per household from P(tenure | income_bracket, raumtyp) using the
    # FINAL household_income_eur (resolved to a bracket) -- so it runs AFTER the
    # income block above regardless of which income path produced the EUR value.
    # OFF -> the attribute is never added, so the output schema is byte-identical.
    if context.config("synthesise_housing_tenure"):
        from braunschweig.data.mid.tenure_by_income import (
            load_tenure_by_income_bundesland,
            load_tenure_by_income_raumtyp,
        )
        df_regiostar_tenure = context.stage("braunschweig.data.bbsr.regiostar")
        df_tenure_bund = load_tenure_by_income_bundesland(data_path)
        df_tenure_raum = load_tenure_by_income_raumtyp(data_path)
        df_persons = _apply_housing_tenure(
            df_persons, df_tenure_bund, df_tenure_raum,
            df_regiostar_tenure, context.config("random_seed"),
        )

    # ``commune_id`` (kept by _execute_base only for the distribution income +
    # housing-tenure raumtyp lookups) is dropped now so the returned schema matches
    # the legacy path (the tenure draw above is the last consumer).
    if "commune_id" in df_persons.columns:
        df_persons = df_persons.drop(columns=["commune_id"])

    # BS-specific residency flag (aligns with is_munich_resident semantics).
    if "inside_braunschweig" in df_persons.columns:
        df_persons["is_bs_resident"] = df_persons["inside_braunschweig"]
        # Region-neutral alias consumed by synthesis.output and the MATSim
        # writer; written to MATSim XML under the legacy attribute key
        # "isParis" (BavariaPredictorUtils.isParisResident).
        df_persons["is_urban_resident"] = df_persons["inside_braunschweig"]

    # ------------------------------------------------------------------
    # Post-enriched control variables - hard asserts that ensure no silent
    # NaN propagation, no impossible categorical, and (when the IPF
    # hh_size margin is enabled) that the per-cell shares survive the
    # household-formation/sampling round-trip with low deviation against
    # the IPF input target.
    # ------------------------------------------------------------------
    n = len(df_persons)
    for col in [
        "household_size", "household_income", "high_income", "economic_status",
        "car_availability", "bicycle_availability", "has_pt_subscription",
        "pt_subscription_type",
        "number_of_cars", "number_of_bicycles",
    ]:
        if col not in df_persons.columns:
            raise RuntimeError(
                f"[braunschweig.enriched] expected column '{col}' missing after enrichment"
            )
        n_na = int(df_persons[col].isna().sum())
        if n_na > 0:
            raise RuntimeError(
                f"[braunschweig.enriched] column '{col}' has {n_na}/{n} NaN values"
            )

    if context.config("braunschweig.ipf.use_household_size_margin"):
        achieved = (
            df_persons["household_size"].astype(str).value_counts(normalize=True)
            .sort_index()
        )
        df_size_ref = context.stage("braunschweig.data.census.household_size").copy()
        if "household_size" in df_size_ref.columns and "weight" in df_size_ref.columns:
            target = (
                df_size_ref.groupby("household_size", observed=True)["weight"].sum()
            )
            target = (target / target.sum()).sort_index()
            common = sorted(set(achieved.index) & set(target.index))
            if common:
                deltas = {
                    k: float(achieved.get(k, 0.0)) - float(target.get(k, 0.0))
                    for k in common
                }
                max_dev = max(abs(v) for v in deltas.values())
                print(
                    "[braunschweig.enriched] hh_size deviation vs reference (pp): "
                    + ", ".join(f"{k}={v*100:+.2f}" for k, v in deltas.items())
                    + f" - max |delta|={max_dev*100:.2f}pp"
                )
                if max_dev > 0.05:
                    raise RuntimeError(
                        "[braunschweig.enriched] hh_size shares drift more than "
                        f"5pp from reference: {deltas}"
                    )

    # Sanity ranges (catch arithmetic blow-ups, e.g. INKAR scale gone wild).
    if "household_income_eur" in df_persons.columns:
        eur = df_persons["household_income_eur"]
        n_eur_na = int(eur.isna().sum())
        if n_eur_na > 0:
            raise RuntimeError(
                f"[braunschweig.enriched] household_income_eur has {n_eur_na} NaN values"
            )
        if (eur < 100).any() or (eur > 20000).any():
            raise RuntimeError(
                f"[braunschweig.enriched] household_income_eur outside plausible "
                f"range [100, 20000]: min={eur.min():.0f}, max={eur.max():.0f}"
            )

    print(
        "[braunschweig.enriched] post-enrichment OK: "
        f"n={n:,}, high_income={df_persons['high_income'].mean():.2%}, "
        f"car_avail={(df_persons['car_availability']=='all').mean():.2%}, "
        f"bike_avail={(df_persons['bicycle_availability']=='all').mean():.2%}, "
        f"pt_sub={df_persons['has_pt_subscription'].mean():.2%}."
    )

    return df_persons
