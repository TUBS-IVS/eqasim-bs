# ADR-0068 — Inner VerBindungen calibration anchor: built, measured, default ON by HUMAN OVERRIDE of the pre-registered gate (#193) (2026-07-17)

- **Context:** #193 built a flag-gated inner calibration level that transfers the VerBindungen 2019
  within-Kreis-pair ROW-CONDITIONAL destination shares into the calibrated work OD (comparison-zone
  level, 41 zones; Kreis-pair block totals conserved to 1e-9, asserted; censoring rule A; every
  fallback counted/logged). The plan's pre-registered decision rule v1 gated the default flip on a
  held-out CV improvement; the final whole-branch review PROVED that criterion structurally inert
  for this in-sample anchor (the anchor never touches held-out flows; pinned by
  `test_heldout_cv_is_inert_by_construction`). Rule v2 (amended 2026-07-17 BEFORE any measurement
  run) replaced it on the two provably discriminating axes: (i') AO-margin corroboration beyond
  measured fold noise AND (ii) no P13-by-RS7 EMD regression beyond per-class measured fold noise;
  P38.2-vs-MiD directional only; the CV retained as a harness-leak detector.
- **Measured (100pct cache, 2026-07-17, seeds 20260716 + 42 -> gate-identical, seed-stable):**
  `default_flip_supported = False`. Fit axis (LABELLED FIT) weighted TVD 0.1136 -> 0.0809; leak
  check PASS (per-fold gap exactly 0.0); (i') AO srmse 0.1300 -> 0.1316 = NEUTRAL within fold noise
  (~0.003), so the demanded positive corroboration failed; (ii) P13 EMD improved in 5/6 RS7 classes
  (75: .188->.148, 76: .083->.065, 77: .160->.148, 73: .141->.124), class 72 regressed
  .1724 -> .1760 beyond its (very tight, 0.0003-0.0006) fold noise; P38.2 vs MiD improved in 6/9
  regions incl. the 03ZGB aggregate (.2287 -> .2245). Coverage default measured: 30 = 3x censoring
  bound keeps 205/239 rows (85.8%) and 98.2% of anchorable mass.
- **Class-72 diagnosis (`scripts/diagnose_anchor_p13.py`, reproduces the verdict EMDs exactly):**
  the shift is a SMALL SYSTEMATIC SHORTENING spread over all 8 (BS-origin, dest-Kreis) blocks
  (mean-km shifts -1.7 to -4.3; leave-one-in contributions each <= 0.0006): the 2019 QZM observes
  BS residents working in NEARER zones within each dest Kreis than gravity predicts. Cross-checked
  against BOTH reference flavours (user question 2026-07-17): vs the NATIONAL MiD RS7-72 class AND
  vs the REGIONAL per-Kreis P38.2 tables from the MiD 2023 Braunschweig report -- the three cities
  also worsen slightly against their own regional references (03101 +0.0019, 03102 +0.0045,
  03103 +0.0029) while ALL FIVE Landkreise improve (up to -0.0263) and 03ZGB improves; the
  city-side signals sit inside the thin-n directional range this project itself assigns to the
  per-Kreis tables. KNOWN LIMITATION of the distance axes as ABSOLUTE measures (user-identified):
  the holdout compares the INTERNAL ZGB-to-ZGB OD only (the CLI runs before
  `_append_outbound_flows`), while the MiD references describe ALL commutes of residents incl.
  out-of-region destinations (~13% cross-cordon per the 2019 QZM), which land disproportionately
  in the 30km+ bands -- so the pre-existing mid-band gap (model 0.109 vs target 0.191 at 30-50km)
  is to a substantial degree a STUDY-AREA-TRUNCATION artifact, not purely model misfit
  (scale-plausible, not decomposed). Both variants share the same truncation, so the A/B DELTAS
  remain internally consistent; the axes are used as deltas only. The distance axis itself is a
  detour-scaled Euclidean proxy, identical for both variants.
- **Decision (HUMAN OVERRIDE, explicitly NOT "gate passed"):** default ON
  (`braunschweig.gravity.verbindungen_anchor_enabled = True` in both declaring stages). Rationale:
  evidence judged net-positive (5/6 P13 classes + P38.2 ZGB improve; AO neutral, not worse; the one
  dissent is tiny, mechanically understood, and points from a national class average toward locally
  observed 2019 structure); gate v2 judged too strict in hindsight (it demanded positive
  corroboration on an axis that can legitimately be neutral, and per-class fold noise is an
  ultra-tight floor for the dominant city class). The pre-registered verdict, both seeds, and this
  override are recorded verbatim -- the gate was NOT bent post-hoc; it was overridden transparently.
- **Consequences:** scientific outputs CHANGE for every config that does not set the flag False
  (work OD destination structure within Kreis pairs; downstream location choice). With the anchor
  ON, VerBindungen check B is stamped `reference_role=fit` (automatic in the stage; REQUIRED
  `--reference-role` on the cache runner); independent validation = MiD distance axes. Pipelines
  now need the verbindungen raw data unless the flag is set False (local raw-data gap: set False
  locally or restore the drop). Existing synpp caches re-execute gravity + downstream on next run.
  Artefacts: server `~/wt/verbindungen-anchor/{holdout_out_seed20260716,holdout_out_seed42,diag_p13_72}/`.
  Follow-up: numbering note -- ADR-0067 (TAZ) lives on main (merged 2026-07-17); this ADR appends as 0068.

