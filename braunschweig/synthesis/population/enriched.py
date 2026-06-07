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
"""

import synthesis.population.enriched as delegate

import pandas as pd
import geopandas as gpd
import numpy as np

from braunschweig.data.mid.reference_tables import (
    load_class_midpoint_eur,
    load_kreis_share_table,
)
from braunschweig.ipf.joint_age_size import rake_2d


# Hard-coded median € midpoint applied when a person's ``household_income``
# class label is absent from the MiD H4 class-midpoint table (the PRIMARY
# lookup). Kept at the legacy value so behaviour is unchanged; only the
# primary/fallback split is now counted and logged for transparency.
INCOME_MIDPOINT_FALLBACK_EUR = 2800.0

# Fallback-rate threshold above which the class-midpoint fallback is logged at
# WARNING level (fraction of persons in [0, 1]). A non-zero but rare fallback is
# expected only on malformed reference data; a large share signals a broken
# class-midpoint table or an unexpected income_class vocabulary.
INCOME_MIDPOINT_FALLBACK_WARN_RATE = 0.001

# Fallback-rate threshold above which the per-Kreis share fallback in
# :func:`_sample_counts` (cars from MiD H7, bikes from MiD H12.3) is logged at
# WARNING level (fraction of persons in [0, 1]). Every in-scope Kreis ARS-5 code
# is expected to be present in the per-Kreis share table, so the region-wide
# fallback should never fire on correct data. A non-zero rate almost always
# signals a Kreis-ARS format mismatch (e.g. AGS-5 vs AGS-8) that would silently
# route persons to the region-wide distribution; even a small share is therefore
# escalated.
KREIS_SHARE_FALLBACK_WARN_RATE = 0.0


# --- 5-class MiD economic status -------------------------------------------
# The MiD 2023 Tabelle H4 "Oekonomischer Status des Haushalts" reports the
# BMDV-defined needs-adjusted net-equivalent-income quintile of each household
# (sehr niedrig / niedrig / mittel / hoch / sehr hoch). The income-synthesis
# pipeline samples a household_income EUR-class from the H4 quintile vector via
# braunschweig.data.census.household_income.INCOME_CLASS_MAP, which is a 1:1
# correspondence (EUR-class i  <->  H4 quintile position i). The upcoming
# vehicle-segment IPF (Spec 2) needs the quintile status itself, not the
# best-effort EUR-class label, so we re-expose it under the canonical English
# names below.
#
# ECONOMIC_STATUS_CATEGORIES is ordered low -> high and matches the H4 quintile
# column order (sehr_niedrig, niedrig, mittel, hoch, sehr_hoch) one-to-one. The
# EUR-class -> status mapping is DERIVED from INCOME_CLASS_MAP so that income and
# economic status can never disagree: each EUR-class maps to the status at the
# exact H4 quintile position from which that EUR-class was sampled.
ECONOMIC_STATUS_CATEGORIES = ("very_low", "low", "medium", "high", "very_high")


def _build_economic_status_by_income_class():
    """Return the deterministic EUR-class -> 5-class economic status mapping.

    Built from INCOME_CLASS_MAP (EUR-class label, H4 quintile position) so the
    status is exactly the quintile that produced the EUR-class. Kept as a
    function-built constant rather than a literal to avoid duplicating the
    EUR-class vocabulary, which lives in household_income.INCOME_CLASS_MAP.
    """
    from braunschweig.data.census.household_income import INCOME_CLASS_MAP

    return {
        income_class: ECONOMIC_STATUS_CATEGORIES[idx]
        for income_class, idx in INCOME_CLASS_MAP
    }


ECONOMIC_STATUS_BY_INCOME_CLASS = _build_economic_status_by_income_class()


def _derive_economic_status(df_persons):
    """Add the 5-class MiD economic status as ``economic_status`` (additive).

    Maps each person's already-sampled ``household_income`` EUR-class onto its
    BMDV quintile status via ECONOMIC_STATUS_BY_INCOME_CLASS. This is purely
    derived: ``household_income`` / ``household_income_eur`` / ``high_income``
    are left untouched. An EUR-class absent from the mapping is a FALLBACK (it
    means the income vocabulary drifted from INCOME_CLASS_MAP); such cells are
    left as ``None`` and counted, and the primary/fallback split is logged so a
    high fallback rate surfaces a broken income vocabulary rather than silently
    producing a wrong status (project no-silent-fallback rule).
    """
    income_class = df_persons["household_income"].astype(str)
    status = income_class.map(ECONOMIC_STATUS_BY_INCOME_CLASS)

    fallback_mask = status.isna()
    n_fallback = int(fallback_mask.sum())
    n_total = len(df_persons)
    n_primary = n_total - n_fallback
    fallback_rate = (n_fallback / n_total) if n_total else 0.0

    df_persons["economic_status"] = status.values
    df_persons.attrs["economic_status_primary_count"] = n_primary
    df_persons.attrs["economic_status_fallback_count"] = n_fallback
    df_persons.attrs["economic_status_fallback_rate"] = fallback_rate

    if n_fallback:
        unknown = sorted(set(income_class[fallback_mask.values].unique()))
        print(
            f"WARNING: [braunschweig.enriched] economic_status fallback for "
            f"{n_fallback}/{n_total} persons ({fallback_rate:.2%}); primary hit "
            f"{n_primary}. Unmapped household_income classes {unknown} are not in "
            f"ECONOMIC_STATUS_BY_INCOME_CLASS (income vocabulary drifted from "
            f"INCOME_CLASS_MAP)."
        )
    else:
        print(
            f"[braunschweig.enriched] economic_status PRIMARY mapping hit all "
            f"{n_primary}/{n_total} persons (fallback rate 0.00%)."
        )

    return df_persons


# --- Economic status from MiD household-type x region (Bayes) ---------------
# (flag ``status_from_hhtype``; CLAUDE.md). Each synthetic household is mapped
# to a MiD Haushaltstyp and to its home RegioStaR-7 raumtyp; the economic status
# is then sampled from P(status | hhtype, region) via Bayes (Niedersachsen base
# from the bundesland table, tilted within-NDS by the raumtyp table). The income
# class label is re-derived from the sampled status so income and status agree
# by construction (the downstream household_income_eur is then computed from the
# re-derived class as in the legacy path).

# Inverse of ECONOMIC_STATUS_BY_INCOME_CLASS: status key -> income EUR-class.
# Built so a sampled status maps back to exactly the H4 EUR-class that the legacy
# income synthesis associates with that quintile (1:1 by construction).
def _build_income_class_by_status():
    from braunschweig.data.census.household_income import INCOME_CLASS_MAP
    return {
        ECONOMIC_STATUS_CATEGORIES[idx]: income_class
        for income_class, idx in INCOME_CLASS_MAP
    }


INCOME_CLASS_BY_ECONOMIC_STATUS = _build_income_class_by_status()

# Fallback-rate threshold above which the household->MiD-Haushaltstyp mapping
# fallback (a household that cannot be classified -> legacy income-class status)
# is logged at WARNING level. Household formation should classify every
# household, so a non-trivial rate signals a malformed household composition.
STATUS_HHTYPE_FALLBACK_WARN_RATE = 0.01

# Income-aware number_of_cars (cars_income_aware): rate of households that fall
# back from the MiD-coupled P(num_cars | hhtype, status, raumtyp) pmf to the
# per-Kreis MiD-H7 draw (unclassifiable household or missing MiD cell) above
# which the per-run log escalates to WARNING. The per-Kreis H7 marginal is held
# exactly by the rake regardless of the fallback rate; a high rate only means
# the MiD coupling reached few households (a signal the primary method is not
# working), so it is surfaced.
CARS_INCOME_AWARE_FALLBACK_WARN_RATE = 0.05


def _derive_economic_status_from_hhtype(df_persons, data_path, df_regiostar,
                                        random_seed):
    """Sample ``economic_status`` from MiD P(status | hhtype, region) (Bayes).

    For every household: map it to a MiD Haushaltstyp
    (:func:`braunschweig.data.mid.status_by_hhtype.map_households_to_hhtype`)
    and to its home RegioStaR-7 raumtyp (via ``commune_id`` -> AGS-8 ->
    ``df_regiostar``); combine the Niedersachsen base
    ``P(status | hhtype, NDS)`` with the within-NDS raumtyp tilt
    (:func:`region_status_probabilities`); sample the status with a dedicated,
    seeded RNG.

    The income EUR-class ``household_income`` is then OVERWRITTEN from the
    sampled status (``INCOME_CLASS_BY_ECONOMIC_STATUS``) so income and status
    agree; ``high_income`` is recomputed. ``household_income_eur`` is left for
    the downstream INKAR scaling, which now reads the re-derived class.

    Fallback transparency (CLAUDE.md): households that cannot be classified to a
    Haushaltstyp keep the legacy income-class-derived status
    (:func:`_derive_economic_status` semantics) and are counted; the
    primary/fallback rate is logged (WARNING above
    :data:`STATUS_HHTYPE_FALLBACK_WARN_RATE`).
    """
    from braunschweig.data.mid.status_by_hhtype import (
        STATUS_CATEGORIES,
        RS7_TO_RAUMTYP_KEY,
        BUNDESLAND_NIEDERSACHSEN,
        load_status_by_hhtype_bundesland,
        load_status_by_hhtype_raumtyp,
        map_households_to_hhtype,
        region_status_probabilities,
    )
    from braunschweig.data.bbsr.regiostar import ars_to_ags8

    assert tuple(STATUS_CATEGORIES) == ECONOMIC_STATUS_CATEGORIES

    df_bund = load_status_by_hhtype_bundesland(data_path)
    df_raum = load_status_by_hhtype_raumtyp(data_path)

    # Per-person MiD Haushaltstyp key.
    hhtype = map_households_to_hhtype(df_persons)

    # Per-person RS7 raumtyp key (via commune_id -> AGS-8 -> RegioStaR-7).
    rs7_by_ags8 = dict(zip(
        df_regiostar["commune_id"].astype(str),
        df_regiostar["regiostar7"].astype("Int64"),
    ))
    if "commune_id" in df_persons.columns:
        ags8 = df_persons["commune_id"].astype(str).map(ars_to_ags8)
        rs7 = ags8.map(rs7_by_ags8)
    else:
        rs7 = pd.Series([pd.NA] * len(df_persons), index=df_persons.index)
    raumtyp_key = rs7.map(
        lambda c: RS7_TO_RAUMTYP_KEY.get(int(c)) if pd.notna(c) else None
    )

    # Pre-compute the per-(hhtype, raumtyp_key) status probability vectors so we
    # sample each combination only once (the population has ~1.13M persons but
    # only 12 hhtypes x 8 raumtyp states = 96 distinct probability vectors).
    n_status = len(ECONOMIC_STATUS_CATEGORIES)
    status_arr = np.asarray(ECONOMIC_STATUS_CATEGORIES, dtype=object)
    prob_cache: dict[str | None, dict[str, np.ndarray]] = {}

    def _probs_for(raumtyp_region):
        if raumtyp_region not in prob_cache:
            prob_cache[raumtyp_region] = region_status_probabilities(
                df_bund, df_raum, BUNDESLAND_NIEDERSACHSEN, raumtyp_region
            )
        return prob_cache[raumtyp_region]

    # Build the per-person probability matrix; rows with an unmapped hhtype are
    # marked as fallback and filled later from the legacy income-class status.
    n = len(df_persons)
    probs = np.zeros((n, n_status), dtype=float)
    hhtype_arr = hhtype.to_numpy()
    raumtyp_arr = raumtyp_key.to_numpy()
    fallback_mask = np.zeros(n, dtype=bool)

    # Group by (hhtype, raumtyp_key) for vectorised assignment.
    grp = pd.DataFrame({"hhtype": hhtype_arr, "raumtyp": raumtyp_arr})
    for (ht, rk), idx in grp.groupby(
        ["hhtype", "raumtyp"], dropna=False, sort=False
    ).groups.items():
        rows = np.asarray(idx)
        if ht is None or (isinstance(ht, float) and pd.isna(ht)):
            fallback_mask[rows] = True
            continue
        vecs = _probs_for(rk if (rk is not None and not (isinstance(rk, float) and pd.isna(rk))) else None)
        vec = vecs.get(ht)
        if vec is None:
            fallback_mask[rows] = True
            continue
        probs[rows, :] = vec

    n_fallback = int(fallback_mask.sum())
    n_primary = n - n_fallback
    fallback_rate = (n_fallback / n) if n else 0.0

    # Sample status from the per-person vectors (dedicated, distinct RNG offset).
    rng = np.random.RandomState(random_seed + 60413)
    status_sampled = np.empty(n, dtype=object)
    primary_rows = np.where(~fallback_mask)[0]
    if primary_rows.size:
        cdf = np.cumsum(probs[primary_rows], axis=1)
        u = rng.random_sample(primary_rows.size)
        choice = (u[:, None] < cdf).argmax(axis=1)
        status_sampled[primary_rows] = status_arr[choice]

    # Fallback rows: keep the legacy income-class-derived status.
    if n_fallback:
        legacy = (
            df_persons["household_income"].astype(str)
            .map(ECONOMIC_STATUS_BY_INCOME_CLASS)
        )
        status_sampled[fallback_mask] = legacy.to_numpy()[fallback_mask]

    df_persons["economic_status"] = status_sampled
    df_persons.attrs["economic_status_hhtype_primary_count"] = n_primary
    df_persons.attrs["economic_status_hhtype_fallback_count"] = n_fallback
    df_persons.attrs["economic_status_hhtype_fallback_rate"] = fallback_rate

    level = "WARNING: " if fallback_rate > STATUS_HHTYPE_FALLBACK_WARN_RATE else ""
    print(
        f"[braunschweig.enriched] {level}economic_status from MiD hhtype x region: "
        f"primary {n_primary}/{n} ({1 - fallback_rate:.2%}), "
        f"fallback (unclassifiable household -> legacy income-class status) "
        f"{n_fallback} ({fallback_rate:.2%})."
    )
    print(
        "[braunschweig.enriched] economic_status share = "
        + ", ".join(
            f"{k}={v:.1%}"
            for k, v in pd.Series(status_sampled)
            .value_counts(normalize=True)
            .reindex(ECONOMIC_STATUS_CATEGORIES).fillna(0.0).items()
        )
    )

    # Re-derive the income EUR-class from the sampled status so income and status
    # agree (status drives income now). high_income follows; household_income_eur
    # is computed downstream from this re-derived class via the INKAR scaling.
    df_persons["household_income"] = pd.Series(status_sampled, index=df_persons.index).map(
        INCOME_CLASS_BY_ECONOMIC_STATUS
    )
    df_persons["high_income"] = df_persons["household_income"] == "5000+"
    return df_persons


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
    ):
        df_persons = df_persons.drop(columns=["commune_id"])

    return df_persons


# --- Braunschweig-specific -------------------------------------------------
# MiD 2023 Tabelle H7 / H12.3 vehicle counts + INKAR-scaled income.
# The numeric reference values live in CSV files under
#   eqasim-data/data/braunschweig/mid/mid2023_H{7,12_3}_*.csv
# and are written by scripts/seed_mid_constraint_tables.py. They are
# loaded lazily in execute() through braunschweig.data.mid.reference_tables.


# Map inside_<kreis> boolean flags to the 5-digit Kreis ARS code (AGS-5).
INSIDE_FLAG_TO_ARS5 = {
    "inside_braunschweig":  "03101",
    "inside_salzgitter":    "03102",
    "inside_wolfsburg":     "03103",
    "inside_gifhorn":       "03151",
    "inside_goslar":        "03153",
    "inside_helmstedt":     "03154",
    "inside_peine":         "03157",
    "inside_wolfenbuettel": "03158",
}


def _derive_kreis_ars5(df_persons):
    """Return a per-person AGS-5 Series derived from inside_<kreis> flags."""
    ars5 = np.full(len(df_persons), "", dtype=object)
    for flag, code in INSIDE_FLAG_TO_ARS5.items():
        if flag not in df_persons.columns:
            continue
        flag_mask = df_persons[flag].fillna(False).astype(bool).values
        ars5 = np.where((ars5 == "") & flag_mask, code, ars5)
    return pd.Series(ars5, index=df_persons.index)


def _sample_counts(df_persons, column, values, region_shares, kreis_shares,
                   random, kreis=None):
    """Sample an integer count per person given a Kreis-indexed share table.

    ``kreis`` is the per-person AGS-5 Series from :func:`_derive_kreis_ars5`.
    It is accepted as an argument so the caller can derive it once and reuse it
    across the (cars, bikes, income) calls instead of rebuilding the object-dtype
    array on every call; passing ``None`` derives it locally (output-identical).

    Fallback transparency: each Kreis's share vector comes either from the
    PRIMARY per-Kreis lookup in ``kreis_shares`` (the MiD H7 / H12.3 per-Kreis
    table) or, when the Kreis ARS-5 code is absent from that table, from the
    region-wide ``region_shares`` FALLBACK. Because cars and bikes are the most
    widely consumed person attributes, a Kreis-ARS format mismatch (e.g. AGS-5
    vs AGS-8) would silently route ALL persons to the region distribution. The
    primary/fallback split is therefore counted (persons and distinct Kreis
    codes) and the fallback rate is logged; a rate above
    :data:`KREIS_SHARE_FALLBACK_WARN_RATE` is escalated to a WARNING that lists
    the unmapped Kreis codes. The counts are also stored on ``df_persons.attrs``
    (keyed by ``column``) so callers/tests can assert the primary lookup was
    taken without a signature change. This logging is purely observational: it
    does not alter the share vectors, the sampling, or the RNG consumption.
    """
    if kreis is None:
        kreis = _derive_kreis_ars5(df_persons)
    result = np.zeros(len(df_persons), dtype=int)
    n_total = int(len(df_persons))
    n_fallback = 0
    fallback_kreise = []
    # Iterate the Kreis codes in a deterministic (sorted) order. ``set()``
    # iteration order over Python strings depends on PYTHONHASHSEED, so it
    # would vary the order in which the shared ``random`` stream is consumed
    # across the Kreise and thus make the per-person draws non-reproducible.
    # ``sorted`` pins the consumption order (reproducible result).
    for ars in sorted(kreis.unique()):
        is_fallback = ars not in kreis_shares
        shares = kreis_shares.get(ars, region_shares)
        shares = np.asarray(shares, dtype=float)
        shares /= shares.sum()
        mask = (kreis == ars).values
        n = int(mask.sum())
        if n == 0:
            continue
        if is_fallback:
            n_fallback += n
            fallback_kreise.append(ars)
        result[mask] = random.choice(values, size=n, p=shares)
    df_persons[column] = result

    n_primary = n_total - n_fallback
    fallback_rate = (n_fallback / n_total) if n_total else 0.0
    df_persons.attrs[f"{column}_kreis_share_primary_count"] = n_primary
    df_persons.attrs[f"{column}_kreis_share_fallback_count"] = n_fallback
    df_persons.attrs[f"{column}_kreis_share_fallback_rate"] = fallback_rate
    df_persons.attrs[f"{column}_kreis_share_fallback_kreise"] = list(fallback_kreise)
    if n_fallback:
        level = (
            "WARNING: "
            if fallback_rate > KREIS_SHARE_FALLBACK_WARN_RATE
            else ""
        )
        print(
            f"[braunschweig.enriched] {level}{column} per-Kreis share fallback "
            f"used for {n_fallback}/{n_total} persons "
            f"({fallback_rate:.2%}); primary per-Kreis lookup hit {n_primary}. "
            f"Unmapped Kreis ARS-5 codes {sorted(fallback_kreise)}; "
            f"using region-wide share distribution."
        )
    else:
        print(
            f"[braunschweig.enriched] {column} per-Kreis share PRIMARY lookup "
            f"hit all {n_primary}/{n_total} persons (fallback rate 0.00%)."
        )


def _sample_vehicle_counts(df_persons, data_path, random_seed, kreis=None):
    """Sample per-person ``number_of_cars`` / ``number_of_bicycles`` (MiD H7/H12.3).

    Extracted verbatim from the former outer ``execute`` body so the vehicle
    counts can be sampled BEFORE ``car_availability`` (the A-REORDER causal
    chain) without changing the draws. The RNG offset (+91731) and the
    cars-then-bikes consumption order on the SHARED stream are preserved exactly,
    so this helper is output-identical to the legacy outer-execute sampling
    regardless of WHERE it is called in the stage (the +91731 stream is
    independent of the licence/PT/binarisation streams). ``kreis`` is the
    per-person AGS-5 Series; passing ``None`` derives it locally.
    """
    if kreis is None:
        kreis = _derive_kreis_ars5(df_persons)
    cars_by_kreis, cars_region, cars_values = load_kreis_share_table(
        data_path, "mid2023_H7_cars_by_kreis.csv")
    bikes_by_kreis, bikes_region, bikes_values = load_kreis_share_table(
        data_path, "mid2023_H12_3_bikes_by_kreis.csv")

    random = np.random.RandomState(random_seed + 91731)
    _sample_counts(df_persons, "number_of_cars", cars_values,
                   cars_region, cars_by_kreis, random, kreis=kreis)
    _sample_counts(df_persons, "number_of_bicycles", bikes_values,
                   bikes_region, bikes_by_kreis, random, kreis=kreis)
    return df_persons


def _largest_remainder(shares, total):
    """Integer counts that sum EXACTLY to ``total`` from fractional ``shares``.

    Hamilton / largest-remainder apportionment: floor the fractional quotas, then
    hand the leftover units to the categories with the largest fractional
    remainders (ties broken by index, so the result is deterministic). Used to
    turn the per-Kreis MiD-H7 share vector into integer household-count targets
    whose sum is the Kreis household count.
    """
    shares = np.asarray(shares, dtype=float)
    s = shares.sum()
    quotas = (shares / s) * total if s > 0 else np.zeros_like(shares)
    floors = np.floor(quotas).astype(np.int64)
    deficit = int(total - floors.sum())
    if deficit > 0:
        order = np.argsort(-(quotas - floors), kind="stable")
        floors[order[:deficit]] += 1
    return floors


def _assign_to_column_targets(probs, target_counts, rng):
    """Assign each row (household) to exactly one column (car count), EXACTLY
    matching ``target_counts`` per column while respecting the raked row pmf.

    ``probs`` is the per-household ``(n, k)`` pmf matrix already raked so its
    column sums equal ``target_counts`` (continuous). This converts it to a hard
    integer assignment whose column totals equal ``target_counts`` exactly: each
    household, processed in a seeded random order, claims the still-available
    category for which its raked probability is highest (a category drops out of
    the candidate set once its integer quota is filled). Because
    ``sum(target_counts) == n`` every household is placed and every quota is met,
    so the per-Kreis marginal is the H7 control exactly. Returns an int array of
    column indices, one per row.
    """
    n = probs.shape[0]
    remaining = np.array(target_counts, dtype=np.int64).copy()
    assigned = np.full(n, -1, dtype=np.int64)
    # Seeded order so the (rare) ties between equally-probable households are
    # broken reproducibly without biasing toward low-index households.
    order = rng.permutation(n)
    for h in order:
        row = probs[h].copy()
        # Mask out categories whose quota is already exhausted.
        row[remaining <= 0] = -np.inf
        c = int(np.argmax(row))
        assigned[h] = c
        remaining[c] -= 1
    return assigned


def _sample_cars_income_aware(df_persons, data_path, random_seed, df_regiostar):
    """Draw a household ``number_of_cars`` coupled to income/Haushaltstyp/raumtyp,
    then rake the per-Kreis distribution back to the MiD-H7 Kreis control.

    Replaces the legacy per-Kreis MiD-H7 ``number_of_cars`` (already in
    ``df_persons``) with a draw from the MiD-coupled pmf
    ``P(num_cars | hhtype, status, raumtyp)``
    (:func:`braunschweig.data.mid.cars_by_status.cars_probabilities`), then -- per
    Kreis -- rakes the household pmf matrix to the integer MiD-H7 target counts
    (:func:`_largest_remainder` of the H7 share vector x Kreis household count) and
    assigns each household to a single car count exactly matching those targets
    (:func:`_assign_to_column_targets`). The result therefore keeps each Kreis's
    0/1/2/3+ household totals EXACTLY at the H7 control while re-allocating WITHIN
    the Kreis by income / household type / raumtyp.

    Uses a dedicated RNG offset (+47629) so it does NOT touch the +91731
    vehicle-count stream: the legacy cars/bikes draw in
    :func:`_sample_vehicle_counts` still runs first (preserving the bicycle draw
    byte-for-byte), and this function only OVERWRITES ``number_of_cars``. With the
    feature OFF this function is never called, so the OFF path is byte-identical.

    ``number_of_cars`` is a HOUSEHOLD quantity: one draw per household, broadcast
    to every member (exactly as the household-consistent max in the legacy /A5
    path). Fallback transparency: households whose Haushaltstyp is unclassifiable
    or whose ``(hhtype, status)`` MiD cell is absent keep the legacy per-Kreis H7
    draw for the PMF (so the primary MiD coupling is observable); the
    primary/fallback rate is logged and stored on ``df_persons.attrs``.
    """
    from braunschweig.data.mid.cars_by_status import (
        CAR_COUNT_CATEGORIES,
        RS7_TO_RAUMTYP_KEY,
        cars_probabilities_table,
        load_cars_by_raumtyp,
        load_cars_by_status_hhtype,
    )
    from braunschweig.data.mid.status_by_hhtype import _classify_household
    from braunschweig.data.bbsr.regiostar import ars_to_ags8

    cats = np.asarray(CAR_COUNT_CATEGORIES, dtype=np.int64)
    n_cat = len(cats)
    cat_index = {int(c): i for i, c in enumerate(cats)}

    df_hhtype = load_cars_by_status_hhtype(data_path)
    df_raumtyp = load_cars_by_raumtyp(data_path)
    base_map, by_region, national = cars_probabilities_table(df_hhtype, df_raumtyp)

    # Per-Kreis MiD-H7 control (the marginal the rake must preserve exactly).
    cars_by_kreis, cars_region, _ = load_kreis_share_table(
        data_path, "mid2023_H7_cars_by_kreis.csv")

    # Legacy per-household car count (already sampled in _sample_vehicle_counts),
    # taken household-consistent via the per-household max -- this is the PMF
    # fallback when the MiD coupling is unavailable for a household.
    legacy_cars = (
        pd.Series(df_persons["number_of_cars"].to_numpy(), index=df_persons["household_id"])
        .groupby(level=0).max()
    )

    # Per-household Haushaltstyp (MiD classifier) and home raumtyp key.
    rs7_by_ags8 = dict(zip(
        df_regiostar["commune_id"].astype(str),
        df_regiostar["regiostar7"].astype("Int64"),
    ))
    has_hh_type = "hh_type" in df_persons.columns
    has_commune = "commune_id" in df_persons.columns
    age_num = pd.to_numeric(df_persons["age"], errors="coerce")

    # Build per-household attributes in one pass over household groups.
    work = pd.DataFrame({
        "household_id": df_persons["household_id"].to_numpy(),
        "age": age_num.to_numpy(),
        "economic_status": df_persons["economic_status"].astype(str).to_numpy(),
    })
    if has_hh_type:
        work["hh_type"] = df_persons["hh_type"].to_numpy()
    if has_commune:
        work["commune_id"] = df_persons["commune_id"].astype(str).to_numpy()

    hh_ids = []
    hh_pmf = []
    n_primary = 0
    n_fallback = 0
    fallback_reasons = {"unclassifiable": 0, "missing_cell": 0}

    for hid, grp in work.groupby("household_id", sort=False):
        ages = grp["age"].to_numpy()
        hh_type = (
            str(grp["hh_type"].iloc[0])
            if has_hh_type and pd.notna(grp["hh_type"].iloc[0]) else None
        )
        hhtype_key = _classify_household(len(grp), ages, hh_type)
        status = str(grp["economic_status"].iloc[0])

        # Home raumtyp key (commune_id -> AGS-8 -> RS7 -> raumtyp key).
        raumtyp_key = None
        if has_commune:
            ags8 = ars_to_ags8(str(grp["commune_id"].iloc[0]))
            rs7 = rs7_by_ags8.get(ags8)
            if rs7 is not None and pd.notna(rs7):
                raumtyp_key = RS7_TO_RAUMTYP_KEY.get(int(rs7))

        pmf = None
        if hhtype_key is not None:
            base = base_map.get((hhtype_key, status))
            if base is not None:
                pmf = base.copy()
                if raumtyp_key is not None and raumtyp_key in by_region:
                    with np.errstate(divide="ignore", invalid="ignore"):
                        tilt = np.where(national > 1e-12, by_region[raumtyp_key] / national, 1.0)
                    tilted = pmf * tilt
                    total = tilted.sum()
                    if total > 0:
                        pmf = tilted / total

        if pmf is None:
            # FALLBACK: degenerate pmf at the legacy per-Kreis H7 draw for this
            # household, so it still carries an H7-consistent count into the rake
            # but the MiD coupling did not apply (observable, not silent).
            n_fallback += 1
            if hhtype_key is None:
                fallback_reasons["unclassifiable"] += 1
            else:
                fallback_reasons["missing_cell"] += 1
            pmf = np.zeros(n_cat, dtype=float)
            legacy_c = int(legacy_cars.get(hid, 0))
            pmf[cat_index.get(min(legacy_c, int(cats[-1])), 0)] = 1.0
        else:
            n_primary += 1

        hh_ids.append(hid)
        hh_pmf.append(pmf)

    hh_pmf = np.asarray(hh_pmf, dtype=float)
    hh_id_arr = np.asarray(hh_ids, dtype=object)

    # Per-household Kreis (one row per household; the per-person AGS-5 is
    # household-consistent because the home commune fixes the Kreis).
    kreis_person = _derive_kreis_ars5(df_persons)
    kreis_by_hh = (
        pd.Series(kreis_person.to_numpy(), index=df_persons["household_id"])
        .groupby(level=0).first()
    )
    hh_kreis = pd.Series(hh_id_arr).map(kreis_by_hh).to_numpy()

    # Per-Kreis rake to the H7 integer target counts, then exact assignment.
    rng = np.random.RandomState(random_seed + 47629)
    hh_cars = np.zeros(len(hh_id_arr), dtype=np.int64)
    n_rake_fallback_kreise = []
    for ars in sorted(pd.unique(hh_kreis)):
        rows = np.where(hh_kreis == ars)[0]
        n_hh = len(rows)
        if n_hh == 0:
            continue
        shares = cars_by_kreis.get(ars)
        if shares is None:
            shares = cars_region
            n_rake_fallback_kreise.append(ars)
        target = _largest_remainder(shares, n_hh)  # integer counts, sum = n_hh
        M = hh_pmf[rows]
        fitted = rake_2d(M, np.ones(n_hh), target.astype(float))
        assigned = _assign_to_column_targets(fitted, target, rng)
        hh_cars[rows] = cats[assigned]

    # Broadcast the household car count back to every person (household-consistent).
    cars_map = dict(zip(hh_id_arr, hh_cars))
    df_persons["number_of_cars"] = (
        df_persons["household_id"].map(cars_map).astype(np.int64).to_numpy()
    )

    n_hh_total = len(hh_id_arr)
    fallback_rate = (n_fallback / n_hh_total) if n_hh_total else 0.0
    df_persons.attrs["number_of_cars_income_aware_primary_count"] = n_primary
    df_persons.attrs["number_of_cars_income_aware_fallback_count"] = n_fallback
    df_persons.attrs["number_of_cars_income_aware_fallback_rate"] = fallback_rate
    df_persons.attrs["number_of_cars_income_aware_fallback_reasons"] = dict(fallback_reasons)
    df_persons.attrs["number_of_cars_income_aware_rake_fallback_kreise"] = list(
        n_rake_fallback_kreise)

    level = "WARNING: " if fallback_rate > CARS_INCOME_AWARE_FALLBACK_WARN_RATE else ""
    print(
        f"[braunschweig.enriched] {level}income-aware number_of_cars: MiD-coupled "
        f"PMF primary {n_primary}/{n_hh_total} households "
        f"({1 - fallback_rate:.2%}), fallback (legacy H7 PMF) {n_fallback} "
        f"({fallback_rate:.2%}); reasons {dict(fallback_reasons)}. "
        f"Per-Kreis totals raked to the MiD-H7 control"
        + (f" (region-fallback shares for Kreise {sorted(n_rake_fallback_kreise)})."
           if n_rake_fallback_kreise else " (per-Kreis H7 shares, all Kreise).")
    )
    return df_persons


def _binarise_availability(df_persons, random_seed, apply_car=True):
    """Binarise car/bike availability from fractional IPF weights (Bernoulli).

    Converts the fractional IPF ``car_availability`` / ``bicycle_availability``
    weights produced by the P19 / P22 raking into the categorical {none, all}
    via a Bernoulli draw on a DISTINCT RNG offset (+23761), exactly as the legacy
    in-line block did. Extracted into a helper so the A-REORDER moves the
    vehicle-count sampling earlier WITHOUT changing this draw (the +23761 stream
    is independent of the +91731 vehicle-count stream).

    ``apply_car`` controls only whether the CAR result is written: with A5
    (consistent car_availability) ON, ``car_availability`` is derived separately
    AFTER this call, so the car uniform must still be drawn here (to keep the
    bicycle draw byte-identical to the OFF path) but its result discarded. The
    car uniform is ALWAYS consumed first, then the bicycle uniform second, so the
    bicycle binarisation is byte-identical regardless of ``apply_car``.
    """
    random = np.random.RandomState(random_seed + 23761)

    u = random.random_sample(len(df_persons))
    if apply_car:
        selection = u < df_persons["car_availability"]
        df_persons["car_availability"] = "none"
        df_persons.loc[selection, "car_availability"] = "all"
        df_persons["car_availability"] = df_persons["car_availability"].astype("category")

    u = random.random_sample(len(df_persons))
    selection = u < df_persons["bicycle_availability"]
    df_persons["bicycle_availability"] = "none"
    df_persons.loc[selection, "bicycle_availability"] = "all"
    df_persons["bicycle_availability"] = df_persons["bicycle_availability"].astype("category")
    return df_persons


# --- A6: condition pt_subscription on student / employment status -------------
def _condition_pt_subscription_probs(pt_probs, df_persons, pt_categories):
    """Apply the DATA-FREE logical PT-ticket constraints to the per-person
    probability matrix and re-normalise (A6).

    The MiD P24.1 combined category ``jobticket_semesterticket`` (Jobticket =
    employer-subsidised pass, Semesterticket = student Solidarmodell pass) is a
    work/study-bound ticket: it can only be held by a person who is ``employed``
    OR ``studies`` (see ``PT_TICKET_WORK_STUDY_BOUND``; MiD reports the two as one
    column, so ``employed OR studies`` is the tightest defensible rule). For every
    person who is neither employed nor studying, that category's probability is
    set to zero and the remaining vector re-normalised so it still sums to 1, then
    that person samples among the categories they CAN actually hold.

    Eligible persons (employed or studying) and the remaining categories are left
    untouched, so the P24.1 marginal stays matched within tolerance -- only the
    redistributed work/study mass of the non-eligible minority drifts.

    A defensive guard: if zeroing leaves an all-zero row (a degenerate vector
    whose mass sat entirely on the disallowed category), the person falls back to
    ``fahre_nie`` deterministically rather than producing an un-normalisable row.
    The fallback count is recorded in ``df_persons.attrs`` for traceability.

    Parameters
    ----------
    pt_probs : np.ndarray
        Per-person probability matrix, shape ``(n_persons, n_categories)``.
    df_persons : pd.DataFrame
        Must carry boolean ``employed`` and ``studies`` columns.
    pt_categories : sequence[str]
        The PT ticket categories in column order (``PT_TICKET_CATEGORIES``).

    Returns
    -------
    np.ndarray
        The conditioned, re-normalised probability matrix (a copy).
    """
    from braunschweig.data.mid.reference_tables import PT_TICKET_WORK_STUDY_BOUND

    out = pt_probs.copy()
    categories = list(pt_categories)
    idx_fahre_nie = categories.index("fahre_nie")
    work_study_idx = [categories.index(c) for c in PT_TICKET_WORK_STUDY_BOUND
                      if c in categories]

    employed = df_persons["employed"].astype(bool).to_numpy()
    studies = df_persons["studies"].astype(bool).to_numpy()
    not_work_study = ~(employed | studies)

    # Zero the work/study-bound categories for persons who are neither employed
    # nor studying.
    if work_study_idx and not_work_study.any():
        out[np.ix_(not_work_study, work_study_idx)] = 0.0

    # Re-normalise rows. Rows that became all-zero (mass had sat entirely on the
    # disallowed category) fall back to fahre_nie deterministically.
    row_sums = out.sum(axis=1)
    degenerate = row_sums <= 0.0
    fallback_count = int(np.count_nonzero(degenerate))
    if fallback_count > 0:
        out[degenerate, :] = 0.0
        out[degenerate, idx_fahre_nie] = 1.0
        row_sums = out.sum(axis=1)
    out = out / row_sums[:, None]

    df_persons.attrs["pt_subscription_workstudy_zeroed_count"] = int(
        np.count_nonzero(not_work_study) if work_study_idx else 0
    )
    df_persons.attrs["pt_subscription_degenerate_fallback_count"] = fallback_count
    return out


# Light rake of the re-weighted PT probabilities back to the overall P24.1
# marginal: how many fixed-point iterations to run (each scales every category by
# target_marginal / current_marginal and re-normalises). Five passes drive the
# column mean to within ~0.1 pp of the target on representative input; the result
# is reported in ``df_persons.attrs`` for traceability.
_PT_CAR_MARGIN_RAKE_ITERATIONS = 5


def _apply_car_availability_pt_margin(
    pt_probs, df_persons, pt_categories, pt_by_car_availability,
):
    """Impose the MiD carless<->PT-pass coupling on the per-person PT probability
    matrix while preserving the overall P24.1 marginal (A6, data-dependent).

    The 3-margin {Kreis, sex, age} IPF above does NOT know the car-availability
    dimension, so the carless<->PT-pass correlation observed in MiD P24.1 x Pkw-
    Verfuegbarkeit is absent from ``pt_probs``. This helper re-weights each
    person's vector by the Bayes factor

        factor(person, ticket) = P(ticket | car_availability_of_person)
                                 / P(ticket)

    where ``P(ticket)`` is the population mean of ``pt_probs`` over the persons
    that carry a usable car-availability value (the "anchor" pool). Multiplying
    by this factor tilts each person toward the ticket types that are over-
    represented in their car-availability group and away from the under-
    represented ones, then the vector is re-normalised. To keep the AGGREGATE
    P24.1 marginal matched (the IPF target), the re-weighted matrix is then lightly
    raked: each category is scaled by ``target_marginal / current_marginal`` and
    the rows re-normalised, repeated :data:`_PT_CAR_MARGIN_RAKE_ITERATIONS` times.
    The rake adjusts only the column LEVELS, so the within-person carless tilt is
    preserved while the column means converge back to the pre-coupling marginal.

    Only persons whose ``car_availability`` maps to a key present in the cross-tab
    are re-weighted; persons with an unusable / missing car-availability value
    keep their original vector (counted + logged -- this is the documented primary
    vs. fallback split, no silent fallback). The P24.1 marginal target is computed
    over the anchor pool ONLY, so the unchanged fallback rows do not bias it.

    Parameters
    ----------
    pt_probs : np.ndarray
        Per-person probability matrix, shape ``(n_persons, n_categories)``.
    df_persons : pd.DataFrame
        Must carry a ``car_availability`` column whose values are in
        ``{none, some, all}`` (categorical, A5 ON) or ``{none, all}`` (A5 OFF).
    pt_categories : sequence[str]
        The PT ticket categories in column order (``PT_TICKET_CATEGORIES``).
    pt_by_car_availability : dict[str, np.ndarray]
        ``{car_availability -> P(ticket | car_availability)}`` from the MiD
        cross-tab (each vector sums to 1, column order == ``pt_categories``).

    Returns
    -------
    np.ndarray
        The re-weighted, marginal-preserving probability matrix (a copy).
    """
    out = pt_probs.copy()
    n_cats = len(pt_categories)
    eps = 1e-12

    car_values = df_persons["car_availability"].astype(str).to_numpy()
    # Anchor pool: persons whose car-availability is covered by the cross-tab.
    anchor = np.zeros(len(out), dtype=bool)
    for key in pt_by_car_availability:
        anchor |= car_values == key

    n_anchor = int(np.count_nonzero(anchor))
    n_total = len(out)
    df_persons.attrs["pt_subscription_car_margin_primary_count"] = n_anchor
    df_persons.attrs["pt_subscription_car_margin_fallback_count"] = n_total - n_anchor
    df_persons.attrs["pt_subscription_car_margin_fallback_rate"] = (
        (n_total - n_anchor) / n_total if n_total else 0.0
    )

    if n_anchor == 0:
        # No person carries a usable car-availability value: nothing to couple.
        # Loudly surfaced by the caller (fallback rate == 100%).
        return out

    # Population ticket marginal over the anchor pool BEFORE re-weighting -- this
    # is the P24.1 target the rake restores.
    target_marginal = out[anchor].mean(axis=0)
    target_marginal = target_marginal / max(target_marginal.sum(), eps)

    # Bayes re-weight: multiply each anchor person's vector by
    # P(ticket | their car_availability) / P(ticket).
    safe_marginal = np.maximum(target_marginal, eps)
    for key, cond_vec in pt_by_car_availability.items():
        f = anchor & (car_values == key)
        if not f.any():
            continue
        factor = np.asarray(cond_vec, dtype=float) / safe_marginal
        out[f] *= factor[None, :]

    # Re-normalise the re-weighted rows.
    row_sums = out[anchor].sum(axis=1, keepdims=True)
    row_sums[row_sums <= 0.0] = 1.0
    out[anchor] = out[anchor] / row_sums

    # Light rake back to the pre-coupling P24.1 marginal (column-level only).
    for _ in range(_PT_CAR_MARGIN_RAKE_ITERATIONS):
        current = out[anchor].mean(axis=0)
        scale = np.where(current > eps, target_marginal / np.maximum(current, eps), 1.0)
        out[anchor] *= scale[None, :]
        row_sums = out[anchor].sum(axis=1, keepdims=True)
        row_sums[row_sums <= 0.0] = 1.0
        out[anchor] = out[anchor] / row_sums

    # Final marginal-deviation diagnostic (max abs over categories, in pp).
    final_marginal = out[anchor].mean(axis=0)
    df_persons.attrs["pt_subscription_car_margin_max_dev_pp"] = float(
        np.max(np.abs(final_marginal - target_marginal)) * 100.0
    )
    assert n_cats == out.shape[1]  # column order preserved
    return out


# --- A5: consistent car_availability ----------------------------------------
# Categorical car_availability values (eqasim core vocabulary). The Java
# mode-choice side and the MATSim writers treat the value as an opaque string,
# so all three pass through unchanged; "some" is fully supported (the eqasim
# core itself emits it, see synthesis/population/enriched.py:96-98).
CAR_AVAILABILITY_CATEGORIES = ("none", "some", "all")

# Fallback-rate threshold above which the per-(Kreis) P19 raking fallback in
# :func:`_derive_car_availability_consistent` is logged at WARNING level. The
# fallback fires only for a Kreis cell whose "some/all" capacity is too small to
# reach the P19 "jederzeit" (>=some) target after the hard floors are applied
# (e.g. almost every person already floored to "none" by the carless/licence
# constraints); such a cell keeps the conditional assignment and is counted.
CAR_AVAILABILITY_RAKE_FALLBACK_WARN_RATE = 0.05


def _derive_car_availability_consistent(df_persons, mid, random_seed,
                                        minimum_age_car):
    """Derive a licence/car-consistent categorical ``car_availability`` (A5).

    Replaces the legacy free P19 IPF + all/none binarisation with a conditional
    derivation that respects the eqasim cars-vs-licences coupling and the MiD H7
    household car ownership, then rakes the residual to the MiD P19 "jederzeit"
    (= car available at any time) marginal so that aggregate target is still
    matched. The result is one of {none, some, all}:

    1. HARD FLOOR ``none`` if the person has no driving licence
       (``has_license == False``) OR lives in a 0-car household
       (household ``number_of_cars == 0``) OR is below the minimum age. These
       persons can never have a car available, so the P19 marginal is matched on
       the eligible remainder only.
    2. ``all`` if the household has at least as many cars as licensed adults
       (``number_of_cars >= n_licensed_adults``): no intra-household competition,
       every licensed adult can always take a car.
    3. otherwise ``some`` (more licensed adults than cars -> the car is shared /
       not always available).

    The conditional rule reproduces the eqasim-core all/some/none logic at
    HOUSEHOLD level (``synthesis/population/enriched.py:91-101``) but on the
    MiD-derived ``number_of_cars`` and the MiD-P17.1-derived ``has_license``,
    instead of the stale HTS-matched values the core saw before this stage
    overwrote them. On top of that, within each Kreis the ELIGIBLE persons
    (floor not applied) are raked toward the P19 per-zone "jederzeit" target:
    P19 reports the share of persons with a car available at any time, which maps
    to ``car_availability == "all"``. If the conditional "all" share in a Kreis
    is above the target, the surplus "all" persons (those in the most-competitive
    households first) are demoted to "some"; if it is below, "some" persons are
    promoted to "all". The carless/licenceless floor is never violated by the
    rake (only eligible persons move), so consistency is preserved while the
    aggregate P19 marginal is matched within the granularity of the eligible
    pool.

    Fallback transparency (CLAUDE.md): a Kreis whose eligible pool is too small
    to reach its P19 "all" target (target above the eligible share) cannot be
    fully raked; it keeps the conditional assignment and is counted. The
    primary/fallback split (persons and Kreis codes) is logged, WARNING above
    :data:`CAR_AVAILABILITY_RAKE_FALLBACK_WARN_RATE`.
    """
    n = len(df_persons)
    has_license = df_persons["has_license"].fillna(False).to_numpy().astype(bool)
    number_of_cars = df_persons["number_of_cars"].to_numpy().astype(np.int64)
    age = df_persons["age"].to_numpy()

    # Household aggregates: total cars per household are stored per-person but
    # represent a household quantity (MiD H7, "Autos im HH"); aggregate to a
    # single household value via the max so a single household car count drives
    # the coupling (drop_duplicates downstream takes one person's value, so the
    # per-person column must be made household-consistent here). Licensed adults
    # per household = count of has_license within the household.
    cars_by_hh = (
        pd.Series(number_of_cars, index=df_persons["household_id"])
        .groupby(level=0).max()
    )
    licensed_by_hh = (
        pd.Series(has_license.astype(np.int64), index=df_persons["household_id"])
        .groupby(level=0).sum()
    )
    hh_cars = df_persons["household_id"].map(cars_by_hh).to_numpy().astype(np.int64)
    hh_licensed = df_persons["household_id"].map(licensed_by_hh).to_numpy().astype(np.int64)

    # Make number_of_cars household-consistent (max within household) so the
    # downstream household table (drop_duplicates) reports a coherent count and
    # the A5 logic is reproducible regardless of which person is kept.
    df_persons["number_of_cars"] = hh_cars

    # Conditional base assignment.
    floor_none = (
        (~has_license)
        | (hh_cars == 0)
        | (age < minimum_age_car)
    )
    eligible = ~floor_none
    base = np.empty(n, dtype=object)
    base[floor_none] = "none"
    all_mask = eligible & (hh_cars >= np.maximum(hh_licensed, 1))
    some_mask = eligible & ~all_mask
    base[all_mask] = "all"
    base[some_mask] = "some"

    # P19 "jederzeit" per-zone targets (share of persons with a car at any time,
    # i.e. car_availability == "all"). Read from the same constraint list the
    # legacy IPF used; only the per-zone targets are needed for the rake.
    zone_target = {}
    for constraint in mid["car_availability_constraints"]:
        if "zone" in constraint:
            zone_target[constraint["zone"]] = float(constraint["target"])

    # A "competition score" orders eligible persons within a Kreis for promotion
    # / demotion: persons with the largest licensed-adults-minus-cars gap are the
    # most plausibly car-sharing ("some"); they are demoted first / promoted
    # last. Ties are broken by a seeded shuffle so the choice is reproducible but
    # not systematically biased by row order.
    rng = np.random.RandomState(random_seed + 41719)
    competition = (hh_licensed - hh_cars).astype(float)
    jitter = rng.random_sample(n)
    score = competition + jitter  # higher score -> more competition -> prefer "some"

    result = base.copy()
    n_total = n
    n_fallback = 0
    fallback_zones = []

    for zone, target in sorted(zone_target.items()):
        col = "inside_{}".format(zone)
        if col not in df_persons.columns:
            continue
        in_zone = df_persons[col].fillna(False).to_numpy().astype(bool)
        if not in_zone.any():
            continue
        zone_rows = np.where(in_zone)[0]
        n_zone = zone_rows.size
        # Target count of "all" persons in this zone (P19 share x persons).
        target_all = int(round(target * n_zone))
        zone_eligible = eligible[zone_rows]
        elig_rows = zone_rows[zone_eligible]
        n_elig = elig_rows.size
        cur_all_rows = elig_rows[result[elig_rows] == "all"]
        n_cur_all = cur_all_rows.size

        if target_all > n_elig:
            # Cannot reach the target: not enough eligible persons (the rest are
            # floored to "none"). Promote ALL eligible to "all" and record the
            # shortfall as fallback.
            result[elig_rows] = "all"
            n_fallback += n_zone
            fallback_zones.append(zone)
            continue

        if n_cur_all > target_all:
            # Demote the surplus "all" persons to "some": pick those with the
            # HIGHEST competition score (most car-sharing) first.
            surplus = n_cur_all - target_all
            order = cur_all_rows[np.argsort(-score[cur_all_rows], kind="stable")]
            result[order[:surplus]] = "some"
        elif n_cur_all < target_all:
            # Promote "some" persons to "all": pick those with the LOWEST
            # competition score (least car-sharing) first.
            deficit = target_all - n_cur_all
            some_rows = elig_rows[result[elig_rows] == "some"]
            order = some_rows[np.argsort(score[some_rows], kind="stable")]
            result[order[:deficit]] = "all"

    fallback_rate = (n_fallback / n_total) if n_total else 0.0
    df_persons.attrs["car_availability_rake_primary_count"] = n_total - n_fallback
    df_persons.attrs["car_availability_rake_fallback_count"] = n_fallback
    df_persons.attrs["car_availability_rake_fallback_rate"] = fallback_rate
    df_persons.attrs["car_availability_rake_fallback_zones"] = list(fallback_zones)

    df_persons["car_availability"] = pd.Categorical(
        result, categories=list(CAR_AVAILABILITY_CATEGORIES)
    )

    if n_fallback:
        level = (
            "WARNING: "
            if fallback_rate > CAR_AVAILABILITY_RAKE_FALLBACK_WARN_RATE
            else ""
        )
        print(
            f"[braunschweig.enriched] {level}car_availability P19 rake fallback "
            f"for {n_fallback}/{n_total} persons ({fallback_rate:.2%}) in zones "
            f"{sorted(fallback_zones)}: eligible pool too small to reach the P19 "
            f"'jederzeit' target after the carless/licence floor; kept conditional."
        )
    else:
        print(
            f"[braunschweig.enriched] car_availability P19 rake matched all "
            f"{n_total} persons (fallback rate 0.00%)."
        )
    print(
        "[braunschweig.enriched] consistent car_availability share = "
        + ", ".join(
            f"{k}={v:.1%}"
            for k, v in pd.Series(result).value_counts(normalize=True)
            .reindex(CAR_AVAILABILITY_CATEGORIES).fillna(0.0).items()
        )
    )
    return df_persons


# --- Distribution-based household_income_eur (income_eur_from_distribution) ---
#
# Open-top heavy-tail mean. The MiD top bracket "over_7000" is open-ended; a
# finite EUR value is drawn as 7000 * (1 + Exponential(mean)). The mean 0.4 puts
# the bracket's expected value at ~9800 EUR/month (a heavy but bounded-in-practice
# right tail), matching the German top-income shape better than a uniform draw
# against an arbitrary cap. Documented in
# braunschweig.data.mid.income_by_size (INCOME_BRACKET_BOUNDS_EUR open-top note).
INCOME_OPEN_TOP_EXP_MEAN_EUR_FRACTION = 0.4

# Plausible clamp for the open-top draw so a rare exponential outlier cannot push
# household_income_eur past the post-enrichment sanity range [100, 20000]. 18000
# leaves head-room below the 20000 hard cap after the INKAR fine tilt.
INCOME_OPEN_TOP_MAX_EUR = 18000.0

# Fallback-rate threshold above which the distribution-income per-cell bracket pmf
# fallback (NDS base cell absent for a household's hh_size -> uniform-over-brackets
# within the cell) is escalated to WARNING. Every synthetic hh_size 1..5+ has an
# NDS base cell, so a non-trivial rate signals a malformed reference table.
INCOME_DISTRIBUTION_FALLBACK_WARN_RATE = 0.01


def _income_class_from_eur(eur_values, class_midpoint_eur):
    """Map continuous EUR values onto the BMDV income EUR-class labels.

    The categorical ``household_income`` label must stay consistent with the
    continuous ``household_income_eur`` when the latter is drawn from the MiD
    distribution. We bucket each EUR value to the income class whose midpoint is
    nearest, using the midpoints between consecutive class midpoints as the bin
    edges (a 1-D nearest-midpoint classifier). The classes are ordered by
    midpoint so the mapping is monotone in EUR.

    ``class_midpoint_eur`` is the MiD H4 class-midpoint table
    (label -> midpoint EUR). Returns a numpy object array of class labels aligned
    to ``eur_values``.
    """
    labels = list(class_midpoint_eur.keys())
    midpoints = np.array([class_midpoint_eur[k] for k in labels], dtype=float)
    order = np.argsort(midpoints)
    labels_sorted = [labels[i] for i in order]
    mids_sorted = midpoints[order]
    # Bin edges = midpoints between consecutive class midpoints.
    edges = (mids_sorted[:-1] + mids_sorted[1:]) / 2.0
    idx = np.searchsorted(edges, np.asarray(eur_values, dtype=float), side="right")
    labels_arr = np.asarray(labels_sorted, dtype=object)
    return labels_arr[idx]


def _apply_distribution_income(df_persons, df_inkar, df_bundesland, df_raumtyp,
                               df_regiostar, class_midpoint_eur, random_seed,
                               kreis=None):
    """Draw ``household_income_eur`` from the MiD net-income distribution.

    Per household, an income bracket is drawn from
    ``P(bracket | hh_size, raumtyp)``
    (:func:`braunschweig.data.mid.income_by_size.income_bracket_probabilities`,
    Niedersachsen base + raumtyp within-NDS tilt). To stay coherent with the
    categorical ``economic_status`` anchor, the draw is RANK-ALIGNED within each
    ``(hh_size, raumtyp)`` cell: the cell's households are sorted by
    economic_status rank (very_low < .. < very_high, ties broken by a seeded
    jitter) and the bracket draws sorted within the cell are assigned so that
    higher-status households receive higher brackets. This keeps the bracket
    MARGINAL EXACTLY equal to the MiD distribution while making
    household_income_eur monotone in economic_status.

    Within the chosen bracket a continuous EUR value is drawn uniformly in
    ``[low, high)``; the open top bracket uses ``7000 * (1 + Exponential(mean))``
    (clamped to :data:`INCOME_OPEN_TOP_MAX_EUR`).

    The INKAR Kreis scale is applied only as a FINE adjustment: each value is
    multiplied by ``scale[kreis] / mean(scale over kreise)``. The MiD
    distribution is already REGIONAL (NDS + raumtyp), so multiplying by the raw
    INKAR scale (which is national-mean-relative) would double-count the regional
    level; dividing by the mean scale makes INKAR a within-region Kreis tilt with
    a regional mean of ~1.0 (no level shift, only the Wolfsburg/Goslar spread).

    Finally ``household_income`` (EUR-class label) and ``high_income`` are
    re-derived from the drawn EUR via :func:`_income_class_from_eur` so the
    categorical label stays consistent with the continuous value.
    ``economic_status`` is left UNCHANGED.

    A dedicated RNG offset (+72831) is used so the OFF path and all other streams
    are untouched.
    """
    from braunschweig.data.mid.income_by_size import (
        INCOME_BRACKET_BOUNDS_EUR,
        INCOME_BRACKET_CATEGORIES,
        RS7_TO_RAUMTYP_KEY,
        income_bracket_probabilities,
    )
    from braunschweig.data.bbsr.regiostar import ars_to_ags8

    n_brackets = len(INCOME_BRACKET_CATEGORIES)
    bracket_low = np.array(
        [INCOME_BRACKET_BOUNDS_EUR[b][0] for b in INCOME_BRACKET_CATEGORIES], dtype=float
    )
    # Closed-bracket high; open top marked with NaN so the EUR draw branches.
    bracket_high = np.array(
        [
            np.nan if INCOME_BRACKET_BOUNDS_EUR[b][1] is None
            else INCOME_BRACKET_BOUNDS_EUR[b][1]
            for b in INCOME_BRACKET_CATEGORIES
        ],
        dtype=float,
    )

    status_rank = {s: i for i, s in enumerate(ECONOMIC_STATUS_CATEGORIES)}

    # Per-household aggregation (income is a HOUSEHOLD quantity: one draw per
    # household, broadcast to every member). household_size / economic_status are
    # household-consistent already, but we take the first per group defensively.
    has_commune = "commune_id" in df_persons.columns
    rs7_by_ags8 = dict(zip(
        df_regiostar["commune_id"].astype(str),
        df_regiostar["regiostar7"].astype("Int64"),
    ))

    work = pd.DataFrame({
        "household_id": df_persons["household_id"].to_numpy(),
        "household_size": df_persons["household_size"].astype(str).to_numpy(),
        "economic_status": df_persons["economic_status"].astype(str).to_numpy(),
    })
    if has_commune:
        work["commune_id"] = df_persons["commune_id"].astype(str).to_numpy()

    hh = work.groupby("household_id", sort=False).first()
    hh_ids = hh.index.to_numpy()
    hh_size = hh["household_size"].to_numpy()
    hh_status = hh["economic_status"].to_numpy()

    # Per-household raumtyp key (commune_id -> AGS-8 -> RS7 -> raumtyp key).
    if has_commune:
        hh_ags8 = pd.Series(hh["commune_id"].to_numpy()).map(ars_to_ags8)
        hh_rs7 = hh_ags8.map(rs7_by_ags8)
        hh_raumtyp = hh_rs7.map(
            lambda c: RS7_TO_RAUMTYP_KEY.get(int(c)) if pd.notna(c) else None
        ).to_numpy()
    else:
        hh_raumtyp = np.array([None] * len(hh_ids), dtype=object)

    n_hh = len(hh_ids)
    rng = np.random.RandomState(random_seed + 72831)

    # Per-cell pmf cache (only n_sizes x n_raumtyp distinct cells).
    pmf_cache: dict[tuple[str, object], np.ndarray | None] = {}

    def _pmf_for(size_key, raumtyp_key):
        ck = (size_key, raumtyp_key)
        if ck not in pmf_cache:
            pmf_cache[ck] = income_bracket_probabilities(
                df_bundesland, df_raumtyp, size_key, raumtyp_key
            )
        return pmf_cache[ck]

    hh_bracket = np.full(n_hh, -1, dtype=np.int64)
    n_primary = 0
    n_fallback = 0

    # Seeded per-household jitter to break economic_status ties deterministically.
    jitter = rng.random_sample(n_hh)
    status_rank_arr = np.array(
        [status_rank.get(s, -1) for s in hh_status], dtype=float
    )

    # Group households by (hh_size, raumtyp) cell; draw + rank-align per cell.
    cell_df = pd.DataFrame({
        "row": np.arange(n_hh),
        "size": hh_size,
        "raumtyp": hh_raumtyp,
        "rank": status_rank_arr,
        "jitter": jitter,
    })
    for (size_key, raumtyp_key), grp in cell_df.groupby(["size", "raumtyp"], dropna=False, sort=False):
        rows = grp["row"].to_numpy()
        m = len(rows)
        pmf = _pmf_for(size_key, raumtyp_key if raumtyp_key is not None else None)
        if pmf is None:
            # FALLBACK: NDS base cell absent for this hh_size -> uniform over
            # brackets within the cell (observable, not silent).
            pmf = np.full(n_brackets, 1.0 / n_brackets)
            n_fallback += m
        else:
            n_primary += m
        # Draw m brackets from the cell pmf (this preserves the cell marginal in
        # expectation; the realised marginal is the multinomial draw of size m).
        u = rng.random_sample(m)
        cdf = np.cumsum(pmf)
        drawn = np.searchsorted(cdf, u, side="right")
        drawn = np.clip(drawn, 0, n_brackets - 1)
        # Rank-align: sort households by (status rank, jitter) ascending and the
        # drawn brackets ascending, then pair them so higher-status households
        # get higher brackets. This keeps the realised bracket marginal EXACTLY
        # the multinomial draw while making the bracket monotone in status.
        rank_key = grp["rank"].to_numpy() + grp["jitter"].to_numpy() * 1e-6
        hh_order = np.argsort(rank_key, kind="stable")
        bracket_sorted = np.sort(drawn, kind="stable")
        hh_bracket[rows[hh_order]] = bracket_sorted

    # Draw a continuous EUR value within each household's bracket.
    hh_low = bracket_low[hh_bracket]
    hh_high = bracket_high[hh_bracket]
    eur = np.empty(n_hh, dtype=float)
    is_open_top = np.isnan(hh_high)
    closed = ~is_open_top
    if closed.any():
        u_eur = rng.random_sample(int(closed.sum()))
        eur[closed] = hh_low[closed] + u_eur * (hh_high[closed] - hh_low[closed])
    if is_open_top.any():
        n_top = int(is_open_top.sum())
        exp_draw = rng.exponential(
            scale=INCOME_OPEN_TOP_EXP_MEAN_EUR_FRACTION, size=n_top
        )
        top_vals = hh_low[is_open_top] * (1.0 + exp_draw)
        eur[is_open_top] = np.minimum(top_vals, INCOME_OPEN_TOP_MAX_EUR)

    # INKAR FINE Kreis tilt (scale / mean(scale)), broadcast per household.
    if kreis is None:
        kreis = _derive_kreis_ars5(df_persons)
    kreis_by_hh = (
        pd.Series(kreis.to_numpy(), index=df_persons["household_id"])
        .groupby(level=0).first()
    )
    hh_kreis = pd.Series(hh_ids).map(kreis_by_hh).to_numpy()
    scale_lookup = dict(zip(df_inkar["ars5"], df_inkar["scale"]))
    raw_scale = pd.Series(hh_kreis).map(scale_lookup).astype(float)
    # Mean over the IN-SCOPE kreise present in the population (not the national
    # INKAR mean) so the fine tilt has a population mean of ~1.0.
    in_scope_scales = [
        scale_lookup[k] for k in pd.unique(hh_kreis)
        if k in scale_lookup
    ]
    mean_scale = float(np.mean(in_scope_scales)) if in_scope_scales else 1.0
    fine_tilt = (raw_scale / mean_scale).fillna(1.0).to_numpy()
    eur = eur * fine_tilt

    # Broadcast the household EUR back to every person.
    eur_by_hh = dict(zip(hh_ids, np.round(eur, 0)))
    df_persons["household_income_eur"] = (
        df_persons["household_id"].map(eur_by_hh).astype(float).to_numpy()
    )

    # Re-derive the EUR-class label + high_income from the continuous value so the
    # categorical household_income stays consistent (economic_status untouched).
    new_class = _income_class_from_eur(
        df_persons["household_income_eur"].to_numpy(), class_midpoint_eur
    )
    df_persons["household_income"] = new_class
    df_persons["high_income"] = df_persons["household_income"] == "5000+"

    fallback_rate = (n_fallback / n_hh) if n_hh else 0.0
    df_persons.attrs["income_distribution_primary_count"] = n_primary
    df_persons.attrs["income_distribution_fallback_count"] = n_fallback
    df_persons.attrs["income_distribution_fallback_rate"] = fallback_rate
    level = "WARNING: " if fallback_rate > INCOME_DISTRIBUTION_FALLBACK_WARN_RATE else ""
    print(
        f"[braunschweig.enriched] {level}distribution household_income_eur: "
        f"MiD-distribution primary {n_primary}/{n_hh} households "
        f"({1 - fallback_rate:.2%}), fallback (uniform-over-brackets, NDS base "
        f"cell absent) {n_fallback} ({fallback_rate:.2%}). "
        f"INKAR applied as a fine Kreis tilt (mean scale {mean_scale:.3f}). "
        f"mean income {np.mean(eur):.0f} EUR."
    )
    print(
        "[braunschweig.enriched] mean household_income_eur by economic_status = "
        + ", ".join(
            f"{s}={df_persons.loc[df_persons['economic_status'] == s, 'household_income_eur'].mean():.0f}"
            for s in ECONOMIC_STATUS_CATEGORIES
        )
    )
    return df_persons


def _apply_inkar_income_scale(df_persons, df_inkar, class_midpoint_eur,
                              kreis=None):
    """Add ``household_income_eur`` = class_midpoint * INKAR-scale[home_kreis].

    ``kreis`` is the per-person AGS-5 Series from :func:`_derive_kreis_ars5`,
    accepted so the caller can reuse a single derivation (passing ``None``
    derives it locally; output-identical).

    Fallback transparency: each person's € midpoint comes either from the
    PRIMARY per-class lookup in ``class_midpoint_eur`` (the MiD H4 class-midpoint
    table) or, when the person's ``household_income`` class label is absent from
    that table, from the hard-coded FALLBACK median midpoint
    (:data:`INCOME_MIDPOINT_FALLBACK_EUR`). The primary/fallback split is counted
    and the fallback rate is logged; a rate above
    :data:`INCOME_MIDPOINT_FALLBACK_WARN_RATE` is escalated to a WARNING and the
    distinct unmapped class labels are listed. The counts are also stored on
    ``df_persons.attrs`` so callers/tests can assert the primary mapping was
    taken without a signature change. This logging is purely observational: it
    does not alter any computed ``household_income_eur`` value.
    """
    midpoint = df_persons["household_income"].astype(str).map(class_midpoint_eur)
    n_total = int(len(midpoint))
    fallback_mask = midpoint.isna()
    n_fallback = int(fallback_mask.sum())
    n_primary = n_total - n_fallback
    fallback_rate = (n_fallback / n_total) if n_total else 0.0
    df_persons.attrs["income_midpoint_primary_count"] = n_primary
    df_persons.attrs["income_midpoint_fallback_count"] = n_fallback
    df_persons.attrs["income_midpoint_fallback_rate"] = fallback_rate
    if n_fallback:
        unknown_classes = sorted(
            df_persons.loc[fallback_mask.values, "household_income"]
            .astype(str).unique().tolist()
        )
        level = (
            "WARNING: "
            if fallback_rate > INCOME_MIDPOINT_FALLBACK_WARN_RATE
            else ""
        )
        print(
            f"[braunschweig.enriched] {level}income class-midpoint fallback used "
            f"for {n_fallback}/{n_total} persons "
            f"({fallback_rate:.2%}); primary lookup hit {n_primary}. "
            f"Unmapped income_class labels {unknown_classes}; "
            f"using median midpoint {INCOME_MIDPOINT_FALLBACK_EUR:.0f} EUR."
        )
        midpoint = midpoint.fillna(INCOME_MIDPOINT_FALLBACK_EUR)
    else:
        print(
            f"[braunschweig.enriched] income class-midpoint PRIMARY lookup hit "
            f"all {n_primary}/{n_total} persons (fallback rate 0.00%)."
        )

    if kreis is None:
        kreis = _derive_kreis_ars5(df_persons)
    scale_lookup = dict(zip(df_inkar["ars5"], df_inkar["scale"]))
    scale = kreis.map(scale_lookup).fillna(1.0).astype(float)

    df_persons["household_income_eur"] = (midpoint.astype(float) * scale).round(0)
    return df_persons


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
        df_regiostar_income = context.stage("braunschweig.data.bbsr.regiostar")
        df_income_bund = load_income_by_size_bundesland(data_path)
        df_income_raum = load_income_by_size_raumtyp(data_path)
        df_persons = _apply_distribution_income(
            df_persons, df_inkar, df_income_bund, df_income_raum,
            df_regiostar_income, class_midpoint_eur,
            context.config("random_seed"), kreis=kreis,
        )
    else:
        df_persons = _apply_inkar_income_scale(df_persons, df_inkar,
                                               class_midpoint_eur, kreis=kreis)

    # ``commune_id`` (kept by _execute_base only for the distribution income
    # raumtyp lookup) is dropped now so the returned schema matches the legacy
    # path (the income draw above is the last consumer).
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
