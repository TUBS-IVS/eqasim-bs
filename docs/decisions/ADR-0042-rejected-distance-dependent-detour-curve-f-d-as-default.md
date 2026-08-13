# ADR-0042 · 2026-06-25 · REJECTED — Distance-dependent detour curve f(d) as default
- **Status:** rejected
- **Context:** Circuity decays with distance (Giacomin & Levinson 2015), so a fitted curve
  `c(d)=c_inf+a·exp(-d/tau)` could in principle improve the euclidean→routed axis vs the constant 1.3.
- **Decision:** Keep the constant detour factor 1.3 as the DEFAULT; the fitted curve is opt-in
  infrastructure (`mode="curve"`) only.
- **Rationale:** Fitted on the 25% synthesis and measured: commute EMD vs P13 0.0878→0.0849
  (Δ~0.003), pooled secondary walk vs W12 0.0712→0.0729 (slightly worse) — both far below the 0.01
  materiality threshold (`docs/features/detour-circuity.md` VERDICT 2026-06-25).
- **Consequences:** No education re-pin; pipeline byte-identical to the pre-Tier-3 constant 1.3; the
  pt-uplift placeholder must be verified before any future curve activation.
- **Evidence:** `docs/features/detour-circuity.md` (VERDICT); commit `4de2d51`,
  `5aa7fe5` (`band_shift_impact.csv`); SESSION_LOG.md 2026-06-25; PROJECT_BACKLOG.md Tier 5.

