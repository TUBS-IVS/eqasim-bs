# ADR-0054 — Placement-based income geography: economic_status × Kreis control (MiD H4), retiring the post-hoc income overwrite (design; Phase 0/1 built, gated)

- **Status:** accepted (design + Phase-0/1 reference data & gate diagnostic built on branch
  `worktree-income-placement-refdata-gate`, pushed to the fork as backup, **not merged**); the model
  change (L1/L2) is **gated** on a server Phase-0 measurement — not yet on `main`.
- **Decision:** the spatial income geography of `popsim_mid` must emerge from WHICH real MiD donor
  households PopulationSim places where — an `economic_status` × Kreis control (target = **MiD H4
  status-by-Kreis**) with a within-(Kreis,status) reconciliation of the income LEVEL to INKAR via the
  real class-internal donor income spread — and the post-hoc `income_kreis_control` EUR overwrite is
  bypassed (flag `placement_income`, default-ON, OFF byte-identical). Scope **deliberately excludes**
  SAE/Fay-Herriot/Bayes-fusion/net-wealth/FDZ: a direct per-Kreis target exists (H4) and the population
  is donor-based, so Raking/IPF suffices. A sub-Kreis wealth surface (LSN income-tax + BORIS
  Bodenrichtwerte + Zensus Wohnfläche + SGB-II, dasymetric, mean-preserving), validated against KBA
  EV/Gemeinde, revisits the rejected ADR-0045 with a Gemeinde anchor + a validation path.
- **Why:** measured on `output_bs_100pct_allfeat_popsim` — the income NUMBER already tracks INKAR
  (Spearman 1.00 per-capita) but is INERT: `economic_status` composition is nearly flat across Kreise
  (CV 0.033), and car ownership follows tenure/household-size (ρ 0.98 / 0.91), not income (ρ 0.14) — so
  the post-hoc overwrite carries no coherent household bundle. The MiD regional study gives a direct
  per-Kreis status target the synthetic does not reproduce (Salzgitter synthetic `hoch 33 / very_low 12`
  vs H4 `hoch 42 / very_low 5`). User requirement (2026-07-04): placement-based, not post-hoc ("a scaled
  number afterwards is useless"). Also established this session: the trip-purpose / mobility "gaps"
  (work +21pp, leisure −20pp, mobility −8pp) are **metric artifacts** (eqasim `work`=arbeit+dienstlich
  vs W1 arbeit; `freizeit` folds W_ZWECK-10 into `other`; unweighted synthetic vs P_GEW-weighted P36_1),
  not synthesis errors — the twin reproduces the MiD purpose mix to ~3pp on a consistent taxonomy.
- **Consequences:** Phase 0/1 built + tested (H4 extraction+CSV, `status_by_kreis` loader, Phase-0
  `placement_income_gate` diagnostic; 11 tests) and pushed as backup. Issues **#108** (hub) / **#109**
  (Phase 2 L1/L2) / **#110** (Phase 3 L3); PROJECT_BACKLOG 3.1 now points to #108. The model change is
  NOT yet built — gated on the server Phase-0 gate (with the overwrite off, does the existing placement
  already reproduce per-Kreis income + status, and is the donor pool sufficient?).
- **Evidence:** branch `worktree-income-placement-refdata-gate` @ `2d8e8aa` (origin fork, no PR); spec
  `docs/superpowers/specs/2026-07-04-income-weighted-household-placement-design.md`; plan
  `docs/superpowers/plans/2026-07-04-income-placement-reference-data-and-gate.md`; diagnostics
  `scratchpad/{phase0_income_geo,mobility_by_age,purpose_decomp}.py`; MiD H4 (infas 7555 PDF, page 20);
  issues #108/#109/#110; memory `project-income-placement-control`, `feedback-validate-metric-apples-to-apples`.

