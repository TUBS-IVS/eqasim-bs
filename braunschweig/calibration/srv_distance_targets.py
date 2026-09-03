"""SrV 2023 distance-distribution targets for the primary-activity location models.

Builders turn the local-only SrV 2023 "Braunschweig und RGB" scientific-use microdata
(trips + persons + households) into small committed aggregate tables per home Kreis:
work and education distance band shares (with an intra/inter-Gemeinde split for work),
and per-Kreis distance quantiles for the per-person commute-distance targets. Loaders
read the committed tables back. This module has no synpp dependency and is not
imported by any pipeline stage.

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


HOME_CODE = 19            # V_ZWECK / E_START_ZWECK "Eigene Wohnung"
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


def _kreis_from_ags(ags: pd.Series) -> pd.Series:
    """5-digit Kreis key from an 8-digit AGS; NaN propagates for missing/sentinel AGS values."""
    return _ags8(ags).str[:5]


def select_person_observations(trips, persons, households, purpose_codes,
                               max_distance_km=DEFAULT_MAX_DISTANCE_KM):
    """One home<->purpose distance observation per person for the given purpose codes.

    Selection mirrors eqasim's ``data.hts.commute_distance``: per person the FIRST
    home->purpose trip (start at own home, destination purpose in ``purpose_codes``);
    if that direction has no GIS-valid distance, the FIRST purpose->home trip (start
    purpose in ``purpose_codes``, destination at own home).

    GIS validity is resolved BEFORE the per-person pick, because it decides which
    DIRECTION represents the person (a data-quality substitution): the fallback
    direction is only used when the preferred one carries no routed distance at all.
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
    ``n_missing_trip_ags``).

    Exclusion/diagnostic log (unit noted per key): ``n_candidate_trips`` (trips, both
    directions); ``n_excluded_gis_invalid`` (trips, both directions); ``n_pool_weight_negative``
    / ``n_pool_over_cap`` (trips in the candidate pool, informational only, counted
    BEFORE per-person selection -- do not sum these with the "excluded" keys below);
    ``n_excluded_weight_negative`` / ``n_excluded_over_cap`` (persons, counted on the
    SELECTED trip only, see the ASSUMPTION above); ``n_excluded_no_kreis`` (persons,
    household AGS missing/sentinel); ``n_missing_trip_ags`` (persons, the selected
    trip's own home-direction or away-direction AGS is missing/sentinel);
    ``n_missing_age`` / ``n_missing_regiostar7`` (persons, kept in ``obs`` with NaN/-1
    rather than excluded -- a downstream consumer decides); ``n_persons_selected``
    (persons); ``share_start_ags_equals_household_ags`` (share, over persons with a
    known trip-side AGS, whose home-direction AGS matches the household AGS).
    """
    purpose_codes = tuple(int(c) for c in purpose_codes)
    # A fresh RangeIndex guarantees `outbound[cand.index]` below is a safe positional
    # lookup even if the caller passed a frame with a non-unique or non-default index.
    t = trips.copy().reset_index(drop=True)
    t["weight"] = pd.to_numeric(t["GEWICHT_W_ZENSUS"], errors="coerce")
    t["gis_valid"] = pd.to_numeric(t["GIS_LAENGE_GUELTIG"], errors="coerce") > 0
    t["distance_km"] = pd.to_numeric(t["GIS_LAENGE"], errors="coerce")

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

    # Step 1: GIS-invalid TRIPS cannot represent a person at all; drop them from the
    # pool, then pick the preferred-direction trip per person from what remains.
    n_gis = int((~cand["gis_valid"]).sum())
    log["n_excluded_gis_invalid"] = n_gis
    gis_valid_cand = cand[cand["gis_valid"]].sort_values(
        ["HHNR", "PNR", "direction_rank", "WNR"], kind="stable")
    first = gis_valid_cand.drop_duplicates(["HHNR", "PNR"], keep="first")

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
    dest_ags8 = _ags8(first["V_ZIEL_AGS"])
    is_outbound = (first["direction_rank"] == 0).values
    home_ags8 = np.where(is_outbound, start_ags8, dest_ags8)
    away_ags8 = np.where(is_outbound, dest_ags8, start_ags8)
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
    })
    log["n_persons_selected"] = int(len(obs))
    logger.info(
        "[srv_distance_targets] purposes %s: %d candidate trips -> %d persons selected; "
        "gis_invalid trips %d; pool weight<0 %d trips, pool >%.0f km %d trips; selected persons "
        "dropped: weight<0 %d, >%.0f km %d, no Kreis %d, missing trip AGS %d; home AGS == "
        "household AGS %.1f%% (of %d persons with known trip AGS)",
        purpose_codes, log["n_candidate_trips"], log["n_persons_selected"], n_gis,
        log["n_pool_weight_negative"], max_distance_km, log["n_pool_over_cap"],
        n_neg, max_distance_km, n_cap, n_no_kreis, log["n_missing_trip_ags"],
        100.0 * log["share_start_ags_equals_household_ags"], int(known_home_ags.sum()),
    )
    return obs, log


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


def dominant_rs7_by_kreis(obs: pd.DataFrame) -> dict:
    """Weight-modal RegioStaR-7 type per Kreis (the pool a Kreis shrinks toward).

    Only covers Kreise that actually have persons in ``obs``; a Kreis absent from the
    data has no entry here and callers must fall back to the next-higher pool (ZGB).
    """
    g = obs.groupby(["kreis", "regiostar7"])["weight"].sum().reset_index()
    g = g.sort_values(["kreis", "weight"], ascending=[True, False]).drop_duplicates("kreis")
    return dict(zip(g["kreis"], g["regiostar7"].astype(int)))


def _weighted_mean_median(d, w):
    if len(d) == 0:
        return float("nan"), float("nan")
    return float(np.average(d, weights=w)), float(weighted_quantiles(d, w, [0.5])[0])


def _share_block(d, w, edges, labels, prefix):
    shares = weighted_band_shares(d, w, edges)
    return {f"{prefix}_{lbl}": float(s) for lbl, s in zip(labels, shares)}, shares


def _pool_shares(obs, edges, mask=None):
    sub = obs if mask is None else obs[mask]
    return weighted_band_shares(sub["distance_km"].values, sub["weight"].values, edges), int(len(sub))


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

    rs7_shares = {}
    for rs7 in sorted(obs_work["regiostar7"].unique()):
        sel = obs_work["regiostar7"] == rs7
        rs7_shares[int(rs7)] = {}
        for s, m in scopes.items():
            mask = sel if m is None else (sel & m)
            raw, n = _pool_shares(obs_work, edges, mask)
            shrunk = shrink_toward_pool(raw, n, zgb_shares[s][0], prior_strength)
            rs7_shares[int(rs7)][s] = (raw, shrunk, n)

    def _row(level_geo, code, source, sub, pool_for_scope):
        row = {"level_geo": level_geo, "code": code, "source": source, "n_persons": int(len(sub))}
        row["mean_km"], row["median_km"] = _weighted_mean_median(sub["distance_km"].values, sub["weight"].values)
        w_intra = sub.loc[sub["intra_gemeinde"].astype(bool), "weight"].sum()
        row["share_intra"] = float(w_intra / sub["weight"].sum()) if sub["weight"].sum() > 0 else float("nan")
        for s, m in scopes.items():
            part = sub if m is None else sub[m.loc[sub.index]]
            block, raw = _share_block(part["distance_km"].values, part["weight"].values, edges, labels, f"share_{s}")
            row.update(block)
            shrunk = shrink_toward_pool(raw, len(part), pool_for_scope[s], prior_strength) if pool_for_scope else raw
            row.update({f"share_{s}_shrunk_{lbl}": float(v) for lbl, v in zip(labels, shrunk)})
            row[f"emd_noise_95_{s}"] = bootstrap_emd_noise_floor(
                part["distance_km"].values, part["weight"].values, edges, n_bootstrap=n_bootstrap, seed=seed)
        return row

    # Wolfsburg's own RS7-72 pool must exist at all (a global data gap, not merely an
    # empty Kreis) for the proxy row to be scientifically defensible; fail early.
    proxy_sub = obs_work[obs_work["regiostar7"] == PROXY_RS7]
    if proxy_sub.empty:
        raise ValueError("No SrV persons with RegioStaR-7 == %d; cannot build the Wolfsburg proxy" % PROXY_RS7)

    for kreis in ZGB_KREISE:
        if kreis == WOLFSBURG_KREIS:
            # Wolfsburg proxy: the RS7-72 pool row (no further shrinkage; source flags
            # the assumption).
            rows.append(_row("kreis", kreis, PROXY_SOURCE, proxy_sub, None))
            continue
        sub = obs_work[obs_work["kreis"] == kreis]
        rs7 = rs7_of.get(kreis)
        pool = {s: rs7_shares[rs7][s][1] for s in scopes} if rs7 is not None \
            else {s: zgb_shares[s][0] for s in scopes}
        rows.append(_row("kreis", kreis, "srv", sub, pool))

    for rs7 in sorted(rs7_shares):
        rows.append(_row("rs7", str(rs7), "srv", obs_work[obs_work["regiostar7"] == rs7],
                         {s: zgb_shares[s][0] for s in scopes}))
    rows.append(_row("zgb", "zgb", "srv", obs_work, None))
    table = pd.DataFrame(rows)
    logger.info("[srv_distance_targets] commute table: %d rows, %d persons total",
                len(table), int(len(obs_work)))
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

    def _rows_for_level(level_col, level, comparable):
        sel = obs[obs[level_col] == level]
        zgb_raw, zgb_n = _pool_shares(sel, edges)
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
                out.append(_r("kreis", kreis, PROXY_SOURCE, proxy_sub, None))
                continue
            sub = sel[sel["kreis"] == kreis]
            pool = rs7_pool.get(rs7_of.get(kreis, -1), zgb_raw)
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

    for kreis in ZGB_KREISE:
        if kreis == WOLFSBURG_KREIS:
            raw, _, n = rs7_q[PROXY_RS7]
            _emit("kreis", kreis, PROXY_SOURCE, n, raw, raw)
            continue
        sub = obs[obs["kreis"] == kreis]
        n = len(sub)
        rs7 = rs7_of.get(kreis)
        pool = rs7_q[rs7][1] if rs7 is not None else zgb_q
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
