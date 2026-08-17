# ADR-0055 — SrV 2023 aggregates use ZENSUS expansion weights; standard weights are stratum-internal

- **Date:** 2026-07-08
- **Status:** accepted (fix branch `fix/srv-zensus-weights`, follows the PR #113 data layer)
- **Context:** The first release of the committed SrV 2023 reference tables (PR #113) weighted all
  aggregation levels with `GEWICHT_HH` / `GEWICHT_P` ("fuer Standardauswertungen"). A post-merge weight
  audit (systematic debugging) established empirically that these weights are normalized to mean ~1
  WITHIN each `ST_CODE` stratum (per-stratum means 0.99-1.26) while the true expansion factor varies
  18x-70x across strata, and additionally varies per municipality within the two
  kleinstaedtisch-doerflich strata (ratio CV 0.89-1.33). Any aggregate crossing strata — the per-Kreis
  rows for GF/GS/HE/PE/WF, all totals, the age bands — therefore weighted strata by SAMPLE share
  instead of population share (e.g. Helmstedt: 66% sample share vs 36% population share for stratum 98).
- **Decision:** All SrV aggregate extractions use `GEWICHT_*_ZENSUS` ("fuer stadtuebergreifende
  Auswertungen", full expansion to Zensus 2022, per municipality) for EVERY aggregation level. All
  committed SrV tables, the blended `target2026_*` tables and all test pins were regenerated against
  independently pre-computed reference values (never pinned to the pipeline's own output).
- **Impact:** cars up to 4.7 pp (Gifhorn cars_1), bikes up to 3.0 pp, ebike-HH up to -2.6 pp (WF),
  region-total car-free 11.8% -> 13.6%; Braunschweig + Salzgitter rows invariant (single-stratum);
  blend sources shifted (economic_status BS `mid_arbitrated` -> `blend`, HE/WF/GS -> `srv_arbitrated`).
  Part of the earlier MiD-vs-SrV "divergence" for HE/WF was this weighting artifact; the Goslar
  car-ownership divergence persists.
- **Evidence:** weight audit scripts (session scratchpad `srv_weight_audit{,2}.py`,
  `srv_zensus_reference_values.py`); codebook weight definitions (SrV2023_Datenkodierung_SciUse.xlsx);
  memory `project-srv2023-braunschweig-data`.

---

