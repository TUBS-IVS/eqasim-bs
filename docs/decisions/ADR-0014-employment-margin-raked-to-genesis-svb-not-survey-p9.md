# ADR-0014 · 2026-06 · Employment margin raked to GENESIS SvB (not survey P9)
- **Status:** active
- **Context:** Employment could be controlled from the MiD P9 survey or the GENESIS register.
- **Decision:** Add an employment margin to the IPF (`ipf.use_employment_margin`) raked to GENESIS
  SvB (register data), and do NOT rake to MiD P9.
- **Rationale:** P9 is survey noise (~900/Kreis, 43–59% spread, ~4pp definitional difference);
  raking to it would overfit noise (PROJECT_BACKLOG.md §1 Tier 5). See the rejected ADR-0035.
- **Consequences:** Employment is anchored to register totals; P9 is used as a validation
  cross-check only.
- **Evidence:** PROJECT_STATUS.md §2.2 (GENESIS SvB); PROJECT_BACKLOG.md Tier 5
  ("Raking employment to MiD P9"); memory `synthesis-method-and-optimization`.

