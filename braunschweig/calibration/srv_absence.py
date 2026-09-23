"""SrV 2023 full-day absence aggregates (issue #370, sub-project B).

Two committed tables for the general-day-absence state draw
(:mod:`braunschweig.synthesis.day_absence.absence`):

* ``srv2023_absence_by_age_band.csv`` -- share of persons away from home over the WHOLE reporting
  day (``E_ANZ_WEGE == -7``, :data:`srv_plan_structure.AWAY_FROM_HOME_CODE`) per age band, plus an
  ``all`` row. Weight ``GEWICHT_P_ZENSUS`` (ADR-0055).
* ``srv2023_absence_household_by_size.csv`` -- share of households in which EVERY member is away,
  per household size class (1..5, 5 = five or more), weight ``GEWICHT_HH_ZENSUS``. Also carries a
  PERSON-level reporting reference for issue #388 (``n_persons_unweighted``,
  ``n_absent_persons_unweighted``, ``p_absent_person``): the ``GEWICHT_P_ZENSUS`` person-weighted
  share of absent persons among ALL persons living in a household of that size class, distinct
  from the household-level ``p_all_absent`` above. Issue #426 adds ``n_partial_absent_unweighted`` /
  ``p_partial_absent`` (household-weighted share of the class's households with SOME but not ALL
  members away) so that none / partial / all partition each size class.

Universe: every delivered person with a valid positive person weight (the SrV ``at_home_zero``
universe of :mod:`braunschweig.calibration.srv_plan_structure` -- absence IS the state being
measured, so away persons are kept). Guards raise rather than adapt: ``MITTL_WERKTAG`` must be 1
for every person (pure Tuesday-Thursday delivery) and the only negative ``E_ANZ_WEGE`` code may
be -7. Pure module: no file I/O; ``scripts/extract_srv_absence.py`` owns the CLI and the header.
"""
from __future__ import annotations

import logging
import math

import numpy as np
import pandas as pd

from braunschweig.calibration.srv_plan_structure import AWAY_FROM_HOME_CODE

logger = logging.getLogger(__name__)
_LOG_TAG = "[srv absence]"

ABSENCE_BY_AGE_TABLE = "srv2023_absence_by_age_band.csv"
ABSENCE_HOUSEHOLD_TABLE = "srv2023_absence_household_by_size.csv"

#: Seven bands, every one with >= 1,081 persons and >= 31 absent persons in the 2026-09-09
#: extraction (spec 2026-09-09-general-day-absence-design.md, table 1.1).
AGE_BAND_EDGES = (-1, 5, 17, 29, 44, 64, 74, 200)
AGE_BAND_LABELS = ("0-5", "6-17", "18-29", "30-44", "45-64", "65-74", "75+")
AGE_BAND_BOUNDS = {"0-5": (0, 5), "6-17": (6, 17), "18-29": (18, 29), "30-44": (30, 44),
                   "45-64": (45, 64), "65-74": (65, 74), "75+": (75, 200)}
ALL_BAND = "all"
HOUSEHOLD_SIZE_CLASS_TOP = 5
AVERAGE_WEEKDAY = 1  # MITTL_WERKTAG
#: Child / adult split used by the composition tables and the model-side analysis (issue #426);
#: the same age <= 17 convention as the arm-4 manifest's children rows.
CHILD_MAX_AGE = 17
ADULT_MIN_AGE = 18

PERSON_COLUMNS = ["HHNR", "PNR", "V_ALTER", "E_ANZ_WEGE", "GEWICHT_P_ZENSUS", "MITTL_WERKTAG"]
#: V_ANZ_PERS is read ONLY to verify the roster assumption (check_household_roster), never as
#: the household size itself.
HOUSEHOLD_COLUMNS = ["HHNR", "GEWICHT_HH_ZENSUS", "V_ANZ_PERS"]
BY_AGE_COLUMNS = ["band", "age_min", "age_max", "n_unweighted", "n_absent_unweighted", "p_absent"]
BY_SIZE_COLUMNS = ["size_class", "n_households_unweighted", "n_all_absent_unweighted", "p_all_absent",
                  "n_persons_unweighted", "n_absent_persons_unweighted", "p_absent_person",
                  "n_partial_absent_unweighted", "p_partial_absent"]

ABSENCE_COMPOSITION_TABLE = "srv2023_absence_composition_by_age_band.csv"
ABSENCE_PARTIAL_SUBSET_TABLE = "srv2023_absence_partial_subset_size.csv"
#: Children aggregate row of the composition table: the row the #426 acceptance criterion is
#: evaluated on (the seven bands are diagnostic -- thin cells).
CHILDREN_ROW = "0-17"
#: Mutually exclusive household patterns of an ABSENT person (classify_absence_composition).
PATTERN_WHOLE = "whole_household"
PATTERN_PARTIAL_WITH_ADULT = "partial_with_absent_adult"
PATTERN_PARTIAL_NO_ADULT = "partial_no_absent_adult"
PATTERNS = (PATTERN_WHOLE, PATTERN_PARTIAL_WITH_ADULT, PATTERN_PARTIAL_NO_ADULT)
COMPOSITION_COUNT_COLUMNS = ("n_whole_household_unweighted", "n_partial_with_absent_adult_unweighted",
                             "n_partial_no_absent_adult_unweighted")
COMPOSITION_SHARE_COLUMNS = ("p_whole_household", "p_partial_with_absent_adult", "p_partial_no_absent_adult")
COMPOSITION_COLUMNS = ["band", "age_min", "age_max", "n_absent_unweighted",
                       *COMPOSITION_COUNT_COLUMNS, *COMPOSITION_SHARE_COLUMNS]
#: n_absent_members of a partially absent household is top-coded at HOUSEHOLD_SIZE_CLASS_TOP - 1.
PARTIAL_SUBSET_TOP = HOUSEHOLD_SIZE_CLASS_TOP - 1
PARTIAL_SUBSET_COLUMNS = ["size_class", "n_absent_members", "n_households_unweighted", "share_within_partial"]


def _require_columns(frame, required, name):
    missing = [c for c in required if c not in frame.columns]
    if missing:
        raise ValueError(f"{_LOG_TAG} {name}: missing required column(s) {missing}")


def age_band(ages) -> pd.Series:
    """Age (years) -> band label; NaN for a missing age (the caller decides how to treat it)."""
    return pd.cut(pd.to_numeric(pd.Series(ages), errors="coerce"), list(AGE_BAND_EDGES),
                  labels=list(AGE_BAND_LABELS)).astype(object)


def household_size_class(sizes) -> np.ndarray:
    """Household size -> class 1..HOUSEHOLD_SIZE_CLASS_TOP (top class = 'or more')."""
    return np.minimum(np.asarray(sizes, dtype=int), HOUSEHOLD_SIZE_CLASS_TOP)


def household_absence_patterns(prepared: pd.DataFrame) -> pd.DataFrame:
    """One row per household: ``n`` delivered members, ``n_absent``, ``n_adult_absent`` (absent
    members aged >= ADULT_MIN_AGE), ``all_absent`` (every member absent), ``partial`` (some but not
    all absent) and ``size_class``. A person without a valid age counts as a member but never as
    an adult. Pure; the shared basis of the by-size, composition and subset-size tables."""
    _require_columns(prepared, ["hhnr", "pnr", "age", "absent"], "prepared")
    adult_absent = (prepared["age"] >= ADULT_MIN_AGE) & prepared["absent"]
    per_hh = (prepared.assign(adult_absent=adult_absent)
              .groupby("hhnr")
              .agg(n=("pnr", "size"), n_absent=("absent", "sum"), n_adult_absent=("adult_absent", "sum"))
              .reset_index())
    per_hh["all_absent"] = per_hh["n_absent"] == per_hh["n"]
    per_hh["partial"] = (per_hh["n_absent"] > 0) & ~per_hh["all_absent"]
    per_hh["size_class"] = household_size_class(per_hh["n"])
    return per_hh


def _attach_household_weight(per_hh: pd.DataFrame, households: pd.DataFrame) -> pd.DataFrame:
    """Left-join GEWICHT_HH_ZENSUS onto the per-household frame; raise on a missing or non-positive
    weight (a household of the person file MUST resolve in the household file)."""
    hh = households[["HHNR", "GEWICHT_HH_ZENSUS"]].rename(columns={"HHNR": "hhnr"})
    per_hh = per_hh.merge(hh, on="hhnr", how="left")
    n_unweighted_hh = int(per_hh["GEWICHT_HH_ZENSUS"].isna().sum())
    if n_unweighted_hh:
        raise ValueError(f"{_LOG_TAG} {n_unweighted_hh} households of the person file have no row / weight "
                         "in the household file")
    n_non_positive_weight_hh = int((per_hh["GEWICHT_HH_ZENSUS"] <= 0).sum())
    if n_non_positive_weight_hh:
        raise ValueError(f"{_LOG_TAG} {n_non_positive_weight_hh} household(s) have a non-positive "
                         "GEWICHT_HH_ZENSUS; a household weight must be > 0")
    return per_hh


def check_household_roster(per_hh: pd.DataFrame, households: pd.DataFrame) -> int:
    """Raise unless every household's DELIVERED person count equals the household file's V_ANZ_PERS.

    The by-size tables define household size as the number of delivered persons per HHNR. That is
    an assumption about the delivery's roster completeness; this guard turns the former ad-hoc
    check into committed code and logs the verified count as an explicit rate. Returns the number
    of households checked (``len(per_hh)``), so a caller can cite the guard's OWN count in a
    provenance header instead of inferring it from an unrelated total."""
    roster = households[["HHNR", "V_ANZ_PERS"]].rename(columns={"HHNR": "hhnr"})
    merged = per_hh[["hhnr", "n"]].merge(roster, on="hhnr", how="left")
    declared = pd.to_numeric(merged["V_ANZ_PERS"], errors="coerce")
    mismatch = merged[declared.isna() | (declared != merged["n"])]
    if len(mismatch):
        examples = mismatch.head(5).to_dict("records")
        raise ValueError(f"{_LOG_TAG} {len(mismatch)}/{len(merged)} households: delivered person count "
                         f"differs from V_ANZ_PERS, so 'household size = delivered roster' would be wrong; "
                         f"examples {examples}")
    logger.info("%s household roster: %d/%d households (100.0%%) have delivered persons == V_ANZ_PERS",
                _LOG_TAG, len(merged), len(merged))
    return int(len(merged))


def prepare_absence_persons(persons: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Harmonise the SrV person file for the absence tables; raises on universe drift."""
    _require_columns(persons, PERSON_COLUMNS, "persons")
    p = persons[PERSON_COLUMNS].copy()
    werktag = pd.to_numeric(p["MITTL_WERKTAG"], errors="coerce")
    if not (werktag == AVERAGE_WEEKDAY).all():
        raise ValueError(f"{_LOG_TAG} MITTL_WERKTAG is not {AVERAGE_WEEKDAY} for every person; "
                         "the delivery is not the pure average-weekday universe this table assumes")
    trips = pd.to_numeric(p["E_ANZ_WEGE"], errors="coerce")
    if trips.isna().any():
        raise ValueError(f"{_LOG_TAG} {int(trips.isna().sum())} persons have a non-numeric E_ANZ_WEGE")
    other_negative = sorted(trips[(trips < 0) & (trips != AWAY_FROM_HOME_CODE)].unique().tolist())
    if other_negative:
        raise ValueError(f"{_LOG_TAG} unexpected negative E_ANZ_WEGE code(s) {other_negative}; only "
                         f"{AWAY_FROM_HOME_CODE} (away from home) is a known state")
    weight = pd.to_numeric(p["GEWICHT_P_ZENSUS"], errors="coerce")
    valid = weight > 0
    n_dropped = int((~valid).sum())
    if n_dropped:
        n_before_drop = len(p)
        drop_rate = 100.0 * n_dropped / n_before_drop if n_before_drop else float("nan")
        logger.warning("%s %d/%d persons (%.2f%%) dropped for a missing/non-positive "
                       "GEWICHT_P_ZENSUS", _LOG_TAG, n_dropped, n_before_drop, drop_rate)
    p = p[valid].copy()
    out = pd.DataFrame({
        "hhnr": p["HHNR"].values, "pnr": p["PNR"].values,
        "age": pd.to_numeric(p["V_ALTER"], errors="coerce").values,
        "weight": weight[valid].astype(float).values,
        "absent": (trips[valid] == AWAY_FROM_HOME_CODE).values,
    })
    out["band"] = age_band(out["age"]).values
    n_missing_age = int(out["band"].isna().sum())
    if n_missing_age:
        logger.warning("%s %d persons have no age and fall into no band (kept in the 'all' row only)",
                       _LOG_TAG, n_missing_age)
    diagnostics = {"n_persons_raw": int(len(persons)), "n_persons_dropped_weight": n_dropped,
                   "n_persons_universe": int(len(out)), "n_absent": int(out["absent"].sum()),
                   "n_persons_missing_age": n_missing_age}
    weight_total = float(out["weight"].sum())
    absent_weight = float(out.loc[out["absent"], "weight"].sum())
    logger.info("%s universe %d persons, %d absent (%.2f%% weighted)", _LOG_TAG, len(out),
                diagnostics["n_absent"],
                100.0 * absent_weight / weight_total if weight_total else float("nan"))
    return out, diagnostics


def _weighted_share(weight, mask) -> float:
    total = float(weight.sum())
    return float(weight[mask].sum() / total) if total > 0 else float("nan")


def build_absence_by_age_band(prepared: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for band in AGE_BAND_LABELS:
        g = prepared[prepared["band"] == band]
        lo, hi = AGE_BAND_BOUNDS[band]
        rows.append({"band": band, "age_min": lo, "age_max": hi, "n_unweighted": int(len(g)),
                     "n_absent_unweighted": int(g["absent"].sum()),
                     "p_absent": _weighted_share(g["weight"], g["absent"])})
    rows.append({"band": ALL_BAND, "age_min": 0, "age_max": 200, "n_unweighted": int(len(prepared)),
                 "n_absent_unweighted": int(prepared["absent"].sum()),
                 "p_absent": _weighted_share(prepared["weight"], prepared["absent"])})
    return pd.DataFrame(rows, columns=BY_AGE_COLUMNS)


def build_absence_household_by_size(prepared: pd.DataFrame, households: pd.DataFrame) -> pd.DataFrame:
    _require_columns(households, HOUSEHOLD_COLUMNS, "households")
    per_hh = household_absence_patterns(prepared)
    per_hh = _attach_household_weight(per_hh, households)
    check_household_roster(per_hh, households)
    # Person-level size class (issue #388): join each PERSON row -- not each household -- to its
    # household's size class, so the PERSON-weighted absence rate can be aggregated per class as a
    # reporting reference distinct from the household-level "every member absent" share above.
    persons_sized = prepared.merge(per_hh[["hhnr", "size_class"]], on="hhnr", how="left")
    rows = []
    for size_class in range(1, HOUSEHOLD_SIZE_CLASS_TOP + 1):
        g = per_hh[per_hh["size_class"] == size_class]
        g_persons = persons_sized[persons_sized["size_class"] == size_class]
        hh_weight = g["GEWICHT_HH_ZENSUS"].astype(float)
        rows.append({"size_class": size_class, "n_households_unweighted": int(len(g)),
                     "n_all_absent_unweighted": int(g["all_absent"].sum()),
                     "p_all_absent": _weighted_share(hh_weight, g["all_absent"]),
                     "n_persons_unweighted": int(len(g_persons)),
                     "n_absent_persons_unweighted": int(g_persons["absent"].sum()),
                     "p_absent_person": _weighted_share(g_persons["weight"].astype(float), g_persons["absent"]),
                     # issue #426: SOME but not ALL members away, the pattern the draw lacks
                     "n_partial_absent_unweighted": int(g["partial"].sum()),
                     "p_partial_absent": _weighted_share(hh_weight, g["partial"])})
    return pd.DataFrame(rows, columns=BY_SIZE_COLUMNS)


def classify_absence_composition(prepared: pd.DataFrame) -> pd.DataFrame:
    """ABSENT persons only, with a ``pattern`` column (one of :data:`PATTERNS`).

    Evaluated on the person's OWN household: ``whole_household`` when every delivered member is
    absent; otherwise ``partial_with_absent_adult`` when at least one OTHER member aged
    >= ADULT_MIN_AGE is absent (a sibling does not count), else ``partial_no_absent_adult``.
    The 'other' excludes the person themselves, so an absent adult whose partner is home is
    ``partial_no_absent_adult`` even though the household has one absent adult (them)."""
    per_hh = household_absence_patterns(prepared)
    merged = prepared.merge(per_hh[["hhnr", "all_absent", "n_adult_absent"]], on="hhnr", how="left")
    absent = merged[merged["absent"]].copy()
    self_is_adult = (absent["age"] >= ADULT_MIN_AGE).astype(int)
    other_adult_absent = absent["n_adult_absent"] - self_is_adult
    absent["pattern"] = np.where(absent["all_absent"], PATTERN_WHOLE,
                                 np.where(other_adult_absent > 0, PATTERN_PARTIAL_WITH_ADULT,
                                          PATTERN_PARTIAL_NO_ADULT))
    return absent


def _composition_row(label: str, age_min: int, age_max: int, group: pd.DataFrame) -> dict:
    weight = group["weight"].astype(float)
    row = {"band": label, "age_min": age_min, "age_max": age_max, "n_absent_unweighted": int(len(group))}
    for pattern, count_column, share_column in zip(PATTERNS, COMPOSITION_COUNT_COLUMNS, COMPOSITION_SHARE_COLUMNS):
        mask = group["pattern"] == pattern
        row[count_column] = int(mask.sum())
        row[share_column] = _weighted_share(weight, mask)     # NaN when the row has no absent person
    return row


def build_absence_composition_by_band(prepared: pd.DataFrame) -> pd.DataFrame:
    """Per age band + the ``0-17`` children aggregate + ``all``: counts and GEWICHT_P_ZENSUS-weighted
    shares of ABSENT persons per pattern. The three shares partition each row (sum to 1 unless
    the row has no absent person, then all NaN).

    The children row takes ``0 <= age <= CHILD_MAX_AGE``: the SrV delivery encodes a missing age as
    a NEGATIVE code, which is not a valid age, so such a person falls into no band and not into
    ``0-17`` either -- they appear in the ``all`` row only (and ``0-5`` + ``6-17`` = ``0-17``)."""
    classified = classify_absence_composition(prepared)
    rows = []
    for band in AGE_BAND_LABELS:
        lo, hi = AGE_BAND_BOUNDS[band]
        rows.append(_composition_row(band, lo, hi, classified[classified["band"] == band]))
    is_child = (classified["age"] >= 0) & (classified["age"] <= CHILD_MAX_AGE)
    rows.append(_composition_row(CHILDREN_ROW, 0, CHILD_MAX_AGE, classified[is_child]))
    rows.append(_composition_row(ALL_BAND, 0, 200, classified))
    table = pd.DataFrame(rows, columns=COMPOSITION_COLUMNS)
    children = table[table["band"] == CHILDREN_ROW].iloc[0]
    logger.info("%s composition %s (n_absent=%d): %s", _LOG_TAG, CHILDREN_ROW, int(children["n_absent_unweighted"]),
                ", ".join("%s %d (%.1f%% weighted)" % (p, int(children[c]), 100.0 * children[s])
                          for p, c, s in zip(PATTERNS, COMPOSITION_COUNT_COLUMNS, COMPOSITION_SHARE_COLUMNS)
                          if not pd.isna(children[s])))
    return table


def build_absence_partial_subset_size(prepared: pd.DataFrame, households: pd.DataFrame) -> pd.DataFrame:
    """Among PARTIALLY absent households of each size class 2..TOP: how many members are away
    (1..PARTIAL_SUBSET_TOP, top-coded), as unweighted counts and GEWICHT_HH_ZENSUS-weighted shares
    within the class. Rows exist for every (class, k <= min(class - 1, TOP)) even when empty; a
    class with no partial household reports NaN shares."""
    _require_columns(households, HOUSEHOLD_COLUMNS, "households")
    per_hh = _attach_household_weight(household_absence_patterns(prepared), households)
    partial = per_hh[per_hh["partial"]].copy()
    partial["n_absent_members"] = np.minimum(partial["n_absent"].astype(int), PARTIAL_SUBSET_TOP)
    rows = []
    for size_class in range(2, HOUSEHOLD_SIZE_CLASS_TOP + 1):
        g = partial[partial["size_class"] == size_class]
        total_weight = float(g["GEWICHT_HH_ZENSUS"].sum())
        for k in range(1, min(size_class - 1, PARTIAL_SUBSET_TOP) + 1):
            mask = g["n_absent_members"] == k
            rows.append({"size_class": size_class, "n_absent_members": k,
                         "n_households_unweighted": int(mask.sum()),
                         "share_within_partial": (float(g.loc[mask, "GEWICHT_HH_ZENSUS"].sum() / total_weight)
                                                  if total_weight > 0 else float("nan"))})
    return pd.DataFrame(rows, columns=PARTIAL_SUBSET_COLUMNS)


def wilson_interval(k: int, n: int, z: float = 1.959964) -> tuple[float, float]:
    """Wilson score interval for a binomial share k/n (default 95 %). ``(nan, nan)`` for
    k == n == 0; raises when k is outside ``[0, n]`` (checked first, so ``k > 0`` with ``n == 0``
    raises rather than returning NaN).

    The pre-registered acceptance bound of issue #426 for thin cells: computed on UNWEIGHTED
    counts because the design effect of the expansion weights is unknown (ASSUMPTION, stated in
    every consumer)."""
    if k < 0 or k > n:
        raise ValueError(f"{_LOG_TAG} wilson_interval: k={k} must satisfy 0 <= k <= n={n}")
    if n <= 0:
        return (float("nan"), float("nan"))
    share = k / n
    denominator = 1.0 + z * z / n
    centre = (share + z * z / (2.0 * n)) / denominator
    half_width = z * math.sqrt(share * (1.0 - share) / n + z * z / (4.0 * n * n)) / denominator
    return (max(0.0, centre - half_width), min(1.0, centre + half_width))


def clustering_share(prepared: pd.DataFrame) -> dict:
    """Person-weighted and unweighted share of absent persons living in a fully absent household.

    This is the traceable SOURCE of ADR-0110's "55.8 % (person-weighted; 49.4 % unweighted)"
    household-clustering figure (CLAUDE.md "no invented reference values": a number cited in a
    decision record must be reproducible by committed code, not a number measured once ad hoc and
    then carried into prose). ``scripts/extract_srv_absence.py`` writes the result as a
    ``# Clustering:`` header line of ``srv2023_absence_household_by_size.csv`` so the figure stays
    reproducible from the committed file alone, without re-running the extraction against the
    local-only raw delivery.

    A household is "fully absent" when EVERY one of its delivered members has ``absent`` True
    (the same ``all_absent`` definition :func:`build_absence_household_by_size` uses). Returns a
    dict with the plain counts ``n_absent_persons`` / ``n_absent_in_fully_absent_households`` and
    ``unweighted_share`` / ``weighted_share`` (the PERSON weight ``GEWICHT_P_ZENSUS``, i.e.
    ``prepared["weight"]`` -- deliberately not the household weight
    :func:`build_absence_household_by_size` uses, because this figure describes a share of
    PERSONS). A share is ``NaN`` only when there are no absent persons at all (an empty
    numerator/denominator, never a substituted zero).
    """
    _require_columns(prepared, ["hhnr", "pnr", "weight", "absent"], "prepared")
    per_hh = prepared.groupby("hhnr").agg(n=("pnr", "size"), n_absent=("absent", "sum")).reset_index()
    per_hh["all_absent"] = per_hh["n_absent"] == per_hh["n"]
    merged = prepared.merge(per_hh[["hhnr", "all_absent"]], on="hhnr", how="left")
    absent = merged[merged["absent"]]
    n_absent_persons = int(len(absent))
    n_absent_in_fully_absent_households = int(absent["all_absent"].sum())
    weight_absent_total = float(absent["weight"].sum())
    weight_absent_clustered = float(absent.loc[absent["all_absent"], "weight"].sum())
    unweighted_share = (n_absent_in_fully_absent_households / n_absent_persons
                        if n_absent_persons else float("nan"))
    weighted_share = (weight_absent_clustered / weight_absent_total
                      if weight_absent_total > 0 else float("nan"))
    logger.info("%s clustering: %d/%d (%.2f%%) absent persons unweighted, %.2f%% person-weighted, "
               "live in a fully absent household", _LOG_TAG, n_absent_in_fully_absent_households,
               n_absent_persons, 100.0 * unweighted_share if n_absent_persons else float("nan"),
               100.0 * weighted_share if weight_absent_total > 0 else float("nan"))
    return {"n_absent_persons": n_absent_persons,
           "n_absent_in_fully_absent_households": n_absent_in_fully_absent_households,
           "unweighted_share": unweighted_share, "weighted_share": weighted_share}


def check_invariants(by_age: pd.DataFrame, by_size: pd.DataFrame, composition: pd.DataFrame = None,
                     partial_subset: pd.DataFrame = None) -> None:
    if list(by_age["band"]) != list(AGE_BAND_LABELS) + [ALL_BAND]:
        raise ValueError(f"{_LOG_TAG} by-age table must carry exactly the bands {AGE_BAND_LABELS} + 'all'")
    bands = by_age[by_age["band"] != ALL_BAND]
    all_row_n_unweighted = int(by_age.loc[by_age["band"] == ALL_BAND, "n_unweighted"].iloc[0])
    if int(bands["n_unweighted"].sum()) > all_row_n_unweighted:
        raise ValueError(f"{_LOG_TAG} band person counts exceed the 'all' row")
    for name, table, col in (("by_age", by_age, "p_absent"), ("by_size", by_size, "p_all_absent"),
                             ("by_size", by_size, "p_absent_person"),
                             ("by_size", by_size, "p_partial_absent")):
        shares = table[col].dropna()
        if ((shares < 0) | (shares > 1)).any():
            raise ValueError(f"{_LOG_TAG} {name}: {col} outside [0, 1]")
    if list(by_size["size_class"]) != list(range(1, HOUSEHOLD_SIZE_CLASS_TOP + 1)):
        raise ValueError(f"{_LOG_TAG} by-size table must carry size classes 1..{HOUSEHOLD_SIZE_CLASS_TOP}")
    # Person-level guards (issue #388): a size class can never report more absent persons than
    # persons, and every delivered person belongs to exactly one size class, so the by-size
    # person counts must reconcile with the by-age 'all' row (same universe, same prepared frame).
    if (by_size["n_absent_persons_unweighted"] > by_size["n_persons_unweighted"]).any():
        raise ValueError(f"{_LOG_TAG} by_size: n_absent_persons_unweighted exceeds n_persons_unweighted "
                         "for at least one size class")
    n_persons_total = int(by_size["n_persons_unweighted"].sum())
    if n_persons_total != all_row_n_unweighted:
        raise ValueError(f"{_LOG_TAG} by_size: sum(n_persons_unweighted)={n_persons_total} does not match "
                         f"the by-age 'all' row n_unweighted={all_row_n_unweighted}")
    # Partial-household columns (issue #426): none / partial / all partition each size class.
    if int(by_size.loc[by_size["size_class"] == 1, "n_partial_absent_unweighted"].iloc[0]) != 0:
        raise ValueError(f"{_LOG_TAG} by_size: size class 1 reports n_partial_absent_unweighted != 0, "
                         "but a single-person household cannot be partially absent")
    if ((by_size["n_all_absent_unweighted"] + by_size["n_partial_absent_unweighted"])
            > by_size["n_households_unweighted"]).any():
        raise ValueError(f"{_LOG_TAG} by_size: n_all_absent_unweighted + n_partial_absent_unweighted exceeds "
                         "n_households_unweighted for at least one size class")
    if composition is not None:
        expected_rows = list(AGE_BAND_LABELS) + [CHILDREN_ROW, ALL_BAND]
        if list(composition["band"]) != expected_rows:
            raise ValueError(f"{_LOG_TAG} composition table must carry exactly the rows {expected_rows}")
        counts = composition[list(COMPOSITION_COUNT_COLUMNS)].sum(axis=1)
        if (counts != composition["n_absent_unweighted"]).any():
            raise ValueError(f"{_LOG_TAG} composition: pattern counts do not sum to n_absent_unweighted")
        # The children row is exactly the union of the two child bands (a negative age code is in
        # neither), so every count column must reconcile: 0-5 + 6-17 == 0-17.
        by_band = composition.set_index("band")
        for column in ("n_absent_unweighted", *COMPOSITION_COUNT_COLUMNS):
            bands_sum = int(by_band.loc["0-5", column]) + int(by_band.loc["6-17", column])
            children_count = int(by_band.loc[CHILDREN_ROW, column])
            if bands_sum != children_count:
                raise ValueError(f"{_LOG_TAG} composition: {column} of rows 0-5 + 6-17 = {bands_sum} does not "
                                 f"equal the {CHILDREN_ROW} row's {children_count}")
        populated = composition[composition["n_absent_unweighted"] > 0]
        shares = populated[list(COMPOSITION_SHARE_COLUMNS)]
        if ((shares < 0) | (shares > 1)).any().any():
            raise ValueError(f"{_LOG_TAG} composition: a share is outside [0, 1]")
        if (np.abs(shares.sum(axis=1) - 1.0) > 1e-9).any():
            raise ValueError(f"{_LOG_TAG} composition: the three pattern shares do not sum to 1 in every populated row")
        n_absent_all = int(composition.loc[composition["band"] == ALL_BAND, "n_absent_unweighted"].iloc[0])
        n_absent_by_age = int(by_age.loc[by_age["band"] == ALL_BAND, "n_absent_unweighted"].iloc[0])
        if n_absent_all != n_absent_by_age:
            raise ValueError(f"{_LOG_TAG} composition 'all' n_absent_unweighted={n_absent_all} does not match the "
                             f"by-age 'all' row n_absent_unweighted={n_absent_by_age}")
    if partial_subset is not None:
        expected_keys = [(s, k) for s in range(2, HOUSEHOLD_SIZE_CLASS_TOP + 1)
                         for k in range(1, min(s - 1, PARTIAL_SUBSET_TOP) + 1)]
        if list(zip(partial_subset["size_class"], partial_subset["n_absent_members"])) != expected_keys:
            raise ValueError(f"{_LOG_TAG} partial-subset table must carry exactly the rows {expected_keys}")
        shares = partial_subset["share_within_partial"].dropna()
        if ((shares < 0) | (shares > 1)).any():
            raise ValueError(f"{_LOG_TAG} partial_subset: share_within_partial outside [0, 1]")
        per_class = partial_subset.groupby("size_class").agg(n=("n_households_unweighted", "sum"),
                                                              share=("share_within_partial", "sum"),
                                                              populated=("share_within_partial", "count"))
        if ((per_class["populated"] > 0) & (np.abs(per_class["share"] - 1.0) > 1e-9)).any():
            raise ValueError(f"{_LOG_TAG} partial_subset: shares within a size class do not sum to 1")
        by_size_partial = by_size.set_index("size_class")["n_partial_absent_unweighted"]
        for size_class, n_subset in per_class["n"].items():
            if int(n_subset) != int(by_size_partial.loc[size_class]):
                raise ValueError(f"{_LOG_TAG} partial_subset: size class {size_class} counts {int(n_subset)} partial "
                                 f"households but by_size reports n_partial_absent_unweighted="
                                 f"{int(by_size_partial.loc[size_class])}")
