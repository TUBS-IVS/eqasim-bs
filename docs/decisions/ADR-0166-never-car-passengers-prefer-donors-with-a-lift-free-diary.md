# ADR-0166 · 2026-10-23 · "Never" car passengers prefer donors with a lift-free diary

- **Status:** accepted (implemented; measured on the raw MiD donor, not yet in a simulation run)
- **Numbering:** 0153-0162 taken by the #465 branches, 0163-0165 by sibling sessions (0165 claimed by
  `i465-link-129867-bottleneck`); 0166 checked free across all local and remote branches and worktrees on
  2026-10-23 and claimed in the shared session notes.
- **Issue:** #465 (stuck agents at the end of the simulated day). **Owner decision (2026-10-23):** option A ("Option A
  umsetzen: für "nie"-Personen Spender mit mitfahrtfreiem Tagebuch bevorzugen (Personen-Match und
  Haushaltsabgleich); Filler-Rest akzeptieren."), chosen over a household-level class key and over accepting
  the ADR-0162 residual. **Refines:** ADR-0162 (passenger access class as a match key). **Relates to:** ADR-0121
  (passenger availability from `P_VAUTO`; a valid own answer stays authoritative), ADR-0106 (plan sources are
  re-matched, attributes stay).

## Context

- ADR-0162 keeps the passenger access class (`P_VAUTO` 3 "never" or not) when the weekend and diary plan matches
  give a person another respondent's diary. On the raw MiD donor it removed every class crossing of the diary
  re-match, but the defect it targets (a "never" person whose foreign diary has a car-passenger trip that the mode
  choice cannot offer, person 903464 of the 1 % rerun) only fell from 2,064 to 1,782 donor persons.
- The split of the 1,782 (measurement below, `class_key` arm) showed three sources:
  - **weekend household alignment, 971:** 889 paired across the class (the matched weekday household has no
    "never" member in the target's age band; the household keys do not carry the class) and 82 paired with a
    "never" member who reports a lift;
  - **diary re-match, 496:** all from "never" donors who report a lift themselves. The class key moved these
    re-matches from passenger-capable donors (680 crossings without the key) to "never" donors, and a "never"
    answer does not mean a lift-free diary (ADR-0121 keeps such answers);
  - **member-completion fillers, 315:** a filler copies attributes AND diary from the same mirror respondent, so
    this is that respondent's own answer/diary conflict (the ADR-0121 limitation), not a matching defect.
- What makes a donor consistent for a "never" person is a diary without a car-passenger trip; the class is only a
  proxy for it.

## Decision

1. **Lift flag.** `passenger_availability.car_passenger_diary_keys`: the (H_ID, P_ID) of every MiD diary with a
   non-rbW trip whose main mode `hvm_imp` is 3. It is the single rule behind `src_has_car_passenger_trip`
   (`attach_car_passenger_diary_evidence` now calls it), so the matcher and the measurement use one definition.
2. **Person-level matches** (`match_person(lift_free_never=True)`, weekend person fallback, mixed-household sweep,
   diary re-match): on the DONOR side the passenger key becomes "never AND a lift-free diary". A "never" target's
   exact cell then holds only donors whose day needs no car-passenger option; a "never" donor with a lift joins the
   other class. Key position (right after the licence) and ladder are unchanged.
3. **Household alignment** (`align_members(lift_free_never=True)`): a "never" target first tries a lift-free "never"
   donor in its (age band, sex) and (age band) cell, then a lift-free donor of the other class in the same cells,
   then the ADR-0162 tiers. The age band stays the stronger key: no lift-free donor of another band is preferred,
   because a child's or a pensioner's day is a worse substitute than a lift. Targets of the other class keep the
   ADR-0162 tiers.
4. **Which diary is flagged.** The diary the donor row would hand over: in the weekend match its current plan
   source (a member-completion filler hands over its mirror's diary), in the diary re-match its own diary. The flag
   lives on an internal column `weekend_plan_match.DONOR_LIFT_COLUMN` of the matchers' copies and never leaves
   them.
5. **Switch.** `braunschweig.population.popsim.donor_match_lift_free_never_passenger` (default true, pinned in
   `configs/base_bs.yml`, declared by `braunschweig.popsim.completed_donor`); effective only when the ADR-0162 key is
   effective (`donor_match_passenger_access` and `mid_passenger_availability` on), otherwise logged once (INFO).
   The Wege table is then loaded before the weekend match instead of after it; the load draws no rng.
6. **Randomness.** `match_person` still draws exactly one value per call and `align_members` none, so the shared
   completion stream is unchanged; with no lift in any diary the assignment equals ADR-0162 exactly (tested).
7. **Observability.** `completed_donor.log_never_passenger_foreign_lifts` logs, in both arms, how many "never" persons
   with another respondent's diary still carry a lift, as a rate.
8. **Accepted residuals.** The filler copies (ADR-0121 own-answer conflict, owner decision) and the household
   alignments where the matched household offers no lift-free donor in the target's age band.

## Rejected options

- **The class as a household-level soft key** (number of "never" members in `match_household`): addresses only the
  889 alignment crossings, not the 496 + 82 same-class lifts, and moves the household match of every weekend
  household, including the many without a "never" member.
- **Accept the ADR-0162 residual:** leaves 1,467 matching-made conflicts (all but the 315 filler copies) that the
  measurement shows can be avoided without an extra ladder relaxation.
- **Prefer a lift-free donor across the age band in the alignment:** trades a lift for a day of the wrong life
  stage; not measured, rejected by the ordering principle of ADR-0118 (age bands decide whose day a person gets).
- **Rewrite the availability of the receiving person** (ADR-0162 option 2): overrides a respondent's valid answer.

## Measurement (raw MiD 2023 B1 donor, seed 1234)

`scripts/measure_donor_match_passenger_access.py` on felix, 2026-10-23; unweighted counts of the 485,709 completed
donor persons, not synthetic-population estimates. Split baseline at commit 96ff2cc8 (arms `off`/`on` = key off/on),
decision arms at commit baff2789; commands, arms, artifacts and hashes in run manifest
`docs/runs/donor-match-passenger-residual-2026-10-23.yml`. The `class_key` arm reproduces
the ADR-0162 numbers exactly (1,782; 10,825 crossings).

| "never" persons whose FOREIGN diary has a lift | key off | class key (ADR-0162) | + lift-free (ADR-0166) |
|---|---|---|---|
| weekend household alignment, donor of the other class | 902 | 889 | 806 |
| weekend household alignment, "never" donor with a lift | 75 | 82 | 75 |
| weekend person match / sweep | 0 | 0 | 0 |
| diary re-match, donor of the other class | 680 | 0 | 0 |
| diary re-match, "never" donor with a lift | 92 | 496 | 0 |
| member-completion filler (mirror's own conflict) | 315 | 315 | 315 |
| **total** (rate over the 14,548 "never" persons with a foreign diary) | 2,064 | 1,782 (12.25 %) | 1,196 (8.22 %) |

- The weekend match report and the diary match-level histogram (`{0: 74879, 1: 2, 2: 2, 3: 1}`) are identical in
  both arms: the preference costs no extra relaxation. It moves 65,541 plan sources against the class-key arm.
- Persons whose class differs from their diary's respondent rise from 10,825 to 11,260 (4.60 % to 4.79 % of the
  235,158 foreign diaries); in the diary re-match from 0 to 432 of 74,884. These are passenger-capable persons
  who now get the diary of a "never" respondent with a lift, which their own availability can reproduce. It is
  the intended price of item 2, not a defect.
- **Residual 1,196:** 315 filler copies (accepted, ADR-0121) and 881 household alignments in which the matched
  weekday household has no lift-free member in the target's age band (accepted by item 3).

## Consequences

- Output change by design: "never" persons with a foreign plan source get a lift-free diary where the donor pool
  offers one; passenger-capable persons absorb the "never" diaries with lifts. The completed donor stage and
  everything downstream rebuild (the key is in its config hash).
- `n_crossed_passenger_class` and `count_passenger_class_crossings` keep measuring the class, so with this flag
  on they no longer read as a defect count; the lift rate of item 7 is the defect count.
- **Status:** implemented, tested (`tests/test_donor_match_passenger_access.py`,
  `tests/test_measure_donor_match_passenger_access.py`) and measured on the raw donor; not yet measured in a
  simulation run (whether stuck agents of this kind disappear needs the next rerun).
