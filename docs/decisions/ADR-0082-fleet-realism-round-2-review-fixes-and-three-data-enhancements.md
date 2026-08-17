# ADR-0082 · 2026-07-02 · Fleet realism round 2 — whole-model review fixes + three data enhancements (originally numbered ADR-0051 on `feature/fleet-quality-and-data`; renumbered at merge -- ADR-0051 on `main` is a different, unrelated record)

- **Status:** active — implemented on `feature/fleet-quality-and-data` (Plan 3, tasks A1–A4, B1–B6).
- **Renumbering (resolved at merge, 2026-08-17):** this record was drafted as
  `ADR-0051` and its companion as `ADR-0050` on `feature/fleet-quality-and-data`.
  On `main`, `ADR-0050` is an unrelated record (TAZ per-RS7 gravity friction) and
  `ADR-0051` was listed as *reserved* for this fleet record in
  `docs/decisions/README.md`. At merge the pair was renumbered to the next free
  numbers, `ADR-0081` (data-regionalization ceiling) and `ADR-0082` (this
  record); both are kept, they are unrelated decisions. Every cross-reference in
  the body below was updated accordingly; no other body text was changed.
- **Context:** After the fleet regionalization (ADR-0081) landed, a data-utilization audit + a
  fresh-eyes whole-model logic review (opus) surfaced three real defects and three unused data signals.
- **Decision (review fixes):**
  1. *Model-fuel weight scale (review Finding 1):* untracked powertrains (gas/hydrogen/other) in the
     per-model fuel-weight mask are weighted by the MEAN of the tracked shares, not a hardcoded 1.0
     (which had boosted a feasible untracked powertrain by ~1/tracked_share after renormalisation).
  2. *Validator segment target (Finding 2):* the realised-margin validator now compares `segment`
     against the EFFECTIVE per-car pmf (status-conditioned + sonstige-redistributed), not the raw KBA
     marginal — the raw target flagged `segment` on every run at scale (cry-wolf).
  3. *Gemeinde-EV tilt vintage (Finding 3):* when the 2026 per-Gemeinde EV source is active the tilt
     denominator is derived from the SAME 2026 file (private_total-weighted per-Kreis mean), so the
     tilt is a pure within-Kreis RELATIVE factor. The per-Kreis EV LEVEL stays anchored to the
     46251-02 (2025) powertrain marginal; a 2026-numerator / 2025-denominator mismatch had injected a
     fleet-wide EV level shift the per-Kreis rake then preserved.
  4. *Minors:* symmetric electric-rake over-shoot warning; extractor NaN guards on alt-drive counts;
     precision docstrings (F6/F8/F11/F12/F4).
- **Decision (data enhancements):**
  5. *EV-income tilt (item 1):* a MiD-2023 `P(powertrain|economic status)` table
     (`mid2023_antrieb_by_status.csv`, A_ANTRIEB×oek_status, A_GEW-weighted) drives a within-Kreis
     redistribution of BEV/PHEV mass toward higher-income households
     (`f = clip(P(pt|status)/P(pt|all), 0.2, 5)`), applied AFTER the unmasked rake-target capture so
     the per-Kreis electric AGGREGATE is preserved (thin cells n<30 → factor 1). This closes the
     largest remaining realism gap (EV ownership was purely spatial, income-blind).
  6. *All-Kreise 46251 (item 2):* the per-Kreis fuel+euro tables now cover ALL German Kreise (not just
     the 8 ZGB), so cross-cordon in-commuters get their REAL home-Kreis mix instead of the national
     fallback. Loader validation loosened to `_require_zgb_subset` (ZGB required, extras allowed); the
     per-(kreis,powertrain) euro joint is built lazily (`LazyKreisEuroJoint`) to keep the ~400-Kreis
     build fast.
  7. *Euro-6 substage (item 3):* the discarded Euro-6d / 6d-temp breakdown (per-Kreis 46251-03 +
     national FZ 27.4) is now extracted and a conditional substage (`euro6ab`/`euro6dtemp`/`euro6d`) is
     drawn for combustion euro6 cars, mapped to distinct HBEFA emission concepts. The age/euro joint's
     euro axis stays `euro6` (headline); the substage is a post-draw conditional refinement. Emissions
     relevance is contingent on running the HBEFA emissions contrib.
  8. *RegioStaR7 EV cross-check (item, Kleinkram):* `kba_ev_regiostar7.csv` +
     `crosscheck_ev_by_regiostar7` — a LOGGING-ONLY per-RS7 realised-vs-KBA-national EV cross-check
     that NEVER flags the run (national reference ≠ regional target, per the no-invented-reference rule).
- **Accepted quirks (documented, not fixed):** the routing `default_car` rows keep the legacy
  `technology="Gazole"`/`euro=6` vocabulary (analyses exclude routing cars via `fleet_vehicles()`, so
  KPIs are unaffected — changing the values would break byte-comparability for no model gain); after
  the model-fuel weighting only the ELECTRIC mass is re-raked per Kreis, so the per-Kreis combustion
  split may drift from 46251-02 (spot-check in the run summary); the income-age tilt shifts the euro
  marginal slightly via the age–euro coupling (physically plausible: richer → newer → newer euro); the
  0.2 lower clip on the Gemeinde/grid tilts is a deliberate anti-explosion floor (no-EV pockets are not
  represented at the extreme); the 46251-03 non-diesel euro proxy (`all − diesel`) is mildly
  contaminated by electrified vehicles' euro grouping.
- **Deliberately UNUSED signals (recorded for future work, NOT model inputs now):** the per-Gemeinde
  EV QUARTERLY TREND 2023→2026 (the current-state model uses only the latest quarter; the trend is
  future-scenario / prognosis material); the further MiD-Autos validation columns `A_STELL` (parking),
  `A_LADEN` (home charging), `A_JAHRESFL` (annual mileage), `A_HALTER` (private/company) — validation
  gold for a later stage, not inputs to this generative chain.
- **Consequences:** every new data-driven feature is default-ON with a graceful, byte-identical
  fallback when its (server-generated) CSV is absent; no NA/missing markers anywhere in the emitted
  fleet; the legacy `consistency_v2=False` path stays verbatim; real-data behaviour (per-Kreis euro
  variation, EV-income gradient, in-commuter home-Kreis mix, euro6 substage split) is verified on the
  server smoke before merge.
- **Final whole-branch review addendum (2026-07-03, opus):** one further robustness fix landed after
  the review. The Task B3 all-Kreise marginal lets a cross-cordon in-commuter carry any home Kreis; a
  Kreis with `insg>0` but every fuel component suppressed/zero produced a NaN column target ->
  NaN P(powertrain|segment) -> `rng.choice(p=nan)` crash at draw time. The per-Kreis rake was
  extracted into `PowertrainModel._rake_per_kreis_powertrain` (byte-identical for healthy Kreise) with
  a degenerate-Kreis guard: NaN/inf components -> 0, and if no finite positive mass remains the Kreis
  falls back to the national `P(powertrain|segment)` with a counted+logged degenerate rate
  (no-silent-fallback). Findings #3 (empty-`df_cars` ZeroDivisionError, unreachable in production) and
  #4 (per-draw euro-joint Kreis-miss not per-draw counted; build-time coverage log already covers it)
  were assessed and deliberately left. Fix commit `99c660d` (9 new tests).
- **OFF-path baseline sign-off (2026-07-03, user):** the OFF (`consistency_v2=False`) path is NOT
  byte-identical to the PRE-branch baseline because the shared segment-IPF zero-row seed improvement
  (seed the NDS status marginal instead of uniform, commit `9eb2050`) legitimately shifts segment
  draws in BOTH paths. The legacy loop CODE is verbatim and no new flagged feature leaks into it
  (final-review-confirmed). The user signed off on keeping the improvement active in both paths rather
  than gating it; the two OFF-path golden fixtures (`test_off_path_byte_identical`,
  `test_age_income_off_unchanged`) are therefore regenerated from the canonical server run.
- **Evidence:** branch `feature/fleet-quality-and-data` (Plan 3 commits, SDD ledger
  `.superpowers/sdd/progress.md`); `scripts/build_mid_antrieb_by_status.py`,
  `scripts/extract_kba_fleet.py`; `braunschweig/synthesis/vehicles/{fleet_sampling_de,fleet_validation,hbefa}.py`;
  `braunschweig/data/kba/fleet_tables.py`; the household-weighted EV variance decomposition
  (`population_explorer.gpkg`, 558,281 households). Follows ADR-0081.