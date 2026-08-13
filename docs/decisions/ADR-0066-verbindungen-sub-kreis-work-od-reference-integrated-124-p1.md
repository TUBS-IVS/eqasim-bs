# ADR-0066 — VerBindungen sub-Kreis work-OD reference integrated (#124 P1); svb_wohn production mass measured, default OFF (#132) (2026-07-16, PR #189/#190 MERGED)

- **Context:** Below the Kreis pair (BA Pendleratlas anchor) the model's work-OD had no ground
  truth, so neither the sub-Kreis destination choice nor the #132 question (gravity production mass
  = total population vs. employed residents `svb_wohn`) could be measured. The VerBindungen project
  (StBA/BMDV FuE 97.421/2019, open data) publishes 2019 commuter OD on Verkehrszellen. PR #189 added
  the download + loaders + a default-ON validation stage (checks A margin / B conditional-OD /
  C vintage-drift); PR #190 wired it into the three allfeat server configs.
- **Measured ZGB geography (live files 2026-07-15):** 44 cells over 129 Gemeinden — Braunschweig
  split into 2 stadtteil cells, Salzgitter into 3, **Wolfsburg NOT subdivided** (issue #124's
  "Stadtteil resolution for BS/WOB/SZ" was too optimistic). The reference resolves the between-cell
  axis WITHIN each Kreis, not city-internal structure. QZM ZGB-internal: 730 relations >= 10,
  510,095 commuters, intra-cell share 46.9 % (Germany-wide totals match the report exactly:
  41,030,553). DBF caps `ags_0` at 254 chars -> 34/3,189 cells nationally truncated (0 in ZGB).
- **Baseline validation of the current 100pct all-features run** (felix `~/wt/verbindungen-ab` @ main,
  read-only from `cache_bs_100pct_allfeat_popsim`; realised home-cell x work-cell assignment vs the
  2019 reference, share-based, censoring-aware):
  - home-cell worker margins (check A) vs BA `Statisch_WO`: SRMSE 0.132, **Pearson r 0.9968** — the
    popsim + home placement put the right number of workers in the right cells;
  - conditional work-OD (check B): **weighted TVD 0.137**, band EMD 0.080, censored model share only
    1.6 % (censoring does not explain the gap); **intra-cell share model 0.4694 vs reference 0.4687**
    — near-exact;
  - vintage drift (check C, 2019 QZM vs 2025 Pendleratlas Kreis pairs): **Pearson r 0.9984,
    max abs share drift 0.0076** — the sub-Kreis structure is essentially stable 2019->2025;
  - 12.95 % of workers sit outside the ZGB cells (cross-cordon out-commuters), reported not dropped.
- **#132 A/B (paired, OD-level: Gemeinde gravity + Pendleratlas IPF recomputed offline from the same
  cached inputs, only the production mass differs; stadtteil cells collapsed to their parent Gemeinde
  so the Gemeinde-path OD is compared like-for-like; svb_wohn primary 113/118 Gemeinden, 4.2 %
  Kreis-mean fallback):** weighted TVD **0.1136 (population) -> 0.1137 (svb_wohn)** = +0.0001
  (no improvement on the primary metric); band EMD -0.0044 and intra-cell share -0.0015 (a whisper
  closer to the reference). Effect is negligible.
- **Decision: #132 default stays OFF (`work_production_mass: population`); the flag stays available.**
  The Kreis-level Pendleratlas IPF is the binding anchor and swamps the production-mass refinement —
  same outcome pattern as ADR-0065 (#128) and the #129 in-commuter A/B. Flipping the default is not
  justified by the data; the code + tests stay (measured-and-parked).
- **Stage-3 calibration gate (whether to promote VerBindungen from validation reference to a
  sub-Kreis calibration anchor): NOT decided here — deferred to a follow-up issue.** Both gate
  criteria are technically met (the check-B gap of ~0.11-0.14 TVD is real, not censoring-explained;
  vintage drift is small, so a 2019 anchor is low-risk), but weighted TVD ~0.14 is already a
  reasonable doubly-constrained gravity fit and there is **no committed threshold** for "substantial
  enough to calibrate" (CLAUDE.md: no invented reference values) — so the promote/park call is a
  team judgment, proposed as an issue rather than auto-taken. If ever built, sub-Kreis OD becomes
  labelled **fit**, not validated, and independent validation moves to MiD distance distributions.
- **One real bug surfaced only by this 100pct e2e A/B (not by the unit tests):** `margin_check`
  received the reference margin as a nullable `Float64` Series (BA counts carry Dominanz NAs), and
  `np.corrcoef` crashes on a masked-backed Series under the run server's older numpy; the unit tests
  used plain `float64` fixtures and stayed green. Fixed by materialising the share vectors as plain
  `float64` before the reduction, with a `Float64`-reference regression test (the CLAUDE.md
  "test the primary method on representative input" / "e2e smoke over mocked tests" failure mode).
- **No scientific-output change from the merge:** the validation stage is read-only analysis;
  #132 default OFF path is byte-identical to the pre-change gravity (verified against the base commit
  in review).
- **Evidence:** PR #189 (reference + #132 code), PR #190 (server wiring); A/B artefacts on felix
  `~/wt/verbindungen-ab/ab_out/` (`realised_100pct/`, `od_ab/{summary_population,summary_svb_wohn,
  ab_table}.csv`); driver `scripts`-local `ab_driver.py`; run row `verbindungen-ab-2026-07-16` in
  RUNS.md; memory `project-verbindungen-reference-124-132`.

---

