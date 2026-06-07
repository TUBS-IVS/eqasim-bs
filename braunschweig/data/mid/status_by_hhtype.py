r"""MiD 2023 'oekonomischer Status x Haushaltstyp x Region' reference + Bayes.

This module backs the household-type-driven economic-status derivation (see
``braunschweig.synthesis.population.enriched`` and CLAUDE.md). It provides:

  * the canonical label vocabularies (status, Haushaltstyp, region) and the
    German-label -> English-key maps used by the extract script;
  * loaders for the two committed tidy CSVs (raumtyp / bundesland);
  * :func:`bayes_status_given_hhtype` -- P(status | hhtype, region) by Bayes:

        P(status | hhtype, region)
            \propto P(hhtype | status, region) * P(status | region)

    with ``P(hhtype | status, region)`` = the column-% ``share_pct`` and
    ``P(status | region)`` recovered from the per-(status, region)
    ``base_weighted`` weighted bases;

  * :func:`map_households_to_hhtype` -- the synthetic-household -> MiD
    Haushaltstyp mapping (documented rules below);

  * a documented extension hook
    (:func:`bayes_status_given_hhtype_employment`) for an additional
    ``status x Erwerbstaetigkeit`` margin to be added later.

The numeric reference values live in the committed CSVs
``mid2023_status_by_hhtype_{raumtyp,bundesland}.csv`` produced by
``scripts/extract_mid_status_by_hhtype.py`` from the local-only raw xlsx;
hard-coding percentages in Python is prohibited (see CLAUDE.md).
"""

from __future__ import annotations

import os
from typing import Iterable

import numpy as np
import pandas as pd

MID_SUBDIR = os.path.join("braunschweig", "mid")

# Canonical 5-class economic status, ordered low -> high. Identical to
# ``braunschweig.synthesis.population.enriched.ECONOMIC_STATUS_CATEGORIES`` and
# the H4 quintile order; kept here too so this module has no import cycle with
# the enrichment stage.
STATUS_CATEGORIES: tuple[str, ...] = (
    "very_low", "low", "medium", "high", "very_high",
)

# MiD German status level (lower-cased, ws-normalised) -> canonical English key.
STATUS_LABEL_TO_KEY: dict[str, str] = {
    "sehr niedrig": "very_low",
    "niedrig": "low",
    "mittel": "medium",
    "hoch": "high",
    "sehr hoch": "very_high",
}

# Canonical MiD Haushaltstyp keys (English), one per MiD category row. The
# "nicht zuzuordnen" (not classifiable) MiD residual is kept as its own key so
# the column percentages stay a true partition (they sum to 100 %); the
# household-mapping side never PRODUCES it (every synthetic household is mapped
# to one of the substantive keys), it only appears as a reference column.
HHTYPE_CATEGORIES: tuple[str, ...] = (
    "single_18_29",
    "single_30_59",
    "single_60_plus",
    "couple_youngest_18_29",
    "couple_youngest_30_59",
    "couple_youngest_60_plus",
    "three_plus_adults",
    "child_under_6",
    "child_under_14",
    "child_under_18",
    "single_parent",
    "not_classifiable",
)

# MiD German Haushaltstyp label (ws-normalised) -> canonical English key.
HHTYPE_LABEL_TO_KEY: dict[str, str] = {
    "1-Personen-HH: Person 18-29 Jahre": "single_18_29",
    "1-Personen-HH: Person 30-59 Jahre": "single_30_59",
    "1-Personen-HH: Person 60 Jahre und älter": "single_60_plus",
    "2-Personen-HH: jüngste Person 18-29 Jahre": "couple_youngest_18_29",
    "2-Personen-HH: jüngste Person 30-59 Jahre": "couple_youngest_30_59",
    "2-Personen-HH: jüngste Person 60 Jahre und älter": "couple_youngest_60_plus",
    "HH mit mind. 3 Erwachsenen": "three_plus_adults",
    "HH mit mind. einem Kind unter 6 Jahren": "child_under_6",
    "HH mit mind. einem Kind unter 14 Jahren": "child_under_14",
    "HH mit mind. einem Kind unter 18 Jahren": "child_under_18",
    "Alleinerziehende/r": "single_parent",
    "nicht zuzuordnen": "not_classifiable",
}

# Region labels (ws-normalised, exactly as they appear in the sheet header --
# the MiD export line-wraps long Land names, leaving an internal space such as
# 'Niedersach sen'; we map those wrapped spellings here rather than guessing a
# repair, so the parse is exact and reproducible).
REGION_LABEL_TO_KEY_RAUMTYP: dict[str, str] = {
    "Stadtregion - Metropole": "stadtregion_metropole",
    "Stadtregion - Regiopole und Großstadt": "stadtregion_regiopole_grossstadt",
    "Stadtregion - Mittelstadt, städtischer Raum": "stadtregion_mittelstadt",
    "Stadtregion - kleinstädtische r, dörflicher Raum": "stadtregion_kleinstaedtisch",
    "ländliche Region - zentrale Stadt": "laendlich_zentrale_stadt",
    "ländliche Region - Mittelstadt, städtischer Raum": "laendlich_mittelstadt",
    "ländliche Region - kleinstädtische r, dörflicher Raum": "laendlich_kleinstaedtisch",
}

REGION_LABEL_TO_KEY_BUNDESLAND: dict[str, str] = {
    "Schleswig- Holstein": "schleswig_holstein",
    "Hamburg": "hamburg",
    "Niedersach sen": "niedersachsen",
    "Bremen": "bremen",
    "Nordrhein- Westfalen": "nordrhein_westfalen",
    "Hessen": "hessen",
    "Rheinland- Pfalz": "rheinland_pfalz",
    "Baden- Württember g": "baden_wuerttemberg",
    "Bayern": "bayern",
    "Saarland": "saarland",
    "Berlin": "berlin",
    "Brandenbu rg": "brandenburg",
    "Mecklenbur g- Vorpommer n": "mecklenburg_vorpommern",
    "Sachsen": "sachsen",
    "Sachsen- Anhalt": "sachsen_anhalt",
    "Thüringen": "thueringen",
}

# Niedersachsen is the only Bundesland relevant to the ZGB scope: the bundesland
# table is used as the BASE P(status | hhtype, NDS).
BUNDESLAND_NIEDERSACHSEN = "niedersachsen"

# RegioStaR-7 code (71..77) -> raumtyp region key of the raumtyp table. The MiD
# "zusammengefasster regionalstatistischer Raumtyp (7 Kategorien)" is the same
# BMV/BBSR RegioStaR-7 typology used by ``braunschweig.data.bbsr.regiostar``, so
# the per-home RS7 code maps 1:1 onto the raumtyp columns.
RS7_TO_RAUMTYP_KEY: dict[int, str] = {
    71: "stadtregion_metropole",
    72: "stadtregion_regiopole_grossstadt",
    73: "stadtregion_mittelstadt",
    74: "stadtregion_kleinstaedtisch",
    75: "laendlich_zentrale_stadt",
    76: "laendlich_mittelstadt",
    77: "laendlich_kleinstaedtisch",
}


def _path(data_path: str, filename: str) -> str:
    return os.path.join(data_path, MID_SUBDIR, filename)


def _load_tidy(data_path: str, filename: str) -> pd.DataFrame:
    """Load + validate one tidy status-by-hhtype CSV."""
    df = pd.read_csv(_path(data_path, filename), comment="#")
    expected = {"region", "hhtype", "status", "share_pct", "base_weighted"}
    missing = expected - set(df.columns)
    if missing:
        raise RuntimeError(
            f"{filename}: missing columns {sorted(missing)} "
            f"(have {sorted(df.columns)})"
        )
    bad_status = set(df["status"]) - set(STATUS_CATEGORIES)
    if bad_status:
        raise RuntimeError(f"{filename}: unexpected status keys {sorted(bad_status)}")
    bad_hhtype = set(df["hhtype"]) - set(HHTYPE_CATEGORIES)
    if bad_hhtype:
        raise RuntimeError(f"{filename}: unexpected hhtype keys {sorted(bad_hhtype)}")
    return df


def load_status_by_hhtype_raumtyp(data_path: str) -> pd.DataFrame:
    """Load the RegioStaR-7 raumtyp tidy table."""
    return _load_tidy(data_path, "mid2023_status_by_hhtype_raumtyp.csv")


def load_status_by_hhtype_bundesland(data_path: str) -> pd.DataFrame:
    """Load the 16-Laender bundesland tidy table."""
    return _load_tidy(data_path, "mid2023_status_by_hhtype_bundesland.csv")


def _pooled_bayes(
    df: pd.DataFrame,
    include_status: Iterable[str] = STATUS_CATEGORIES,
) -> dict[str, np.ndarray]:
    """Bayes ``P(status | hhtype)`` pooled across ALL regions of ``df``.

    Pools the per-region tables by their weighted bases so the result is the
    national (within-table) distribution, used as the denominator of the raumtyp
    within-NDS tilt. ``share_pct`` is averaged over regions weighted by the
    per-(status, region) base before applying the per-status prior.
    """
    statuses = list(include_status)
    # P(status) pooled = sum of bases over regions, per status.
    base = (
        df.drop_duplicates(["region", "status"])
        .groupby("status")["base_weighted"].sum()
        .reindex(statuses).fillna(0.0)
    )
    base_sum = base.sum()
    p_status = (base / base_sum) if base_sum > 0 else pd.Series(
        np.ones(len(statuses)) / len(statuses), index=statuses
    )
    # Pooled P(hhtype | status): base-weighted mean of share_pct over regions.
    out: dict[str, np.ndarray] = {}
    for hhtype in df["hhtype"].unique():
        sub = df[df["hhtype"] == hhtype]
        num = (
            sub.assign(w=sub["share_pct"] * sub["base_weighted"])
            .groupby("status")["w"].sum().reindex(statuses).fillna(0.0)
        )
        den = (
            sub.groupby("status")["base_weighted"].sum()
            .reindex(statuses).fillna(0.0)
        )
        share = np.where(den.to_numpy() > 0, num.to_numpy() / np.maximum(den.to_numpy(), 1e-12), 0.0)
        joint = share * p_status.to_numpy(dtype=float)
        total = joint.sum()
        out[hhtype] = joint / total if total > 0 else p_status.to_numpy(dtype=float).copy()
    return out


def region_status_probabilities(
    df_bundesland: pd.DataFrame,
    df_raumtyp: pd.DataFrame,
    base_region: str,
    raumtyp_region: str | None,
    include_status: Iterable[str] = STATUS_CATEGORIES,
) -> dict[str, np.ndarray]:
    """Combine the Bundesland base with a within-region raumtyp tilt.

    The Bundesland table (``base_region``, here Niedersachsen) is the BASE
    ``P(status | hhtype, NDS)``. The raumtyp (RegioStaR-7) table provides a
    WITHIN-NDS tilt: a household in raumtyp ``raumtyp_region`` is re-weighted by

        tilt(status | hhtype) = P_raumtyp,region(status | hhtype)
                              / P_raumtyp,national(status | hhtype)

    and the result is ``P_NDS(status | hhtype) * tilt``, renormalised over
    status. With ``raumtyp_region is None`` (no RS7 available) the raw NDS base
    is returned (tilt = 1). This keeps the strong, NDS-specific signal while
    letting urban/rural differences modulate it -- the documented choice over a
    pure raumtyp model (the raumtyp table is national, not NDS-specific).
    """
    statuses = list(include_status)
    base = bayes_status_given_hhtype(df_bundesland, base_region, statuses)
    if raumtyp_region is None:
        return base

    region_b = bayes_status_given_hhtype(df_raumtyp, raumtyp_region, statuses)
    national_b = _pooled_bayes(df_raumtyp, statuses)

    out: dict[str, np.ndarray] = {}
    for hhtype, base_vec in base.items():
        reg = region_b.get(hhtype)
        nat = national_b.get(hhtype)
        if reg is None or nat is None:
            out[hhtype] = base_vec.copy()
            continue
        with np.errstate(divide="ignore", invalid="ignore"):
            tilt = np.where(nat > 1e-12, reg / nat, 1.0)
        tilted = base_vec * tilt
        total = tilted.sum()
        out[hhtype] = tilted / total if total > 0 else base_vec.copy()
    return out


def bayes_status_given_hhtype(
    df: pd.DataFrame,
    region: str,
    include_status: Iterable[str] = STATUS_CATEGORIES,
) -> dict[str, np.ndarray]:
    """Return ``{hhtype: P(status | hhtype, region)}`` by Bayes for one region.

    For the given ``region`` column:

        P(status | hhtype) \\propto P(hhtype | status) * P(status)

    where ``P(hhtype | status)`` is ``share_pct`` (normalised over hhtypes per
    status; MiD rounds to integer percent so columns sum to ~100) and
    ``P(status)`` is the weighted base of the (status, region) cell normalised
    over status. Each returned vector is ordered like :data:`STATUS_CATEGORIES`
    and sums to 1.

    Households are only ever mapped to substantive hhtype keys, but the helper
    returns a vector for EVERY hhtype present in ``df`` (incl.
    ``not_classifiable``) so the result is complete and testable.
    """
    statuses = list(include_status)
    sub = df[df["region"] == region]
    if sub.empty:
        raise RuntimeError(
            f"bayes_status_given_hhtype: region '{region}' not in table "
            f"(have {sorted(df['region'].unique())})"
        )

    # P(status | region) from the per-(status, region) weighted base. The base is
    # repeated across hhtype rows, so take one value per status.
    base = (
        sub.drop_duplicates("status").set_index("status")["base_weighted"]
        .reindex(statuses).fillna(0.0)
    )
    base_sum = base.sum()
    p_status = (base / base_sum) if base_sum > 0 else pd.Series(
        np.ones(len(statuses)) / len(statuses), index=statuses
    )

    out: dict[str, np.ndarray] = {}
    for hhtype in sub["hhtype"].unique():
        row = (
            sub[sub["hhtype"] == hhtype]
            .set_index("status")["share_pct"]
            .reindex(statuses).fillna(0.0)
        )
        # P(hhtype | status) -- share_pct is already a column-% conditioned on
        # status, so it IS P(hhtype | status) up to the integer rounding. No
        # extra normalisation over status is needed for the Bayes product.
        joint = row.to_numpy(dtype=float) * p_status.to_numpy(dtype=float)
        total = joint.sum()
        if total > 0:
            out[hhtype] = joint / total
        else:
            # Degenerate (all-zero share for this hhtype across status): fall
            # back to the region status prior so the vector is a valid pmf.
            out[hhtype] = p_status.to_numpy(dtype=float).copy()
    return out


# Extension hook (TODO): a future ``status x Erwerbstaetigkeit`` margin will be
# exported from the MiD tool. When available, add a loader analogous to
# :func:`load_status_by_hhtype_bundesland` and combine it as a SECOND Bayes
# factor:
#
#     P(status | hhtype, employment, region)
#         \propto P(hhtype | status, region)
#               * P(employment | status, region)
#               * P(status | region)
#
# i.e. multiply the per-status vector returned by
# :func:`bayes_status_given_hhtype` by the per-status P(employment | status)
# vector (renormalised over status). The signature below is the intended entry
# point; it is intentionally unimplemented until the employment table exists.
def bayes_status_given_hhtype_employment(
    df_hhtype: pd.DataFrame,
    df_employment: pd.DataFrame,
    region: str,
    employment_status: str,
    include_status: Iterable[str] = STATUS_CATEGORIES,
) -> dict[str, np.ndarray]:  # pragma: no cover - extension hook
    """Planned extension: add a ``status x Erwerbstaetigkeit`` Bayes factor.

    Not yet implemented -- the MiD ``status x Erwerbstaetigkeit`` table has not
    been exported. See the module-level TODO for the intended combination rule.
    """
    raise NotImplementedError(
        "bayes_status_given_hhtype_employment: the status x Erwerbstaetigkeit "
        "MiD table is not yet available. Export it from the MiD tool and "
        "implement the second Bayes factor as documented in the module TODO."
    )


# ---------------------------------------------------------------------------
# Synthetic-household -> MiD Haushaltstyp mapping
# ---------------------------------------------------------------------------
#
# Age cut-offs. A "child" in the MiD Haushaltstyp partition is a household member
# below the adult age; the categories then bucket the household by the age of its
# YOUNGEST child (<6, 6..13, 14..17). An "adult" is >= ADULT_MIN_AGE.
ADULT_MIN_AGE = 18


def _classify_household(size: int, ages: np.ndarray, hh_type: str | None) -> str:
    """Return the MiD Haushaltstyp key for one household.

    Mapping rules (applied in order; the result is a TRUE partition matching the
    MiD column-% rows, so exactly one key is produced):

    1. Children present (any member < ADULT_MIN_AGE):
         - if the household has exactly ONE adult -> ``single_parent``
           (matches MiD 'Alleinerziehende/r'; also honoured when the upstream
           ``hh_type`` is 'single_parent');
         - else bucket by the YOUNGEST child age:
             youngest < 6   -> ``child_under_6``
             youngest < 14  -> ``child_under_14``
             youngest < 18  -> ``child_under_18``
    2. No children:
         - 3+ adults                       -> ``three_plus_adults``
         - 2 adults: bucket by the YOUNGER adult age:
             18..29 -> ``couple_youngest_18_29``
             30..59 -> ``couple_youngest_30_59``
             60+    -> ``couple_youngest_60_plus``
         - 1 adult: bucket by that adult's age:
             18..29 -> ``single_18_29``
             30..59 -> ``single_30_59``
             60+    -> ``single_60_plus``

    A household that cannot be placed (e.g. zero adults, or a single adult below
    18 with no children -- not expected after household formation) returns
    ``None`` so the caller counts it as a FALLBACK.
    """
    ages = np.asarray(ages)
    is_adult = ages >= ADULT_MIN_AGE
    n_adults = int(is_adult.sum())
    has_child = bool((~is_adult).any())

    if has_child:
        # Single parent: one adult + children, or upstream label says so.
        if n_adults <= 1 or (hh_type == "single_parent"):
            return "single_parent"
        youngest_child = int(ages[~is_adult].min())
        if youngest_child < 6:
            return "child_under_6"
        if youngest_child < 14:
            return "child_under_14"
        return "child_under_18"

    # No children -> classify by adult count + (youngest) adult age.
    if n_adults == 0:
        return None  # no adult, no child -> not classifiable
    if n_adults >= 3:
        return "three_plus_adults"
    youngest_adult = int(ages[is_adult].min())
    prefix = "couple_youngest" if n_adults == 2 else "single"
    if youngest_adult <= 29:
        band = "18_29"
    elif youngest_adult <= 59:
        band = "30_59"
    else:
        band = "60_plus"
    return f"{prefix}_{band}"


def map_households_to_hhtype(df_persons: pd.DataFrame) -> pd.Series:
    """Map every person to its household's MiD Haushaltstyp key.

    Aggregates ``df_persons`` (one row per person; needs ``household_id``,
    ``age``, optional ``hh_type``) to a per-household classification via
    :func:`_classify_household`, then broadcasts the key back to every person.

    Returns a per-person ``pd.Series`` of hhtype keys aligned to
    ``df_persons.index``; households that cannot be classified yield ``None``
    (the caller logs the primary/fallback rate -- no silent fallback).
    """
    has_hh_type = "hh_type" in df_persons.columns
    cols = ["household_id", "age"] + (["hh_type"] if has_hh_type else [])
    work = df_persons[cols].copy()
    work["age"] = pd.to_numeric(work["age"], errors="coerce")

    hh_keys: dict = {}
    for hid, grp in work.groupby("household_id", sort=False):
        ages = grp["age"].to_numpy()
        size = len(grp)
        hh_type = (
            str(grp["hh_type"].iloc[0]) if has_hh_type and pd.notna(grp["hh_type"].iloc[0])
            else None
        )
        hh_keys[hid] = _classify_household(size, ages, hh_type)

    return df_persons["household_id"].map(hh_keys)
