# ADR-0063 — Per-Bundesland commute-mode reference for cross-cordon in-commuters (#129) (2026-07-15, PR #180 MERGED)

- **Context:** `braunschweig.data.mikrozensus.reference.build_mode_reference_by_bundesland` (+ committed GENESIS
  12251-0105/0106 margin CSVs `mid_mode_margin_by_bundesland.csv` / `mid_distance_margin_by_bundesland.csv`) had
  **zero callers**. Production (`incommuters.execute`) gave every cross-cordon in-commuter the same NATIONAL
  PT/car split, although a traceable regional reference already existed — a direct instance of the "no invented
  reference values; use the committed regional reference that exists" rule.
- **Decision:** select the commute-mode reference **per in-commuter by its origin Bundesland** (first 2 ARS
  digits via new `bundesland_of_ars` / `BUNDESLAND_BY_ARS2`), built only for the Bundeslaender present among the
  source Kreise (`source_bundeslaender`); missing Laender fall back to the national reference, logged
  (WARNING > 5%). New pure helper `assign_fixed_mode_per_agent` (per-agent-reference twin of `assign_fixed_mode`,
  byte-identical on a homogeneous list). Gated by `cordon_incommuter_mode_reference_by_bundesland` (default ON;
  OFF byte-identical for the same seed). The two umlaut CSV names are spliced via `chr(0xFC)` so the source
  stays ASCII while byte-matching the UTF-8 CSV (test-guarded).
- **Consequence / honesty note:** the realised aggregate effect is **small**. felix 25% run (17,105 in-commuters,
  seed 1234, mode balancer on): in-commuter PT share 15.28% (national) -> 15.15% (per-Bundesland) = **-0.13 pp**.
  The issue premise (in-commuters predominantly NDS/ST, PT far below the national blend) did NOT translate into a
  large shift — the national reference already yields ~15% PT at in-commuter (long) distances. An early
  single-distance NDS/ST-only hand proxy suggested -2.3 pp; it used invented test reference values and OVERSTATED
  the effect (superseded; measure, don't assert). Primary coverage IS real: 16/16 source Laender, primary
  17105/17105 (100%), fallback 0%.
- **Evidence:** PR #180 (Closes #129, MERGED `2490c37`); RUNS row `incommuter-mode-bundesland-smoke-2026-07-15`;
  memory `project-incommuter-mode-reference-by-bundesland`, `feedback-no-divergent-branch-against-shared-cache`.

---

