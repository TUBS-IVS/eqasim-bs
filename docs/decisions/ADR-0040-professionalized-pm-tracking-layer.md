# ADR-0040 · 2026-06-28 · Professionalized PM / tracking layer
- **Status:** active
- **Context:** The project history and open work were spread across memory files and ad-hoc notes;
  a durable, committed PM layer was needed for traceability and onboarding.
- **Decision:** Add `PROJECT_STATUS.md` (feature matrix), `PROJECT_BACKLOG.md` (ranked open work),
  `docs/DECISIONS.md` (this ADR log), `docs/UPSTREAM_DELTA.md`, `docs/ONBOARDING.md`,
  `CONTRIBUTING.md`, `.github/` templates, `RUNS.md`, and split deep feature detail into
  `docs/features/*` (verbatim, no-loss), leaving CLAUDE.md as rules-only.
- **Rationale:** Single sources of truth, kept current via `/close`, per the working discipline in
  CLAUDE.md (spec 2026-06-28).
- **Consequences:** One canonical backlog/status; CLAUDE.md and git win on disagreement.
- **Evidence:** spec `docs/superpowers/specs/2026-06-28-pm-layer-professionalization-design.md`;
  PR #21 (merged 2026-06-28); commits `6b2bdd4`, `d2401a9`, `67a3cd5`.

---

## Rejected / not-adopted decisions

> Recorded so they are not re-attempted. The "why we did NOT do it" is half the value; each cites
> the measurement that killed it (PROJECT_BACKLOG.md §1 Tier 5).

