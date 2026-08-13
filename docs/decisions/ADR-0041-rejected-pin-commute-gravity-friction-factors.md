# ADR-0041 · 2026-06-25 · REJECTED — Pin commute gravity friction factors
- **Status:** rejected
- **Context:** A per-band commute friction (`gravity_friction_factors`) was built to make the
  realised home→work distance distribution match MiD P13, motivated by a historical "EMD 0.47 FAIL".
- **Decision:** Do NOT pin any friction factors; leave them at the `None` default (legacy
  `exp(slope·d)`); keep the machinery as gated-off infrastructure.
- **Rationale:** Measured on `cache_bs_25pct_allfeat`, the model already matches P13 (donor targets
  EMD 0.0037, gravity OD EMD 0.037, realised straight-line ~0.065, all below the 0.08 threshold). The
  "0.47 FAIL" was a STALE figure on MATSim-routed distances from a run *before* the building-activity
  potentials (ADR-0025) reshaped placement (`docs/features/calibration-corner.md`).
- **Consequences:** Pipeline stays byte-identical to legacy friction; lesson "measure before
  calibrating" reinforced.
- **Evidence:** `docs/features/calibration-corner.md` (Finding 2026-06-25); commit `1a10e15`;
  PROJECT_BACKLOG.md Tier 5; memory `feedback-measure-before-calibrating`.

