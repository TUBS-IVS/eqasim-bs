"""MiD 2023 Tabelle H7 / H12.3 vehicle counts and per-Kreis sampling.

Household ``number_of_cars`` / ``number_of_bicycles`` sampling from the MiD
2023 per-Kreis share tables, the income/household-type-aware
``number_of_cars`` overwrite (``cars_income_aware``), and the shared
Kreis-ARS / binarisation helpers they depend on. The numeric reference
values live in CSV files under
    eqasim-data/data/braunschweig/mid/mid2023_H{7,12_3}_*.csv
and are written by scripts/seed_mid_constraint_tables.py. They are loaded
lazily through braunschweig.data.mid.reference_tables.

Extracted verbatim from the stage module (``__init__``); see the package
docstring for the stage-level context.
"""

import pandas as pd
import numpy as np

from braunschweig.data.mid.reference_tables import load_kreis_share_table
from braunschweig.ipf.joint_age_size import rake_2d


# Fallback-rate threshold above which the per-Kreis share fallback in
# :func:`_sample_counts` (cars from MiD H7, bikes from MiD H12.3) is logged at
# WARNING level (fraction of persons in [0, 1]). Every in-scope Kreis ARS-5 code
# is expected to be present in the per-Kreis share table, so the region-wide
# fallback should never fire on correct data. A non-zero rate almost always
# signals a Kreis-ARS format mismatch (e.g. AGS-5 vs AGS-8) that would silently
# route persons to the region-wide distribution; even a small share is therefore
# escalated.
KREIS_SHARE_FALLBACK_WARN_RATE = 0.0


# Income-aware number_of_cars (cars_income_aware): rate of households that fall
# back from the MiD-coupled P(num_cars | hhtype, status, raumtyp) pmf to the
# per-Kreis MiD-H7 draw (unclassifiable household or missing MiD cell) above
# which the per-run log escalates to WARNING. The per-Kreis H7 marginal is held
# exactly by the rake regardless of the fallback rate; a high rate only means
# the MiD coupling reached few households (a signal the primary method is not
# working), so it is surfaced.
CARS_INCOME_AWARE_FALLBACK_WARN_RATE = 0.05


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
