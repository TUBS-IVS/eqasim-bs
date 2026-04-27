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

Persons whose home Kreis is outside ZGB-8 or who match an HTS person
without a commute trip keep the ENTD-sampled value unchanged.

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
    df["kreis"] = df["commune_id"].astype(str).str[:5]

    overrides_applied = 0
    for kreis, group_idx in df.groupby("kreis", sort=False,
                                       dropna=True).groups.items():
        cdf = cdfs.get(str(kreis), fallback_cdf)
        if cdf is None:
            continue
        n = len(group_idx)
        samples = _draw_from_cdf(cdf, random, n)
        # Convert km → metres to match the ENTD distance_slot convention.
        df.loc[group_idx, "commute_distance"] = samples * 1000.0
        overrides_applied += n

    print("[braunschweig.synthesis.spatial.commute_distance] MiD P13 "
          "override applied to {}/{} synthetic persons".format(
              overrides_applied, len(df)))

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
