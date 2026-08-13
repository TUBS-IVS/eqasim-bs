# ADR-0043 · 2026-06-27 · REJECTED — Tune secondary scorer `pot_weight`
- **Status:** rejected
- **Context:** The combined chainsolvers scorer's `pot_weight` (pull toward large buildings) might
  add a residual distance distortion worth tuning.
- **Decision:** Keep `secondary_scorer_pot_weight` at the default 1.0; do not tune it.
- **Rationale:** A sweep at 100% showed `pot_weight` is a *concentration* knob — raising it makes the
  building-capacity fit WORSE (over-concentration), while distance never breaks even up to 128;
  default 1.0 is optimal (memory `feedback-capacity-fit-sampling-power`; SESSION_LOG 2026-06-27).
- **Consequences:** Scorer weights stay at config values; the real within-zone lever is a building
  worker-count dataset, not the scorer.
- **Evidence:** SESSION_LOG.md 2026-06-27; commit `8196ec3` (scorer-sweep bench);
  memory `feedback-capacity-fit-sampling-power`; PROJECT_BACKLOG.md Tier 5.

