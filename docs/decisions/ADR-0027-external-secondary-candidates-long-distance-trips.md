# ADR-0027 · 2026-06-26 · External secondary candidates (long-distance trips)
- **Status:** active
- **Context:** Some leisure/other secondary trips exceed the ~50km study area (~6% leisure / ~3%
  other), so carla truncates them to the area edge instead of matching the long MiD desired distance.
- **Decision:** Append German Gemeinde centroids OUTSIDE ZGB (population-weighted) to the secondary
  candidate set (`secondary_external_candidates`, on only where `cordon_enabled`); eqasim's
  `RunScenarioCutter` converts the boundary-crossing trip into a fixed "outside" activity.
- **Rationale:** Reuses the existing out-commuter mechanism (`external_workplaces`); direction is a
  distance-only proxy (ASSUMPTION, no secondary OD data); a warning is logged if on without cordon
  (CLAUDE.md "External secondary candidates").
- **Consequences:** Matches the long desired-distance tail; OFF path byte-identical.
- **Evidence:** PR #19 (merged 2026-06-26); commits `0cc2ad2`, `c4fcdda`, `d1aa17c`;
  CLAUDE.md "External secondary candidates".

---

## Cordon / cross-border (Einpendler)

