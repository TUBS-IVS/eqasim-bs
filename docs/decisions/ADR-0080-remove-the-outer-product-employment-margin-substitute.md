# ADR-0080 · 2026-08-17 · Remove the outer-product substitute for the TASK-010 employment margin and park the flag (issue #252)
- **Status:** active
- **Context:** `braunschweig.ipf.use_employment_margin` gates an additional
  (Kreis × hh_size × employed) joint margin (TASK-010) whose targets come from
  `braunschweig.ipf.employment_by_hhsize_path`. Issue #252 reported that the flag
  was `true` in nine committed configs while the path was set in none, so the
  stage always took a fallback branch that synthesised the joint targets as the
  **outer product** of the existing employment and hh_size marginals. The code
  described that branch as "a pure marginal-consistency check that does not add
  information beyond what the existing IPF already enforces … useful as a smoke
  test". The issue's own diagnosis — an *uninstrumented* silent fallback — turned
  out to be inaccurate in one respect and to understate the defect in another:
  the branch did print a line, and the real problem is not that it was silent but
  that its self-description is wrong.
- **Decision:** Delete the substitute rather than instrument it. Concretely:
  1. **Raise when the flag is on and no usable cross-tab is configured.** The
     load path moved into `load_employment_by_hhsize_targets` in
     `braunschweig/ipf/model.py`, which raises with the offending config key,
     the resolved path and the remedy when the path is unset, the file is absent
     or the required columns are missing. There is no second method.
  2. **Park the flag.** `braunschweig.ipf.use_employment_margin: false` in the
     four `simple_ipf_open` run configs that actually execute the stage, with the
     reason stated inline. The key is **deleted** from the five configs on the
     popsim path (`configs/base_bs.yml` and the four popsim fixtures), where
     `braunschweig.ipf.model` is off the DAG and the key is therefore dead — the
     defect class ADR-0078 removed.
  3. **Instrument the cell-level join** that remains: the TASK-010 selector loop
     now counts how many target cells matched a `(Kreis, hh_size, employed)`
     combination present in the model and logs the rate, raising when nothing
     matches (an enabled margin appending only inert constraints). This complements
     the Kreis-level coverage check already in `_map_departement_index`.
  4. **Do not rewrite ADR-0014.** See the correction below.
- **Rationale:** A joint target set to the product of its marginals is *not*
  implied by those marginals. Adding it as a constraint block pins every joint
  cell and thereby imposes one additional assumption the data does not support:
  that employment status is independent of household size within a Kreis. The IPF
  otherwise reaches the constraint-satisfying distribution closest to the donor
  seed, which preserves the seed's employment × household-size association; the
  substitute overwrote that association with independence. Crucially all base
  margins remain satisfied under the substitute, so neither the convergence
  criterion nor the post-IPF margin-deviation check could ever reveal it —
  instrumenting the fallback, as issue #252 proposed, would have made a fabricated
  constraint visible without making it any less fabricated. Under CLAUDE.md's
  fallback rule a fallback rate of 100 % that "cannot be scientifically
  defensible" is to be raised on, not logged.
- **Evidence:** `tests/test_employment_margin_task010.py` encodes the argument as
  an executable check against the repository's own `run_ipf_iterations`. On a
  minimal one-Kreis replica (cells = hh_size {1,2} × employed, base margins =
  Kreis employed total and per-size person totals) the base margins alone
  reproduce the seed's odds ratio exactly (0.1837), while adding the
  outer-product joint forces it to exactly 1.0000 and equalises the employment
  rate across household sizes (0.6000 / 0.6000 against 0.3348 / 0.7326 without
  it); individual cell weights move by up to ±79 %, and all three base margins
  still match their targets. The seed values are an **ASSUMPTION** chosen to make
  the structural question decidable — no reference value is claimed — but the
  conclusion does not depend on them: pinning every joint cell to a product of
  marginals forces the odds ratio to 1 for any seed. The full suite is green
  apart from one pre-existing failure unrelated to this change
  (`tests/test_mid_donor.py::test_execute_on_real_mid_yields_commute_donors`,
  a missing local-only MiD raw CSV; verified to fail identically on a clean tree).
- **Consequences:** Production output is unchanged — `braunschweig.ipf.model`
  appears in the `simple_ipf_open` DAG snapshot only, in neither `production`
  nor `popsim_open`. `simple_ipf_open` runs change: the fabricated independence
  constraint is gone, so the donor seed's employment × household-size association
  survives into the synthetic population, and cached `braunschweig.ipf.*` stages
  are devalidated. Any config that sets the flag without a cross-tab now fails at
  stage start instead of running; a guard test rejects that combination before it
  can be committed.
- **Limitations (explicit):** The **magnitude** of the distortion on real
  Braunschweig data was NOT measured — only its structural existence was
  demonstrated, on a synthetic minimal example. No OFF/ON pair was executed on
  real census data and no run manifest records one, so the improvement is argued
  from the constraint algebra, not from a measured fit. The feature stays
  `unvalidated` with `assessment.status: pending`: parking it removes a
  fabricated assumption but does not add an observed one. The loader's
  `employed` column is still coerced with `.astype(bool)`, which maps any
  non-empty string to `True`; that trap only becomes reachable once a real
  cross-tab is wired and is recorded in issue #311 rather than fixed here.
- **Correction to ADR-0014 (record left intact):** ADR-0014 states "Add an
  employment margin to the IPF (`ipf.use_employment_margin`) raked to GENESIS
  SvB". Those are two different mechanisms. The margin raked to the GENESIS SvB
  register is the **unconditional** (Kreis × sex × employment-age-class × employed)
  block in `braunschweig/ipf/model.py`, which is always built and is governed by
  no flag; `use_employment_margin` gates only the additional TASK-010 joint margin
  this record parks. ADR-0014's decision — rake employment to the register rather
  than to MiD P9 — is unaffected and remains active. Per the project's
  no-invented-history rule (`docs/decisions/README.md`: the record text is
  evidence, not editable prose) ADR-0014 is not rewritten; this paragraph is the
  correction of record, as ADR-0078 did for ADR-0005.
- **Alternatives rejected:** (a) Add primary-vs-fallback rate logging and keep the
  substitute — what issue #252 asked for. It makes the constraint visible but
  leaves it in force, and a rate that would read 100 % in every committed config
  is not a diagnostic, it is a defect. (b) Keep the substitute behind an explicit
  `allow_outer_product_proxy` opt-in — more config surface for a path with no
  defensible use; the wiring smoke test it was meant to provide is what the new
  unit tests do, without touching any run config. (c) Acquire and wire the real
  Zensus 13111-06-02-4 cross-tab now — the correct end state, but it needs data
  that is not present in the repository; split out as issue #311 so the active
  distortion is removed immediately rather than waiting on a data acquisition.
- **Issue / PR:** #252 · follow-up #311
