# ADR-0061 — popsim KREIS-control universe hygiene: align per-Kreis targets to the resolved dominant Kreis (#147); complete fallback-transparency (#149/#150) (2026-07-14, PR #175 MERGED)

- **Context (issue #147):** two per-Kreis universe inconsistencies in the popsim_mid KREIS attribute-control
  path. (sub-1) The per-Kreis household/person totals the category targets partition were grouped by the RAW
  `ARS[:5]` of each 100 m cell (`stage._kac_kreis`), while the batch KREIS backbone
  (`folders.build_kreis_control_totals`) and its apportionment key on the RESOLVED dominant Kreis per 1 km
  parent (`_resolve_parent_kreis`, POP_TOTAL_100m_adj weight). For border cells reassigned to a neighbouring
  Kreis the two universes disagreed. (sub-2) The Tier-3 `kreis_table` was pre-populated with the full national
  set (~400 Kreise) and left-merged, carrying rows never read downstream. The original authors deliberately
  DEFERRED sub-1 "measure-first" (documented in `_resolve_parent_kreis`).
- **Decision:** align sub-1 now (user-approved). `stage._kac_kreis` uses `mid.resolved_kreis_per_cell`, which
  builds the identical region-wide crosswalk the backbone uses (resolve_parent_kreis=True, same weight), so the
  category targets partition the SAME Kreis universe the 100 m backbone constrains. sub-2:
  `mid.load_kreis_control_table(restrict_to_kreise=)` filters the national table to the run's Kreise at load.
- **Consequence / honesty note:** sub-1 is a **scientific-output change of ~0.1% of cells** (100% run
  ~48/43598 border cells) — their household/person target attribution moves onto their parent's dominant
  Kreis. **Region-wide per-Kreis sums are provably unchanged** (a 1 km parent is atomic to one resolved Kreis).
  sub-2 is pure hygiene, no output change (dropped rows were never read). The **realized** synthetic effect is
  not verifiable in unit tests and needs a small resolved-Kreis A/B rerun of one multi-batch Kreis on felix
  before it is treated as validated (tracked in memory + SESSION_LOG, not as a separate issue by choice).
- **Also in PR #175 (#149/#150, fallback-transparency, not output-changing):** shared helper
  `cells.sum_columns_logging_nan` wired into all four multi-column row-sum sites (make the skipna NaN->0
  suppression observable); `add_aggregated_controls` raises when ALL source columns are missing. **#163**
  (14-item fallback-transparency wave 2) was found already implemented+merged via PR #165 and verify-closed.
- **Evidence:** PR #175 (merged, origin/main 5466b74); TDD (`resolved_kreis_per_cell` border-cell + no-border
  equivalence, `restrict_to_kreise`); senior-reviewer subagent confirmed identical crosswalk params + 1 km
  atomicity + `resolved ⊆ kreise`; 1108 popsim tests green. Memory `project-popsim-controls-audit-fix`.

