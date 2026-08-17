# ADR-0037 · 2026-06-22 · Tier-A/B shareable-stage set + fixed popsim work_dir
- **Status:** active (config wiring partial)
- **Context:** Beyond the freight chain, many synpp stages (and the popsim donor build) are
  sampling-/path-independent and could be shared across runs, but sharing `popsim.stage` needs a
  single fixed work_dir so its hash is identical.
- **Decision:** Share the 32 empirically verified sampling-/path-independent stages plus
  `popsim.stage` and `popsim.completed_donor`, using a single fixed
  `braunschweig.population.popsim.work_dir` across all configs, protected by a stale-batch guard.
- **Rationale:** The MiD donor build depends only on MiD data/seed/day-filter/weekend flag (not on
  controls/sampling/work_dir), so it is computed once and reused across all runs (`docs/features/cache-share.md`).
- **Consequences:** Makes a 100% production run affordable; config wiring is partial
  (PROJECT_BACKLOG.md Tier 1.3).
- **Evidence:** spec `docs/superpowers/specs/2026-06-22-tier-a-b-caching-design.md`;
  plan `2026-06-22-tier-a-b-caching.md`; PROJECT_STATUS.md §2.8.

