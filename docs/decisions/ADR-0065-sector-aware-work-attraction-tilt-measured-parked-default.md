# ADR-0065 — Sector-aware work-attraction tilt: measured, PARKED (default OFF); two enabling-path bugs fixed (#128) (2026-07-15)

- **Context:** `apply_sector_aware_attraction` (`a0ecee3`, model-improvement item #8) tilts the per-Gemeinde
  gravity work attraction by establishment density (`n_betriebe`/employee vs the Kreis mean, Kreis totals
  preserved). Built flag-gated (default OFF), never activated in any config, never recorded in PM docs.
  Issue #128 asked: A/B-measure, then flip the default or park with an ADR. Original intent (legitimate):
  equal-headcount Gemeinden with one dominant plant vs many small firms should not commute identically;
  eqasim-IDF fills this with SIRENE establishment microdata that Braunschweig lacks.
- **Measurement (2026-07-15, felix `~/wt-128-ab`, gravity-only A/B, private working dir, ZGB-8, only the flag
  differs; ON arm re-executed exactly 1 stage — all upstream inputs byte-identical):**
  (1) commute-distance band distribution unchanged (TV 0.003, mean 9.17 -> 9.21 km);
  (2) per-Gemeinde work inflows vs the OBSERVED SvB-am-Arbeitsort counts: OFF mean within-Kreis TV 0.009
  (near-exact), ON 0.087 — **9x worse** (LK Gifhorn 0.21; Gifhorn Stadt observed 20,299 -> ON 12,909, -36%).
  An independent offline run of the same function on the raw XLSX predicted the per-Kreis TVs to ~1%.
- **Decision: PARK — default stays OFF; no config enables the flag.** The mechanism cannot help by
  construction: the destination attraction IS an observed per-Gemeinde marginal (GENESIS 13111-01-03-5, SvB am
  Arbeitsort), the doubly-constrained balancing reproduces it, and the tilt strictly moves the margin away from
  observed data using a proxy carrying no information about inflow totals (establishment density is
  definitionally low in large-plant towns -> systematically drains the Kreisstaedte). The structural concern
  lives on other axes: within-Gemeinde placement is already covered by the building work potentials
  (PR #15/#16); structure-dependent commute SHAPE would be WZ-sectoral friction (issue #128 "phase 2",
  deliberately deferred, overfitting risk). Code + tests stay (now runnable and documented as
  measured-and-rejected); removal was considered and rejected as unnecessary.
- **Bugs fixed on the enabling path (same PR):** (a) the ON path had NEVER been runnable — the tilt was applied
  to the raw stage schema `(commune_id, weight)` before the `weight -> employees` rename -> `KeyError:
  'employees'`; unit tests fed the helper the post-rename schema and stayed green (the CLAUDE.md
  "test the primary method" failure mode). Fixed via `build_destination_attraction` owning the rename-then-tilt
  handoff + regression tests on the true stage schema. (b) `braunschweig.data.census.employees` padded 5-digit
  LANDKREIS aggregate rows to fabricated 8-digit AGS; the codes merge dropped them and the loss accounting
  reported 26.9% "lost" SvB on the full ZGB-8 scope — above the 25% raise threshold, so **every full-region run
  would have aborted** (kreis-subset runs stayed under the threshold, hiding it). Now only 5-digit AGS whose
  padded form is a real Gemeinde in the codes table are treated as kreisfrei; aggregates are excluded and
  logged (merge loss now 0.00%). Same guard applied to the gemband `n_betriebe` reader.
- **No scientific-output change:** OFF path byte-identical (tilt) and merge-output identical (aggregates were
  already dropped, only the accounting/raise changes); the fixes alter behaviour only where the pipeline
  previously crashed.
- **Evidence:** issue #128 (measurement comment 2026-07-15); A/B artefacts on felix `~/wt-128-ab`
  (`ab128_off.log`, `ab128_on.log`, `work_od_off.p`, cache `braunschweig.gravity.model__{47c862d…,b158f62d…}`);
  contract-test finding in `analysis_suite.py` spun off as #183.

