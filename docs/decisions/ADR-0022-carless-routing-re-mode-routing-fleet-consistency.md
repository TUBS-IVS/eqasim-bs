# ADR-0022 · 2026-06-08 · Carless routing re-mode (routing/fleet consistency)
- **Status:** active
- **Context:** MATSim routing could assign car legs to agents whose household has no car (a
  household-fleet × routing gap).
- **Decision:** Re-mode car legs for carless agents (`remode_carless_car_legs`,
  `matsim/scenario/population.py`), and give every non-owner a routing `default_car` so eqasim-core
  car coverage holds. The flag is registered in that module's `configure`, forwarded by
  `write_population` and applied in `add_person`; it does not appear in
  `matsim/simulation/prepare.py`, which earlier revisions of this record named (issue #254).
- **Rationale:** Routing consistency — the fleet and the routed mode must agree (memory
  `allfeatures-run-fleet-routing-fix`).
- **Consequences:** Closes the fleet×routing gap; OFF path unaffected.
- **Evidence:** memory `allfeatures-run-fleet-routing-fix`; commit `b736953`; PROJECT_STATUS.md §2.3.

---

## Location choice / gravity

