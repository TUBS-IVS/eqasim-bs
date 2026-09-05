"""SrV 2023 distance-distribution targets for the primary-activity location models.

Builders turn the local-only SrV 2023 "Braunschweig und RGB" scientific-use microdata
(trips + persons + households) into small committed aggregate tables per home Kreis:
work and education distance band shares (with an intra/inter-Gemeinde split for work),
per-Kreis distance quantiles for the per-person commute-distance targets, and (addendum
Task 15) a SENSITIVITY table -- NOT a target -- measuring two known caveats of the work
table (destinations outside the surveyed ZGB polygon, and the GIS-invalid tail) as
measured band-share variants. Loaders read the committed tables back. This module has
no synpp dependency. It is imported by
the analysis stages ``braunschweig.analysis.reference.srv.commute_distance`` and
``braunschweig.analysis.synthesis.commute_distance_by_kreis`` (and the extraction
script), but by no POPULATION-synthesis or location stage, so editing it never
devalidates a cached POPULATION-synthesis or location result (the two analysis stages
above are re-executed, which is intended).

Conventions (spec docs/superpowers/specs/2026-09-03-srv-primary-distance-calibration-design.md):
- observation unit = person: first home->purpose trip, else first purpose->home trip;
- distance = GIS-routed km (``GIS_LAENGE``) where ``GIS_LAENGE_GUELTIG > 0``; invalid rows
  are excluded and their share is reported;
- weight = ``GEWICHT_W_ZENSUS`` (expansion weight), rows with negative weight dropped;
- levels follow the model's AGE banding because the model's education output has no
  level column (oberstufe and bbs are pooled into ``upper_secondary``).
"""
from __future__ import annotations

import logging
import os

import numpy as np
import pandas as pd

from braunschweig.gravity.friction import BAND_EDGES_KM, band_index

logger = logging.getLogger(__name__)

WORK_BAND_EDGES_KM = BAND_EDGES_KM
WORK_BAND_LABELS = ("0_5", "5_10", "10_20", "20_30", "30_50", "50_100", "100_plus")
EDUCATION_BAND_EDGES_KM = (0.0, 1.0, 2.0, 5.0, 10.0, 20.0, float("inf"))
EDUCATION_BAND_LABELS = ("0_1", "1_2", "2_5", "5_10", "10_20", "20_plus")

# SrV V_ZWECK destination-purpose codes (codebook SrV2023_Datenkodierung_SciUse.xlsx).
PURPOSE_WORK = 1
PURPOSE_BUSINESS = 2          # excluded: "Anderer Dienstort/-weg"
PURPOSE_KITA = 3
PURPOSE_GRUNDSCHULE = 4
PURPOSE_SCHOOL_SECONDARY = 5  # "Weiterfuehrende Schule"
PURPOSE_TERTIARY = 6          # "Berufs-, Fach-, Hochschule"
PURPOSE_OTHER_EDUCATION = 7   # excluded: "Andere Bildungseinrichtung"
EDUCATION_PURPOSES = (PURPOSE_KITA, PURPOSE_GRUNDSCHULE, PURPOSE_SCHOOL_SECONDARY, PURPOSE_TERTIARY)

COMPARABLE_LEVELS = ("kindergarten", "grundschule", "sekundar_1", "upper_secondary", "university")
DESCRIPTIVE_ONLY_LEVELS = ("oberstufe", "bbs")

# Model age banding (braunschweig.synthesis.locations.education_gravity._SCHOOL_BANDS):
# kindergarten 0-5, grundschule 6-9, sekundar_1 10-15, upper_secondary 16-19, university 20+.
_MODEL_AGE_LEVELS = (
    (0, 5, "kindergarten"),
    (6, 9, "grundschule"),
    (10, 15, "sekundar_1"),
    (16, 19, "upper_secondary"),
    (20, 200, "university"),
)


def model_education_level(age) -> str | None:
    """Model-side education level from age alone (the education output carries no level)."""
    if pd.isna(age):
        return None
    a = int(age)
    for lower, upper, level in _MODEL_AGE_LEVELS:
        if lower <= a <= upper:
            return level
    return None


def education_level(purpose_code, age) -> str | None:
    """Comparable education level from the SrV purpose code and the person's age.

    Purpose decides the institution type; age also bounds the early childhood and
    primary codes (Kita 0-6, Grundschule 5-10) and splits the secondary-school and
    tertiary codes into the model's age bands. Combinations that the model cannot
    produce (e.g. secondary school at age 25, Kita at age 40) return None and are
    excluded upstream with a logged rate.
    """
    if pd.isna(age) or pd.isna(purpose_code):
        return None
    a = int(age)
    code = int(purpose_code)
    if code == PURPOSE_KITA:
        if 0 <= a <= 6:
            return "kindergarten"
        return None
    if code == PURPOSE_GRUNDSCHULE:
        if 5 <= a <= 10:
            return "grundschule"
        return None
    if code == PURPOSE_SCHOOL_SECONDARY:
        if 10 <= a <= 15:
            return "sekundar_1"
        if 16 <= a <= 19:
            return "upper_secondary"
        return None
    if code == PURPOSE_TERTIARY:
        if 16 <= a <= 19:
            return "upper_secondary"
        if a >= 20:
            return "university"
        return None
    return None


def education_level_descriptive(purpose_code, age) -> str | None:
    """Like :func:`education_level` but keeps the SrV-only oberstufe / bbs split at 16-19."""
    level = education_level(purpose_code, age)
    if level == "upper_secondary":
        return "oberstufe" if int(purpose_code) == PURPOSE_SCHOOL_SECONDARY else "bbs"
    return level


START_AT_OWN_HOME = 1     # V_START_LAGE
DEST_AT_OWN_HOME = 1      # V_ZIEL_LAGE
DEFAULT_MAX_DISTANCE_KM = 300.0


def _ags8(series: pd.Series) -> pd.Series:
    """8-digit, zero-padded AGS string; NaN for missing or non-positive (sentinel) values.

    SrV "not applicable"/"missing" AGS values are encoded either as real NaN or as a
    non-positive sentinel (e.g. ``-9``). Converting naively through pandas' nullable
    ``Int64`` and then ``str`` turns those into plausible-looking garbage keys
    (``pd.NA`` stringifies to ``"<NA>"``, ``-9`` to ``"-0000009"``), which then pass a
    ``notna()`` filter and can even compare equal to another garbage key -- silently
    fabricating a Kreis/Gemeinde match. This helper resolves missing/sentinel input to
    a real ``NaN`` so downstream ``notna()``/``isna()`` filters and equality checks
    behave correctly instead of treating garbage as a valid AGS.
    """
    numeric = pd.to_numeric(series, errors="coerce")
    valid = numeric.notna() & (numeric > 0)
    padded = numeric.fillna(0).astype("int64").astype(str).str.zfill(8)
    return padded.where(valid, np.nan)


def kreis_from_ags(ags: pd.Series) -> pd.Series:
    """5-digit Kreis key from an 8-digit AGS; NaN propagates for missing/sentinel AGS values."""
    return _ags8(ags).str[:5]


# Public alias (ruling R8, fix round 1 of the SrV work-participation task): kept so any
# existing caller of the original private name keeps working unchanged (no behaviour change).
_kreis_from_ags = kreis_from_ags


def select_person_observations(trips, persons, households, purpose_codes,
                               max_distance_km=DEFAULT_MAX_DISTANCE_KM,
                               distance_source="gis"):
    """One home<->purpose distance observation per person for the given purpose codes.

    Selection mirrors eqasim's ``data.hts.commute_distance``: per person the FIRST
    home->purpose trip (start at own home, destination purpose in ``purpose_codes``);
    if that direction has no valid distance (see ``distance_source`` below), the FIRST
    purpose->home trip (start purpose in ``purpose_codes``, destination at own home).

    ``distance_source`` (Task 15, sensitivity variant, item 3 of the addendum plan):
    ``"gis"`` (default) is the calibration-target definition used everywhere else in
    this module -- distance = ``GIS_LAENGE`` where ``GIS_LAENGE_GUELTIG > 0``, GIS-invalid
    trips excluded. ``"gis_or_self_reported"`` is a SENSITIVITY variant only (never used
    for the committed target tables): distance = ``GIS_LAENGE`` where GIS-valid, else the
    self-reported ``V_LAENGE`` where ``V_LAENGE > 0``; a trip with neither is excluded and
    counted in ``n_excluded_no_length``. Every selected observation carries the resolved
    ``distance_source`` ("gis" or "self_reported") as an output column; under the default
    "gis" source it is always "gis" (additive column, default behaviour unchanged).

    GIS/length validity is resolved BEFORE the per-person pick, because it decides which
    DIRECTION represents the person (a data-quality substitution): the fallback
    direction is only used when the preferred one carries no usable distance at all.
    Negative weight and over-``max_distance_km`` are resolved AFTER the per-person
    pick and are terminal exclusions of that person's selected observation (no
    fallback to the other direction), because they flag the specific selected trip
    as unusable for calibration rather than merely GIS-unrouted. Mixing the two
    filters into a single upfront pass would silently swap in the other direction
    for a person whose preferred trip has a bad weight, understating the exclusion
    (caught by ``test_select_person_observations_drops_negative_weight_and_over_cap``).

    ASSUMPTION: a negative expansion weight or an over-cap GIS distance on the
    SELECTED trip is treated as a defect of that specific trip record (a corrupted
    weight, or an implausible routed distance), not as evidence that the person has
    no usable home<->purpose trip at all. This differs from a GIS-invalid distance,
    which only means that ONE direction was never routed and the other direction can
    still represent the person. There is no committed evidence that the other
    direction's weight/distance would also be defective, so substituting it would be
    an unjustified assumption; the person is excluded instead and the exclusion is
    counted (``n_excluded_weight_negative`` / ``n_excluded_over_cap``).

    Output columns (``obs``): ``hhnr``, ``pnr``, ``purpose_code``, ``regiostar7`` (all
    int64); ``kreis`` (5-char str); ``age`` (float64; NaN when the person record has no
    age, see ``n_missing_age``); ``distance_km``, ``weight`` (float64); ``intra_gemeinde``
    (bool; False when either end of the selected trip has a missing/sentinel AGS, see
    ``n_missing_trip_ags``); ``distance_source`` (str, "gis" or "self_reported"; always
    "gis" under the default ``distance_source="gis"``); ``dest_ags8`` (8-char str, the
    purpose/away-direction location's AGS -- NaN when missing/sentinel -- so a caller can
    filter observations by destination Kreis, e.g. the ZGB sensitivity variant).

    Exclusion/diagnostic log (unit noted per key): ``n_candidate_trips`` (trips, both
    directions); ``n_excluded_gis_invalid`` (trips, both directions -- see
    ``n_persons_dropped_gis_invalid`` below for the matching PERSON count);
    ``n_pool_weight_negative`` / ``n_pool_over_cap`` (trips in the candidate pool,
    informational only, counted BEFORE per-person selection -- do not sum these with the
    "excluded" keys below); ``n_persons_dropped_gis_invalid`` (persons: candidate persons
    for whom EVERY candidate trip, both directions, was GIS-invalid, so no direction could
    represent them at all -- R25/Minor 9, resolves the trip-vs-person unit contradiction of
    ``n_excluded_gis_invalid`` above); ``n_excluded_weight_negative`` / ``n_excluded_over_cap``
    (persons, counted on the SELECTED trip only, see the ASSUMPTION above);
    ``n_excluded_no_kreis`` (persons, household AGS missing/sentinel); ``n_missing_trip_ags``
    (persons, the selected trip's own home-direction or away-direction AGS is
    missing/sentinel); ``n_missing_age`` / ``n_missing_regiostar7`` (persons, kept in
    ``obs`` with NaN/-1 rather than excluded -- a downstream consumer decides);
    ``n_persons_selected`` (persons); ``share_start_ags_equals_household_ags`` (share, over
    persons with a known trip-side AGS, whose home-direction AGS matches the household AGS).
    Under ``distance_source="gis_or_self_reported"`` only: ``n_excluded_no_length`` (trips,
    both directions -- neither GIS-valid nor a usable self-reported length, the fallback-mode
    analogue of ``n_excluded_gis_invalid``); ``n_persons_dropped_no_length`` (persons, the
    matching person-level count, analogous to ``n_persons_dropped_gis_invalid``);
    ``n_persons_self_reported_distance`` / ``share_persons_self_reported_distance`` (persons /
    share of ``n_persons_selected`` whose selected observation used the self-reported
    fallback rather than a GIS-routed length -- the fallback rate, logged at INFO per the
    no-silent-fallback rule). Under the default "gis" source this rate is trivially zero by
    construction (every surviving trip IS GIS-valid) and these two keys are omitted from
    ``log`` entirely, so the three "gis"-mode target tables' committed provenance header
    stays byte-identical across a regeneration (Task 15 requirement).
    """
    if distance_source not in ("gis", "gis_or_self_reported"):
        raise ValueError(
            f"Unknown distance_source {distance_source!r}; expected 'gis' or 'gis_or_self_reported'")
    purpose_codes = tuple(int(c) for c in purpose_codes)
    # A fresh RangeIndex guarantees `outbound[cand.index]` below is a safe positional
    # lookup even if the caller passed a frame with a non-unique or non-default index.
    t = trips.copy().reset_index(drop=True)
    t["weight"] = pd.to_numeric(t["GEWICHT_W_ZENSUS"], errors="coerce")
    t["gis_valid"] = pd.to_numeric(t["GIS_LAENGE_GUELTIG"], errors="coerce") > 0
    t["gis_km"] = pd.to_numeric(t["GIS_LAENGE"], errors="coerce")
    if distance_source == "gis_or_self_reported":
        # Sensitivity variant only (Task 15): a self-reported length is only trusted when
        # positive -- SrV missing-data sentinel codes (e.g. -5 "weiss nicht") are negative
        # and must not leak in as a plausible-looking distance (mirrors the ruling R27
        # convention already used by `gis_validity_bias_check`).
        t["self_reported_km"] = pd.to_numeric(t["V_LAENGE"], errors="coerce")
        t["self_reported_valid"] = t["self_reported_km"] > 0
        t["length_valid"] = t["gis_valid"] | t["self_reported_valid"]
        t["distance_km"] = np.where(t["gis_valid"], t["gis_km"], t["self_reported_km"])
        t["distance_source_label"] = np.where(t["gis_valid"], "gis", "self_reported")
    else:
        t["length_valid"] = t["gis_valid"]
        t["distance_km"] = t["gis_km"]
        t["distance_source_label"] = "gis"

    outbound = (t["V_START_LAGE"] == START_AT_OWN_HOME) & t["V_ZWECK"].isin(purpose_codes)
    inbound = (t["V_ZIEL_LAGE"] == DEST_AT_OWN_HOME) & t["E_START_ZWECK"].isin(purpose_codes)
    cand = t[outbound | inbound].copy()
    cand["direction_rank"] = np.where(outbound[cand.index], 0, 1)
    cand["purpose_code"] = np.where(outbound[cand.index], cand["V_ZWECK"], cand["E_START_ZWECK"])
    log = {"n_candidate_trips": int(len(cand))}

    # Informational only (ruling R6): how many pool TRIPS (both directions, before
    # per-person selection) would fail the weight/cap checks, regardless of whether
    # they end up selected. Kept separate from the person-level exclusion counts below.
    log["n_pool_weight_negative"] = int((cand["weight"] < 0).sum())
    log["n_pool_over_cap"] = int((cand["distance_km"] > max_distance_km).sum())

    # Step 1: TRIPS with no usable length (GIS-invalid, or under the fallback source also
    # lacking a usable self-reported length) cannot represent a person at all; drop them
    # from the pool, then pick the preferred-direction trip per person from what remains.
    n_invalid_trips = int((~cand["length_valid"]).sum())
    valid_cand = cand[cand["length_valid"]].sort_values(
        ["HHNR", "PNR", "direction_rank", "WNR"], kind="stable")
    first = valid_cand.drop_duplicates(["HHNR", "PNR"], keep="first")

    # R25/Minor 9 (unit contradiction fix): n_invalid_trips above is a TRIP count (both
    # directions, see the Exclusions log docstring below); this is the matching PERSON
    # count -- candidate persons with at least one candidate trip but NONE surviving the
    # length-valid filter, i.e. every one of their candidate trip rows (both directions)
    # had no usable length. A one-line derivation from the same two frames, so it is added
    # here rather than left undocumented.
    candidate_persons = cand[["HHNR", "PNR"]].drop_duplicates()
    surviving_persons = valid_cand[["HHNR", "PNR"]].drop_duplicates()
    n_persons_dropped_no_length = len(candidate_persons) - len(
        candidate_persons.merge(surviving_persons, on=["HHNR", "PNR"], how="inner"))
    if distance_source == "gis_or_self_reported":
        log["n_excluded_no_length"] = n_invalid_trips
        log["n_persons_dropped_no_length"] = int(n_persons_dropped_no_length)
    else:
        # Default source: identical computation, original key names (behaviour unchanged).
        log["n_excluded_gis_invalid"] = n_invalid_trips
        log["n_persons_dropped_gis_invalid"] = int(n_persons_dropped_no_length)

    # Step 2: apply weight and distance-cap checks to the SELECTED observation only
    # (counted in PERSONS, not trips); a failure here drops the person, it does not
    # fall back to the other direction (see the ASSUMPTION above).
    n_neg = int((first["weight"] < 0).sum())
    first = first[first["weight"] >= 0]
    n_cap = int((first["distance_km"] > max_distance_km).sum())
    first = first[first["distance_km"] <= max_distance_km]
    log.update(n_excluded_weight_negative=n_neg, n_excluded_over_cap=n_cap)

    hh = households[["HHNR", "AGS"]].copy()
    hh["kreis"] = _kreis_from_ags(hh["AGS"])
    hh["household_ags8"] = _ags8(hh["AGS"])
    first = first.merge(hh[["HHNR", "kreis", "household_ags8"]], on="HHNR", how="left", validate="m:1")
    first = first.merge(
        persons[["HHNR", "PNR", "V_ALTER"]], on=["HHNR", "PNR"], how="left", validate="m:1")

    n_no_kreis = int(first["kreis"].isna().sum())
    first = first[first["kreis"].notna()]
    log["n_excluded_no_kreis"] = n_no_kreis

    # Diagnostics only (ruling R6 / IMPORTANT-3): these rows are KEPT in `obs` with
    # NaN age / -1 regiostar7 as before; a downstream consumer decides whether to
    # exclude them. A high rate is still surfaced loudly per the no-silent-fallback rule.
    n_missing_age = int(pd.to_numeric(first["V_ALTER"], errors="coerce").isna().sum())
    n_missing_regiostar7 = int(pd.to_numeric(first["REGIOSTAR7"], errors="coerce").isna().sum())
    log["n_missing_age"] = n_missing_age
    log["n_missing_regiostar7"] = n_missing_regiostar7
    if n_missing_age > 0:
        logger.warning(
            "[srv_distance_targets] purposes %s: %d/%d selected persons (%.1f%%) have a missing age",
            purpose_codes, n_missing_age, len(first),
            100.0 * n_missing_age / len(first) if len(first) else 0.0,
        )
    if n_missing_regiostar7 > 0:
        logger.warning(
            "[srv_distance_targets] purposes %s: %d/%d selected persons (%.1f%%) have a missing "
            "REGIOSTAR7",
            purpose_codes, n_missing_regiostar7, len(first),
            100.0 * n_missing_regiostar7 / len(first) if len(first) else 0.0,
        )

    start_ags8 = _ags8(first["V_START_AGS"])
    trip_ziel_ags8 = _ags8(first["V_ZIEL_AGS"])
    is_outbound = (first["direction_rank"] == 0).values
    home_ags8 = np.where(is_outbound, start_ags8, trip_ziel_ags8)
    # `away_ags8` is the AGS of the PURPOSE/activity location regardless of which
    # direction was selected (outbound: the trip's own V_ZIEL_AGS; inbound: the trip's
    # own V_START_AGS, since the person is travelling FROM the purpose location home) --
    # exposed below as the `dest_ags8` output column so callers can filter observations
    # by destination Kreis (e.g. the `inter_zgb` sensitivity variant, Task 15).
    away_ags8 = np.where(is_outbound, trip_ziel_ags8, start_ags8)
    home_missing = pd.isna(home_ags8)
    away_missing = pd.isna(away_ags8)
    log["n_missing_trip_ags"] = int((home_missing | away_missing).sum())

    # A missing/sentinel AGS on either end cannot be evaluated for intra-Gemeinde,
    # so it is reported as False rather than as a (possibly spurious) equality.
    intra_gemeinde = np.where(home_missing | away_missing, False, home_ags8 == away_ags8)

    # The household AGS is already guaranteed known here (rows with a missing/sentinel
    # household AGS were dropped above via the Kreis filter); only the trip-side AGS
    # can still be missing, so the agreement share is restricted to rows where it is known.
    household_ags8 = first["household_ags8"].values
    known_home_ags = ~home_missing
    if known_home_ags.sum() > 0:
        agree = home_ags8[known_home_ags] == household_ags8[known_home_ags]
        log["share_start_ags_equals_household_ags"] = float(agree.mean())
    else:
        log["share_start_ags_equals_household_ags"] = float("nan")

    obs = pd.DataFrame({
        "hhnr": first["HHNR"].astype("int64").values,
        "pnr": first["PNR"].astype("int64").values,
        "kreis": first["kreis"].values,
        "regiostar7": pd.to_numeric(first["REGIOSTAR7"], errors="coerce").fillna(-1).astype("int64").values,
        "purpose_code": first["purpose_code"].astype("int64").values,
        "age": pd.to_numeric(first["V_ALTER"], errors="coerce").values,
        "distance_km": first["distance_km"].astype(float).values,
        "weight": first["weight"].astype(float).values,
        "intra_gemeinde": intra_gemeinde,
        "distance_source": first["distance_source_label"].values,
        "dest_ags8": away_ags8,
    })
    log["n_persons_selected"] = int(len(obs))
    logger.info(
        "[srv_distance_targets] purposes %s: distance_source=%s: %d candidate trips -> %d persons "
        "selected; length-invalid trips %d; pool weight<0 %d trips, pool >%.0f km %d trips; "
        "selected persons dropped: weight<0 %d, >%.0f km %d, no Kreis %d, missing trip AGS %d; "
        "home AGS == household AGS %.1f%% (of %d persons with known trip AGS)",
        purpose_codes, distance_source, log["n_candidate_trips"], log["n_persons_selected"],
        n_invalid_trips, log["n_pool_weight_negative"], max_distance_km, log["n_pool_over_cap"],
        n_neg, max_distance_km, n_cap, n_no_kreis, log["n_missing_trip_ags"],
        100.0 * log["share_start_ags_equals_household_ags"], int(known_home_ags.sum()),
    )
    # No-silent-fallback rule (CLAUDE.md, MANDATORY), gated to the "gis_or_self_reported"
    # source only: under the default "gis" source this rate is trivially 0/0.0% by
    # construction (every surviving trip IS GIS-valid, see the pool filter above), and
    # adding a constant key/value pair to `log` here would needlessly change the committed
    # header of the three "gis"-mode target tables on every regeneration (Task 15 requires
    # those to stay byte-identical except for the header date line).
    if distance_source == "gis_or_self_reported":
        n_self_reported = int((obs["distance_source"] == "self_reported").sum())
        share_self_reported = n_self_reported / len(obs) if len(obs) else float("nan")
        log["n_persons_self_reported_distance"] = n_self_reported
        log["share_persons_self_reported_distance"] = share_self_reported
        logger.info(
            "[srv_distance_targets] purposes %s: distance_source=%s: primary (GIS) %d/%d (%.1f%%), "
            "self-reported fallback %d/%d (%.1f%%)",
            purpose_codes, distance_source, len(obs) - n_self_reported, len(obs),
            100.0 * (1.0 - share_self_reported) if len(obs) else 0.0,
            n_self_reported, len(obs), 100.0 * share_self_reported if len(obs) else 0.0,
        )
    return obs, log


def gis_validity_bias_check(trips: pd.DataFrame) -> dict:
    """R25: is GIS-invalidity missing at random with respect to distance?

    Selects the SAME home-based work candidate trips as :func:`select_person_observations`
    would for ``purpose_codes=(PURPOSE_WORK,)`` (both directions: home->work with
    ``V_START_LAGE == START_AT_OWN_HOME`` and ``V_ZWECK == PURPOSE_WORK``, or work->home with
    ``V_ZIEL_LAGE == DEST_AT_OWN_HOME`` and ``E_START_ZWECK == PURPOSE_WORK``), BEFORE the
    per-person pick and any weight/distance-cap filtering -- this checks GIS validity itself
    on the full candidate pool, not the already-selected sample.

    Compares the self-reported distance (``V_LAENGE``) between GIS-invalid and GIS-valid
    candidate trips (median AND mean each); if GIS-invalid trips were systematically longer
    or shorter, excluding them (per R6, when both directions of a person are GIS-invalid)
    would bias the reference away from a plausible ASSUMPTION of missingness at random.
    Also reports the median of ``GIS_LAENGE / V_LAENGE`` over GIS-valid trips with
    ``V_LAENGE > 0``, a sanity check that the two distance measures broadly agree.

    Ruling R27 (controller, whole-branch review follow-up): ``V_LAENGE`` carries SrV missing-
    data sentinel codes (e.g. -5 "weiss nicht", -10 "unplausibel") alongside real lengths, and
    an earlier version of this function let those sentinels leak into the median/mean as
    large-magnitude negative numbers, understating the GIS-invalid median. Every median/mean
    below is computed over ``V_LAENGE > 0`` only (excluding both sentinel codes and genuine
    zero-length trips); the share of GIS-invalid trips with NO usable self-reported length at
    all is reported explicitly instead, so that exclusion is visible rather than silently
    absorbed into a mis-stated "typical" distance.

    Returns a dict: ``n_gis_invalid``, ``n_gis_valid`` (candidate trips, both directions, ALL
    of them regardless of whether they have a usable self-reported length);
    ``n_gis_invalid_without_self_reported`` (GIS-invalid trips with no ``V_LAENGE > 0`` value)
    and ``share_gis_invalid_without_self_reported`` (that count over ``n_gis_invalid``, NaN if
    ``n_gis_invalid`` is 0); ``median_self_reported_km_gis_invalid`` /
    ``median_self_reported_km_gis_valid`` and ``mean_self_reported_km_gis_invalid`` /
    ``mean_self_reported_km_gis_valid`` (each over the ``V_LAENGE > 0`` subset of its group,
    NaN if that subset is empty); ``median_gis_over_self_reported`` (NaN if no GIS-valid trip
    has ``V_LAENGE > 0``). This is a pure diagnostic: it does not feed the committed target
    tables and has no synpp dependency.
    """
    t = trips.copy()
    outbound = (t["V_START_LAGE"] == START_AT_OWN_HOME) & (t["V_ZWECK"] == PURPOSE_WORK)
    inbound = (t["V_ZIEL_LAGE"] == DEST_AT_OWN_HOME) & (t["E_START_ZWECK"] == PURPOSE_WORK)
    cand = t[outbound | inbound].copy()
    cand["gis_valid"] = pd.to_numeric(cand["GIS_LAENGE_GUELTIG"], errors="coerce") > 0
    cand["self_reported_km"] = pd.to_numeric(cand["V_LAENGE"], errors="coerce")
    cand["gis_km"] = pd.to_numeric(cand["GIS_LAENGE"], errors="coerce")
    # R27: a usable self-reported length excludes both SrV missing-data sentinel codes
    # (negative) and a literal zero, none of which represent an actual reported distance.
    cand["has_self_reported"] = cand["self_reported_km"] > 0

    invalid = cand[~cand["gis_valid"]]
    valid = cand[cand["gis_valid"]]
    n_invalid = int(len(invalid))
    n_valid = int(len(valid))

    n_invalid_without_self_reported = int((~invalid["has_self_reported"]).sum())
    share_invalid_without_self_reported = (
        n_invalid_without_self_reported / n_invalid if n_invalid else float("nan"))

    invalid_reported = invalid[invalid["has_self_reported"]]
    valid_reported = valid[valid["has_self_reported"]]
    median_invalid = float(invalid_reported["self_reported_km"].median()) if len(invalid_reported) else float("nan")
    median_valid = float(valid_reported["self_reported_km"].median()) if len(valid_reported) else float("nan")
    mean_invalid = float(invalid_reported["self_reported_km"].mean()) if len(invalid_reported) else float("nan")
    mean_valid = float(valid_reported["self_reported_km"].mean()) if len(valid_reported) else float("nan")

    median_ratio = (float((valid_reported["gis_km"] / valid_reported["self_reported_km"]).median())
                    if len(valid_reported) else float("nan"))

    result = {
        "n_gis_invalid": n_invalid,
        "n_gis_valid": n_valid,
        "n_gis_invalid_without_self_reported": n_invalid_without_self_reported,
        "share_gis_invalid_without_self_reported": share_invalid_without_self_reported,
        "median_self_reported_km_gis_invalid": median_invalid,
        "median_self_reported_km_gis_valid": median_valid,
        "mean_self_reported_km_gis_invalid": mean_invalid,
        "mean_self_reported_km_gis_valid": mean_valid,
        "median_gis_over_self_reported": median_ratio,
    }
    logger.info(
        "[srv_distance_targets] GIS-validity bias check (home-based work candidate trips): "
        "n_gis_invalid=%d (%d, %.1f%% with no usable self-reported length), n_gis_valid=%d, "
        "median self-reported km (V_LAENGE>0) gis_invalid=%.2f vs gis_valid=%.2f, "
        "mean self-reported km (V_LAENGE>0) gis_invalid=%.2f vs gis_valid=%.2f, "
        "median GIS/self-reported ratio (gis_valid, V_LAENGE>0)=%.3f",
        n_invalid, n_invalid_without_self_reported, 100.0 * share_invalid_without_self_reported,
        n_valid, median_invalid, median_valid, mean_invalid, mean_valid, median_ratio)
    return result


def weighted_band_shares(distances_km, weights, edges) -> np.ndarray:
    """Weighted share per distance band; all-zero vector for empty or zero-weight input.

    Raises ValueError for NaN or negative distances (fail early).
    """
    d = np.asarray(distances_km, dtype=float)
    w = np.asarray(weights, dtype=float)
    n_bands = len(edges) - 1
    if d.size == 0 or w.sum() <= 0:
        return np.zeros(n_bands)
    if np.any(np.isnan(d)) or np.any(d < 0):
        raise ValueError("distances_km contains NaN or negative values")
    idx = band_index(d, edges)
    counts = np.bincount(idx, weights=w, minlength=n_bands)[:n_bands]
    return counts / counts.sum()


def weighted_quantiles(values, weights, probabilities) -> np.ndarray:
    """Weighted empirical quantiles (linear interpolation on the weighted CDF midpoints).

    All-NaN for empty or zero-weight input; the table builders report n_persons = 0 beside it.
    Raises ValueError if any value is NaN (fail early). Uses Hazen midpoint-CDF convention,
    which differs from np.quantile away from the median by design.
    """
    v = np.asarray(values, dtype=float)
    w = np.asarray(weights, dtype=float)
    prob = np.asarray(probabilities, dtype=float)
    if v.size == 0 or w.sum() <= 0:
        return np.full(len(prob), np.nan)
    if np.any(np.isnan(v)):
        raise ValueError("values contains NaN")
    order = np.argsort(v)
    v, w = v[order], w[order]
    cdf = np.cumsum(w) - 0.5 * w
    cdf /= w.sum()
    return np.interp(prob, cdf, v)


def shrink_toward_pool(values, n, pool_values, prior_strength) -> np.ndarray:
    """Empirical-Bayes style mix: weight n/(n+k) on the cell, k/(n+k) on the pool.

    n = 0 returns the pool; prior_strength = 0 with n > 0 returns values unchanged.
    """
    values = np.asarray(values, dtype=float)
    pool = np.asarray(pool_values, dtype=float)
    n = float(n)
    k = float(prior_strength)
    lam = n / (n + k) if (n + k) > 0 else 0.0
    return lam * values + (1.0 - lam) * pool


def emd_on_shares(p, q) -> float:
    """1-D EMD between two band-share vectors, normalised to [0, 1] by (n_bands - 1).

    Numerically identical to braunschweig.calibration.metrics.emd_on_bands
    (re-implemented because that module is imported by pipeline stages).
    Both inputs must sum to 1.
    """
    p = np.asarray(p, dtype=float)
    q = np.asarray(q, dtype=float)
    cdf_diff = np.cumsum(p) - np.cumsum(q)
    return float(np.abs(cdf_diff[:-1]).sum() / (len(p) - 1))


def bootstrap_emd_noise_floor(distances_km, weights, edges, n_bootstrap=500, seed=0,
                              quantile=0.95) -> float:
    """The `quantile`-th quantile (default 0.95) of EMD(bootstrap band shares, full-sample band shares).

    Persons are resampled with replacement (n = sample size) with their weights carried
    along; the result is the EMD a model would reach against this reference by sampling
    noise alone. Returns 0.0 for fewer than two observations.
    Raises ValueError for n_bootstrap < 1.
    """
    if n_bootstrap < 1:
        raise ValueError("n_bootstrap must be >= 1")
    d = np.asarray(distances_km, dtype=float)
    w = np.asarray(weights, dtype=float)
    if d.size < 2:
        return 0.0
    base = weighted_band_shares(d, w, edges)
    rng = np.random.default_rng(seed)
    emds = np.empty(n_bootstrap)
    for b in range(n_bootstrap):
        idx = rng.integers(0, d.size, d.size)
        emds[b] = emd_on_shares(weighted_band_shares(d[idx], w[idx], edges), base)
    return float(np.quantile(emds, quantile))


ZGB_KREISE = ("03101", "03102", "03103", "03151", "03153", "03154", "03157", "03158")
WOLFSBURG_KREIS = "03103"
PROXY_RS7 = 72
PROXY_SOURCE = "proxy_rs7_%d" % PROXY_RS7
DEFAULT_PRIOR_STRENGTH = 100.0
DEFAULT_DETOUR_FACTOR = 1.3
QUANTILE_PROBABILITIES = np.arange(1, 100) / 100.0

COMMUTE_TABLE = "srv2023_commute_distance_by_kreis.csv"
EDUCATION_TABLE = "srv2023_education_distance_by_kreis_level.csv"
QUANTILE_TABLE = "srv2023_commute_distance_quantiles_by_kreis.csv"
SENSITIVITY_TABLE = "srv2023_commute_distance_sensitivity_by_kreis.csv"


def dominant_rs7_by_kreis(obs: pd.DataFrame) -> dict:
    """Weight-modal RegioStaR-7 type per Kreis (the pool a Kreis shrinks toward).

    Only covers Kreise that actually have persons in ``obs``; a Kreis absent from the
    data has no entry here and callers must fall back to the next-higher pool (ZGB).
    Ties on summed weight are broken by ``regiostar7`` (ascending) for a deterministic
    result regardless of input row order (``kind="stable"``).
    """
    g = obs.groupby(["kreis", "regiostar7"])["weight"].sum().reset_index()
    g = g.sort_values(["kreis", "weight", "regiostar7"], ascending=[True, False, True], kind="stable")
    g = g.drop_duplicates("kreis")
    return dict(zip(g["kreis"], g["regiostar7"].astype(int)))


def _weighted_mean_median(d, w):
    """Weighted mean/median; NaN for an empty subset or one whose total weight is <= 0.

    ``np.average`` raises ZeroDivisionError on an all-zero weight vector (a non-empty
    subset can still have zero total weight, since a zero weight passes the ``weight
    >= 0`` filter upstream); guarding on the weight sum, not just the count, avoids
    that crash instead of merely narrowing the len(d) == 0 case.
    """
    w = np.asarray(w, dtype=float)
    if len(d) == 0 or w.sum() <= 0:
        return float("nan"), float("nan")
    return float(np.average(d, weights=w)), float(weighted_quantiles(d, w, [0.5])[0])


def _share_block(d, w, edges, labels, prefix):
    shares = weighted_band_shares(d, w, edges)
    return {f"{prefix}_{lbl}": float(s) for lbl, s in zip(labels, shares)}, shares


def _pool_shares(obs, edges, mask=None):
    sub = obs if mask is None else obs[mask]
    return weighted_band_shares(sub["distance_km"].values, sub["weight"].values, edges), int(len(sub))


def _pool_for_kreis(kreis, rs7_of, pools_by_rs7, zgb_pool):
    """The shrinkage pool for one Kreis: its dominant RS7 pool, or the ZGB pool as a
    fallback when no RS7-modal pool is available for it (no persons at all, or none
    within the RS7 group that has this Kreis's dominant type).

    Returns ``(pool, used_fallback)`` so callers can count and log how often the ZGB
    fallback fires per CLAUDE.md's fallback-transparency rule: a Kreis-code join bug
    or an empty upstream extract would otherwise silently degrade every Kreis row to
    the ZGB pool without anyone noticing.
    """
    rs7 = rs7_of.get(kreis)
    if rs7 is not None and rs7 in pools_by_rs7:
        return pools_by_rs7[rs7], False
    return zgb_pool, True


def _rs7_pool_shares(obs, rs7, scopes, zgb_shares, prior_strength, edges):
    """Raw and ZGB-pool-shrunk band shares for one RS7 type, per scope.

    Returns ``{scope: (raw, shrunk, n)}``. Shared by :func:`build_commute_table` for two
    purposes that must never silently drift apart (Task 14 minor): the per-Kreis
    shrinkage pool (the ``shrunk`` values, keyed by scope) and the "rs7" summary row's
    OWN raw/shrunk shares. Before this helper existed, the same computation was done
    twice -- once here to build the pools, and again inside the row-building closure
    when emitting the "rs7" rows -- which risked the two silently diverging under a
    future edit even though they are mathematically the same quantity.
    """
    sel = obs["regiostar7"] == rs7
    result = {}
    for s, m in scopes.items():
        mask = sel if m is None else (sel & m)
        raw, n = _pool_shares(obs, edges, mask)
        shrunk = shrink_toward_pool(raw, n, zgb_shares[s][0], prior_strength)
        result[s] = (raw, shrunk, n)
    return result


def build_commute_table(obs_work, prior_strength=DEFAULT_PRIOR_STRENGTH, n_bootstrap=500, seed=0):
    """Per home Kreis (plus RS7 pools, ZGB, Wolfsburg proxy) work distance band shares.

    Scopes: ``all`` (every person), ``inter`` (home Gemeinde != workplace Gemeinde),
    ``intra`` (same Gemeinde). Shrinkage: Kreis -> its dominant RS7 pool -> ZGB with
    weight n/(n+k). The Wolfsburg row copies the RS7-72 pool (ASSUMPTION, see the ADR).

    Emits exactly one ``kreis`` row per code in :data:`ZGB_KREISE`, even when a Kreis
    has zero persons in ``obs_work`` (n_persons = 0, raw shares all zero, shrunk shares
    equal the pool -- the n/(n+k) limit at n = 0 -- mean/median/share_intra = NaN); a
    Kreis without its own RS7-modal pool (because it has no persons at all) falls back
    to the ZGB pool directly.
    """
    edges, labels = WORK_BAND_EDGES_KM, WORK_BAND_LABELS
    scopes = {"all": None, "inter": ~obs_work["intra_gemeinde"].astype(bool),
              "intra": obs_work["intra_gemeinde"].astype(bool)}
    rs7_of = dominant_rs7_by_kreis(obs_work)
    rows = []

    zgb_shares = {s: _pool_shares(obs_work, edges, m) for s, m in scopes.items()}

    rs7_shares = {int(rs7): _rs7_pool_shares(obs_work, rs7, scopes, zgb_shares, prior_strength, edges)
                 for rs7 in sorted(obs_work["regiostar7"].unique())}

    def _row(level_geo, code, source, sub, pool_for_scope, precomputed_shares=None):
        # R16: n_persons_inter / n_persons_intra are the UNWEIGHTED person counts of the
        # inter-/intra-Gemeinde scope subsets (n_persons stays the all-scope count), so
        # the synthesis stage can pick the scope-matching reference count for `decide_layer`
        # instead of reusing the all-scope n_persons for the inter/intra decisions.
        #
        # ``precomputed_shares`` (Task 14 minor), when given, is ``{scope: (raw, shrunk)}``
        # from :func:`_rs7_pool_shares` -- used ONLY for the "rs7" summary rows below, so
        # their shares are not recomputed a second time from the same underlying subset.
        intra_mask = sub["intra_gemeinde"].astype(bool)
        row = {"level_geo": level_geo, "code": code, "source": source, "n_persons": int(len(sub)),
              "n_persons_inter": int((~intra_mask).sum()), "n_persons_intra": int(intra_mask.sum())}
        row["mean_km"], row["median_km"] = _weighted_mean_median(sub["distance_km"].values, sub["weight"].values)
        w_intra = sub.loc[intra_mask, "weight"].sum()
        row["share_intra"] = float(w_intra / sub["weight"].sum()) if sub["weight"].sum() > 0 else float("nan")
        for s, m in scopes.items():
            part = sub if m is None else sub[m.loc[sub.index]]
            if precomputed_shares is not None:
                raw, shrunk = precomputed_shares[s]
            else:
                _, raw = _share_block(part["distance_km"].values, part["weight"].values, edges, labels, f"share_{s}")
                shrunk = shrink_toward_pool(raw, len(part), pool_for_scope[s], prior_strength) if pool_for_scope else raw
            row.update({f"share_{s}_{lbl}": float(v) for lbl, v in zip(labels, raw)})
            row.update({f"share_{s}_shrunk_{lbl}": float(v) for lbl, v in zip(labels, shrunk)})
            row[f"emd_noise_95_{s}"] = bootstrap_emd_noise_floor(
                part["distance_km"].values, part["weight"].values, edges, n_bootstrap=n_bootstrap, seed=seed)
        return row

    # Wolfsburg's own RS7-72 pool must exist at all (a global data gap, not merely an
    # empty Kreis) for the proxy row to be scientifically defensible; fail early.
    proxy_sub = obs_work[obs_work["regiostar7"] == PROXY_RS7]
    if proxy_sub.empty:
        raise ValueError("No SrV persons with RegioStaR-7 == %d; cannot build the Wolfsburg proxy" % PROXY_RS7)

    pools_by_rs7_and_scope = {s: {rs7: rs7_shares[rs7][s][1] for rs7 in rs7_shares} for s in scopes}
    n_own_pool = {s: 0 for s in scopes}
    n_fallback = {s: 0 for s in scopes}
    for kreis in ZGB_KREISE:
        if kreis == WOLFSBURG_KREIS:
            # Wolfsburg proxy: the RS7-72 pool row (no further shrinkage; source flags
            # the assumption).
            rows.append(_row("kreis", kreis, PROXY_SOURCE, proxy_sub, None))
            continue
        sub = obs_work[obs_work["kreis"] == kreis]
        pool = {}
        for s in scopes:
            pool[s], fb = _pool_for_kreis(kreis, rs7_of, pools_by_rs7_and_scope[s], zgb_shares[s][0])
            # Task 14 minor: counted PER SCOPE rather than OR-ed into one combined flag,
            # so a Kreis whose "all" scope has its own RS7 pool but whose "inter"/"intra"
            # scope fell back to ZGB no longer hides that partial fallback behind a
            # single boolean.
            if fb:
                n_fallback[s] += 1
            else:
                n_own_pool[s] += 1
        rows.append(_row("kreis", kreis, "srv", sub, pool))

    for rs7 in sorted(rs7_shares):
        precomputed = {s: rs7_shares[rs7][s][:2] for s in scopes}
        rows.append(_row("rs7", str(rs7), "srv", obs_work[obs_work["regiostar7"] == rs7],
                         None, precomputed_shares=precomputed))
    rows.append(_row("zgb", "zgb", "srv", obs_work, None))
    table = pd.DataFrame(rows)
    n_kreis = len(ZGB_KREISE)
    # Fix round 1 (#358): build the per-scope portion of the log line from the `scopes`
    # dict's own keys rather than hardcoding "all"/"inter"/"intra" -- so the message
    # cannot silently drift out of sync if the set of scopes ever changes.
    own_pool_str = ", ".join(f"{s} {n_own_pool[s]}/{n_kreis}" for s in scopes)
    fallback_str = ", ".join(f"{s} {n_fallback[s]}/{n_kreis}" for s in scopes)
    logger.info(
        "[srv_distance_targets] commute table: Kreis rows from own RS7 pool per scope: "
        "%s; ZGB fallback per scope: %s; proxy 1/%d",
        own_pool_str, fallback_str, n_kreis)
    logger.info("[srv_distance_targets] commute table: %d rows, %d persons total",
                len(table), int(len(obs_work)))
    return table


def _commute_sensitivity_variant_rows(variant, obs, edges, labels, prior_strength, n_bootstrap, seed):
    """Kreis/RS7/ZGB rows (a single scope, unlike :func:`build_commute_table`'s all/inter/
    intra scopes) for one sensitivity variant's observation subset, sharing the same
    shrinkage hierarchy and pool-provenance logging discipline as the other builders.

    Unlike :func:`build_commute_table`, an empty Wolfsburg-proxy subset does not raise --
    this table is a diagnostic SENSITIVITY measurement, not a calibration target, and a
    variant (e.g. ``inter_zgb`` on a small sample) can plausibly have zero RS7-72 persons;
    it falls back to the ZGB pool with a logged warning, exactly like
    :func:`build_education_table` does for the same situation.
    """
    zgb_raw, _ = _pool_shares(obs, edges)
    rs7_of = dominant_rs7_by_kreis(obs)
    rs7_pool = {}
    for rs7 in sorted(obs["regiostar7"].unique()):
        raw, n = _pool_shares(obs, edges, obs["regiostar7"] == rs7)
        rs7_pool[int(rs7)] = shrink_toward_pool(raw, n, zgb_raw, prior_strength)

    def _row(level_geo, code, source, sub, pool):
        row = {"variant": variant, "level_geo": level_geo, "code": code, "source": source,
              "n_persons": int(len(sub))}
        _, raw = _share_block(sub["distance_km"].values, sub["weight"].values, edges, labels, "share")
        row.update({f"share_{lbl}": float(v) for lbl, v in zip(labels, raw)})
        shrunk = shrink_toward_pool(raw, len(sub), pool, prior_strength) if pool is not None else raw
        row.update({f"share_shrunk_{lbl}": float(v) for lbl, v in zip(labels, shrunk)})
        row["emd_noise_95"] = bootstrap_emd_noise_floor(
            sub["distance_km"].values, sub["weight"].values, edges, n_bootstrap=n_bootstrap, seed=seed)
        return row

    rows = []
    n_own_pool = n_fallback = 0
    for kreis in ZGB_KREISE:
        if kreis == WOLFSBURG_KREIS:
            proxy_sub = obs[obs["regiostar7"] == PROXY_RS7]
            if proxy_sub.empty:
                logger.warning(
                    "[srv_distance_targets] commute sensitivity/%s: no RS7-72 persons for the "
                    "Wolfsburg proxy, ZGB pool used", variant)
                rows.append(_row("kreis", kreis, PROXY_SOURCE, proxy_sub, zgb_raw))
            else:
                rows.append(_row("kreis", kreis, PROXY_SOURCE, proxy_sub, None))
            continue
        sub = obs[obs["kreis"] == kreis]
        pool, used_fallback = _pool_for_kreis(kreis, rs7_of, rs7_pool, zgb_raw)
        n_fallback += int(used_fallback)
        n_own_pool += int(not used_fallback)
        rows.append(_row("kreis", kreis, "srv", sub, pool))
    for rs7 in sorted(rs7_pool):
        rows.append(_row("rs7", str(rs7), "srv", obs[obs["regiostar7"] == rs7], zgb_raw))
    rows.append(_row("zgb", "zgb", "srv", obs, None))
    n_kreis = len(ZGB_KREISE) - 1  # Wolfsburg is a proxy row, not an own/fallback Kreis
    logger.info(
        "[srv_distance_targets] commute sensitivity/%s: Kreis rows from own RS7 pool %d/%d, "
        "ZGB fallback %d/%d, proxy 1/%d; %d persons total",
        variant, n_own_pool, n_kreis, n_fallback, n_kreis, len(ZGB_KREISE), int(len(obs)))
    return rows


def build_commute_sensitivity_table(obs_gis, obs_fallback, prior_strength=DEFAULT_PRIOR_STRENGTH,
                                    n_bootstrap=500, seed=0):
    """SENSITIVITY variants of the work distance-band shares (addendum Task 15, item 3) --

    these are NOT calibration targets; they quantify how much the two known caveats of
    :func:`build_commute_table` move the reference if resolved differently. Same geographic
    rows as :func:`build_commute_table` (kreis x 8 ZGB Kreise incl. the Wolfsburg proxy, rs7
    pools, zgb), one row per (variant, level_geo, code):

    - ``inter_zgb``: from ``obs_gis`` (the calibration-target GIS-only observations),
      inter-Gemeinde persons (``intra_gemeinde == False``) whose destination Kreis (first 5
      digits of ``dest_ags8``) is one of the 8 ZGB Kreise -- i.e. inter-Gemeinde commutes
      that stay WITHIN the surveyed ZGB polygon. Quantifies how much the main table's
      ``inter`` scope is diluted by commutes leaving the polygon (the "polygon-external
      destinations" caveat). Persons with an unknown destination AGS are excluded from
      the band shares (numerator and denominator); the logged exclusion rate is relative to all inter persons.
    - ``all_gis_fallback``: every person of ``obs_fallback`` (expected to come from
      :func:`select_person_observations` with ``distance_source="gis_or_self_reported"``) --
      quantifies how much the ``all`` scope target would shift if the GIS-invalid tail were
      recovered via the self-reported length instead of excluded (the "GIS-invalid tail"
      caveat). ``obs_fallback`` RE-RUNS the home<->purpose direction pick with the
      "gis_or_self_reported" source (it is not the GIS-only selection plus the persons it
      dropped): a person whose preferred direction was GIS-invalid but whose other
      direction is both GIS-valid and was already the GIS-only pick may instead select a
      DIFFERENT leg here if it ranks higher in the direction preference order, so
      ``all_gis_fallback`` / ``inter_gis_fallback`` are not simply "the main selection plus
      recovered persons".
    - ``inter_gis_fallback``: ``obs_fallback`` restricted to ``intra_gemeinde == False``.

    ASSUMPTION (fix round 1, #358): the ``*_gis_fallback`` variants mix GIS-routed km
    (GIS-valid trips) with self-reported km (GIS-invalid trips) WITHOUT rescaling --
    justified by the measured GIS/self-reported ratio recorded in ADR-0102 Assumption 2
    (not restated here; reproduce with
    ``scripts/extract_srv_primary_distance_targets.py --bias-check``).

    Shrinkage hierarchy is identical to :func:`build_commute_table` (Kreis -> its dominant
    RS7 pool -> ZGB, weight n/(n+k)), computed SEPARATELY per variant -- a Kreis's dominant
    RS7 pool can differ between ``obs_gis`` and ``obs_fallback`` if the extra self-reported
    persons shift the weight-modal type, so reusing one pool across variants would be wrong.
    :func:`dominant_rs7_by_kreis` is likewise recomputed on EACH variant's own filtered
    subset (e.g. ``inter_zgb`` only sees inter-Gemeinde, polygon-internal persons), so a
    given Kreis's dominant RS7 pool -- and therefore its shrunk shares -- can differ between
    variants of THIS table, and between this table and :func:`build_commute_table`'s own
    pools for the same Kreis. Keep that in mind before comparing two ``share_shrunk_*``
    values across tables/variants: the shrinkage target itself may not be the same pool.

    Columns: ``variant``, ``level_geo``, ``code``, ``source``, ``n_persons``,
    ``share_<label>`` / ``share_shrunk_<label>`` (work bands), ``emd_noise_95``.
    """
    edges, labels = WORK_BAND_EDGES_KM, WORK_BAND_LABELS

    inter_gis = obs_gis[~obs_gis["intra_gemeinde"].astype(bool)]
    dest_kreis = inter_gis["dest_ags8"].str[:5]
    n_dest_unknown = int(dest_kreis.isna().sum())
    inter_zgb = inter_gis[dest_kreis.isin(ZGB_KREISE)]
    logger.info(
        "[srv_distance_targets] commute sensitivity inter_zgb: %d/%d inter-Gemeinde persons "
        "have a destination within the 8 ZGB Kreise (%d with an unknown destination AGS "
        "excluded from both the numerator and denominator, %.1f%% of inter-Gemeinde persons)",
        len(inter_zgb), len(inter_gis), n_dest_unknown,
        100.0 * n_dest_unknown / len(inter_gis) if len(inter_gis) else 0.0,
    )

    n_self_reported = int((obs_fallback["distance_source"] == "self_reported").sum())
    logger.info(
        "[srv_distance_targets] commute sensitivity gis_fallback variants: %d/%d persons in "
        "obs_fallback used the self-reported fallback length (%.1f%%)",
        n_self_reported, len(obs_fallback),
        100.0 * n_self_reported / len(obs_fallback) if len(obs_fallback) else 0.0,
    )

    variants = {
        "inter_zgb": inter_zgb,
        "all_gis_fallback": obs_fallback,
        "inter_gis_fallback": obs_fallback[~obs_fallback["intra_gemeinde"].astype(bool)],
    }
    rows = []
    for variant, obs in variants.items():
        rows.extend(_commute_sensitivity_variant_rows(
            variant, obs, edges, labels, prior_strength, n_bootstrap, seed))
    table = pd.DataFrame(rows)
    logger.info("[srv_distance_targets] commute sensitivity table: %d rows, variants %s",
                len(table), sorted(variants))
    return table


def build_education_table(obs_edu, prior_strength=DEFAULT_PRIOR_STRENGTH, n_bootstrap=500, seed=0):
    """Per home Kreis x education level distance band shares (education band edges).

    Comparable levels follow the model's age banding; ``oberstufe`` / ``bbs`` rows are
    descriptive only (``comparable = False``). Persons whose (purpose, age) combination
    maps to no level are excluded with a logged rate.

    Emits one ``kreis`` row per code in :data:`ZGB_KREISE` x level, even when a Kreis
    has zero persons for that level (n_persons = 0, raw shares zero, shrunk shares
    equal the pool); a Kreis without its own RS7-modal pool for that level falls back
    to the level's ZGB pool.
    """
    edges, labels = EDUCATION_BAND_EDGES_KM, EDUCATION_BAND_LABELS
    obs = obs_edu.copy()
    obs["level"] = [education_level(p, a) for p, a in zip(obs["purpose_code"], obs["age"])]
    obs["level_descriptive"] = [education_level_descriptive(p, a) for p, a in zip(obs["purpose_code"], obs["age"])]
    n_unmapped = int(obs["level"].isna().sum())
    logger.info("[srv_distance_targets] education: %d/%d persons without a comparable level (%.1f%%) excluded",
                n_unmapped, len(obs), 100.0 * n_unmapped / max(len(obs), 1))
    obs = obs[obs["level"].notna()]
    rs7_of = dominant_rs7_by_kreis(obs)
    rows = []
    pool_counts = {"own": 0, "fallback": 0, "proxy": 0}

    def _rows_for_level(level_col, level, comparable):
        sel = obs[obs[level_col] == level]
        zgb_raw, _ = _pool_shares(sel, edges)
        rs7_pool = {}
        for rs7 in sorted(sel["regiostar7"].unique()):
            raw, n = _pool_shares(sel, edges, sel["regiostar7"] == rs7)
            rs7_pool[int(rs7)] = shrink_toward_pool(raw, n, zgb_raw, prior_strength)

        def _r(level_geo, code, source, sub, pool):
            row = {"level_geo": level_geo, "code": code, "source": source,
                   "education_level": level, "comparable": bool(comparable), "n_persons": int(len(sub))}
            row["mean_km"], row["median_km"] = _weighted_mean_median(sub["distance_km"].values, sub["weight"].values)
            block, raw = _share_block(sub["distance_km"].values, sub["weight"].values, edges, labels, "share")
            row.update(block)
            shrunk = shrink_toward_pool(raw, len(sub), pool, prior_strength) if pool is not None else raw
            row.update({f"share_shrunk_{lbl}": float(v) for lbl, v in zip(labels, shrunk)})
            row["emd_noise_95"] = bootstrap_emd_noise_floor(
                sub["distance_km"].values, sub["weight"].values, edges, n_bootstrap=n_bootstrap, seed=seed)
            return row

        out = []
        for kreis in ZGB_KREISE:
            if kreis == WOLFSBURG_KREIS:
                proxy_sub = sel[sel["regiostar7"] == PROXY_RS7]
                pool_counts["proxy"] += 1
                if proxy_sub.empty:
                    # This level has region-wide persons but none in RS7-72: copying an
                    # empty proxy subset would silently give Wolfsburg an all-zero row
                    # (raw AND shrunk, since pool=None means shrunk=raw); use the ZGB
                    # pool instead so the row is a defensible (if coarser) estimate, and
                    # say so loudly rather than letting the zero pass unnoticed.
                    logger.warning(
                        "[srv_distance_targets] education/%s: no RS7-72 persons for the "
                        "Wolfsburg proxy, ZGB pool used", level)
                    out.append(_r("kreis", kreis, PROXY_SOURCE, proxy_sub, zgb_raw))
                else:
                    out.append(_r("kreis", kreis, PROXY_SOURCE, proxy_sub, None))
                continue
            sub = sel[sel["kreis"] == kreis]
            pool, used_fallback = _pool_for_kreis(kreis, rs7_of, rs7_pool, zgb_raw)
            pool_counts["fallback" if used_fallback else "own"] += 1
            out.append(_r("kreis", kreis, "srv", sub, pool))
        for rs7 in sorted(rs7_pool):
            out.append(_r("rs7", str(rs7), "srv", sel[sel["regiostar7"] == rs7], zgb_raw))
        out.append(_r("zgb", "zgb", "srv", sel, None))
        return out

    for level in COMPARABLE_LEVELS:
        rows.extend(_rows_for_level("level", level, True))
    for level in DESCRIPTIVE_ONLY_LEVELS:
        rows.extend(_rows_for_level("level_descriptive", level, False))
    table = pd.DataFrame(rows)
    n_kreis_rows = pool_counts["own"] + pool_counts["fallback"] + pool_counts["proxy"]
    logger.info(
        "[srv_distance_targets] education table: Kreis rows from own RS7 pool %d/%d, "
        "ZGB fallback %d/%d, proxy %d/%d",
        pool_counts["own"], n_kreis_rows, pool_counts["fallback"], n_kreis_rows,
        pool_counts["proxy"], n_kreis_rows)
    logger.info("[srv_distance_targets] education table: %d rows, %d persons total",
                len(table), int(len(obs)))
    return table


def build_quantile_table(obs_work, detour_factor=DEFAULT_DETOUR_FACTOR,
                         prior_strength=DEFAULT_PRIOR_STRENGTH):
    """Per home Kreis the 1..99 percentiles of the EUCLIDEAN-equivalent work distance.

    ``distance_km_euclid = GIS routed km / detour_factor`` matches the euclidean metres
    convention of ``synthesis.population.spatial.commute_distance``. Shrinkage is
    quantile-wise toward the dominant RS7 pool (itself shrunk toward ZGB), which keeps
    the shrunk quantile function monotone (Wasserstein barycenter of the two).

    Emits one ``kreis`` row (x 99 percentiles) per code in :data:`ZGB_KREISE`, even
    when a Kreis has zero persons (n_persons = 0, raw quantiles NaN, shrunk quantiles
    equal the pool). The n = 0 case is handled explicitly rather than through
    ``shrink_toward_pool``, because the pool weight there is n/(n+k) = 0 at n = 0, and
    ``0 * NaN`` is NaN, not 0 -- relying on that arithmetic would silently propagate NaN
    into the shrunk column instead of yielding the pool.
    """
    probs = QUANTILE_PROBABILITIES
    obs = obs_work.assign(euclid=obs_work["distance_km"] / float(detour_factor))
    rs7_of = dominant_rs7_by_kreis(obs)
    zgb_q = weighted_quantiles(obs["euclid"].values, obs["weight"].values, probs)
    rs7_q = {}
    for rs7 in sorted(obs["regiostar7"].unique()):
        sub = obs[obs["regiostar7"] == rs7]
        raw = weighted_quantiles(sub["euclid"].values, sub["weight"].values, probs)
        rs7_q[int(rs7)] = (raw, shrink_toward_pool(raw, len(sub), zgb_q, prior_strength), int(len(sub)))

    if PROXY_RS7 not in rs7_q:
        raise ValueError(
            "No SrV persons with RegioStaR-7 == %d; cannot build the Wolfsburg proxy quantiles" % PROXY_RS7)

    rows = []

    def _emit(level_geo, code, source, n, raw, shrunk):
        for p, r, s in zip(probs, raw, shrunk):
            rows.append({"level_geo": level_geo, "code": code, "source": source, "n_persons": int(n),
                         "percentile": int(round(p * 100)),
                         "distance_km_euclid_raw": float(r), "distance_km_euclid_shrunk": float(s)})

    pools_by_rs7 = {rs7: rs7_q[rs7][1] for rs7 in rs7_q}
    n_own_pool = n_fallback = 0
    for kreis in ZGB_KREISE:
        if kreis == WOLFSBURG_KREIS:
            raw, _, n = rs7_q[PROXY_RS7]
            _emit("kreis", kreis, PROXY_SOURCE, n, raw, raw)
            continue
        sub = obs[obs["kreis"] == kreis]
        n = len(sub)
        pool, used_fallback = _pool_for_kreis(kreis, rs7_of, pools_by_rs7, zgb_q)
        n_fallback += int(used_fallback)
        n_own_pool += int(not used_fallback)
        if n == 0:
            # Explicit n = 0 handling (see the docstring): the raw quantiles are NaN
            # (no observations to compute them from) and the shrunk quantiles are the
            # pool verbatim, not an arithmetic mix that would propagate the NaN.
            raw = np.full(len(probs), np.nan)
            shrunk = np.asarray(pool, dtype=float).copy()
        else:
            raw = weighted_quantiles(sub["euclid"].values, sub["weight"].values, probs)
            shrunk = shrink_toward_pool(raw, n, pool, prior_strength)
        _emit("kreis", kreis, "srv", n, raw, shrunk)
    for rs7, (raw, shrunk, n) in sorted(rs7_q.items()):
        _emit("rs7", str(rs7), "srv", n, raw, shrunk)
    _emit("zgb", "zgb", "srv", len(obs), zgb_q, zgb_q)
    table = pd.DataFrame(rows)
    n_kreis = len(ZGB_KREISE)
    logger.info(
        "[srv_distance_targets] quantile table: Kreis rows from own RS7 pool %d/%d, "
        "ZGB fallback %d/%d, proxy 1/%d",
        n_own_pool, n_kreis, n_fallback, n_kreis, n_kreis)
    logger.info("[srv_distance_targets] quantile table: %d rows, %d persons total",
                len(table), int(len(obs)))
    return table


def _load(srv_dir, name):
    path = os.path.join(str(srv_dir), name)
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"Committed SrV target table missing: {path}. Regenerate with "
            "scripts/extract_srv_primary_distance_targets.py (needs the local-only SrV raw data).")
    return pd.read_csv(path, comment="#", dtype={"code": str})


def load_commute_targets(srv_dir):
    return _load(srv_dir, COMMUTE_TABLE)


def load_education_targets(srv_dir):
    return _load(srv_dir, EDUCATION_TABLE)


def load_commute_quantiles(srv_dir):
    return _load(srv_dir, QUANTILE_TABLE)


def load_commute_sensitivity(srv_dir):
    return _load(srv_dir, SENSITIVITY_TABLE)
