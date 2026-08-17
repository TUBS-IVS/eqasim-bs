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
(they fall under ``other`` upstream), so only the four unambiguous purposes are
scored against W1 by default; the rest are reported descriptively. With the
optional ``escort_purpose`` flag ON (issue #201) the synthetic taxonomy
additionally carries ``escort -> begleitung``, a fifth purpose scored whenever
it is present in the synthetic distribution (see ``scored_mid_purposes``).
"""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd

LOGGER = logging.getLogger("braunschweig.analysis.population_validation.trip_coherence")

# Straight-line -> routed detour factor. The synthetic trips table carries
# ``euclidean_distance`` (straight-line, metres) while MiD W12 ``mittel_km`` is a
# ROUTED trip length. To compare fairly the synthetic straight-line distance is
# multiplied by the project-wide canonical factor (braunschweig.constants;
# the same factor the synthesis pipeline divides by). If the trips table
# carries a ``routed_distance`` column it is preferred directly.
from braunschweig.constants import ROUTED_DETOUR_FACTOR as DETOUR_FACTOR

# eqasim activity purpose -> MiD W1 Zweck. The four always-scored purposes,
# the conditionally-scored escort purpose, plus two explicit non-W1 categories
# (return-home trips, residual other).
EQASIM_TO_MID_PURPOSE = {
    "work": "arbeit",
    "education": "ausbildung",
    "shop": "einkauf",
    "leisure": "freizeit",
    "escort": "begleitung",  # issue #201; W1 carries a dedicated begleitung column
    "home": "heimweg",
    "other": "sonstiges",
}

# The MiD W1 purposes that have an unambiguous eqasim equivalent (scored subset).
SCORED_MID_PURPOSES = ("arbeit", "ausbildung", "einkauf", "freizeit")

# Scored set when the synthetic taxonomy carries the dedicated escort purpose
# (escort_purpose ON, issue #201): Begleitung becomes unambiguous and scoreable.
SCORED_MID_PURPOSES_WITH_ESCORT = SCORED_MID_PURPOSES + ("begleitung",)


def scored_mid_purposes(distribution) -> tuple:
    """Presence-based scored-purpose selection: include ``begleitung`` exactly
    when the synthetic purpose distribution contains it (i.e. the population
    was built with escort_purpose ON); a flag-OFF population keeps the
    four-purpose comparison unchanged."""
    if "begleitung" in distribution:
        return SCORED_MID_PURPOSES_WITH_ESCORT
    return SCORED_MID_PURPOSES


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


def renormalize_scored(distribution, scored_purposes=SCORED_MID_PURPOSES):
    """Restrict a {mid_purpose -> share} distribution to ``scored_purposes``
    (default: the four unambiguous W1 purposes) and re-normalise so they sum to
    1. This makes the synthetic and W1 distributions apples-to-apples on the
    scored purposes, removing the home/dienst/erledigung crosswalk ambiguity
    (and, unless ``begleitung`` is passed in ``scored_purposes``, escort) from
    the comparison."""
    scored = {p: float(distribution.get(p, 0.0)) for p in scored_purposes}
    total = sum(scored.values())
    if total <= 0:
        return {p: float("nan") for p in scored_purposes}
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


def w1_scored_target(data_path, scored_purposes=SCORED_MID_PURPOSES,
                     escort_passive_education=False):
    """MiD W1 (Wege je Zweck) ZGB-overall target, restricted and re-normalised to
    ``scored_purposes`` (default: {arbeit, ausbildung, einkauf, freizeit}). Pass
    ``scored_purposes=SCORED_MID_PURPOSES_WITH_ESCORT`` to additionally include
    ``begleitung`` (issue #201). The W1 columns are integer-percent shares per
    Kreis.

    ``escort_passive_education=True`` (issue #256) additionally adjusts the raw
    row BEFORE the restrict/renormalise step above: the model's escort purpose
    is ACTIVE-only under this flag, while the published W1 ``begleitung``
    column folds in both the active (W_ZWECK 6) and passive (W_ZWECK 13) MiD
    legs, so ``begleitung`` is scaled down to its active share and the passive
    remainder is folded into ``ausbildung`` (see
    ``apply_escort_active_adjustment``). The active share is loaded from the
    pinned ``mid2023_escort_w_zweck_split.csv`` (never hardcoded). The fold
    only fires when ``"begleitung"`` is itself in ``scored_purposes`` --
    mirroring ``w12_mean_length_target``'s ``"escort" in target`` guard -- so a
    caller that does not request the escort purpose gets a byte-identical
    no-op instead of a silently inflated ``ausbildung`` with the corresponding
    ``begleitung`` remainder dropped at the restriction below. Whenever the
    fold DOES fire, ``"ausbildung"`` must also be in ``scored_purposes`` (the
    passive remainder folds into it) -- raises ``ValueError`` otherwise, since
    silently dropping that remainder would corrupt the total. Default False
    keeps the original W1 target byte-identical."""
    row = _zgb_overall_row(data_path, "mid2023_W1")
    raw = {p: float(row[p]) for p in scored_purposes}
    # Presence guard: without 'begleitung' in scored_purposes there is no
    # escort mass in `raw` to redistribute, so the fold must be skipped --
    # applying it anyway would inflate 'ausbildung' from the full W1 row while
    # the 'begleitung' remainder is dropped (never added back anywhere) once
    # `raw` is restricted to scored_purposes, silently corrupting the target.
    if escort_passive_education and "begleitung" in scored_purposes:
        # 'ausbildung' must be in scored_purposes: the passive remainder folds
        # into it below (apply_escort_active_adjustment). Fail early instead of
        # silently dropping that redistributed mass once `raw` is restricted to
        # scored_purposes just below -- production callers always score
        # 'ausbildung' alongside 'begleitung' (SCORED_MID_PURPOSES_WITH_ESCORT).
        if "ausbildung" not in scored_purposes:
            raise ValueError(
                "escort_passive_education requires 'ausbildung' in scored_purposes "
                "(the passive remainder folds into it); got scored_purposes="
                f"{scored_purposes!r}.")
        shares = dict(raw)
        active_share = load_escort_active_share(data_path)
        shares = apply_escort_active_adjustment(shares, active_share)
        raw = {p: shares[p] for p in scored_purposes}
        LOGGER.info(
            "Trip coherence W1 target adjusted for escort_passive_education "
            "(issue #256): begleitung scaled to active share %.4f (pinned MiD "
            "escort W_ZWECK split); passive remainder folded into ausbildung",
            active_share)
    return renormalize_scored(raw, scored_purposes=scored_purposes)


def apply_escort_active_adjustment(shares: dict, active_share: float) -> dict:
    """W1 shares adjusted for escort_passive_education (issue #256): the model's
    escort purpose is ACTIVE-only, while published W1 Begleitung contains both
    sides (MiD folds W_ZWECK 13 into Begleitung). Scale begleitung to the active
    share and fold the passive remainder into ausbildung (the passive legs ARE
    education trips in the model). Mass-preserving; returns a fresh dict."""
    if not 0.0 < active_share <= 1.0:
        raise ValueError(f"active_share must be in (0, 1], got {active_share}.")
    out = dict(shares)
    begleitung = out.get("begleitung", 0.0)
    out["begleitung"] = begleitung * active_share
    out["ausbildung"] = out.get("ausbildung", 0.0) + begleitung * (1.0 - active_share)
    return out


def _load_escort_split_table(data_path: str) -> pd.DataFrame:
    """Load the pinned MiD escort W_ZWECK active/passive split CSV (issue
    #256), indexed by ``w_zweck`` ('code_6' / 'code_13' / 'both'). Shared by
    ``load_escort_active_share`` and ``load_escort_active_length_reference``
    so the missing-file guard is not duplicated between the two.

    Raises FileNotFoundError with a contextual message (naming the derivation
    script) if the pinned CSV is absent, instead of relying on pandas'
    generic error."""
    path = f"{data_path}/braunschweig/mid/mid2023_escort_w_zweck_split.csv"
    if not Path(path).exists():
        raise FileNotFoundError(
            f"Pinned escort W_ZWECK split CSV not found: {path} -- this is a "
            "pinned reference generated by scripts/derive_escort_w_zweck_split.py; "
            "regenerate it there, do not create it by hand.")
    return pd.read_csv(path, comment="#").set_index("w_zweck")


def load_escort_active_share(data_path: str) -> float:
    """W_GEW-weighted share of ACTIVE (W_ZWECK 6) legs among MiD escort legs,
    from the pinned mid2023_escort_w_zweck_split.csv (issue #256). Raises
    FileNotFoundError if the pinned CSV is missing (see
    ``_load_escort_split_table``)."""
    table = _load_escort_split_table(data_path)
    return float(table.loc["code_6", "share_weighted"])


def load_escort_active_length_reference(data_path: str) -> dict:
    """ACTIVE-only (W_ZWECK 6) escort length profile, from the pinned
    mid2023_escort_w_zweck_split.csv code_6 row (issue #256): the reference
    used to score the model's escort trip DISTANCE once escort_passive_education
    is ON, replacing the both-sides MiD W12 Begleitung row (mittel_km 10.1 km),
    which folds in the passive W_ZWECK 13 leg and is therefore not comparable to
    an active-only synthetic escort purpose (see w12_mean_length_target).

    Returns {mean_km, median_km, plus the nine length-band row-% shares
    (d_unter_0_5km .. d_100km_plus, same band convention as
    mid2023_W12_triplength_by_purpose.csv)}. Raises FileNotFoundError if the
    pinned CSV is missing (see ``_load_escort_split_table``)."""
    table = _load_escort_split_table(data_path)
    row = table.loc["code_6"]
    columns = ["mean_km", "median_km", "d_unter_0_5km", "d_0_5_1km", "d_1_2km",
               "d_2_5km", "d_5_10km", "d_10_20km", "d_20_50km", "d_50_100km",
               "d_100km_plus"]
    return {column: float(row[column]) for column in columns}


def _p36_mobile_share(row) -> float:
    """Mobility share from a P36_1 row, excluding item-nonresponse.

    The published P36.1 rows sum mobil + nicht_mobil + unbekannt = 100 with
    1-3% 'unbekannt' (item-nonresponse). The synthetic side has no 'unknown'
    state, so the like-for-like target is mobil / (mobil + nicht_mobil);
    keeping 'unbekannt' in the denominator understated the mobility target by
    up to 2.35pp per Kreis (2026-07-12 validation audit).
    """
    mobil = float(row["mobil"])
    nicht_mobil = float(row["nicht_mobil"])
    denominator = mobil + nicht_mobil
    if denominator <= 0:
        raise ValueError(
            "P36_1 row has non-positive mobil + nicht_mobil "
            f"({mobil} + {nicht_mobil}); cannot form a mobility share.")
    return mobil / denominator


def p36_mobility_target(data_path):
    """MiD P36_1 ZGB-overall mobility rate: share of persons that are 'mobil',
    with the 'unbekannt' item-nonresponse excluded from the denominator."""
    row = _zgb_overall_row(data_path, "mid2023_P36_1")
    return _p36_mobile_share(row)


# -- MiD W12 mean-trip-length-by-purpose coherence ---------------------------
#
# W12 (MiD 2023 Grossraum Braunschweig, infas 7555, Tabelle A W12) gives the
# arithmetic MEAN routed trip length (km, ``mittel_km``) per MiD Hauptwegezweck.
# By default only the FOUR unambiguous eqasim<->MiD purposes are scored, the
# same subset as the W1 purpose-distribution check above (SCORED_MID_PURPOSES):
# the MiD purposes ``dienstlich`` (business) and ``Erledigung`` (errands)
# crosswalk ambiguously onto eqasim ``work``/``other`` and are therefore
# excluded so the comparison stays apples-to-apples. ``Begleitung`` (escort)
# has an unambiguous eqasim equivalent when escort_purpose is ON (issue #201)
# and is scored via W12_PURPOSE_BY_MID_WITH_ESCORT below. There is no
# per-Kreis / ZGB-aggregate row in W12 -- one row per MiD purpose.
W12_PURPOSE_BY_MID = {
    "Arbeit": "work",
    "Ausbildung": "education",
    "Einkauf": "shop",
    "Freizeit": "leisure",
}

# With escort_purpose ON (issue #201) the Begleitung row (mittel 10.1 km in the
# committed W12 CSV) becomes scoreable against the synthetic escort purpose.
W12_PURPOSE_BY_MID_WITH_ESCORT = dict(W12_PURPOSE_BY_MID, Begleitung="escort")

# The four eqasim purposes scored against W12 by default (the values of
# W12_PURPOSE_BY_MID).
W12_SCORED_PURPOSES = tuple(W12_PURPOSE_BY_MID.values())


def w12_mean_length_target(data_path, include_escort=False,
                           escort_passive_education=False):
    """MiD W12 mean ROUTED trip length (km) per scored eqasim purpose.

    Reads ``mid2023_W12_triplength_by_purpose.csv`` (a ``# Source:`` comment line
    precedes the header) and maps the scored MiD Hauptwegezwecke onto their
    eqasim purpose. Returns {eqasim_purpose -> mittel_km}, e.g.
    {work: 15.2, education: 5.7, shop: 5.2, leisure: 15.0}. The km are MiD routed
    trip lengths, compared against the synthetic detour-inflated straight-line
    distance (see ``synthetic_mean_length_by_purpose``). ``include_escort=True``
    (issue #201) additionally maps ``Begleitung -> escort``, using
    W12_PURPOSE_BY_MID_WITH_ESCORT instead of the four-purpose default.

    ``escort_passive_education=True`` (issue #256; only meaningful together with
    ``include_escort=True``) additionally overrides the ``escort`` entry with
    the ACTIVE-only reference from the pinned MiD escort split CSV (see
    ``load_escort_active_length_reference``) instead of the both-sides MiD W12
    Begleitung row, since the model's escort purpose is active-only under this
    flag. Default False keeps the original target byte-identical.

    At the same guard, also logs an informational note (never a target change)
    that the ``education`` entry is intentionally left unadjusted even though
    the model's synthetic ``education`` purpose absorbs the relabeled passive
    escort legs upstream (issue #256/#257; see docs/features/escort-purpose.md
    Validation) -- the realised education mean is therefore expected to run
    definitionally higher than this target."""
    purpose_map = W12_PURPOSE_BY_MID_WITH_ESCORT if include_escort else W12_PURPOSE_BY_MID
    path = f"{data_path}/braunschweig/mid/mid2023_W12_triplength_by_purpose.csv"
    df = pd.read_csv(path, comment="#")
    by_mid = dict(zip(df["hauptwegezweck"].astype(str), df["mittel_km"].astype(float)))
    missing = set(purpose_map) - set(by_mid)
    if missing:
        raise ValueError(f"W12 table is missing scored purposes: {sorted(missing)}")
    target = {eqasim: float(by_mid[mid]) for mid, eqasim in purpose_map.items()}
    if escort_passive_education and "escort" in target:
        # Deliberate independent read of the same tiny pinned split CSV that
        # load_escort_active_share (via w1_scored_target) may already have
        # read earlier in the same build_trip_coherence_report call: both
        # loaders stay pure, single-purpose functions rather than sharing a
        # cache, and the committed file is a handful of rows, so the repeat
        # read is negligible.
        active_ref = load_escort_active_length_reference(data_path)
        LOGGER.info(
            "Trip coherence W12 escort length target adjusted for "
            "escort_passive_education (issue #256): using the active-only "
            "pinned split mean %.2f km instead of the both-sides MiD W12 "
            "Begleitung mean %.2f km", active_ref["mean_km"], target["escort"])
        target["escort"] = active_ref["mean_km"]

        # I-1 (combined review #256/#257): unlike escort above, 'education' is
        # deliberately left at the published (active-only-by-MiD's-own-
        # derivation) Ausbildung mean -- it is NOT passive-adjusted here or
        # anywhere else. But the model's synthetic 'education' purpose DOES
        # absorb the relabeled passive escort legs upstream
        # (escort_passive_education in map_purpose; see
        # docs/features/escort-purpose.md), which run longer on average (MiD
        # W_ZWECK 13 pinned mean) than genuine Ausbildung trips. The realised
        # education mean is therefore expected to be definitionally HIGHER
        # than this (unchanged) target, for a reason that has nothing to do
        # with model fit -- report it as definitional, never calibrate
        # against it. The expectation figure logged below is an ASSUMPTION
        # that mixes W1 trip-COUNT weights with W12 per-trip MEANS (a
        # weighted-average approximation, not an exact derivation); it is
        # stated purely for expectation-setting and is never used as a score.
        w1_row = _zgb_overall_row(data_path, "mid2023_W1")
        w1_education_share = float(w1_row["ausbildung"])
        w1_escort_share = float(w1_row["begleitung"])
        passive_share = 1.0 - load_escort_active_share(data_path)
        code_13_mean_km = float(
            _load_escort_split_table(data_path).loc["code_13", "mean_km"])
        education_km = target["education"]
        expected_education_km = (
            (w1_education_share * education_km
             + w1_escort_share * passive_share * code_13_mean_km)
            / (w1_education_share + w1_escort_share * passive_share))
        LOGGER.info(
            "Trip coherence W12 education-length target intentionally NOT "
            "passive-adjusted (issue #256/#257): kept at the published MiD "
            "Ausbildung mean %.2f km. The model's 'education' purpose absorbs "
            "the relabeled passive escort legs, so the realised education "
            "mean is expected DEFINITIONALLY higher, roughly (W1_edu %.1f x "
            "%.2f + W1_escort %.1f x passive_share %.4f x %.2f) / (W1_edu "
            "%.1f + W1_escort %.1f x passive_share %.4f) ~= %.2f km "
            "(ASSUMPTION mixing W1 trip weights with W12 means; "
            "expectation-setting only, not a scored target)",
            education_km, w1_education_share, education_km, w1_escort_share,
            passive_share, code_13_mean_km, w1_education_share,
            w1_escort_share, passive_share, expected_education_km)
    return target


def synthetic_mean_length_by_purpose(trips, *, detour_factor=DETOUR_FACTOR,
                                     purpose_col="following_purpose",
                                     purposes=W12_SCORED_PURPOSES):
    """Mean ROUTED trip length (km) per purpose in ``purposes`` of the synthetic
    trips, comparable to the MiD W12 ``mittel_km``.

    The synthetic trips carry ``euclidean_distance`` (straight-line, metres). It
    is converted to a routed-equivalent by ``routed_km = euclidean_distance / 1000
    * detour_factor`` (DETOUR_FACTOR = 1.3, the pipeline's factor). If the trips
    table instead carries a ``routed_distance`` column (metres, already routed),
    that is used directly (no detour multiply). NaN distances are skipped so they
    never propagate into a purpose mean.

    Returns {eqasim_purpose -> mean routed km} over ``purposes`` (default: the
    four scored purposes; pass the keys of ``w12_mean_length_target(...,
    include_escort=True)`` to additionally cover escort, issue #201); a purpose
    with no trips (or only NaN distances) yields NaN."""
    if "routed_distance" in trips.columns:
        routed_km = trips["routed_distance"].astype(float) / 1000.0
    else:
        routed_km = trips["euclidean_distance"].astype(float) / 1000.0 * float(detour_factor)
    work = pd.DataFrame({
        "purpose": trips[purpose_col].astype(str).values,
        "routed_km": routed_km.values,
    })
    result = {}
    for purpose in purposes:
        sub = work.loc[work["purpose"] == purpose, "routed_km"].dropna()
        result[purpose] = float(sub.mean()) if len(sub) else float("nan")
    return result


def w12_length_coherence(trips, data_path, *, detour_factor=DETOUR_FACTOR,
                         purpose_col="following_purpose", include_escort=False,
                         escort_passive_education=False):
    """W12 mean-trip-length coherence: per scored eqasim purpose, the synthetic
    realised mean routed km vs the MiD W12 target, with signed deltas.

    ``include_escort=True`` (issue #201) additionally scores MiD Begleitung
    (10.1 km mittel) against the synthetic escort purpose, using
    ``w12_mean_length_target(..., include_escort=True)``; the synthetic side is
    computed over exactly the same (target-dict) purposes, so escort is
    compared only when it is actually requested. The default False preserves
    the original four-purpose comparison.

    ``escort_passive_education=True`` (issue #256) additionally swaps the
    escort target for the active-only pinned-split reference (see
    ``w12_mean_length_target``); only meaningful together with
    ``include_escort=True``. Default False keeps the original behaviour
    byte-identical.

    Returns a list of dicts (one per scored purpose) with keys
    ``purpose, target_km, realised_km, delta_km, rel_delta`` (delta = realised -
    target, rel_delta = delta / target). Logs a one-line info summary in the same
    style as the W1/P36 trip-coherence logging."""
    target = w12_mean_length_target(data_path, include_escort=include_escort,
                                    escort_passive_education=escort_passive_education)
    realised = synthetic_mean_length_by_purpose(
        trips, detour_factor=detour_factor, purpose_col=purpose_col,
        purposes=tuple(target))
    rows = []
    for purpose in target:
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


# -- MiD P38.2 commute-distance-band coherence (per Kreis) --------------------
#
# P38.2 (MiD 2023 Grossraum Braunschweig, infas 7555, Tabelle A P38.2) gives the
# row-% distribution of the one-way commute distance ("Entfernung zur Arbeit")
# over distance bands, per Kreis plus the ZGB aggregate ("Gesamt"). The BAND
# SHARES are scored; the table's arithmetic ``mittel_km`` is reported only
# descriptively and deliberately NOT scored: it is dominated by the long-distance
# / weekly-commuter tail and the item-nonresponse column (e.g. Salzgitter
# mittel_km = 237.6 km), which a same-day straight-line x detour synthetic
# distance cannot and should not reproduce.
#
# The ``d_unplausibel_keine_angabe`` column is item non-response, not a distance
# band; it is excluded and the remaining band shares are re-normalised to 1.
P38_2_REGION_TO_ARS5 = {
    "Gesamt": "03ZGB",
    "Braunschweig": "03101",
    "Salzgitter": "03102",
    "Wolfsburg": "03103",
    "Landkreis Gifhorn": "03151",
    "Landkreis Goslar": "03153",
    "Landkreis Helmstedt": "03154",
    "Landkreis Peine": "03157",
    "Landkreis Wolfenbüttel": "03158",
}

# Ordered (column, lower_km, upper_km) commute-distance bands of P38.2.
P38_2_BANDS = (
    ("d_unter_5km", 0.0, 5.0),
    ("d_5_10km", 5.0, 10.0),
    ("d_10_20km", 10.0, 20.0),
    ("d_20_30km", 20.0, 30.0),
    ("d_30_50km", 30.0, 50.0),
    ("d_50_100km", 50.0, 100.0),
    ("d_100_200km", 100.0, 200.0),
    ("d_200_300km", 200.0, 300.0),
    ("d_300km_plus", 300.0, float("inf")),
)

P38_2_NONRESPONSE_COL = "d_unplausibel_keine_angabe"


def p38_2_band_target(data_path):
    """MiD P38.2 commute-distance band shares per region.

    Returns ``({ars5 -> {band_col -> share}}, {ars5 -> mittel_km})`` where the
    band shares exclude the item-nonresponse column and are re-normalised to sum
    to 1 per region. Regions are keyed by ars5 ("03ZGB" = the ZGB aggregate).
    Unknown region names in the CSV raise (fail-fast, no silent drop)."""
    path = f"{data_path}/braunschweig/mid/mid2023_P38_2_commute_distance_by_kreis.csv"
    df = pd.read_csv(path, comment="#")
    unknown = set(df["region"].astype(str).str.strip()) - set(P38_2_REGION_TO_ARS5)
    if unknown:
        raise ValueError(
            f"P38.2 table contains unmapped region names: {sorted(unknown)}. "
            "Extend P38_2_REGION_TO_ARS5 explicitly.")
    shares = {}
    means = {}
    for _, row in df.iterrows():
        ars5 = P38_2_REGION_TO_ARS5[str(row["region"]).strip()]
        raw = {col: float(row[col]) for col, _, _ in P38_2_BANDS}
        total = sum(raw.values())
        if total <= 0:
            raise ValueError(f"P38.2 row {row['region']!r} has no band mass.")
        shares[ars5] = {col: v / total for col, v in raw.items()}
        means[ars5] = float(row["mittel_km"])
    return shares, means


def _band_label(routed_km):
    """Assign each routed commute km value to its P38.2 band column."""
    bins = [lower for _, lower, _ in P38_2_BANDS] + [float("inf")]
    labels = [col for col, _, _ in P38_2_BANDS]
    return pd.cut(routed_km, bins=bins, labels=labels, right=False,
                  include_lowest=True)


def synthetic_commute_band_distribution(persons, trips, *,
                                        detour_factor=DETOUR_FACTOR,
                                        person_id_col="person_id",
                                        purpose_col="following_purpose",
                                        geo_col="ars5"):
    """Per-region commute-distance band shares of the synthetic population.

    The commute distance of a person is the distance of their FIRST work trip
    (home->work when ``preceding_purpose`` is available, else the first trip
    with ``following_purpose == 'work'``), converted to a routed-equivalent km
    (``routed_distance`` preferred, else ``euclidean_distance`` x detour
    factor; same convention as the W12 check). The universe is persons with at
    least one work trip -- the same commuter universe P38.2 describes.

    Returns ``({ars5 -> {band_col -> share}}, {ars5 -> n_commuters})``, with
    the additional aggregate key "03ZGB" over all persons."""
    work = trips[trips[purpose_col].astype(str) == "work"].copy()
    if "preceding_purpose" in work.columns:
        home_work = work[work["preceding_purpose"].astype(str) == "home"]
        # Persons whose chain never has a direct home->work leg keep their
        # first work trip (documented approximation, no person dropped).
        rest = work[~work[person_id_col].isin(home_work[person_id_col])]
        work = pd.concat([home_work, rest], ignore_index=True)
    first = work.drop_duplicates(subset=[person_id_col], keep="first")

    if "routed_distance" in first.columns:
        routed_km = first["routed_distance"].astype(float) / 1000.0
    else:
        routed_km = (first["euclidean_distance"].astype(float) / 1000.0
                     * float(detour_factor))
    frame = pd.DataFrame({
        person_id_col: first[person_id_col].values,
        "band": _band_label(routed_km.reset_index(drop=True)),
    })
    frame = frame.merge(
        persons[[person_id_col, geo_col]].drop_duplicates(person_id_col),
        on=person_id_col, how="left")

    def _shares(sub):
        # value_counts() drops NaN bands (commuters with no usable distance) by
        # default, silently shrinking the denominator. Count them explicitly and
        # log, so the share base is the full commuter universe minus a VISIBLE
        # no-distance remainder (2026-07-12 validation audit).
        n_total = len(sub)
        n_nan = int(sub["band"].isna().sum())
        counts = sub["band"].value_counts(dropna=True)
        total = int(counts.sum())
        if n_nan:
            LOGGER.warning(
                "P38.2 commute bands: %d/%d commuter(s) have no usable distance "
                "(NaN band) and are excluded from the share denominator",
                n_nan, n_total)
        if total == 0:
            return {col: float("nan") for col, _, _ in P38_2_BANDS}, 0
        return ({col: float(counts.get(col, 0)) / total
                 for col, _, _ in P38_2_BANDS}, total)

    shares = {}
    counts = {}
    shares["03ZGB"], counts["03ZGB"] = _shares(frame)
    for geo, sub in frame.groupby(geo_col, dropna=True):
        shares[str(geo)], counts[str(geo)] = _shares(sub)
    return shares, counts


def p38_2_commute_coherence(persons, trips, data_path, *,
                            detour_factor=DETOUR_FACTOR,
                            person_id_col="person_id",
                            purpose_col="following_purpose",
                            geo_col="ars5"):
    """P38.2 commute-distance-band coherence: per region (ZGB + Kreise) and band,
    the synthetic realised share vs the MiD P38.2 target share with signed delta.

    ``persons`` must carry the home ``geo_col`` (ars5). Returns a long DataFrame
    [ars5, band, lower_km, upper_km, target_share, realised_share, delta_pp,
    n_commuters, target_mean_km] (the mean km column is descriptive only, see
    the module comment) plus a one-line srmse log per region."""
    target, target_means = p38_2_band_target(data_path)
    realised, n_commuters = synthetic_commute_band_distribution(
        persons, trips, detour_factor=detour_factor,
        person_id_col=person_id_col, purpose_col=purpose_col, geo_col=geo_col)

    rows = []
    srmse_by_region = {}
    for ars5 in target:
        t = target[ars5]
        r = realised.get(ars5, {col: float("nan") for col, _, _ in P38_2_BANDS})
        srmse_by_region[ars5] = _srmse(
            {k: (0.0 if pd.isna(v) else v) for k, v in r.items()}, t)
        for col, lower, upper in P38_2_BANDS:
            t_share = float(t[col])
            r_share = float(r.get(col, float("nan")))
            rows.append({
                "ars5": ars5,
                "band": col,
                "lower_km": lower,
                "upper_km": upper,
                "target_share": t_share,
                "realised_share": r_share,
                "delta_pp": (r_share - t_share) * 100.0,
                "n_commuters": int(n_commuters.get(ars5, 0)),
                "target_mean_km": float(target_means[ars5]),
            })
    LOGGER.info(
        "Trip coherence P38.2 commute-distance bands (synthetic routed vs MiD), "
        "SRMSE per region: %s",
        ", ".join(f"{k} {v:.3f}" for k, v in sorted(srmse_by_region.items())))
    return pd.DataFrame(rows)


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
    """MiD P36_1 mobility rate per Kreis: {ars5 -> share mobile}, 'unbekannt'
    excluded from the denominator (see _p36_mobile_share)."""
    df = _mid_table_by_kreis(data_path, "mid2023_P36_1")
    return {row["ars5"]: _p36_mobile_share(row) for _, row in df.iterrows()}


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
    # Persons without a Kreis join (geo is NaN) must not become an undocumented
    # 9th "ars5=NaN" output row -- count and log them, then skip (2026-07-12
    # validation audit). dropna=True on the groupby drops the NaN group; the
    # explicit count keeps the drop observable (no-silent-fallback rule).
    n_no_geo = int(persons[geo_col].isna().sum())
    if n_no_geo:
        LOGGER.warning(
            "trip_coherence_by_kreis: %d person(s) have no %s (Kreis) assignment "
            "and are excluded from the per-Kreis table", n_no_geo, geo_col)
    for geo, grp in persons.groupby(geo_col, dropna=True):
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
                                purpose_col="following_purpose",
                                escort_passive_education=False):
    """Assemble the trip-coherence report comparing the donor-derived activity
    chains against MiD W1 (purpose) and P36_1 (mobility) targets.

    ``escort_passive_education=True`` (issue #256) adjusts both the W1 purpose
    target and the W12 escort length target to the active-only pinned-split
    reference instead of the both-sides MiD Begleitung figures, since the
    model's escort purpose is active-only under this flag (see
    ``w1_scored_target`` / ``w12_mean_length_target``). Only has an effect when
    the synthetic distribution actually carries ``begleitung`` (escort_purpose
    ON upstream); default False keeps the report byte-identical.

    Returns a dict with:
      - ``mobility``: {overall_rate, target_rate, abs_delta}
      - ``mobility_by_segment``: long DataFrame [segment, segment_value,
        n_persons, n_mobile, mobility_rate] for every requested segment column
        present in ``persons``
      - ``purpose``: {realized, target, abs_delta_pp, srmse} over the scored W1
        purposes (re-normalised on both sides): the four unambiguous purposes,
        plus ``begleitung`` when the synthetic distribution carries it (i.e. the
        population was built with escort_purpose ON, issue #201) -- see
        ``scored_mid_purposes``
      - ``length``: list of per-scored-purpose dicts {purpose, target_km,
        realised_km, delta_km, rel_delta} comparing the synthetic mean routed
        trip length (detour-inflated straight-line) against MiD W12 ``mittel_km``;
        includes escort under the same presence-based rule as ``purpose``
      - ``n_persons``, ``n_trips``
    """
    overall = mobility_rate(persons, trips, person_id_col)
    target_mob = p36_mobility_target(data_path)

    # Presence-based scored-purpose selection (issue #201): a population built
    # with escort_purpose ON carries "begleitung" in the synthetic distribution
    # and is scored against the five-purpose W1/W12 targets; a flag-OFF
    # population has no "begleitung" and keeps the original four-purpose
    # comparison (byte-identical default behaviour).
    synth_distribution = purpose_distribution(trips, purpose_col)
    scored = scored_mid_purposes(synth_distribution)
    realized = renormalize_scored(synth_distribution, scored_purposes=scored)
    target_pur = w1_scored_target(data_path, scored_purposes=scored,
                                  escort_passive_education=escort_passive_education)
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
        length = w12_length_coherence(
            trips, data_path, purpose_col=purpose_col,
            include_escort=("begleitung" in synth_distribution),
            escort_passive_education=escort_passive_education)
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
