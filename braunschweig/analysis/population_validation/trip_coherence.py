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


def purpose_participation_by_segment(persons, trips, segment_col,
                                     purpose_value="work",
                                     person_id_col="person_id",
                                     purpose_col="following_purpose"):
    """Share of persons with at least one trip of ``purpose_value`` (eqasim
    purpose, e.g. "work") per value of ``segment_col``. The work-by-employed
    version is the most direct "is the matching key working" KPI. Returns
    [segment, segment_value, n_persons, n_with_purpose, participation_rate]."""
    has_purpose_ids = set(
        trips.loc[trips[purpose_col] == purpose_value, person_id_col].unique())
    df = persons[[person_id_col, segment_col]].copy()
    df["has_purpose"] = df[person_id_col].isin(has_purpose_ids)
    grouped = df.groupby(segment_col, dropna=False)["has_purpose"].agg(["size", "sum"])
    grouped = grouped.reset_index().rename(
        columns={segment_col: "segment_value", "size": "n_persons", "sum": "n_with_purpose"})
    grouped["segment"] = segment_col
    grouped["purpose"] = purpose_value
    grouped["participation_rate"] = grouped["n_with_purpose"] / grouped["n_persons"]
    return grouped[["segment", "segment_value", "purpose", "n_persons",
                    "n_with_purpose", "participation_rate"]]


def trips_per_person_by_segment(persons, trips, segment_col,
                                person_id_col="person_id"):
    """Mean number of trips per person per value of ``segment_col`` (trip
    generation differentiation). Returns [segment, segment_value, n_persons,
    n_trips, trips_per_person]."""
    counts = trips.groupby(person_id_col).size().rename("n_trips")
    df = persons[[person_id_col, segment_col]].copy()
    df = df.merge(counts, left_on=person_id_col, right_index=True, how="left")
    df["n_trips"] = df["n_trips"].fillna(0)
    grouped = df.groupby(segment_col, dropna=False)["n_trips"].agg(["size", "sum"])
    grouped = grouped.reset_index().rename(
        columns={segment_col: "segment_value", "size": "n_persons", "sum": "n_trips"})
    grouped["segment"] = segment_col
    grouped["trips_per_person"] = grouped["n_trips"] / grouped["n_persons"]
    return grouped[["segment", "segment_value", "n_persons", "n_trips", "trips_per_person"]]


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


def _mid_table_by_kreis(data_path, table):
    """Load a MiD reference table and drop the ZGB-aggregate row, returning the
    per-Kreis rows keyed by the 5-digit ``ars5``."""
    path = f"{data_path}/braunschweig/mid/{table}.csv"
    df = pd.read_csv(path, comment="#", dtype={"ars5": str})
    return df[df["ars5"] != "03ZGB"].copy()


def w1_scored_target_by_kreis(data_path):
    """MiD W1 scored four-purpose target per Kreis: {ars5 -> {purpose -> share}}."""
    df = _mid_table_by_kreis(data_path, "mid2023_W1")
    return {
        row["ars5"]: renormalize_scored({p: float(row[p]) for p in SCORED_MID_PURPOSES})
        for _, row in df.iterrows()
    }


def p36_mobility_target_by_kreis(data_path):
    """MiD P36_1 mobility rate per Kreis: {ars5 -> share mobile}."""
    df = _mid_table_by_kreis(data_path, "mid2023_P36_1")
    return {row["ars5"]: float(row["mobil"]) / 100.0 for _, row in df.iterrows()}


def trip_coherence_by_kreis(persons, trips, data_path, geo_col="ars5",
                            person_id_col="person_id", purpose_col="following_purpose"):
    """Per-Kreis trip coherence for spatial visualisation: realised mobility rate
    and scored purpose shares per Kreis, each with its MiD per-Kreis target
    (P36_1 / W1) and signed delta, plus work-trip participation. Returns a
    DataFrame keyed by ``ars5`` (joinable to the Kreis polygons).

    ``persons`` must carry the home ``geo_col`` (ars5)."""
    w1 = w1_scored_target_by_kreis(data_path)
    p36 = p36_mobility_target_by_kreis(data_path)
    tp = trips.merge(persons[[person_id_col, geo_col]], on=person_id_col, how="left")

    rows = []
    for geo, grp in persons.groupby(geo_col, dropna=False):
        geo_trips = tp[tp[geo_col] == geo]
        mobile_ids = set(geo_trips[person_id_col].unique())
        work_ids = set(geo_trips.loc[geo_trips[purpose_col] == "work", person_id_col])
        mob = float(grp[person_id_col].isin(mobile_ids).mean())
        realised = renormalize_scored(purpose_distribution(geo_trips, purpose_col))
        target_mob = p36.get(geo, float("nan"))
        target_pur = w1.get(geo, {})

        row = {
            geo_col: geo,
            "n_persons": int(len(grp)),
            "mobility_rate": mob,
            "mobility_target": float(target_mob),
            "mobility_delta_pp": (mob - target_mob) * 100.0,
            "work_participation": float(grp[person_id_col].isin(work_ids).mean()),
        }
        for p in SCORED_MID_PURPOSES:
            r = realised.get(p, float("nan"))
            t = float(target_pur.get(p, float("nan")))
            row[f"purpose_{p}_realised"] = r
            row[f"purpose_{p}_w1"] = t
            row[f"purpose_{p}_delta_pp"] = (r - t) * 100.0
        rows.append(row)
    return pd.DataFrame(rows)


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
# evaluated. ``is_urban_resident`` is the urbanity column written to the output
# persons.csv; ``urban_class`` is the raw matching-frame column -- listing both
# means the breakdown works on a finished run output and on a raw frame.
# ``household_size`` is merged onto persons from the households frame by the
# runner before this is called.
DEFAULT_SEGMENT_COLS = ("employed", "is_urban_resident", "urban_class", "household_size")


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

    # Differentiation KPIs (does the richer matching produce demographically
    # plausible, segment-specific activity chains?). Work-trip participation and
    # trips-per-person by each present segment, plus the headline employed gap.
    work_frames = [
        purpose_participation_by_segment(persons, trips, c, "work", person_id_col)
        for c in present
    ]
    work_participation = (pd.concat(work_frames, ignore_index=True)
                          if work_frames else pd.DataFrame())
    tpp_frames = [
        trips_per_person_by_segment(persons, trips, c, person_id_col) for c in present
    ]
    trips_per_person = (pd.concat(tpp_frames, ignore_index=True)
                        if tpp_frames else pd.DataFrame())

    differentiation = {"work_share_employed_gap_pp": float("nan")}
    if "employed" in persons.columns:
        wp = purpose_participation_by_segment(persons, trips, "employed", "work",
                                              person_id_col)
        by_val = {bool(v): r for v, r in
                  zip(wp["segment_value"], wp["participation_rate"])}
        if True in by_val and False in by_val:
            differentiation["work_share_employed_gap_pp"] = (
                float(by_val[True] - by_val[False]) * 100.0)

    return {
        "n_persons": int(len(persons)),
        "n_trips": int(len(trips)),
        "mobility": {
            "overall_rate": float(overall),
            "target_rate": float(target_mob),
            "abs_delta": abs(float(overall) - float(target_mob)),
        },
        "mobility_by_segment": by_segment,
        "work_participation_by_segment": work_participation,
        "trips_per_person_by_segment": trips_per_person,
        "differentiation": differentiation,
        "purpose": {
            "realized": realized,
            "target": target_pur,
            "abs_delta_pp": abs_delta_pp,
            "srmse": _srmse(realized, target_pur),
        },
    }
