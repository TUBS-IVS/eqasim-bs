# ADR-0047 · 2026-06-25 · REJECTED — ATTACH strategy for building potentials
- **Status:** superseded by ADR-0025
- **Context:** Building-level activity potentials for work/secondary were first designed to ATTACH a
  potential weight to the existing zone-level candidate set.
- **Decision:** Replace ATTACH with REPLACE (use the gpkg buildings as the candidate set directly) for
  work and secondary, after a mid-session pivot.
- **Rationale:** not recoverable from the committed record beyond the pivot itself; recorded in the
  backlog as "Replaced by REPLACE (gpkg buildings as candidate set) after mid-session pivot"
  (PROJECT_BACKLOG.md Tier 5).
- **Consequences:** Work/secondary source candidates from real `potential_work`/`pot_*` buildings;
  education keeps ATTACH within the assigned facility (ADR-0025).
- **Evidence:** PROJECT_BACKLOG.md Tier 5 ("ATTACH strategy for building potentials");
  memory `project-building-activity-potentials`; PR #16.

