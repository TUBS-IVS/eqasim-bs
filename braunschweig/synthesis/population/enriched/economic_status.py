"""5-class MiD economic status derivation.

Two ways of deriving the per-person ``economic_status`` used by the enriched
population stage:

- :func:`_derive_economic_status` -- the legacy 1:1 mapping from the already-
  sampled ``household_income`` EUR-class (via ``ECONOMIC_STATUS_BY_INCOME_CLASS``).
- :func:`_derive_economic_status_from_hhtype` -- the ``status_from_hhtype``
  feature: sample status from MiD P(status | hhtype, region) (Bayes) and
  re-derive ``household_income`` from the sampled status.

Extracted verbatim from the stage module (``__init__``); see the package
docstring for the stage-level context.
"""

import pandas as pd
import numpy as np


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
