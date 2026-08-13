# ADR-0044 · 2026-06 · REJECTED — Rake employment to MiD P9
- **Status:** rejected
- **Context:** Employment could be controlled against the MiD P9 survey instead of the GENESIS
  register (see ADR-0014).
- **Decision:** Do NOT rake employment to P9; keep it raked to GENESIS 13111 (register).
- **Rationale:** P9 is survey noise (~900/Kreis, 43–59% spread, ~4pp definitional difference); raking
  to it would overfit noise. P9 is a validation cross-check, not a control (PROJECT_BACKLOG.md Tier 5).
- **Consequences:** Employment anchored to register totals.
- **Evidence:** PROJECT_BACKLOG.md Tier 5 ("Raking employment to MiD P9");
  memory `synthesis-method-and-optimization`.

