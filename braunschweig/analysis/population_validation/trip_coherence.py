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

import logging

import numpy as np
import pandas as pd

LOGGER = logging.getLogger("braunschweig.analysis.population_validation.trip_coherence")

# Straight-line -> routed detour factor. The synthetic trips table carries
# ``euclidean_distance`` (straight-line, metres) while MiD W12 ``mittel_km`` is a
# ROUTED trip length. To compare fairly the synthetic straight-line distance is
# multiplied by this factor (the same ``DETOUR_FACTOR = 1.3`` the synthesis
# pipeline uses in braunschweig/popsim/trips_stage.py and
# synthesis/population/trips.py). If the trips table carries a ``routed_distance``
# column it is preferred directly (already routed -> no detour multiply).
DETOUR_FACTOR = 1.3

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


# -- MiD W12 mean-trip-length-by-purpose coherence ---------------------------
#
# W12 (MiD 2023 Grossraum Braunschweig, infas 7555, Tabelle A W12) gives the
# arithmetic MEAN routed trip length (km, ``mittel_km``) per MiD Hauptwegezweck.
# Only the FOUR unambiguous eqasim<->MiD purposes are scored, the same subset as
# the W1 purpose-distribution check above (SCORED_MID_PURPOSES): the MiD purposes
# ``dienstlich`` (business), ``Erledigung`` (errands) and ``Begleitung``
# (escort/accompanying) crosswalk ambiguously onto eqasim ``work``/``other`` and
# are therefore excluded so the comparison stays apples-to-apples. There is no
# per-Kreis / ZGB-aggregate row in W12 -- one row per MiD purpose.
W12_PURPOSE_BY_MID = {
    "Arbeit": "work",
    "Ausbildung": "education",
    "Einkauf": "shop",
    "Freizeit": "leisure",
}

# The four eqasim purposes scored against W12 (the values of W12_PURPOSE_BY_MID).
W12_SCORED_PURPOSES = tuple(W12_PURPOSE_BY_MID.values())


def w12_mean_length_target(data_path):
    """MiD W12 mean ROUTED trip length (km) per scored eqasim purpose.

    Reads ``mid2023_W12_triplength_by_purpose.csv`` (a ``# Source:`` comment line
    precedes the header) and maps the four unambiguous MiD Hauptwegezwecke onto
    their eqasim purpose. Returns {eqasim_purpose -> mittel_km}, e.g.
    {work: 15.2, education: 5.7, shop: 5.2, leisure: 15.0}. The km are MiD routed
    trip lengths, compared against the synthetic detour-inflated straight-line
    distance (see ``synthetic_mean_length_by_purpose``)."""
    path = f"{data_path}/braunschweig/mid/mid2023_W12_triplength_by_purpose.csv"
    df = pd.read_csv(path, comment="#")
    by_mid = dict(zip(df["hauptwegezweck"].astype(str), df["mittel_km"].astype(float)))
    missing = set(W12_PURPOSE_BY_MID) - set(by_mid)
    if missing:
        raise ValueError(f"W12 table is missing scored purposes: {sorted(missing)}")
    return {eqasim: float(by_mid[mid]) for mid, eqasim in W12_PURPOSE_BY_MID.items()}


def synthetic_mean_length_by_purpose(trips, *, detour_factor=DETOUR_FACTOR,
                                     purpose_col="following_purpose"):
    """Mean ROUTED trip length (km) per scored eqasim purpose of the synthetic
    trips, comparable to the MiD W12 ``mittel_km``.

    The synthetic trips carry ``euclidean_distance`` (straight-line, metres). It
    is converted to a routed-equivalent by ``routed_km = euclidean_distance / 1000
    * detour_factor`` (DETOUR_FACTOR = 1.3, the pipeline's factor). If the trips
    table instead carries a ``routed_distance`` column (metres, already routed),
    that is used directly (no detour multiply). NaN distances are skipped so they
    never propagate into a purpose mean.

    Returns {eqasim_purpose -> mean routed km} over the four scored purposes; a
    purpose with no trips (or only NaN distances) yields NaN."""
    if "routed_distance" in trips.columns:
        routed_km = trips["routed_distance"].astype(float) / 1000.0
    else:
        routed_km = trips["euclidean_distance"].astype(float) / 1000.0 * float(detour_factor)
    work = pd.DataFrame({
        "purpose": trips[purpose_col].astype(str).values,
        "routed_km": routed_km.values,
    })
    result = {}
    for purpose in W12_SCORED_PURPOSES:
        sub = work.loc[work["purpose"] == purpose, "routed_km"].dropna()
        result[purpose] = float(sub.mean()) if len(sub) else float("nan")
    return result


def w12_length_coherence(trips, data_path, *, detour_factor=DETOUR_FACTOR,
                         purpose_col="following_purpose"):
    """W12 mean-trip-length coherence: per scored eqasim purpose, the synthetic
    realised mean routed km vs the MiD W12 target, with signed deltas.

    Returns a list of dicts (one per scored purpose) with keys
    ``purpose, target_km, realised_km, delta_km, rel_delta`` (delta = realised -
    target, rel_delta = delta / target). Logs a one-line info summary in the same
    style as the W1/P36 trip-coherence logging."""
    target = w12_mean_length_target(data_path)
    realised = synthetic_mean_length_by_purpose(
        trips, detour_factor=detour_factor, purpose_col=purpose_col)
    rows = []
    for purpose in W12_SCORED_PURPOSES:
        t = float(target[purpose])
        r = float(realised.get(purpose, float("nan")))
        delta = r - t
        rel = delta / t if t else float("nan")
        rows.append({
            "purpose": purpose,
            "target_km": t,
            "realised_km": r,
            "delta_km": delta,
            "rel_delta": rel,
        })
    summary = ", ".join(
        f"{row['purpose']} {row['realised_km']:.1f}/{row['target_km']:.1f}km "
        f"(d {row['delta_km']:+.1f})" for row in rows)
    LOGGER.info(
        "Trip coherence W12 mean trip length per purpose (synthetic routed vs MiD): %s",
        summary)
    return rows


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
      - ``length``: list of per-scored-purpose dicts {purpose, target_km,
        realised_km, delta_km, rel_delta} comparing the synthetic mean routed
        trip length (detour-inflated straight-line) against MiD W12 ``mittel_km``
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

    # W12 mean-trip-length coherence. Needs a distance column on the trips frame
    # (``routed_distance`` preferred, else detour-inflated ``euclidean_distance``).
    # Absent on some narrow run-output schemas -> reported as None, not invented.
    length = None
    if "routed_distance" in trips.columns or "euclidean_distance" in trips.columns:
        length = w12_length_coherence(trips, data_path, purpose_col=purpose_col)
    else:
        LOGGER.info(
            "Trip coherence W12 length check skipped: trips carry neither "
            "'routed_distance' nor 'euclidean_distance'.")

    return {
        "n_persons": int(len(persons)),
        "n_trips": int(len(trips)),
        "length": length,
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
