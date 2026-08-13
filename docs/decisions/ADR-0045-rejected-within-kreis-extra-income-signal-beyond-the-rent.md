# ADR-0045 · 2026-06-15 · REJECTED — Within-Kreis extra income signal beyond the rent tilt
- **Status:** rejected
- **Context:** Beyond the Nettokaltmiete rent tilt (ADR-0010), an additional sub-Kreis income signal
  was considered.
- **Decision:** Do NOT add a within-Kreis *extra* income signal; keep only the rent tilt (+0.032 Pearson).
- **Rationale:** No external sub-Kreis income ground truth exists (RWI-GEO-GRID is FDZ-restricted),
  and the size/tenure/age controls already dominate the within-Kreis income variation
  (PROJECT_BACKLOG.md Tier 5; memory `project-income-spatial-tilt`).
- **Consequences:** A Kreis-level income control (via INKAR targets) remains a deferred future option
  (PROJECT_BACKLOG.md Tier 3.1), distinct from this rejected within-Kreis extra signal.
- **Evidence:** PROJECT_BACKLOG.md Tier 5 + Tier 3.1; memory `project-income-spatial-tilt`.

