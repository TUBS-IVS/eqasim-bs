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

    # Economic status from MiD household-type x region (Bayes). Default ON.
    # OFF -> exact legacy income-class-derived status (commit c65399d), so the
    # pipeline is byte-identical. The RegioStaR-7 stage (raumtyp tilt) is only a
    # dependency when the feature is on, so the OFF path keeps the legacy
    # dependency graph.
    if context.config("status_from_hhtype", True):
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
    if context.config("status_from_hhtype") and "commune_id" in _df_home_src.columns:
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

    # Sample categorical values from the IPF probabilities.
    #
    # Use a DISTINCT seed offset from the PT block above (which uses +8572):
    # the car/bike draw is a statistically independent attribute and must not
    # share the same uniform stream as the PT-subscription draw. Re-using +8572
    # here made the two draws identical uniforms (correlated by construction);
    # +23761 gives the car/bike block its own independent stream.
    random = np.random.RandomState(context.config("random_seed") + 23761)

    u = random.random_sample(len(df_persons))
    selection = u < df_persons["car_availability"]
    df_persons["car_availability"] = "none"
    df_persons.loc[selection, "car_availability"] = "all"
    df_persons["car_availability"] = df_persons["car_availability"].astype("category")

    u = random.random_sample(len(df_persons))
    selection = u < df_persons["bicycle_availability"]
    df_persons["bicycle_availability"] = "none"
    df_persons.loc[selection, "bicycle_availability"] = "all"
    df_persons["bicycle_availability"] = df_persons["bicycle_availability"].astype("category")

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

    # Urban-area resident flag (legacy Munich/Paris name kept for downstream
    # compatibility). Region-neutral default: only set when the regional
    # enricher attaches an "inside_<region>" column. Braunschweig overrides
    # this via the post-execute hook below (is_bs_resident → is_urban_resident).
    df_persons["is_munich_resident"] = (
        df_persons["inside_munich"]
        if "inside_munich" in df_persons.columns
        else False
    )

    # ``commune_id`` was only merged in to resolve the home RegioStaR-7 raumtyp
    # for the status_from_hhtype derivation; drop it so the returned schema is
    # unchanged vs the legacy path (the OFF path never reads it).
    if "commune_id" in df_persons.columns:
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

    # Load the H7 / H12.3 reference distributions from CSV (replaces the
    # legacy module-level CARS_BY_KREIS / BIKES_BY_KREIS dictionaries).
    data_path = context.config("data_path")
    cars_by_kreis, cars_region, cars_values = load_kreis_share_table(
        data_path, "mid2023_H7_cars_by_kreis.csv")
    bikes_by_kreis, bikes_region, bikes_values = load_kreis_share_table(
        data_path, "mid2023_H12_3_bikes_by_kreis.csv")

    # Derive the per-person Kreis AGS-5 once and reuse it across all three
    # consumers below (cars, bikes, income). The derivation rebuilds an
    # object-dtype array via a Python loop over the 8 political-prefix flags on
    # the full ~1.13M-row population, so computing it a single time avoids the
    # previous 3x redundant passes. Output is identical (same Kreis per row).
    kreis = _derive_kreis_ars5(df_persons)

    # Re-sample vehicle counts from MiD H7 / H12.3 instead of the hardcoded 1s.
    random = np.random.RandomState(context.config("random_seed") + 91731)
    _sample_counts(df_persons, "number_of_cars", cars_values,
                   cars_region, cars_by_kreis, random, kreis=kreis)
    _sample_counts(df_persons, "number_of_bicycles", bikes_values,
                   bikes_region, bikes_by_kreis, random, kreis=kreis)

    # INKAR-based EUR income (Kreis-specific shift on top of the MiD H4
    # regionless quintile distribution).
    df_inkar = context.stage("braunschweig.data.inkar.household_income")
    class_midpoint_eur = load_class_midpoint_eur(data_path)
    df_persons = _apply_inkar_income_scale(df_persons, df_inkar,
                                           class_midpoint_eur, kreis=kreis)

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
