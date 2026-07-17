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
the measured fold noise. P38.2 per-Kreis, via ``p38_band_shares`` below, is
BASELINE-vs-ANCHORED MODEL drift only: this module never loads the MiD P38.2
reference, so it is NOT (yet) a directional comparison against observed data
-- see the TODO in scripts/run_anchor_holdout.py (deferred to Task 8).

KNOWN LIMITATION (inert held-out CV -- final whole-branch review finding,
#193): the anchor (``apply_inner_anchor`` in
braunschweig/gravity/verbindungen_anchor.py) is a pure per-row IN-SAMPLE
reweighting -- for a training fold it only rescales flow AMONG the
destinations that are in THAT fold's training targets. A held-out relation
is, by the CV harness's own construction (scripts/run_anchor_holdout.py:
``t_fold, _ = build_anchor_targets(train_ref, ...)`` where
``train_ref = df_ref_zones[~held]``), excluded from the training targets for
its row, so the anchor never assigns it a scaling factor and its model flow
is passed through UNCHANGED for that fold. ``heldout_conditional_tvd`` only
ever reads model flow on the held-out destinations (see its docstring), so it
reads IDENTICAL numbers for baseline and anchored -- i.e.
``cv_anchored == cv_baseline`` HOLDS BY CONSTRUCTION, for ANY hold-out split,
fold count, or dataset (see ``test_heldout_cv_is_inert_by_construction`` in
tests/test_verbindungen_anchor.py for a hand-derived, bit-exact
demonstration). Consequences:

- ``verdict()``'s ``cv_improves`` is EXPECTED to come back False (or equal up
  to floating point). A False here is NOT evidence that the anchor fails to
  help -- criterion (i) is structurally unable to observe the anchor's
  effect on held-out relations at all, so it cannot distinguish "helps" from
  "does nothing" from "hurts" for THIS anchor.
- Criterion (i) is therefore NON-DISCRIMINATING for this anchor. The flip
  decision must rest on the INDEPENDENT distance axes instead: criterion
  (ii) (P13-by-RS7 EMD, already wired below) and P38.2 per-Kreis once the
  MiD reference is wired in Task 8.
- Whether to drop criterion (i) from the rule, or recast it as a
  non-worsening guard (e.g. a ``cv_anchored <= cv_baseline + p13_noise``
  -style tolerance, which an inert CV trivially satisfies without being
  circular), is an OPEN methodology decision for #193 that the user must
  resolve -- ideally via an ADR -- BEFORE the Task-8 verdict is acted on.
  This module does NOT pre-empt that decision: ``verdict()``'s computation
  and return schema are UNCHANGED; only this documentation is added."""
from __future__ import annotations

import numpy as np
import pandas as pd

# P38.2 band edges in routed km (MiD 2023 Tabelle A P38.2 columns d_unter_5km
# .. d_300km_plus) so that p38_band_shares() below bins MODEL flows onto the
# same bands as the MiD P38.2 reference. This module does NOT load that
# reference CSV or drop/renormalise its d_unplausibel_keine_angabe column --
# p38_band_shares() only shares MODEL flow; see the TODO in
# scripts/run_anchor_holdout.py for the deferred reference comparison.
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
    """Row-renormalised conditional TVD on the held-out relations only.

    An origin whose held-out destination set has FEWER THAN 2 destinations
    carries no conditional structure: with a single held-out destination the
    renormalised share is trivially 1.0 for both model and reference, so the
    row's TVD is always exactly 0 regardless of how well the model actually
    performs there. Such origins are excluded from the pooled num/den (never
    silently averaged in -- CLAUDE.md fallback transparency) so a genuinely
    poor model cannot be handed a free 0 that dilutes the pooled score.
    """
    ref = df_ref_od_zones.copy()
    ref["_held"] = held_mask.to_numpy()
    model = df_model_od_zones.set_index(
        ["origin_zone_id", "destination_zone_id"])["commuters"]

    num, den = 0.0, 0.0
    n_informative, n_skipped_single_dest = 0, 0
    for origin, rows in ref[ref["_held"]].groupby("origin_zone_id"):
        r = rows.set_index("destination_zone_id")["commuters"].astype(float)
        if len(r) < 2:
            n_skipped_single_dest += 1
            continue
        m = pd.Series(
            [float(model.get((origin, d), 0.0)) for d in r.index],
            index=r.index)
        if m.sum() <= 0 or r.sum() <= 0:
            continue
        tvd = 0.5 * float((m / m.sum() - r / r.sum()).abs().sum())
        w = float(r.sum())
        num += w * tvd
        den += w
        n_informative += 1
    print(
        f"[braunschweig.calibration.anchor_holdout] held-out conditional TVD: "
        f"{n_informative} informative origins, {n_skipped_single_dest} skipped "
        f"(single held-out dest)"
    )
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
    """The pre-registered rule. Pure report -- the human + ADR act on it.

    KNOWN LIMITATION (inert held-out CV; see the module docstring above for
    the full mechanism and the #193 cross-reference): for THIS anchor,
    ``cv_anchored`` equals ``cv_baseline`` BY CONSTRUCTION regardless of the
    data, so ``cv_improves`` (and therefore ``default_flip_supported``,
    which ANDs it with ``not regressions``) is EXPECTED to read False here.
    Do NOT read a False ``cv_improves`` as evidence against the anchor --
    criterion (i) cannot see the anchor's effect at all in this design.
    Treat ``p13_regressions`` / ``no_distance_regression`` (criterion (ii))
    and the P38.2 per-Kreis comparison (once wired, Task 8) as the operative
    evidence until the decision rule itself is revisited (open methodology
    decision, #193). This limitation does not change what is computed or
    returned below -- it changes only how the result must be READ.
    """
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
