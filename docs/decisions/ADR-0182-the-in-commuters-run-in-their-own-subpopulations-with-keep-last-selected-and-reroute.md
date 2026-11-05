# ADR-0182 · 2026-11-05 · The in-commuters run in their own subpopulations with KeepLastSelected and ReRoute

- **Status:** accepted (implemented on branch `fix/i566-incommuter-subpopulation`; unmeasured: no run has used it
  yet, see the status line at the end)
- **Numbering:** 0182 checked free across all local and remote branches on 2026-11-05 (0179 to 0181 are taken on
  other branches).
- **Issue:** #566. **Refines:** the fixed-mode policy of ADR-0029 ("fixed mode, ReRoute allowed"); its intent stands,
  its selector and its wiring change.
- **Owner decisions (2026-11-05):** the student in-commuters get the same policy as the SvB in-commuters; the weights
  are KeepLastSelected 0.9 and ReRoute 0.1, the ReRoute share of the original policy.

## Context

- The synthesis puts the SvB in-commuters into the subpopulation `incommuter` (`braunschweig.synthesis.incommuters`)
  and the student in-commuters into `student_incommuter` (`braunschweig.synthesis.student_incommuters`). Their mode
  comes from the Mikrozensus and the pt timetable check (ADR-0029, ADR-0173) and is meant to stay fixed in MATSim.
- In the 100 % run `prod100_20261031` no in-commuter carried a `subpopulation` attribute in the prepared population
  (measurement and file in issue #566; model output). Three defects, found on code `origin/main` 7cdf98de:
  1. `braunschweig.matsim.scenario.population` merges the in-commuter persons with `concat_frame`, which aligns them
     onto the resident columns; the residents have no `subpopulation` column, so the label is dropped there.
  2. The vendored writer `matsim.scenario.population` has no `subpopulation` field, so it would not write the label
     even if it arrived.
  3. `braunschweig.matsim.simulation.cordon_subpopulation.add_incommuter_fixed_mode_strategy` (ChangeExpBeta 0.9 +
     ReRoute 0.1 for `incommuter`) has no production caller: no config ever carried the block. Wired as it was, it
     would have aborted the run: DMC's `DiscreteModeChoiceConfigurator.checkModeChoiceInTheLoop`
     (discrete_mode_choice 2026.0-2026w12, read from the bytecode) throws when any enabled strategy, whatever its
     subpopulation, is not KeepLastSelected, DiscreteModeChoice or ReRoute. The freight injection
     (`org.eqasim.braunschweig.scenario.RunInjectFreight`) uses KeepLastSelected + ReRoute for the same reason.
- So the in-commuters ran in the default subpopulation, with the residents' in-loop discrete mode choice: their tours
  touching an `outside` activity stayed frozen (`OutsideFilter`), tours inside the region could change mode, and the
  plan check and the stuck-agent analysis counted them as residents.

## Decision

1. The population wrapper gives the resident persons a `subpopulation` column of missing values before the merge, so
   the in-commuters' labels survive it, and raises when an injected in-commuter has no label after the merge.
2. The vendored writer writes the optional column `subpopulation` as the person attribute `subpopulation`
   (java.lang.String) where it is set, no attribute where it is missing (the residents' default subpopulation), and
   raises on a label that is no non-empty string. It logs the persons per subpopulation. Without the column the plans
   are byte-identical.
3. With `cordon_enabled`, `braunschweig.matsim.simulation.prepare` writes, into the final config after the cut and
   the freight injection, one strategy pair per in-commuter subpopulation: KeepLastSelected `1 - w` and ReRoute `w`,
   `w` = `cordon_incommuter_reroute_weight` (default 0.1, validated in (0, 1) at configure time). The in-commuters
   keep their synthesised mode and adapt their routes; they no longer take part in the in-loop mode choice.
4. The student in-commuters get the same pair (owner decision).

## Rejected options

- **Keep ChangeExpBeta as the selector.** Aborts every run at startup (context, defect 3).
- **Fix `concat_frame` itself to keep in-commuter-only columns.** The same helper merges the households and
  vehicles frames; changing it would devalidate those stages and widen their frames for no consumer. The column is
  added where it is used (the population wrapper).
- **A strategy block for `incommuter` only.** A student in-commuter with a label but no block would have no strategy
  at all; without a label it would stay in the residents' mode choice, unlike the SvB in-commuters with the same kind
  of synthesised mode.

## Consequences

- Model output changes: the in-commuters' MATSim replanning changes (no in-loop mode choice), so their modes and the
  run's mode shares can move. The plan check (`plan_check.json`) and the stuck-agent table report `incommuter` and
  `student_incommuter` apart from the residents; the plan check's frozen-resident threshold no longer sees the
  in-commuters' plans.
- Cached stages: `matsim.scenario.population` and `matsim.simulation.prepare` recompute, and everything after them;
  the synthesis stages stay cached.
- Not changed here: the standalone initial mode choice (ADR-0178) runs eqasim's `RunStandaloneModeChoice` on every
  person, without a subpopulation filter, so it can still re-choose an in-commuter's tour that touches no `outside`
  activity before iteration 0. Excluding the in-commuters there needs a change in eqasim-java-bs (follow-up).

## Status

Implemented on branch `fix/i566-incommuter-subpopulation`; tests in `tests/test_incommuter_subpopulation.py`. No run
has used it yet; the run manifest that shows the plan check and the stuck-agent table with the in-commuter groups
apart from the residents is the open item of issue #566.
