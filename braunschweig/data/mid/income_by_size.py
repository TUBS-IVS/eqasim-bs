r"""MiD 2023 'monatliches HH-Nettoeinkommen x Haushaltsgroesse x Region'.

This module backs the distribution-based household income in EUR
(``household_income_eur``) -- see ``braunschweig.synthesis.population.enriched``
and CLAUDE.md. It provides:

  * the canonical 10-bracket net-income vocabulary
    (:data:`INCOME_BRACKET_CATEGORIES`) and the German bracket label -> English
    key map used by the extract script;
  * the per-bracket EUR bound table
    (:data:`INCOME_BRACKET_BOUNDS_EUR`, ``bracket -> (low_eur, high_eur)``)
    used to draw a continuous EUR value within a sampled bracket; the open top
    bracket "mehr als 7000" has ``high_eur is None`` (see the open-top handling
    note below);
  * the German household-size column label -> canonical key map
    (:data:`SIZE_LABEL_TO_KEY`) and the region label maps (Bundesland / raumtyp);
  * loaders for the two committed tidy CSVs
    (``mid2023_income_by_size_bundesland.csv`` / ``_raumtyp.csv``);
  * :func:`income_bracket_probabilities` -- ``P(bracket | hh_size, raumtyp)`` as
    a pmf over the 10 brackets, with Niedersachsen (from the Bundesland table) as
    the BASE and the raumtyp table as a within-NDS multiplicative tilt, exactly
    mirroring the status x hhtype / cars derivations in
    :mod:`braunschweig.data.mid.status_by_hhtype` and
    :mod:`braunschweig.data.mid.cars_by_status`.

Distribution semantics
----------------------
Both source tables report ``Spalten % (gewichtet)``: within each household-size
column the bracket percentages sum to 100 %, i.e. each column IS
``P(bracket | hh_size, region)``. The ``Basis gewichtet`` row gives the weighted
household base per (hh_size, region) cell (used only for diagnostics / a
base-weighted national pool in the tilt denominator). The bracket marginal is a
genuine conditional distribution, so unlike the cars/status tables there is no
Bayes inversion: the column-% is already the target pmf.

Tilt derivation (mirror of status_by_hhtype / cars_by_status)
-------------------------------------------------------------
NDS base:           P_NDS(bracket | hh_size)        from the Bundesland table.
raumtyp region:     P_raum,region(bracket | hh_size) from the raumtyp table.
raumtyp national:   P_raum,national(bracket | hh_size), the base-weighted pool of
                    the raumtyp regions (denominator of the tilt).
tilt(bracket)     = P_raum,region(bracket | hh_size) / P_raum,national(bracket | hh_size)
result            = P_NDS(bracket | hh_size) * tilt(bracket), renormalised.

With ``raumtyp_region is None`` (no RS7 available) the raw NDS base is returned
(tilt = 1). A missing (hh_size) cell yields ``None`` so the caller can log a
FALLBACK (NDS-only / uniform) rather than silently defaulting.

Open-top bracket handling
-------------------------
The top bracket "mehr als 7000" is open-ended; its EUR bound is
``(7000.0, None)``. The caller draws a finite EUR value for it with a
documented heavy-tail rule (``7000 * (1 + Exponential(mean))``) rather than a
uniform draw, so the open top does not require an arbitrary upper cap in this
table. All other brackets are closed ``[low, high)`` intervals taken verbatim
from the MiD bracket edges.

The numeric reference values live in the committed CSVs produced by
``scripts/extract_mid_income_by_size.py`` from the local-only raw xlsx;
hard-coding percentages in Python is prohibited (see CLAUDE.md).
"""

from __future__ import annotations

import os
from typing import Iterable

import numpy as np
import pandas as pd

MID_SUBDIR = os.path.join("braunschweig", "mid")

# Canonical 10-bracket monthly net household income vocabulary, ordered
# low -> high (matches the MiD "monatliches HH-Nettoeinkommen in 10 Gruppen"
# row order). Kept here so the extract script and the reader share one source.
INCOME_BRACKET_CATEGORIES: tuple[str, ...] = (
    "under_500",
    "500_900",
    "900_1500",
    "1500_2000",
    "2000_3000",
    "3000_4000",
    "4000_5000",
    "5000_6000",
    "6000_7000",
    "over_7000",
)

# MiD German bracket label (ws-normalised) -> canonical English key.
INCOME_BRACKET_LABEL_TO_KEY: dict[str, str] = {
    "unter 500 Euro": "under_500",
    "500 bis unter 900 Euro": "500_900",
    "900 bis unter 1.500 Euro": "900_1500",
    "1.500 bis unter 2.000 Euro": "1500_2000",
    "2.000 bis unter 3.000 Euro": "2000_3000",
    "3.000 bis unter 4.000 Euro": "3000_4000",
    "4.000 bis unter 5.000 Euro": "4000_5000",
    "5.000 bis unter 6.000 Euro": "5000_6000",
    "6.000 bis 7.000 Euro": "6000_7000",
    "mehr als 7.000 Euro": "over_7000",
}

# Per-bracket EUR bounds [low_eur, high_eur). The open top bracket has
# ``high_eur is None`` (see "Open-top bracket handling" in the module docstring).
# Bounds are the verbatim MiD bracket edges in EUR/month; "6000 bis 7000" is
# treated as the half-open [6000, 7000) interval consistent with the others.
INCOME_BRACKET_BOUNDS_EUR: dict[str, tuple[float, float | None]] = {
    "under_500": (0.0, 500.0),
    "500_900": (500.0, 900.0),
    "900_1500": (900.0, 1500.0),
    "1500_2000": (1500.0, 2000.0),
    "2000_3000": (2000.0, 3000.0),
    "3000_4000": (3000.0, 4000.0),
    "4000_5000": (4000.0, 5000.0),
    "5000_6000": (5000.0, 6000.0),
    "6000_7000": (6000.0, 7000.0),
    "over_7000": (7000.0, None),
}

# MiD German household-size column label (ws-normalised) -> canonical key
# matching the synthetic ``household_size`` bins ("5+" collapses 5 or more).
SIZE_LABEL_TO_KEY: dict[str, str] = {
    "1 Person": "1",
    "2 Personen": "2",
    "3 Personen": "3",
    "4 Personen": "4",
    "5 Personen und mehr": "5+",
}

# Canonical household-size keys (column order in the tidy CSVs).
SIZE_CATEGORIES: tuple[str, ...] = ("1", "2", "3", "4", "5+")

# Bundesland region label (ws-normalised, after stripping the leading '- ') ->
# canonical English key. These sheets carry the clean (non-line-wrapped)
# spellings, so the map is the plain German name -> key.
REGION_LABEL_TO_KEY_BUNDESLAND: dict[str, str] = {
    "Schleswig-Holstein": "schleswig_holstein",
    "Hamburg": "hamburg",
    "Niedersachsen": "niedersachsen",
    "Bremen": "bremen",
    "Nordrhein-Westfalen": "nordrhein_westfalen",
    "Hessen": "hessen",
    "Rheinland-Pfalz": "rheinland_pfalz",
    "Baden-Württemberg": "baden_wuerttemberg",
    "Bayern": "bayern",
    "Saarland": "saarland",
    "Berlin": "berlin",
    "Brandenburg": "brandenburg",
    "Mecklenburg-Vorpommern": "mecklenburg_vorpommern",
    "Sachsen": "sachsen",
    "Sachsen-Anhalt": "sachsen_anhalt",
    "Thüringen": "thueringen",
}

# RegioStaR raumtyp label (ws-normalised, after stripping the leading '- ') ->
# canonical English key. These match the RS7 keys used elsewhere (the same as
# status_by_hhtype's RS7_TO_RAUMTYP_KEY values), but the labels here are the
# clean spellings of THIS export (e.g. "kleinstaedtischer", not the line-wrapped
# "kleinstaedtische r" of the status_by_hhtype export), so the map is local.
REGION_LABEL_TO_KEY_RAUMTYP: dict[str, str] = {
    "Stadtregion - Metropole": "stadtregion_metropole",
    "Stadtregion - Regiopole und Großstadt": "stadtregion_regiopole_grossstadt",
    "Stadtregion - Mittelstadt, städtischer Raum": "stadtregion_mittelstadt",
    "Stadtregion - kleinstädtischer, dörflicher Raum": "stadtregion_kleinstaedtisch",
    "ländliche Region - zentrale Stadt": "laendlich_zentrale_stadt",
    "ländliche Region - Mittelstadt, städtischer Raum": "laendlich_mittelstadt",
    "ländliche Region - kleinstädtischer, dörflicher Raum": "laendlich_kleinstaedtisch",
}

# Niedersachsen is the only Bundesland relevant to the ZGB scope: the Bundesland
# table is the BASE P(bracket | hh_size, NDS).
BUNDESLAND_NIEDERSACHSEN = "niedersachsen"

# RegioStaR-7 code (71..77) -> raumtyp region key, identical to the mapping in
# braunschweig.data.mid.status_by_hhtype (kept local to avoid an import cycle and
# to keep each MiD coupling self-contained).
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


def _load_tidy(data_path: str, filename: str, region_keys: set[str]) -> pd.DataFrame:
    """Load + validate one tidy income-by-size CSV.

    Columns ``[region, hh_size, income_bracket, share_pct, base_weighted]``.
    ``share_pct = P(income_bracket | hh_size, region)`` in percent (a size column
    sums to ~100 over brackets); ``base_weighted`` is the weighted household base
    of the (hh_size, region) cell, repeated across bracket rows.
    """
    df = pd.read_csv(_path(data_path, filename), comment="#")
    expected = {"region", "hh_size", "income_bracket", "share_pct", "base_weighted"}
    missing = expected - set(df.columns)
    if missing:
        raise RuntimeError(
            f"{filename}: missing columns {sorted(missing)} (have {sorted(df.columns)})"
        )
    bad_bracket = set(df["income_bracket"]) - set(INCOME_BRACKET_CATEGORIES)
    if bad_bracket:
        raise RuntimeError(f"{filename}: unexpected income_bracket keys {sorted(bad_bracket)}")
    bad_size = set(df["hh_size"].astype(str)) - set(SIZE_CATEGORIES)
    if bad_size:
        raise RuntimeError(f"{filename}: unexpected hh_size keys {sorted(bad_size)}")
    bad_region = set(df["region"]) - region_keys
    if bad_region:
        raise RuntimeError(f"{filename}: unexpected region keys {sorted(bad_region)}")
    df["hh_size"] = df["hh_size"].astype(str)
    return df


def load_income_by_size_bundesland(data_path: str) -> pd.DataFrame:
    """Load the 16-Laender Bundesland tidy table."""
    return _load_tidy(
        data_path,
        "mid2023_income_by_size_bundesland.csv",
        set(REGION_LABEL_TO_KEY_BUNDESLAND.values()),
    )


def load_income_by_size_raumtyp(data_path: str) -> pd.DataFrame:
    """Load the RegioStaR-7 raumtyp tidy table."""
    return _load_tidy(
        data_path,
        "mid2023_income_by_size_raumtyp.csv",
        set(REGION_LABEL_TO_KEY_RAUMTYP.values()),
    )


def _bracket_pmf_for_region_size(
    df: pd.DataFrame,
    region: str,
    hh_size: str,
    include_brackets: Iterable[str] = INCOME_BRACKET_CATEGORIES,
) -> np.ndarray | None:
    """Return ``P(bracket | hh_size, region)`` as a pmf, or ``None`` if absent.

    ``share_pct`` is already the column-% conditional, so the pmf is the
    bracket-ordered share vector renormalised to sum to 1 (MiD rounds to integer
    percent, so the raw vector sums to ~100). Returns ``None`` when the
    (region, hh_size) cell carries no positive mass (absent / fully suppressed).
    """
    brackets = list(include_brackets)
    sub = df[(df["region"] == region) & (df["hh_size"].astype(str) == str(hh_size))]
    if sub.empty:
        return None
    vec = (
        sub.set_index("income_bracket")["share_pct"]
        .reindex(brackets).fillna(0.0).to_numpy(dtype=float)
    )
    total = vec.sum()
    if total <= 0:
        return None
    return vec / total


def _national_raumtyp_pmf(
    df_raumtyp: pd.DataFrame,
    hh_size: str,
    include_brackets: Iterable[str] = INCOME_BRACKET_CATEGORIES,
) -> np.ndarray | None:
    """Return the base-weighted national raumtyp pmf for one hh_size.

    Pools the raumtyp regions by their per-(hh_size, region) weighted base so the
    denominator of the within-NDS tilt is the national (within-table)
    bracket distribution at that household size:

        P_national(bracket | hh_size)
            = sum_r base(hh_size, r) * P_r(bracket | hh_size)
              / sum_r base(hh_size, r)

    Returns ``None`` if no raumtyp region has positive mass at this hh_size.
    """
    brackets = list(include_brackets)
    acc = np.zeros(len(brackets), dtype=float)
    base_total = 0.0
    for region in df_raumtyp["region"].unique():
        pmf = _bracket_pmf_for_region_size(df_raumtyp, region, hh_size, brackets)
        if pmf is None:
            continue
        sub = df_raumtyp[
            (df_raumtyp["region"] == region)
            & (df_raumtyp["hh_size"].astype(str) == str(hh_size))
        ]
        base = float(sub["base_weighted"].drop_duplicates().sum())
        if base <= 0:
            base = 1.0  # degenerate base -> equal weight (does not zero a region)
        acc += base * pmf
        base_total += base
    if base_total <= 0:
        return None
    return acc / base_total


def income_bracket_probabilities(
    df_bundesland: pd.DataFrame,
    df_raumtyp: pd.DataFrame,
    hh_size: str,
    raumtyp_region: str | None,
    include_brackets: Iterable[str] = INCOME_BRACKET_CATEGORIES,
) -> np.ndarray | None:
    """Return ``P(bracket | hh_size, raumtyp)`` as a pmf over the 10 brackets.

    Niedersachsen (Bundesland table) is the BASE; the raumtyp table is a
    within-NDS multiplicative tilt (see the module docstring). The returned
    vector is ordered like :data:`INCOME_BRACKET_CATEGORIES` and sums to 1.

    Returns ``None`` (caller logs a FALLBACK -- no silent fallback) when the NDS
    base cell for ``hh_size`` is absent. With ``raumtyp_region is None`` or absent
    from the raumtyp table, the untilted NDS base is returned.
    """
    brackets = list(include_brackets)
    base = _bracket_pmf_for_region_size(
        df_bundesland, BUNDESLAND_NIEDERSACHSEN, hh_size, brackets
    )
    if base is None:
        return None

    if raumtyp_region is None:
        return base.copy()

    region_pmf = _bracket_pmf_for_region_size(df_raumtyp, raumtyp_region, hh_size, brackets)
    national_pmf = _national_raumtyp_pmf(df_raumtyp, hh_size, brackets)
    if region_pmf is None or national_pmf is None:
        return base.copy()

    with np.errstate(divide="ignore", invalid="ignore"):
        tilt = np.where(national_pmf > 1e-12, region_pmf / national_pmf, 1.0)
    tilted = base * tilt
    total = tilted.sum()
    return (tilted / total) if total > 0 else base.copy()
