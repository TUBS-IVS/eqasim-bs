# ADR-0000 · 2025-10-06 · eqasim-bavaria baseline (fork point)
- **Status:** active
- **Context:** A scientific MATSim/eqasim transport model was needed for the Zweckverband
  Großraum Braunschweig (ZGB-8, Niedersachsen). Rather than build from scratch, the project
  forks the closest existing regional eqasim configuration.
- **Decision:** Fork `eqasim-org/eqasim-bavaria` at merge-base commit `b20fbe6` ("Merge pull
  request #14 from eqasim-org/chore/rename", 2025-10-06) into `TUBS-IVS/eqasim-bs`. Inherit the
  entire eqasim machinery (Python synpp synthesis from the French ENTD trip donor + census,
  the Java MATSim modules for mode choice/scoring/simulation, and the Bavaria/Munich scenario
  configs) and add a new `braunschweig/` region module on top.
- **Rationale:** Upstream already provides the proven eqasim pipeline; eqasim-bs adds a new
  region plus data-driven realism as a *delta* on a known baseline, keeping the history
  traceable (UPSTREAM_DELTA.md).
- **Consequences:** ~776 commits and a 303-file `braunschweig/` module (~70k insertions) sit on
  top of the baseline; the French ENTD-2008 trip donor is inherited (and later flagged as the
  highest-value replacement lever — see ADR-0038). PRs always target the fork base, never the
  eqasim-org upstream.
- **Evidence:** `docs/UPSTREAM_DELTA.md` (pinned merge-base `b20fbe6`); `CHANGELOG.md`
  v0.1.0-bs (2026-04-27) first tagged regional release on top of `b20fbe6`.

