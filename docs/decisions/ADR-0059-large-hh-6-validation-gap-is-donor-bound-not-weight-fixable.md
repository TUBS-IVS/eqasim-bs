# ADR-0059 — Large-HH (6+) validation gap is donor-bound, not weight-fixable; SrV rejected as a donor supplement

- **Status:** accepted 2026-07-13. Diagnostic session — no code/output change. Refines the
  standing "household composition is donor-bound" note (§ backlog, ADR-0056) with concrete numbers
  and a specific rejected option.
- **Context:** the `household_size` validation control on the newest synthesis (**kreis5 100% run**,
  2026-07-10, controls export 2026-07-12, `output_bs_100pct_allfeat_popsim_kreis5`) still shows the
  6+ class under target: **2.92% of persons vs Zensus-2022 reference 4.75% = 61.5% of reference,
  Δ -1.83 pp** — the one material outlier. The 5-person gap is now essentially closed (96.5%, was
  88.5% in the 06-30 export). Question raised: can we (a) weight the 6+ control harder, and/or
  (b) enrich the donor pool with SrV 2023 households to close it?
- **Decision — (a) importance is exhausted:** the kreis5 run already used importance profile
  `optimized_2026_06_30`, in which the 6+ control (`6_Personen_und_mehr_..._ZENSUS100m`) carries
  **importance 2000** (4× the size-1-5 controls at 500) with `max_expansion_factor: 100`, and 6+
  still hits only 61.5%. This empirically confirms the gap is **donor-bound, not weight-fixable**:
  raising importance further only makes the balancer sacrifice the well-fit controls (age×sex, HH
  total) for a target the seed's fixed person-bundles cannot express. Do NOT raise `six` further.
- **Decision — (b) SrV rejected as a donor supplement:** we DO hold SrV 2023 record-level microdata
  (`eqasim-data/data/braunschweig/srv/srv2023_raw/` — Haushalte/Personen/Wege.csv + SPSS + codebook),
  so it is technically feasible. But **SrV Braunschweig+RGB has only 63 six-plus households = 0.78%**
  of 8,106 — the SAME rarity as the MiD full-pool seed (1,661 distinct 6+ = 0.76%). Large HH are
  ~1% of the real population; every general-population survey mirrors that scarcity, so SrV adds no
  large-HH depth (~+4% distinct records at identical share). Three further costs: (1) **circularity**
  — SrV is already our per-Kreis TARGET source (ADR-0055 MiD=donor / SrV=targets); SrV-as-donor would
  fit SrV to SrV and destroy validation independence; (2) **schema harmonization** — the donor supplies
  person attributes AND trip chains, and SrV uses different variable coding (`V_ANZ_PERS`, `GEWICHT_HH_*`,
  its own Wege taxonomy) vs MiD (`H_GR`, `H_GEW`, `P_TAET`, `bildung1/2`); (3) different weight base
  (national MiD vs regional SrV). ADR-0056 also found the full national pool fits better than
  regional/per-stratum donors. SrV's genuine value stays where it is: per-Kreis targets and a local
  trip/mobility source — not the donor.
- **Candidate lever (UNVERIFIED, deferred):** the plausible mechanism is that 6+ is controlled at
  ZENSUS100m, where per-cell targets are tiny (~0.3-0.4 HH) and integerization rounds them to 0 across
  thousands of cells, "crumbling away" the rare class. Controlling 6+ at a COARSER geography
  (1km / Kreis) would give integer-friendly targets. **Not yet verified** whether PopulationSim
  integerizes at 100m regardless of control geography (which would blunt the fix) — must be checked
  before any implementation. Tracked under #99 (regional-correct popsim); no issue opened yet
  (verify-first).
- **Evidence:** kreis5 controls `output_bs_100pct_allfeat_popsim_kreis5/analysis/population_validation/`
  (on felix); seed 6+ count from `popsim_work_allfeat_opt/batch_000/data/seed_households.csv`; SrV 6+
  count from `SrV2023_Haushalte.csv` (`V_ANZ_PERS`); importance in the run's `controls.csv`;
  `control_spec.py` IMPORTANCE_PROFILES; ADR-0055 (SrV=targets), ADR-0056 (full-pool > per-stratum);
  memory `project-large-hh-6plus-donor-bound`.

---

