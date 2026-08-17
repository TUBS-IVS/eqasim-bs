# ADR-0073 — Escort multi-child anchoring: consecutive-run rule, overflow to draw (#201) (2026-08-12, merged via PR #260)

- **Status:** accepted.
- **Context:** the Phase-2 household link anchored ALL of an escorter's escort
  activities at ONE child's school (youngest). Multi-drop chains
  (home->school->school->work) collapsed onto one point: 674 zero-distance
  escort legs (~9% of escort legs) in the 5% run of 2026-08-11 (RUNS.md row
  `escort-AB-5pct-2026-08-11`) -- an artifact, not behaviour. Non-consecutive
  escort activities (bring ... fetch) at the same location are correct and
  unaffected.
- **Decision:** anchor per activity. `build_escort_links` returns ALL linkable
  household children (youngest first); maximal blocks of consecutive escort
  activities anchor at DISTINCT children in rank order; separate blocks restart
  at the youngest (bring/fetch pairs stay at the same schools -- assumption:
  chains visit children youngest-first, the surveys do not observe within-chain
  child order); activities beyond the linkable children fall back to the
  SrV-weighted draw (rate-logged), NEVER cycled back to child 0 (cycling would
  recreate the artifact for one-child households). `find_assignment_problems`
  gains an optional per-activity anchor table ((person_id, activity_index) ->
  geometry) consulted at escort_linked boundaries; the legacy path (no table)
  is unchanged. No new flag: this corrects a documented assumption INSIDE the
  unmerged `escort_household_link` feature.
- **Consequences:** remaining zero-distance escort->escort legs ~= same-school
  siblings (genuine); anchored/overflow rates are logged by the stage; the
  follow-up 5% re-run measures the drop in consecutive zero-legs and re-checks
  the W1/W12 invariants before the PR.
- **Evidence:** commits `223ff6d` (consecutive-run anchor assignment),
  `d8b66b9` (per-activity anchor table wiring), `200dcd4` (anchor at distinct
  children, overflow to draw) on branch `feature/escort-purpose-201`;
  `docs/features/escort-purpose.md`. The run record
  `escort-AB-5pct-2026-08-11` (674 zero-distance legs; also cited in the
  module docstring of `braunschweig/synthesis/locations/escort_links.py`)
  exists as a pending-commit RUNS.md row in the main checkout as of
  2026-08-12 and becomes traceable in-repo once that PM-layer commit lands or
  this branch merges; it is not present in this branch's own committed
  RUNS.md as of this commit.

