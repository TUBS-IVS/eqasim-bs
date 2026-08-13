# ADR-0015 · 2026-06-16 · Tier-3 Kreis-level PopulationSim controls
- **Status:** active
- **Context:** The popsim controls were 100m/1km only; some marginals (e.g. education attributes)
  are better controlled at Kreis level via a Codeplan-B1 crosswalk.
- **Decision:** Add Tier-3 Kreis controls (`popsim.control_tiers: …tier3`, `popsim/control_spec.py`)
  sourced from Zensus + GENESIS, plumbed through a KREIS geography with the Codeplan-B1 crosswalk
  fix, landed dormant-first then live-wired across several PRs.
- **Rationale:** Built incrementally (foundation/dormant → live wiring → fixes) to keep each PR
  reviewable, per the working discipline in `CLAUDE.md`.
- **Consequences:** Adds 7 KREIS-level controls; measured fit at KREIS mean |%dev| 2.40%
  (PROJECT_BACKLOG.md step-1b).
- **Evidence:** PRs #3/#4/#5/#6/#7/#8 (merged 2026-06-16..06-17); PROJECT_STATUS.md §2.2.

