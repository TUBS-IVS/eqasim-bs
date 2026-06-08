"""Trip-coherence check (optimization step 2).

Compares the donor-derived activity chains of the synthetic population against
real MiD 2023 targets, segmented by the exogenous anchors used for HTS matching
(``employed`` / ``urban_class`` / ``household_size``). It is the closed-loop
measurement that makes the richer matching keys (step 1) evaluable, and it
quantifies the residual donor bias that motivates the planned MiD-donor
replacement (step 3).

Scope (synthesis output, no MATSim run required):
- trip-purpose distribution  vs MiD W1  (``mid2023_W1.csv``, Wege je Zweck/Kreis)
- mobility rate              vs MiD P36_1 (``mid2023_P36_1.csv``, mobil/nicht mobil)

The realised modal split is deliberately NOT computed here: the synthesis
``trips.csv`` carries no transport mode (it is written only by the MATSim
mode-choice run), and donor-inherited modes would be French-biased regardless.
That comparison belongs to the MATSim-output validation (``run_mid_validation``)
and is the very gap the MiD-donor replacement closes.

Crosswalk eqasim purpose -> MiD W1 Zweck (documented approximation):
    work -> arbeit, education -> ausbildung, shop -> einkauf, leisure -> freizeit
``home`` (return trips) and ``other`` are kept as explicit, separate categories
(``heimweg`` / ``sonstiges``) and are never silently folded into a W1 purpose.
The eqasim taxonomy has no direct equivalent of MiD ``dienst`` / ``erledigung``
/ ``begleitung`` (they fall under ``other`` upstream), so only the four
unambiguous purposes are scored against W1; the rest are reported descriptively.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# eqasim activity purpose -> MiD W1 Zweck. The four scored purposes plus two
# explicit non-W1 categories (return-home trips, residual other).
EQASIM_TO_MID_PURPOSE = {
    "work": "arbeit",
    "education": "ausbildung",
    "shop": "einkauf",
    "leisure": "freizeit",
    "home": "heimweg",
    "other": "sonstiges",
}

# The MiD W1 purposes that have an unambiguous eqasim equivalent (scored subset).
SCORED_MID_PURPOSES = ("arbeit", "ausbildung", "einkauf", "freizeit")

# Non-activity purpose excluded from the activity-purpose distribution (a trip
# back home is a return trip, not an activity Zweck in the W1 sense).
RETURN_HOME_PURPOSE = "heimweg"


def mid_purpose_from_eqasim(purposes):
    """Map eqasim activity purposes onto MiD W1 categories.

    Unknown purposes raise rather than silently mapping to ``sonstiges`` so a
    changed eqasim taxonomy surfaces loudly (CLAUDE.md: no silent fallback)."""
    series = pd.Series(purposes).astype(str)
    unknown = set(series.unique()) - set(EQASIM_TO_MID_PURPOSE)
    if unknown:
        raise ValueError(f"Unmapped eqasim purposes: {sorted(unknown)}")
    return series.map(EQASIM_TO_MID_PURPOSE)


def mobility_rate(persons, trips, person_id_col="person_id"):
    """Share of persons with at least one trip (MiD P36_1 'mobil')."""
    n_persons = len(persons)
    if n_persons == 0:
        return float("nan")
    mobile_ids = set(trips[person_id_col].unique())
    n_mobile = persons[person_id_col].isin(mobile_ids).sum()
    return float(n_mobile) / float(n_persons)


def purpose_distribution(trips, purpose_col="following_purpose"):
    """Distribution (shares) of activity-trip purposes mapped to MiD categories,
    taken over NON-home destinations (return-home trips excluded). Returns a dict
    {mid_purpose -> share}; empty if there are no activity trips."""
    mid = mid_purpose_from_eqasim(trips[purpose_col])
    activity = mid[mid != RETURN_HOME_PURPOSE]
    if len(activity) == 0:
        return {}
    return activity.value_counts(normalize=True).to_dict()


def renormalize_scored(distribution):
    """Restrict a {mid_purpose -> share} distribution to the four scored W1
    purposes and re-normalise so they sum to 1. This makes the synthetic and W1
    distributions apples-to-apples on the unambiguous purposes, removing the
    home/dienst/erledigung/begleitung crosswalk ambiguity from the comparison."""
    scored = {p: float(distribution.get(p, 0.0)) for p in SCORED_MID_PURPOSES}
    total = sum(scored.values())
    if total <= 0:
        return {p: float("nan") for p in SCORED_MID_PURPOSES}
    return {p: v / total for p, v in scored.items()}


def _zgb_overall_row(data_path, table):
    """Return the ZGB-aggregate (``ars5 == '03ZGB'``) row of a MiD reference
    table as a Series."""
    path = f"{data_path}/braunschweig/mid/{table}.csv"
    df = pd.read_csv(path, comment="#", dtype={"ars5": str})
    overall = df[df["ars5"] == "03ZGB"]
    if len(overall) != 1:
        raise ValueError(f"Expected exactly one '03ZGB' row in {table}, got {len(overall)}.")
    return overall.iloc[0]


def w1_scored_target(data_path):
    """MiD W1 (Wege je Zweck) ZGB-overall target, restricted and re-normalised to
    the four scored purposes. Returns {arbeit, ausbildung, einkauf, freizeit ->
    share}. The W1 columns are integer-percent shares per Kreis."""
    row = _zgb_overall_row(data_path, "mid2023_W1")
    raw = {p: float(row[p]) for p in SCORED_MID_PURPOSES}
    return renormalize_scored(raw)


def p36_mobility_target(data_path):
    """MiD P36_1 ZGB-overall mobility rate (share of persons that are 'mobil')."""
    row = _zgb_overall_row(data_path, "mid2023_P36_1")
    return float(row["mobil"]) / 100.0


def segment_mobility_rate(persons, trips, segment_col, person_id_col="person_id"):
    """Mobility rate per value of ``segment_col`` (e.g. employed / urban_class /
    household_size). Returns a long-form DataFrame
    [segment, segment_value, n_persons, n_mobile, mobility_rate]."""
    mobile_ids = set(trips[person_id_col].unique())
    df = persons[[person_id_col, segment_col]].copy()
    df["is_mobile"] = df[person_id_col].isin(mobile_ids)
    grouped = df.groupby(segment_col, dropna=False)["is_mobile"].agg(["size", "sum"])
    grouped = grouped.reset_index().rename(
        columns={segment_col: "segment_value", "size": "n_persons", "sum": "n_mobile"})
    grouped["segment"] = segment_col
    grouped["mobility_rate"] = grouped["n_mobile"] / grouped["n_persons"]
    return grouped[["segment", "segment_value", "n_persons", "n_mobile", "mobility_rate"]]


# Default segmentation dimensions: the exogenous anchors used as matching keys
# in optimization step (1). Only those actually present in the persons frame are
# evaluated (urban_class is present only when the matching feature is enabled).
DEFAULT_SEGMENT_COLS = ("employed", "urban_class", "household_size")


def _srmse(realized: dict, target: dict) -> float:
    """Standardised RMSE of a realised vs. target share distribution over a
    common key set: sqrt(mean((r - t)^2)) / mean(t)."""
    keys = sorted(set(realized) | set(target))
    r = np.array([realized.get(k, 0.0) for k in keys], dtype=float)
    t = np.array([target.get(k, 0.0) for k in keys], dtype=float)
    mean_t = t.mean()
    if mean_t <= 0:
        return float("nan")
    return float(np.sqrt(np.mean((r - t) ** 2)) / mean_t)


def build_trip_coherence_report(persons, trips, data_path,
                                segment_cols=DEFAULT_SEGMENT_COLS,
                                person_id_col="person_id",
                                purpose_col="following_purpose"):
    """Assemble the trip-coherence report comparing the donor-derived activity
    chains against MiD W1 (purpose) and P36_1 (mobility) targets.

    Returns a dict with:
      - ``mobility``: {overall_rate, target_rate, abs_delta}
      - ``mobility_by_segment``: long DataFrame [segment, segment_value,
        n_persons, n_mobile, mobility_rate] for every requested segment column
        present in ``persons``
      - ``purpose``: {realized, target, abs_delta_pp, srmse} over the four scored
        W1 purposes (re-normalised on both sides)
      - ``n_persons``, ``n_trips``
    """
    overall = mobility_rate(persons, trips, person_id_col)
    target_mob = p36_mobility_target(data_path)

    realized = renormalize_scored(purpose_distribution(trips, purpose_col))
    target_pur = w1_scored_target(data_path)
    abs_delta_pp = {
        p: abs(realized.get(p, float("nan")) - target_pur[p]) * 100.0
        for p in target_pur
    }

    present = [c for c in segment_cols if c in persons.columns]
    segment_frames = [
        segment_mobility_rate(persons, trips, c, person_id_col) for c in present
    ]
    by_segment = (pd.concat(segment_frames, ignore_index=True)
                  if segment_frames else pd.DataFrame(
                      columns=["segment", "segment_value", "n_persons",
                               "n_mobile", "mobility_rate"]))

    return {
        "n_persons": int(len(persons)),
        "n_trips": int(len(trips)),
        "mobility": {
            "overall_rate": float(overall),
            "target_rate": float(target_mob),
            "abs_delta": abs(float(overall) - float(target_mob)),
        },
        "mobility_by_segment": by_segment,
        "purpose": {
            "realized": realized,
            "target": target_pur,
            "abs_delta_pp": abs_delta_pp,
            "srmse": _srmse(realized, target_pur),
        },
    }
