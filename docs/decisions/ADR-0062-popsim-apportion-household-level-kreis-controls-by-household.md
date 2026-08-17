# ADR-0062 — popsim: apportion household-level KREIS controls by household share, not population share (#148) (2026-07-14, PR #176 OPEN)

- **Context (issue #148, measure-first DONE):** when a Kreis is split across PopulationSim batches, each batch
  targets its share of the Kreis marginal. The share was ALWAYS the batch's population share
  (POP_TOTAL_100m_adj), applied uniformly — including to HOUSEHOLD-level controls (economic_status,
  number_of_cars, number_of_bicycles, has_ebike). Where persons-per-household varies across a Kreis's batches
  the household-level targets are mis-apportioned. Measured on the completed 100% run `popsim_work_allfeat_opt`
  (60 batches, 8 all-multi-batch Kreise): persons-per-household 1.85-2.25 across Kreise; **~5.9% of the region
  economic_status household total reallocated across the spatial batches within each Kreis** (max 2046 HH in
  one batch) — MATERIAL, not the originally-hedged "immaterial".
- **Decision:** apportion household-level KREIS controls by the batch's HOUSEHOLD share
  (`HH_TOTAL_CENSUS_COLUMN`); keep the population share for person-level controls (employment / education /
  trip_class). `folders.build_kreis_control_totals` gains `household_apportion_weights` + `household_control_names`
  (both default None -> byte-identical legacy); `mid.run_popsim_mid` computes the region-wide household total per
  resolved Kreis (`kreis_total_hh`) when household controls are active, and RAISES if the HH column is absent
  with household controls active (no silent fall-back to pop share); `stage.py` collects the household-level
  attribute-control names (`_ctl.level == "household"`).
- **Consequence / honesty note:** **scientific-output change** to the WITHIN-Kreis spatial distribution of
  household-level controls (and anything downstream, e.g. income placement #108). **Region-wide sums provably
  unchanged** (per-batch household shares partition to 1, same machinery as the pop path). The REALIZED effect
  needs a small hh-share A/B rerun of one multi-batch Kreis on felix (the measured KPI is the target
  apportionment, an upper-bound proxy).
- **Evidence:** PR #176 (open; merges origin/main cleanly after resolving the folders.py conflict with PR #175
  — combined #150 NaN-logging sum + #148 level-aware weights); TDD incl. end-to-end cross-batch sum invariant
  + raise guard; senior-reviewer subagent found no correctness defects; 1121 popsim tests green. Memory
  `project-popsim-controls-audit-fix`.

---

