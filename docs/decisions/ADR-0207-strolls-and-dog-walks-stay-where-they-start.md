# ADR-0207 · 2026-11-25 · A stroll or dog walk is a loop and stays where it starts: the secondary location stage places a loop activity at the place of the activity before it

- **Status:** active on branch `feature/i679-stroll-loops` (not merged). The 1 % end-to-end evidence is not linked here yet; no result of the change on a synthetic population is stated in this record.
- **Numbering:** ADR-0207 is the next free id after ADR-0206 (`fix/i678-mode-choice-reach`, same-day parallel branch); checked on 2026-11-25 across all refs (`git for-each-ref` and `git ls-tree` of `docs/decisions/`; the highest id taken is ADR-0206).
- **Issue:** #679. **Owner decision (2026-11-25):** follow the eqasim-france Paris model, a loop gets (almost) no displacement; keep sampling the day from one diary and the secondary distances by class as eqasim does, with no cap on drawn distances and no bound from the diary's own distance; try to identify loops also where the detailed purpose was not asked; one plan with #678.
- **Rule and definition:** `docs/codebase/notes/leisure-loops.md` (the one home of the flag definition and the placement rule). **Switch:** feature record `secondary_loop_zero_displacement`.

## Context

- MiD records a stroll (`W_ZWD` 710) or a dog walk (711) as ONE trip whose length is the whole loop. The model divides that path length by the detour factor and uses it as the distance between two places, so a stroll connects two far-apart activities and the next trip starts from a place the person never went to. How the survey records these trips, how many long walk and bicycle trips they are and how well the diary chain alone detects them: [`docs/runs/i679-stroll-loops-survey-probe-2026-11-25.yml`](../runs/i679-stroll-loops-survey-probe-2026-11-25.yml).
- ASSUMPTION (taken from issue #679 and the design of 2026-11-25; not verified in this repository): the Paris regional survey of the French eqasim pipeline gives the straight line between origin and destination, which is close to zero for a loop, so a stroll there ends up next to its start.
- `W_ZWD` exists only for the interview modes that ask it (not the paper questionnaire, not children under 14). The stage's leisure subtype is drawn for every synthetic leisure leg from the labelled MiD legs; the donor's own `W_ZWD` is carried on the trips table but not read there.

## Decision

1. **Paris model: a loop activity gets the place of the activity before it** (owner decision). The trip count, the purposes and the times of the day are unchanged; only places and distances change. The trip after the loop starts at that place and keeps its own mode, times and drawn distance.
2. **Which legs are loops, and where the loop is put,** is defined only in `docs/codebase/notes/leisure-loops.md`.
3. **Switch** `secondary_loop_zero_displacement`, production `true`, code default `false`; off leaves every output as before (feature record, test named there).
4. **Comparisons leave loops out where both sides can:** the model side of the secondary distance comparison drops loop legs where the cache carries `loop_origin`, and the W_ZWD group reference gets a loop-free variant next to the committed one (data record `mid2023_w_zwd_group_reference_without_loops`).

## Approaches considered

- **Zero displacement at the start place (chosen).** Keeps the diary's trip count and times; the loop kilometres disappear from the walk and bicycle kilometres.
- **Out and back at half the loop length.** Keeps the kilometres, but needs a return trip where the survey has none, which changes trip counts against the participation controls the synthesis holds. Withdrawn.
- **Own leisure group with a near-zero distance layer.** Touches every enumeration of the group names, and the leg after the loop keeps a drawn distance that cannot be met when the loop sits between two identical places.
- **Drop the stroll as a trip.** Lowers the leisure participation the synthesis controls against the regional survey, where a stroll counts as a leisure trip.

## Assumptions

- ASSUMPTION: the loop probability estimated on the legs whose detailed purpose was asked holds where it was not asked (paper questionnaire, children under 14). The survey cannot test it: the unasked legs are exactly the ones without the label.
- ASSUMPTION: the probability estimated on leisure legs (`W_ZWECK` 7) does not transfer to the coarse purpose 10 or to the child purposes, so no loop is imputed for them.
- Loops with a motorised main mode (a drive without destination) stay as before: their vehicle kilometres belong on the network.
- The legs counted as "not asked" (the no-detail codes, 799 included under the production sentinel setting) and the fallback for thin cells are defined in the note.
- ASSUMPTION: a bicycle leg with the stroll code 710 or the dog-walk code 711 (a ride without destination) is a loop, like the same code on foot.

## Consequences

- **Persons with an anchored or linked activity keep their loops** (escort link, portal stay, both sides of a passive joint link): those mechanisms are keyed by the activity index, which the collapse renumbers, and this version does not remap them. Their loops are placed by the solver as before and counted; how large that share is: the stage report block `loops` of a run.
- **The lost loop kilometres.** The walk and bicycle kilometres of the model fall by the loops' share. This is a known difference to survey totals, which count loop lengths. The published MiD W12 table that `scripts/validate_secondary_distances.py` compares with keeps loops and carries no `W_ZWD`, so on that table the survey side cannot drop them (the script states it), while the W_ZWD group reference has the loop-free variant.
- **Trips from home straight back to home (#375)** already have this representation: in `synthesis/population/spatial/secondary/problems.py::find_assignment_problems` a chain whose two ends are fixed purposes and that has no variable activity gets size 0 and is skipped, so the trip starts and ends at the household's one home location (`synthesis/population/spatial/locations.py`). This change adds the same for strolls.
- **Facilities.** A leisure activity at a home, work or education facility is new for MATSim; the facility check accepts it only for the written ids of that kind. RunPreparation and a short simulation have to confirm it end to end; that evidence is not part of this record yet.
- The solver's random streams are consumed by fewer legs with the switch on, so two switch states differ as distributions, not leg by leg.
- Both the secondary location stage and `matsim.scenario.facilities` declare the new key, so both stage hashes change once, with the switch off too (the config key set is part of the hash).
