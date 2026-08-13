# ADR-0012 · 2026-06 · PT subscription as a categorical 3-margin IPF (MiD P24.1)
- **Status:** active
- **Context:** A single boolean `has_pt_subscription` loses the ticket-type structure and is not
  conditioned on demographics.
- **Decision:** Assign each person a categorical `pt_subscription_type` from a 3-margin IPF (raking)
  on `X[kreis, sex, age_bin, ticket_type]` against MiD 2023 P24.1 Kreis/sex/age margins;
  derive `has_pt_subscription = type ∈ PT_TICKET_FLATRATE`. Flag `pt_subscription_conditioned`.
- **Rationale:** The flatrate set matches the legacy single-target Kreis share within ±1pp (tested);
  MiD's three margins are independently rounded to integer percent, so raking finds a least-squares
  compromise within ~5pp on the worst cell (CLAUDE.md "PT ticket type").
- **Consequences:** Reference CSVs are seeded only via `scripts/seed_mid_constraint_tables.py`
  (hard-coding percentages in Python is prohibited).
- **Evidence:** CLAUDE.md "PT ticket type (P24.1)"; `tests/test_mid_reference_tables.py`;
  PROJECT_STATUS.md §2.2 (MiD P24.1).

