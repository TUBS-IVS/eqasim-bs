"""Hold-out cross-validation + pre-registered verdict for the inner
VerBindungen anchor (#193).

CV semantics (spec): held-out observed relations are treated as CENSORED
during anchoring, so their absolute flows never move; what moves are the
row-renormalised conditional shares (anchored siblings change each row's
observed-set normalisation). ``heldout_conditional_tvd`` therefore restricts
BOTH model and reference conditionals to each row's held-out destinations and
renormalises both -- precisely the renormalisation-transfer the anchor claims.

Pre-registered decision rule v2 (#193; structure fixed BEFORE any measurement
run; no invented numeric thresholds -- every noise scale is MEASURED).
AMENDMENT HISTORY: rule v1 required (i) the pooled held-out conditional TVD
to improve vs baseline AND (ii) no P13-by-RS7 EMD regression beyond fold
noise. On 2026-07-17 -- before any Task-8 measurement run produced a verdict
-- criterion (i) was PROVEN structurally inert for this anchor (see the
KNOWN LIMITATION below and ``test_heldout_cv_is_inert_by_construction``), so
v1 could never have flipped the default regardless of the evidence. Rule v2
replaces (i) with an axis the anchor can actually move.

WHY THESE AXES (invariance argument): the anchor's only degrees of freedom
are destination-zone shares WITHIN an (origin zone x dest Kreis) block, under
row-mass and block-total conservation (asserted to 1e-9 in
``apply_inner_anchor``). Every functional that is invariant to within-block
reshuffling therefore CANNOT discriminate baseline from anchored: Kreis-block
totals, row-observed masses, censored-pair flows (and with them the whole
censored-bound diagnostic, whose global universe ratio is conserved),
intra-Kreis shares (a block total), and the held-out conditional TVD (held
flows untouched). Exactly two available axes DO respond to within-block
reshuffling: (a) zone-level workplace-inflow (AO) margins -- the anchor fits
origin-side row conditionals, NEVER destination margins, so movement toward
the independently observed Statisch_AO margins is genuine corroboration
(same 2019 SvB universe, share-based comparison; vintage caveat documented);
and (b) flow-weighted distance distributions (MiD 2023 P13/P38.2 -- a fully
independent source), because destination zones within a Kreis differ in
distance.

RULE v2: the default flips to ON only if
(i')  AO-margin corroboration: the anchored zone-level inflow-share srmse vs
      the observed Statisch_AO margins improves on baseline by MORE than the
      measured fold noise of that srmse; AND
(ii)  no P13-by-RS7 EMD worsens beyond THAT CLASS's measured fold noise
      (per-class noise, not the std of the cross-class mean).
P38.2 per-Kreis vs the MiD reference (loaded via the tested
``p38_2_band_target`` helper in the CLI) is reported as DIRECTIONAL evidence
only (thin n per Kreis -- robust-references rule), never a gate. The held-out
conditional TVD is retained ONLY as a HARNESS-LEAK DETECTOR: the CLI asserts
``cv_anchored == cv_baseline`` (any gap means the CV harness leaked training
information) -- see the KNOWN LIMITATION below for why equality is the
designed expectation.

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

- ``cv_anchored == cv_baseline`` is the DESIGNED expectation, so equality is
  NOT evidence against the anchor -- v1's criterion (i) was structurally
  unable to observe the anchor's effect at all, which is exactly why rule v2
  (above) replaced it on 2026-07-17, before any measurement run.
- The CLI keeps computing both CV values purely as a LEAK DETECTOR: a
  non-zero gap between them means the CV harness leaked training information
  into the held-out evaluation (or the anchor stopped conserving) and the
  run RAISES instead of reporting a verdict."""
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


def verdict(ao_srmse_before: float, ao_srmse_after: float, ao_noise: float,
            p13_emd_baseline: dict, p13_emd_anchored: dict,
            p13_noise_by_rs7: dict) -> dict:
    """The pre-registered rule v2. Pure report -- the human + ADR act on it.

    Rule v2 (#193, amended 2026-07-17 BEFORE any measurement run; see the
    module docstring for the amendment history and the invariance argument):
    ``default_flip_supported`` is True only if
    (i')  the anchored AO-margin share srmse improves on baseline by MORE
          than the measured fold noise (``ao_srmse_after <
          ao_srmse_before - ao_noise``) -- the anchor never fits destination
          margins, so this is corroboration on a non-fitted axis; AND
    (ii)  no P13-by-RS7 EMD worsens beyond THAT CLASS's measured fold noise.

    All noise scales are measured (fold-to-fold std of the anchored-variant
    metric under training-subset perturbation), never invented. A baseline
    RS7 class without a measured noise value RAISES (a silently-defaulted
    noise would make the regression gate vacuous for that class). An RS7
    class present in the baseline but absent from the anchored dict counts
    as a regression (metric lost), mirroring v1's ``float("inf")`` guard.
    """
    missing_noise = sorted(set(p13_emd_baseline) - set(p13_noise_by_rs7))
    if missing_noise:
        raise RuntimeError(
            "[braunschweig.calibration.anchor_holdout] no measured fold "
            f"noise for RS7 class(es) {missing_noise} -- refusing a vacuous "
            "regression gate; every baseline class needs a per-class noise"
        )
    ao_improves = bool(ao_srmse_after < ao_srmse_before - ao_noise)
    regressions = {
        rs7: (p13_emd_anchored.get(rs7, float("inf")), p13_emd_baseline[rs7])
        for rs7 in p13_emd_baseline
        if p13_emd_anchored.get(rs7, float("inf"))
        > p13_emd_baseline[rs7] + p13_noise_by_rs7[rs7]
    }
    return dict(
        ao_srmse_before=ao_srmse_before, ao_srmse_after=ao_srmse_after,
        ao_noise=ao_noise, ao_improves=ao_improves,
        p13_regressions=regressions,
        no_distance_regression=not regressions,
        p13_noise_by_rs7=dict(p13_noise_by_rs7),
        default_flip_supported=ao_improves and not regressions,
    )
