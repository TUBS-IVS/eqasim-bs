"""Hold-out cross-validation + pre-registered verdict for the inner
VerBindungen anchor (#193).

CV semantics (spec): held-out observed relations are treated as CENSORED
during anchoring, so their absolute flows never move; what moves are the
row-renormalised conditional shares (anchored siblings change each row's
observed-set normalisation). ``heldout_conditional_tvd`` therefore restricts
BOTH model and reference conditionals to each row's held-out destinations and
renormalises both -- precisely the renormalisation-transfer the anchor claims.

Pre-registered decision rule (structure fixed BEFORE the runs; no invented
numeric thresholds): the default flips to ON only if (i) the pooled held-out
conditional TVD improves vs baseline AND (ii) no P13-by-RS7 EMD worsens beyond
the measured fold noise. P38.2 per-Kreis is reported as DIRECTIONAL evidence
only (thin n per Kreis -- robust-references rule)."""
from __future__ import annotations

import numpy as np
import pandas as pd

# P38.2 band edges in routed km (MiD 2023 Tabelle A P38.2 columns d_unter_5km
# .. d_300km_plus; the d_unplausibel_keine_angabe column is dropped and the
# reference shares renormalised).
P38_BAND_EDGES_KM = [0.0, 5.0, 10.0, 20.0, 30.0, 50.0, 100.0, 200.0, 300.0,
                     float("inf")]


def assign_folds(df_ref_rows: pd.DataFrame, k: int, seed: int) -> pd.Series:
    """Fold index per observed relation, stratified per (origin, dest-Kreis)
    row. Rows with < 2 observed destinations cannot be split and are always
    TRAIN (fold -1, counted by the caller via (folds == -1).sum())."""
    rng = np.random.default_rng(seed)
    folds = pd.Series(-1, index=df_ref_rows.index, dtype=int)
    for _, idx in df_ref_rows.groupby(
            ["origin_zone_id", "dest_kreis"]).groups.items():
        idx = list(idx)
        if len(idx) < 2:
            continue
        order = rng.permutation(len(idx))
        for pos, i in enumerate(order):
            folds.loc[idx[i]] = pos % k
    return folds


def heldout_conditional_tvd(df_model_od_zones: pd.DataFrame,
                            df_ref_od_zones: pd.DataFrame,
                            held_mask: pd.Series) -> float:
    """Row-renormalised conditional TVD on the held-out relations only."""
    ref = df_ref_od_zones.copy()
    ref["_held"] = held_mask.to_numpy()
    model = df_model_od_zones.set_index(
        ["origin_zone_id", "destination_zone_id"])["commuters"]

    num, den = 0.0, 0.0
    for origin, rows in ref[ref["_held"]].groupby("origin_zone_id"):
        r = rows.set_index("destination_zone_id")["commuters"].astype(float)
        m = pd.Series(
            [float(model.get((origin, d), 0.0)) for d in r.index],
            index=r.index)
        if m.sum() <= 0 or r.sum() <= 0:
            continue
        tvd = 0.5 * float((m / m.sum() - r / r.sum()).abs().sum())
        w = float(r.sum())
        num += w * tvd
        den += w
    return num / den if den else float("nan")


def p38_band_shares(distances_km: np.ndarray,
                    weights: np.ndarray) -> np.ndarray:
    """Flow-weighted shares over the P38.2 band edges (routed km)."""
    idx = np.digitize(distances_km, P38_BAND_EDGES_KM[1:-1], right=False)
    shares = np.zeros(len(P38_BAND_EDGES_KM) - 1)
    for i, w in zip(idx, weights):
        shares[i] += w
    total = shares.sum()
    return shares / total if total > 0 else shares


def verdict(cv_baseline: float, cv_anchored: float,
            p13_emd_baseline: dict, p13_emd_anchored: dict,
            p13_noise: float) -> dict:
    """The pre-registered rule. Pure report -- the human + ADR act on it."""
    improves = bool(cv_anchored < cv_baseline)
    regressions = {
        rs7: (p13_emd_anchored[rs7], p13_emd_baseline[rs7])
        for rs7 in p13_emd_baseline
        if p13_emd_anchored.get(rs7, float("inf"))
        > p13_emd_baseline[rs7] + p13_noise
    }
    return dict(
        cv_baseline=cv_baseline, cv_anchored=cv_anchored,
        cv_improves=improves,
        p13_regressions=regressions,
        no_distance_regression=not regressions,
        p13_noise=p13_noise,
        default_flip_supported=improves and not regressions,
    )
