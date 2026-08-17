# ADR-0029 · 2026-06-02..06-05 · Einpendler injection with road + PT/Bahnhof gates and mode balancer
- **Status:** active
- **Context:** In-commuter agents need a network entry point and a mode that matches observed
  cross-border travel.
- **Decision:** Inject in-commuters (`cordon_enabled`, `synthesis/incommuters.py`,
  `incommuter_merge/`) entering via road and PT/Bahnhof gates (OSM, GTFS), with a mode balancer
  grounded on Mikrozensus modes; cordon network built by enlarge-then-cut.
- **Rationale:** Gates give a realistic entry geometry; the mode reference and balancer ground the
  cross-cordon mode split on Mikrozensus (committed reference) (spec set 06-02..06-05).
- **Consequences:** `einpendler_extern` cross-cordon demand validated; uncalibrated gate
  gravity-beta/capacity-exponent parked (PROJECT_BACKLOG.md Tier 3.3).
- **Evidence:** specs `2026-06-02-incommuter-agents-v1*-design.md`,
  `2026-06-03-incommuter-mode-reference-design.md`, `2026-06-05-cross-cordon-external-demand-design.md`;
  plan `2026-06-05-cordon-einpendler-injection.md`; PROJECT_STATUS.md §2.5.

---

## Freight

