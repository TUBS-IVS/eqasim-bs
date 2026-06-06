"""
Braunschweig-specific commute-distance attachment.

Replaces ``synthesis.population.spatial.commute_distance``.  The upstream
stage attaches ENTD-sampled commute distances to synthetic persons by
HTS-id alone — for the Braunschweig / ZGB region this is a poor match
because the ENTD reference day distribution is French suburban Paris
and does not capture the much shorter typical commutes in e.g. SK
Braunschweig (mittel 19.1 km, MiD P13) vs longer commutes in rural
Kreise like LK Gifhorn (mittel 27.3 km).

Override strategy
-----------------

For every synthetic person whose **home commune_id** starts with one of
the eight ZGB-8 Kreis prefixes and for whom MiD P13 provides a Kreis-
level CDF:

  1. Keep the ENTD-sampled distance as the baseline value.
  2. Draw a replacement from the MiD P13 band distribution for the
     person's home Kreis.  Replacement is uniformly sampled within the
     selected band to avoid a comb-like artefact.
  3. Use the MiD draw for the ``work`` activity (P13 is a work-commute
     table) and leave ``education`` untouched (no MiD equivalent).

Persons whose home Kreis is not one of the eight ZGB Kreise receive a draw
from the ZGB aggregate band (the ``03ZGB`` fallback row); the synthetic
population is ZGB-8-resident, so this fallback is effectively unused today.
Persons who match an HTS person without a commute trip carry a NaN commute
distance into ``df_work`` and are filtered out downstream before the distance
is consumed, so the fabricated value is harmless.

Config keys (with defaults)::

    braunschweig.mid_commute_override: true         # on by default
    braunschweig.mid_commute_activity: work         # P13 is work-only

Returned structure matches the upstream stage::

    { "work": DataFrame[person_id, hts_id, commute_distance],
      "education": DataFrame[person_id, hts_id, commute_distance] }
"""

from __future__ import annotations

import numpy as np
import pandas as pd


# Band edges (km) matched to ``braunschweig.data.mid.references.P13_BANDS``.
# Lower edges are inclusive, upper edges exclusive except for the open
# 100+ band where we cap at 300 km.
P13_BAND_EDGES = [
    (0.0,   0.5),
    (0.5,   5.0),
    (5.0,  10.0),
    (10.0, 20.0),
    (20.0, 30.0),
    (30.0, 50.0),
    (50.0, 100.0),
    (100.0, 300.0),
]


# Fallback transparency: if more than this share of the applied MiD P13
# overrides use the regional 03ZGB fallback CDF instead of the person's own
# Kreis CDF, a "WARNING: " line is logged. The synthetic population is
# ZGB-8-resident with per-Kreis CDFs available, so the expected fallback rate
# is ~0; a non-trivial rate signals a systematic missing-Kreis-CDF problem.
FALLBACK_RATE_WARN_THRESHOLD = 0.05


def configure(context):
    context.config("random_seed")
    context.stage("synthesis.population.enriched")
    context.stage("data.hts.commute_distance")
    context.stage("synthesis.population.spatial.home.locations")
    context.stage("braunschweig.data.mid.references")

    context.config("braunschweig.mid_commute_override", True)
    context.config("braunschweig.mid_commute_activity", "work")


def _draw_from_cdf(cdf: np.ndarray, random: np.random.RandomState,
                   n: int) -> np.ndarray:
    """Sample n distances (km) from a P13 band CDF using uniform draws
    within each selected band."""
    u = random.random_sample(n)
    band_idx = np.searchsorted(cdf, u, side="right")
    band_idx = np.clip(band_idx, 0, len(P13_BAND_EDGES) - 1)
    out = np.empty(n, dtype=float)
    for i, bi in enumerate(band_idx):
        lo, hi = P13_BAND_EDGES[bi]
        out[i] = lo + (hi - lo) * random.random_sample()
    return out


def _override_work_distances(df_work: pd.DataFrame,
                             mid_refs: dict,
                             random: np.random.RandomState) -> pd.DataFrame:
    """df_work must already carry ``commune_id`` from the home join."""
    cdfs = mid_refs["p13_distance_cdfs"]
    fallback_cdf = cdfs.get("03ZGB")

    df = df_work.copy()
    # BUG-003 fix: commune_id is the 12-digit ARS but pandas may carry it as int
    # (which strips the leading 0 of the Niedersachsen state code "03"). Force
    # to string and pad back to 12 digits before slicing the 5-digit Kreis
    # prefix. zfill(12) is a no-op on the normal 12-char string and restores the
    # leading zero if the column ever arrives as an int.
    df["kreis"] = df["commune_id"].astype(str).str.zfill(12).str[:5]

    # Provenance split of the applied overrides (fallback transparency, see
    # CLAUDE.md). A systematic Kreis miss is only visible if primary (own-Kreis
    # CDF) and regional fallback (03ZGB) overrides are counted separately; the
    # plain total hides a Kreis whose own CDF is silently absent.
    n_kreis_cdf = 0   # primary: person's own Kreis CDF was used
    n_region_cdf = 0  # fallback: no own-Kreis CDF -> 03ZGB regional CDF used
    for kreis, group_idx in df.groupby("kreis", sort=False,
                                       dropna=True).groups.items():
        # Distinguish a present own-Kreis CDF from the 03ZGB fallback. Output
        # is unchanged: the CDF actually used is still cdfs.get(kreis, fallback).
        own_cdf = cdfs.get(str(kreis))
        if own_cdf is not None:
            cdf = own_cdf
            is_fallback = False
        else:
            cdf = fallback_cdf
            is_fallback = True
        if cdf is None:
            continue
        n = len(group_idx)
        samples = _draw_from_cdf(cdf, random, n)
        # Convert km → metres to match the ENTD distance_slot convention.
        df.loc[group_idx, "commute_distance"] = samples * 1000.0
        if is_fallback:
            n_region_cdf += n
        else:
            n_kreis_cdf += n

    overrides_applied = n_kreis_cdf + n_region_cdf
    total = len(df)
    fallback_rate = (n_region_cdf / overrides_applied) if overrides_applied else 0.0
    print("[braunschweig.synthesis.spatial.commute_distance] MiD P13 "
          "override applied to {}/{} synthetic persons "
          "(primary own-Kreis CDF {}, regional 03ZGB fallback {}, "
          "fallback rate {:.1%})".format(
              overrides_applied, total, n_kreis_cdf, n_region_cdf,
              fallback_rate))
    if fallback_rate > FALLBACK_RATE_WARN_THRESHOLD:
        print("[braunschweig.synthesis.spatial.commute_distance] WARNING: "
              "{:.1%} of MiD P13 overrides fell back to the regional 03ZGB "
              "CDF (threshold {:.1%}); a ZGB-8 Kreis may be missing its own "
              "P13 CDF.".format(fallback_rate, FALLBACK_RATE_WARN_THRESHOLD))

    return df[["person_id", "hts_id", "commute_distance"]]


def execute(context):
    df_matching = context.stage("synthesis.population.enriched")
    df_commute_distance = context.stage("data.hts.commute_distance")
    df_home = context.stage("synthesis.population.spatial.home.locations")
    mid_refs = context.stage("braunschweig.data.mid.references")

    use_override = bool(
        context.config("braunschweig.mid_commute_override"))
    activity = context.config("braunschweig.mid_commute_activity")
    random = np.random.RandomState(context.config("random_seed"))

    # Baseline join (identical to upstream stage).
    df_work = pd.merge(
        df_matching[["person_id", "household_id", "hts_id"]],
        df_commute_distance["work"][["person_id", "commute_distance"]]
            .rename(columns=dict(person_id="hts_id")),
        how="left",
    )
    df_education = pd.merge(
        df_matching[["person_id", "household_id", "hts_id"]],
        df_commute_distance["education"][["person_id", "commute_distance"]]
            .rename(columns=dict(person_id="hts_id")),
        how="left",
    )

    # Attach home commune_id for override.
    df_homes_slim = df_home[["household_id", "commune_id"]].drop_duplicates()
    df_work = pd.merge(df_work, df_homes_slim,
                       on="household_id", how="left")
    df_education = pd.merge(df_education, df_homes_slim,
                            on="household_id", how="left")

    if use_override and activity == "work":
        df_work = _override_work_distances(df_work, mid_refs, random)
    else:
        df_work = df_work[["person_id", "hts_id", "commute_distance"]]

    df_education = df_education[["person_id", "hts_id", "commute_distance"]]

    assert len(df_work) == len(df_matching)
    assert len(df_education) == len(df_matching)

    return dict(work=df_work, education=df_education)
