# ADR-0162 · 2026-10-23 · The plan-source matches keep the car-passenger access class

- **Status:** accepted (implemented; measured on the raw MiD donor, not yet in a simulation run)
- **Numbering:** 0153-0158 were taken by the sibling branches of #465 and 0159 by `fix/i465-outside-pt-walks`;
  0160-0162 checked free across all local and remote branches and worktrees on 2026-10-23 and claimed for branch
  `fix/i465-last-three-stuck` in the shared session notes (first claimed as 0159-0161, renumbered on the collision).
- **Issue:** #465 (one of the last three residents stuck at the end of the simulated day in the 1 % rerun of the #465
  fixes). **Owner decision (2026-10-23):** resolve the conflict in the population processing, by the match keys
  ("1 bitte umsetzen", option 1 of two: match key vs. rewriting the availability from the plan source's diary).
  **Relates to:** ADR-0106 (plan sources are re-matched, the person's attributes stay), ADR-0118 (fine child age
  bands, the same matcher), ADR-0121 (passenger availability from `P_VAUTO`).

## Context

- A synthetic person carries its attributes from one MiD respondent and its day from a plan source
  (`source_H_ID`, `source_P_ID`). The weekend plan match and the diary plan match of
  `braunschweig.popsim.completed_donor` give a person ANOTHER respondent's diary and leave its attributes untouched
  (ADR-0106). Their keys are licence, sex, age band, employment and pt subscription (`weekend_plan_match.match_person`)
  and, for the household alignment, age band and sex (`weekend_plan_match.align_members`).
- ADR-0121 derives `car_passenger_availability` from the attribute respondent's `P_VAUTO` (car available as driver
  or passenger: 1 anytime, 2 occasionally, 3 never) and keeps a valid answer even when the respondent's own diary
  reports a passenger trip. The mode choice then excludes `car_passenger` for "none".
- Person 903464 of the 1 % rerun: attributes from MiD person (91034110, 2), `P_VAUTO` 3, availability "none"; plan
  from MiD person (16431980, 3), whose night ends with a lift home at 24:59 (29 min, 33 km). Without the car-passenger
  option the mode choice chose pt with the first departure of the next morning (04:50 at 29:12, a wait of 4.1 h,
  counted in the utility), and the agent was still in pt at 30 h.
- In the sampled 1 % population of the rerun (`synthesis.population.sampled`) 124 of 11,424 persons have availability
  "none" while their plan source reports a car-passenger trip: 74 with a plan source from ANOTHER respondent (the
  defect here; 43 through the weekend household alignment, 42 through the diary re-match, overlapping), 50 with their
  own diary (the documented ADR-0121 limitation, unchanged).

## Decision

1. **Key.** `weekend_plan_match.PASSENGER_KEY` ("passenger_never") = `P_VAUTO == 3` (`passenger_never_class`); the
   codes without an answer (9, 206, 402, missing, imputed later by ADR-0121) count as not "never".
2. **Where.** In `match_person` a SOFT key right after the licence (`PERSON_KEYS_WITH_PASSENGER`): licence and
   passenger access decide which car modes the mode choice may offer, so they are the last keys the ladder gives up.
   In `align_members` the (age band, sex) and (age band) tiers are first tried within the target's class. Both plan
   matches forward it (`reassign_weekend_plan_sources`, `reassign_diaryless_plan_sources`, `passenger_key`).
   Member completion needs nothing: a filler copies attributes and diary from the same mirror person.
3. **Switch.** `braunschweig.population.popsim.donor_match_passenger_access` (default true, pinned in
   `configs/base_bs.yml`, declared by `braunschweig.popsim.completed_donor`); effective only with
   `mid_passenger_availability` on, the only case in which `P_VAUTO` is loaded (otherwise logged once, INFO).
4. **Randomness.** Same contract as the fine bands: `match_person` still draws exactly one value per call and
   `align_members` none, so the shared completion stream stays in lockstep; only the assignment changes.
5. **Observability.** `DiaryMatchReport.n_crossed_passenger_class` (both arms) and
   `completed_donor.count_passenger_class_crossings` (persons with a foreign plan source whose class differs from
   their donor's), both logged as rates. A soft key: a residual crossing with the key on is a ladder relaxation.
6. **Valid answers stay as they are** (ADR-0121): the fix chooses consistent diaries, it rewrites no attribute.

## Rejected options

- **Rewrite the availability from the plan source's diary** (option 2): smaller, but it overrides a respondent's
  own valid answer in 47 of the 74 cases (own `P_VAUTO` 3, diary re-matched), against ADR-0121.
- **A hard key:** a person whose class has no donor in a cell would need the whole-pool fallback, crossing licence,
  sex and age at once; a soft key relaxes the weaker keys first.
- **Allow car passenger for everybody** (rejected in ADR-0121): gives up the observed restriction.

## Measurement (raw MiD 2023 B1 donor, seed 1234)

`scripts/measure_donor_match_passenger_access.py` at commit a2376306 on felix, 2026-10-23 (unweighted counts of the
485,709 completed donor persons, not synthetic-population estimates; aggregate report committed as
`docs/runs/artifacts/donor-match-passenger-access-2026-10-23/report.json`):

| | key off | key on |
|---|---|---|
| persons with another respondent's diary | 235,158 | 235,158 |
| of those, passenger class differs from the diary's respondent | 19,781 (8.41 %) | 10,825 (4.60 %) |
| diary re-matches crossing the class (`DiaryMatchReport`) | 8,624 of 74,884 (11.52 %) | 0 of 74,884 |
| "never" persons whose FOREIGN diary has a lift | 2,064 | 1,782 |
| "never" persons whose OWN diary has a lift (ADR-0121, unchanged) | 1,254 | 1,254 |

The key moves 74,819 plan sources. The diary re-match (person 903464's path: a filler without a diary,
`nodiary_mobile`) no longer crosses the class. The remaining 10,825 crossings come from the weekend household
alignment, whose matched donor household has no member of the target's class in the age band (the household keys
do not carry the class). The defect count falls only from 2,064 to 1,782, because a "never" donor can itself report a
lift (the own-answer conflict of ADR-0121, 1,254 donors): a same-class diary is not always a lift-free diary.

## Consequences

- Output change by design: persons with a foreign plan source get diaries of their own passenger access class
  where the donor pool offers one; the weekend and diary matches move other persons' sources as the ladder changes.
  The completed donor stage and everything downstream rebuild (the key is in its config hash).
- The remaining 50 own-diary conflicts are the ADR-0121 limitation, not changed here.
- **Status:** implemented, tested (`tests/test_donor_match_passenger_access.py`) and measured on the raw donor;
  not yet measured in a simulation run.
