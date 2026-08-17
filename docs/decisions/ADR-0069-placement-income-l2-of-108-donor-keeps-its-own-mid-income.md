# ADR-0069 — placement_income (L2 of #108): donor keeps its own MiD income, per-Kreis INKAR relativity approached by signature-preserving donor reallocation; default ON, redraw+tilt overridden (2026-07-18)

- **Status:** accepted 2026-07-18. New feature flag `braunschweig.population.popsim.placement_income`
  default ON (project convention); OFF byte-identical to the prior path. Branch
  `worktree-placement-income-l2` (off `origin/main` @ `1e907c8`), 12 commits, PR pending.
- **Context:** L1 (#109, PR #112) made the economic-STATUS geography placement-based, but the income
  NUMBER was still produced post-hoc by `income_kreis_control` (a fresh per-Kreis draw calibrated to
  INKAR). That draw hits the per-Kreis mean but breaks household coherence: the income a household
  carries is no longer ITS donor's income, so income no longer tracks the same household's real MiD
  car ownership / diary. Consumer audit (2026-07-17): the EUR value is behaviourally consumed by
  exactly one channel — MATSim mode choice (`BraunschweigCar/PtUtilityEstimator`,
  `(income/ref)^lambdaCostIncome`, lambda IDF-transferred, provisional); the vehicle fleet consumes
  `economic_status` (not the EUR); everything else is descriptive.
- **Decision:** when the flag is ON, each synthetic household keeps its OWN MiD income (a seeded
  within-own-bracket draw), and the per-Kreis INKAR relativity is APPROACHED by permuting which real
  donors sit in which Kreis — strictly inside exact control-signature groups (donors with identical
  contribution to every active control are interchangeable), after the PopulationSim merge and before
  build_persons. ON overrides both `income_kreis_control` (redraw skipped) AND `income_spatial_tilt`
  (skipped — it would rescale the own income); both overrides are logged (no silent precedence). A
  `controls_source != "catalog"` + placement-ON combination fail-fasts (signatures are catalog-derived).
- **Why (measured — 2-Kreis OFF/ON gate, 03102+03103, 1%, report `cache_gate_l2_on/gate_placement_income_report.md`):**
  (1) INVARIANTS hold exactly on real output — economic_status×Kreis, number_of_cars×Kreis,
  economic_status×CELL, HH-count×CELL, age×sex_raw×CELL, age×CELL, and per-donor clone counts all
  max|Δ|=0 OFF vs ON. (2) COHERENCE income↔number_of_cars within (Kreis, economic_status) rises from
  Spearman **0.174 (redraw) to 0.364 (placement), Δ+0.19** — the designed benefit. (3) ATTAINMENT is an
  HONEST TRADE: the redraw hits the per-Kreis INKAR mean near-exactly (+0.8%/+0.5%); placement only
  approaches it (03102 +4.7%, 03103 −3.1%, both correct direction), because the continuous λ solve does
  not converge (`converged=False`) and 52.1% of slots sit in singleton signature groups with no
  reallocation freedom. `converged` refers ONLY to the λ solve, never phrased as "calibrated to INKAR"
  (convergence ≠ validation).
- **Consequence / limits:** placement buys household coherence (and a coherent mode-choice cost
  sensitivity) at the cost of exact per-Kreis-mean fit. The no-freedom share (here 52%) bounds the
  achievable movement and is reported per run (diag CSV + a >90% WARNING). One harmless non-determinism:
  reallocation reorders persons, perturbing the SEEDED binary-sex imputation of the ~977 diverse/no-answer
  (HP_SEX 3/9) persons — not a control (100m sex controls count HP_SEX==1/2 only; age×sex_raw is Δ0).
- **Out of scope (separate):** the B' clone-count-reallocation escalation, signature relaxation, a local
  λ/reference re-estimation (Task B1), and #110 (sub-Kreis wealth surface). The payoff at the sub-Kreis
  (Gemeinde) level is measured next by G1/G2 (LSN Z9170111), which double as the #110 gate.
- **Operational notes (local popsim_mid gate runs):** the default-ON per-Kreis attribute controls emit a
  KREIS geography, so a KREIS-enabled PopulationSim settings file is required (the local popsimprep
  `settings.yaml` is a stale 4-level file; the server `settings_tier3_mef100_intseed_numba.yaml` was
  mirrored). Full-donor-pool batches need ~25–30 GB each — on a 68 GB/28 GB-free box, `max_cells=1000`
  + `num_workers=1` fit; `max_cells=3000` OOM'd. The expensive batch solve is independent of
  `placement_income`, so OFF's batch outputs were copied into the ON work_dir (all 7 batches skipped) —
  the solve ran once for both legs.
- **Evidence:** spec `docs/superpowers/specs/2026-07-17-placement-income-l2-design.md`; plan
  `docs/superpowers/plans/2026-07-17-placement-income-l2.md`; `braunschweig/popsim/placement_income.py`;
  `braunschweig/analysis/population_validation/placement_income_gate.py`; gate report above; ADR-0069
  extends the #108 design after ADR (L1) status control; memory `project-income-placement-control`,
  `project-income-spatial-tilt`.

---

