# ADR-0039 · 2026-06 · Urban parking (Braunschweig inner ring)
- **Status:** active
- **Context:** Realistic parking pressure is concentrated in the Braunschweig inner ring.
- **Decision:** Add urban parking (`enable_urban_parking`, `matsim/scenario/population.py` + Java),
  enabled in the 25%/100% server configs for realism, scoped to the BS inner ring only. The flag is
  registered in that module's `configure`, forwarded by `write_population` and applied in
  `add_person`; it does not appear in `matsim/simulation/prepare.py`, which earlier revisions of
  this record named (issue #254).
- **Rationale:** Enabled "for more realistic" behaviour in the server configs (commit `b005a0d`);
  inner-ring-only scope per memory `project-building-activity-potentials`.
- **Consequences:** Flag-gated; ON in the server real-data configs.
- **Evidence:** commits `b005a0d`, `bccb21f`; PROJECT_STATUS.md §2.8.

