# ADR-0028 · 2026-06-02 · Cordon external-demand model — targeted crossing agents with full supply
- **Status:** active
- **Context:** The synthetic population is resident-only ZGB, so demand ENTERING the region
  (in-commuters, visitors, through-traffic) is not represented, undercounting network/PT load.
- **Decision:** Build a cordon/external-demand extension as **targeted cordon-crossing agents**
  (Approach B), with **full eqasim agents with external homes** and a MATSim **supply extension** to
  the cordon ring; decomposed and built in order: (1) supply extension → (2) in-commuters →
  (3) external visitors → (4) through-traffic, where 3 & 4 are out of scope.
- **Rationale:** Approach A (synthesise all of Hannover, discard non-crossers) wastes ~90% and needs
  structural data we have only for ZGB; Approach B reuses the `external_workplaces` pattern and the
  all-Germany BA Pendler matrix already on disk (spec D-1..D-5).
- **Consequences:** Supply must cover the ring (prerequisite); sub-projects 3 & 4 never started
  (PROJECT_BACKLOG.md Tier 3.4 — through-freight is covered separately by ADR-0030).
- **Evidence:** spec/roadmap `docs/superpowers/specs/2026-06-02-cordon-external-demand-roadmap.md`
  (Decisions D-1..D-5); PROJECT_STATUS.md §2.5.

