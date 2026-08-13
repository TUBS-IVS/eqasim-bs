"""Housing tenure completeness attribute (synthesise_housing_tenure).

``housing_tenure`` in {rent, own, other} is sampled per household from
P(tenure | income_bracket, raumtyp) (MiD income x Wohnen, NDS base + raumtyp
tilt, Bayes-inverted; braunschweig.data.mid.tenure_by_income). It is a
COMPLETENESS attribute: written to the MATSim population but NOT consumed by the
simulation (like the HSN/TSN vehicle engine attributes). The income bracket is
resolved from the FINAL household_income_eur via the bracket EUR bounds (so the
tenure agrees with whichever income path -- distribution or class-midpoint --
produced the EUR value). A dedicated RNG offset (+83947) keeps it independent of
every other stream.

Extracted verbatim from the stage module (``__init__``); see the package
docstring for the stage-level context.
"""

import pandas as pd
import numpy as np


# Fallback-rate threshold above which the housing-tenure per-bracket pmf fallback
# (NDS Bayes inversion unavailable for a household's raumtyp cell -> the
# unconditional NDS tenure marginal) is escalated to WARNING.
HOUSING_TENURE_FALLBACK_WARN_RATE = 0.01


def _eur_to_bracket_index(eur_values):
    """Map continuous household income EUR onto the 10-bracket index.

    Uses the half-open bracket lower bounds from
    :data:`braunschweig.data.mid.income_by_size.INCOME_BRACKET_BOUNDS_EUR` (the
    same edges used to draw the EUR value). Values below the first edge fall in the
    lowest bracket; values at/above the top edge fall in the open top bracket.
    """
    from braunschweig.data.mid.income_by_size import (
        INCOME_BRACKET_BOUNDS_EUR,
        INCOME_BRACKET_CATEGORIES,
    )
    lows = np.array(
        [INCOME_BRACKET_BOUNDS_EUR[b][0] for b in INCOME_BRACKET_CATEGORIES],
        dtype=float,
    )
    edges = lows[1:]  # the boundary between consecutive brackets
    idx = np.searchsorted(edges, np.asarray(eur_values, dtype=float), side="right")
    return np.clip(idx, 0, len(INCOME_BRACKET_CATEGORIES) - 1)


def _apply_housing_tenure(df_persons, df_tenure_bund, df_tenure_raum,
                          df_regiostar, random_seed):
    """Sample the completeness attribute ``housing_tenure`` per household.

    Per household: resolve the income bracket from the FINAL
    ``household_income_eur`` (:func:`_eur_to_bracket_index`) and the home raumtyp
    (commune_id -> AGS-8 -> RS7 -> raumtyp key); draw a tenure in
    ``{rent, own, other}`` from ``P(tenure | bracket, raumtyp)``
    (:func:`braunschweig.data.mid.tenure_by_income.tenure_probabilities_given_income`,
    NDS base + raumtyp tilt, Bayes-inverted). The household tenure is broadcast to
    every member. A dedicated RNG offset (+83947) is used so all other streams are
    untouched.

    Fallback transparency (CLAUDE.md): a household whose raumtyp Bayes matrix is
    unavailable (status/tenure cell missing) falls back to the unconditional NDS
    tenure marginal (raumtyp_region=None); the primary/fallback rate is logged and
    stored on ``df_persons.attrs``.
    """
    from braunschweig.data.mid.tenure_by_income import (
        TENURE_CATEGORIES,
        RS7_TO_RAUMTYP_KEY,
        tenure_probabilities_given_income,
    )
    from braunschweig.data.bbsr.regiostar import ars_to_ags8

    tenure_arr = np.asarray(TENURE_CATEGORIES, dtype=object)
    n_tenure = len(TENURE_CATEGORIES)

    has_commune = "commune_id" in df_persons.columns
    rs7_by_ags8 = dict(zip(
        df_regiostar["commune_id"].astype(str),
        df_regiostar["regiostar7"].astype("Int64"),
    ))

    # Per-household income bracket + raumtyp (income/tenure are HOUSEHOLD quantities).
    bracket_person = _eur_to_bracket_index(df_persons["household_income_eur"].to_numpy())
    work = pd.DataFrame({
        "household_id": df_persons["household_id"].to_numpy(),
        "bracket": bracket_person,
    })
    if has_commune:
        work["commune_id"] = df_persons["commune_id"].astype(str).to_numpy()
    hh = work.groupby("household_id", sort=False).first()
    hh_ids = hh.index.to_numpy()
    hh_bracket = hh["bracket"].to_numpy().astype(int)

    if has_commune:
        hh_ags8 = pd.Series(hh["commune_id"].to_numpy()).map(ars_to_ags8)
        hh_rs7 = hh_ags8.map(rs7_by_ags8)
        hh_raumtyp = hh_rs7.map(
            lambda c: RS7_TO_RAUMTYP_KEY.get(int(c)) if pd.notna(c) else None
        ).to_numpy()
    else:
        hh_raumtyp = np.array([None] * len(hh_ids), dtype=object)

    n_hh = len(hh_ids)
    rng = np.random.RandomState(random_seed + 83947)

    # Per-raumtyp Bayes matrix cache: P(tenure | bracket) of shape (10, 3).
    bayes_cache: dict[object, np.ndarray | None] = {}

    def _bayes_for(raumtyp_key):
        if raumtyp_key not in bayes_cache:
            bayes_cache[raumtyp_key] = tenure_probabilities_given_income(
                df_tenure_bund, df_tenure_raum, raumtyp_key
            )
        return bayes_cache[raumtyp_key]

    # Unconditional NDS tenure marginal (raumtyp None) is the documented fallback.
    fallback_matrix = _bayes_for(None)
    if fallback_matrix is None:
        raise RuntimeError(
            "[braunschweig.enriched] housing_tenure: NDS Bayes inversion is "
            "unavailable (income_by_tenure bundesland table missing Niedersachsen); "
            "cannot synthesise the tenure attribute."
        )

    hh_tenure = np.empty(n_hh, dtype=object)
    n_primary = 0
    n_fallback = 0

    # One uniform per household (fixed RNG consumption regardless of cell mix).
    u = rng.random_sample(n_hh)

    # Group by raumtyp so each Bayes matrix is fetched once; within a group draw
    # each household's tenure from the bracket row of the matrix.
    cell = pd.DataFrame({"row": np.arange(n_hh), "raumtyp": hh_raumtyp})
    for raumtyp_key, grp in cell.groupby("raumtyp", dropna=False, sort=False):
        rows = grp["row"].to_numpy()
        rk = raumtyp_key if raumtyp_key is not None else None
        matrix = _bayes_for(rk)
        if matrix is None:
            matrix = fallback_matrix
            n_fallback += len(rows)
        else:
            n_primary += len(rows)
        for r in rows:
            pmf = matrix[hh_bracket[r]]
            c = int(np.searchsorted(np.cumsum(pmf), u[r], side="right"))
            hh_tenure[r] = tenure_arr[min(max(c, 0), n_tenure - 1)]

    tenure_by_hh = dict(zip(hh_ids, hh_tenure))
    df_persons["housing_tenure"] = pd.Categorical(
        df_persons["household_id"].map(tenure_by_hh),
        categories=list(TENURE_CATEGORIES),
    )

    fallback_rate = (n_fallback / n_hh) if n_hh else 0.0
    df_persons.attrs["housing_tenure_primary_count"] = n_primary
    df_persons.attrs["housing_tenure_fallback_count"] = n_fallback
    df_persons.attrs["housing_tenure_fallback_rate"] = fallback_rate

    shares = (
        df_persons.drop_duplicates("household_id")["housing_tenure"]
        .value_counts(normalize=True)
        .reindex(TENURE_CATEGORIES).fillna(0.0)
    )
    level = "WARNING: " if fallback_rate > HOUSING_TENURE_FALLBACK_WARN_RATE else ""
    print(
        f"[braunschweig.enriched] {level}housing_tenure (completeness attribute, "
        f"not consumed by the simulation): MiD Bayes P(tenure|bracket,raumtyp) "
        f"primary {n_primary}/{n_hh} households ({1 - fallback_rate:.2%}), fallback "
        f"(unconditional NDS tenure marginal; raumtyp cell absent) {n_fallback} "
        f"({fallback_rate:.2%}). realised household shares: "
        + ", ".join(f"{k}={v:.1%}" for k, v in shares.items())
    )
    return df_persons
