# ADR-0034 · 2026-06-27 · Secondary leisure W12 fix (leisure_correction_factor)
- **Status:** active
- **Context:** A full 100% synthesis-only validation run revealed the realised secondary leisure
  distribution was off (W12 leisure EMD 0.131).
- **Decision:** Apply the legacy `leisure_correction_factor=2.0` only on the legacy per-mode path,
  not when the Tier-1 purpose-resolved distances are active (it was double-counting with the
  purpose-resolved distances, a mode-only-era heuristic).
- **Rationale:** With the fix, W12 leisure EMD 0.131→0.050 at 100% (all purposes pass; shop/other
  unchanged) — measured, not assumed (SESSION_LOG 2026-06-27).
- **Consequences:** Corrects an interaction introduced by ADR-0026.
- **Evidence:** PR #20 (merged 2026-06-27); commit `ba734c9`; SESSION_LOG.md 2026-06-27.

---

## Calibration corner

