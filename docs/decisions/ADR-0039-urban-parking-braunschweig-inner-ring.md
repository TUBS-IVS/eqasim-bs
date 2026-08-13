# ADR-0039 · 2026-06 · Urban parking (Braunschweig inner ring)
- **Status:** active
- **Context:** Realistic parking pressure is concentrated in the Braunschweig inner ring.
- **Decision:** Add urban parking (`enable_urban_parking`, `matsim/simulation/prepare.py` + Java),
  enabled in the 25%/100% server configs for realism, scoped to the BS inner ring only.
- **Rationale:** Enabled "for more realistic" behaviour in the server configs (commit `b005a0d`);
  inner-ring-only scope per memory `project-building-activity-potentials`.
- **Consequences:** Flag-gated; ON in the server real-data configs.
- **Evidence:** commits `b005a0d`, `bccb21f`; PROJECT_STATUS.md §2.8.

