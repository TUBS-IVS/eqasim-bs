# ADR-0053 — Validate the household_size control on a PERSON basis, not a household count (issue #97 fix)

- **Decision:** the population-validation `household_size` control is registered person-weighted.
  `bucket_household_control` gains an optional `weight_column`; `household_size` passes
  `weight_column="household_size"`, so the realized `synthetic_count` is the SUM of household sizes per
  bin (persons living in a household of that size class) instead of a household count. Default
  `weight_column=None` keeps `cars_per_hh` / `bicycles_per_hh` byte-identical. The
  `households_type.load_household_size_by_commune` docstrings/log were corrected (weight = persons).
- **Why:** Zensus 2022 table 1000A-2081 reports PERSONS in private households by size class, not
  household counts — pinned by the committed test
  `tests/test_hh_size_margin.py::TestHouseholdTypeLoader.test_zgb_persons_match_zensus_reference`
  (ZGB total ~1.135M persons, not ~0.56M households), and `braunschweig/ipf/prepare.py` consumes it
  "in persons". The control's target loader is therefore a person share, while the realized side
  counted households — comparing household-shares against person-shares produced a spurious deviation
  (region 1-person: household basis 43.5% vs person target 21.6%). The apples-to-apples fix is to make
  the realized side persons too (the unconditional reason; the IPF-margin argument is secondary — the
  validated 100% run is `popsim_mid`, and the simple-IPF size margin is default-off).
- **Consequence:** the `household_size` control values in every population-validation report change
  (person basis). On `output_bs_100pct_allfeat_popsim` a felix re-validation moved household_size from
  **7.7pp/"needs improvement" to 1.44pp/"good"** (SRMSE 2.07→0.18); classes 1-4 fit <1.2pp, exposing
  the true residual = 5/6+ donor-bound underrepresentation (a real modelling limitation, #99 territory).
  All OTHER controls stayed byte-identical (diff), confirming the change is isolated. Status-deck QA
  figures refreshed (#104). No synthesis output changed — the IPF/synthesis was always correct.
- **Evidence:** PR **#103** (merged `141284e`), TDD tests in `tests/test_population_controls.py`; the
  felix re-validation diff (only household_size rows changed); follow-ups #105 (PR #106, docstring) +
  #104 (PR #107, deck). Memory `project-status-presentation`, `feedback-felix-isolated-worktree-rerun`,
  `feedback-no-invented-reference-values`.

---

