# ADR-0084 · 2026-08-17 · The Euro-6 substage lives in its own column, not inside euro_class (issue #277)

- **Status:** active
- **Context:** ADR-0082 item 7 introduced a conditional Euro-6 substage draw
  (`euro6ab` / `euro6dtemp` / `euro6d`) for combustion Euro-6 cars, so the fleet
  can carry the distinct HBEFA emission concepts. As implemented on
  `feature/fleet-quality-and-data`, the draw **overwrote** `euro_class` with the
  substage label, and the realised-margin validator mirrored that split into its
  expected `euro_class` marginal. The defect only became observable when the
  derived tables were generated for the first time during the #277 merge (until
  then every substage code path took its absent-data fallback, so nothing in the
  suite ever exercised it). With the data present, three independent contracts
  broke at once:
  1. `tests/test_electric_euro.py` — the branch's OWN new test — reported 357
     combustion vehicles with an "invalid euro_class", because the column had left
     the canonical `EURO_CLASS_LABELS` vocabulary.
  2. `tests/test_fleet_sampling_de.py::test_euro_distribution_for_petrol_matches_fz_27_4`
     measured a realised `euro6` share of **0.0** against the FZ 27.4 reference of
     0.44: the entire Euro-6 mass had been renamed, so the one dimension that ties
     the fleet to a committed KBA reference could no longer be compared to it.
  3. `tests/test_run_fleet_stage.py` found `{'euro6ab', 'euro6dtemp'}` in the
     vehicles frame's canonical-vocabulary check.
- **Decision:** `euro_class` NEVER leaves the canonical KBA vocabulary. The
  substage is emitted as its own column and consumed where it is actually needed:
  1. **New spec column `euro6_substage`**, emitted in the `consistency_v2` path
     alongside the existing provenance columns (`brand_source`,
     `powertrain_feasibility`); the legacy path keeps its exact pre-existing
     schema. Values: the three substage labels, or
     `fleet_tables.EURO6_SUBSTAGE_NOT_APPLICABLE` (`"not_applicable"`) — a REAL
     category, so the no-NA guarantee (ADR-0081 item A4) holds without null
     handling.
  2. **The HBEFA emission concept is derived from the substage when one was
     drawn**, from `euro_class` otherwise (`_finalize_spec` picks
     `euro_for_hbefa`). The substage therefore still reaches the vehicle type id
     and `hbefa_emission` — nothing is lost for emissions modelling.
  3. **The validator gains a separate `euro6_substage` dimension** instead of
     folding substages into the euro dimension. Its expected distribution mirrors
     the draw (per-Kreis pmf → national fallback → not-applicable), so a broken
     substage draw is still caught, while `euro_class` is once again compared
     headline-to-headline against FZ 27.4 / 46251-03.
- **Rationale:** the substage refines an emission concept; it does not redefine a
  registration class. Overwriting `euro_class` conflated the two and silently cost
  the project its only reference-anchored check on the Euro dimension — a
  realised-vs-reference deviation of 68 pp that the wide validation band did not
  flag. Under the project's traceability rule an output column whose vocabulary no
  longer matches its reference table cannot be validated at all, and under the
  no-silent-fallback rule a validator that compares two different vocabularies is
  worse than none. Keeping both columns is strictly more informative than either
  single-column variant: headline class AND substage are available, and each is
  compared against the source that actually defines it.
- **Consequences:** `df_spec` in the v2 path gains one column; consumers reading
  `euro_class` (analyses, `fleet_filter`, the MATSim writer's `euro` attribute)
  keep working unchanged and once again see the canonical vocabulary. The
  validation summary gains a `euro6_substage` dimension. The legacy
  (`consistency_v2=False`) frame is untouched. Cars that reach no usable substage
  pmf are visible as `not_applicable` rather than being indistinguishable from
  plain Euro-6 cars.
- **Correction (2026-08-17, same day):** the first implementation of decision 3 had a
  defect of its own. Pure-electric cars leave the expected-marginal loop early (their
  euro mass collapses to the `electric` category), and that `continue` also skipped the
  substage accumulator — so the expected `euro6_substage` distribution summed to
  `1 − pure-electric share` and `not_applicable` was understated by ~4 pp. The
  validator dutifully reported `euro6_substage: max dev 3.96pp (band 1.29pp) -> DRIFT`
  on a draw that was correct. Fixed by adding the pure-electric mass to the
  not-applicable bucket before the `continue`; the deviation dropped to 0.32 pp and
  `any_flagged` returned to `False`. A false alarm is as damaging as a missed one — it
  trains readers to ignore the flag — so the invariant "every expected marginal sums to
  1.0" is now a test (`test_every_expected_dimension_is_a_distribution`).
- **Evidence:** `tests/test_fleet_euro6_substage.py` (24 tests, all green):
  `test_substage_labels_actually_appear` now asserts both directions — every
  combustion Euro-6 car receives a real substage where data exists, and every
  other car keeps `not_applicable`; `test_low_euro_classes_still_present_and_valid`
  pins `euro_class ⊆ EURO_CLASS_LABELS`; `test_diesel_euro6_substage_reflects_kreis_composition`
  compares the substage composition WITHIN the Euro-6 cars of two contrast Kreise;
  `test_validator_not_flagged_for_euro_class` passes without the 68 pp deviation.
  `tests/test_electric_euro.py` and `tests/test_run_fleet_stage.py` pass unchanged.
- **Alternatives rejected:** (a) Keep the overwrite and extend the canonical
  vocabulary everywhere (validator, vocabulary checks, analyses, dashboards). It
  spreads a modelling detail across every downstream consumer and still leaves the
  Euro dimension incomparable to its reference, which is the actual loss.
  (b) Keep the overwrite and widen the validator's tolerance band — that hides the
  deviation instead of resolving it, and the project rule is explicit that a
  fallback/deviation which "cannot be scientifically defensible" is to be raised
  on, not tolerated. (c) Emit the substage only inside `hbefa_emission` and keep no
  column — the substage would then be recoverable only by string-parsing an HBEFA
  concept, and the validator could not check it.
- **Issue / PR:** #277
