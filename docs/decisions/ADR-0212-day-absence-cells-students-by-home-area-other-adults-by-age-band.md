# ADR-0212 · 2026-11-28 · Day-absence cells: students by home area, other adults by age band

- **Status:** active (branch `claude/i140-student-flows-fde844`, Phase 4 of the student layer). Amends [ADR-0110](ADR-0110-general-day-absence-as-a-two-stage-state-draw.md), Decision 1 (state model: the individual stage draws per age band) and Decision 4 (stage inputs).
- **Numbering:** ADR-0212 is the next free id; checked on 2026-11-28 across all local and remote branches and the open pull requests (highest id taken: ADR-0211).
- **Issue:** #140 (student layer). Design: `docs/superpowers/specs/2026-11-25-student-commuting-design.md`, revision 3 (not in the repository; the sections used are named in words below). Owner decision of 2026-11-27: the approval of revision 3, which includes the absence cells.
- **Numbers:** none are copied here. The rates are in [`srv2023_absence_by_age_band.csv`](../../eqasim-data/data/braunschweig/srv/srv2023_absence_by_age_band.csv) (columns `p_absent`, `p_absent_non_student`), [`srv2023_student_parameters_by_area.csv`](../../eqasim-data/data/braunschweig/srv/srv2023_student_parameters_by_area.csv) and [`srv2023_student_area_rule.csv`](../../eqasim-data/data/braunschweig/srv/srv2023_student_area_rule.csv); config defaults are in the student flows block of `configs/base_bs.yml`.

## Context

ADR-0110 draws the individual residual per AGE BAND at the band's SrV rate of all persons. The student layer needs the students' absence by home area: the day factor of the student balance, `f(k) = e x (1 - a(area(k)))` (design, section 5a "The quantities: one balance"), assumes that the day-absence step takes away `a(area)` of the students of each home area, and the in-commuters count with the same `a`. SrV measures the students' share away from home all day by area (the row `day, away_all_day, students_all, yes` of `srv2023_student_parameters_by_area.csv`); the area rule of Phase 3 gives this parameter a value per area (row `away_all_day` of `srv2023_student_area_rule.csv`: `area_value` True). The band 18 to 29 mixes students and non-students: its rate of all persons (`p_absent`) differs from the rate among its non-students (`p_absent_non_student`, added to `srv2023_absence_by_age_band.csv` by Phase 4); in the older bands the two columns hardly differ.

## Decision

1. **Cells.** The individual stage runs per cell (`braunschweig.synthesis.day_absence.cells`):
   - `student:<area>` for a person aged 18 or older with `P_TAET == STUDENT_P_TAET`, by the home area of the household's Kreis (Braunschweig; the surveyed surrounding Kreise; Wolfsburg, which SrV does not survey, takes the value of the region: [ADR-0205](ADR-0205-one-wolfsburg-rule-for-the-srv-anchored-kreis-targets.md), design assumption A21); rate: the SrV away share of the area;
   - `adult:<band>` for every other adult with a readable `P_TAET`; rate: `p_absent_non_student` of the band, with the raw SrV weights (design assumption A24: the other adults keep what SrV measures for them, without the school-degree correction);
   - `<band>` for a person under 18, rate `p_absent`, unchanged;
   - **an adult of unknown status is drawn at the band's all-person rate** (`P_TAET` missing or the no-answer code): an unknown status is never read as "not a student" (`braunschweig.calibration.mid_student_reference`), so the band's rate of all persons is the expected rate of such an adult. Such an adult goes to the cell `<band>`.
   The rate of an adult band as a whole is no longer a target; it follows from the students and the others the population holds and is reported (`by_band`), not guarded.
2. **Draw unchanged otherwise.** The household stage by household size; the two uniform draw vectors in the same order for all persons; the eligibility gates of ADR-0110 Amendments 1 and 2; the choice of the residual formula; the overshoot and unreachable warnings, now per cell. A cell that holds the persons of a band at the band's rate reproduces the band draw bit for bit (tested), so the persons under 18 keep exactly their state. Under the production settings a student living alone is reached by the household stage only; the cell rate is reached through the students in larger households (design assumption A25: within a cell a student with an education leg is away as often as one without), and a cell whose eligible pool cannot carry its rate warns.
3. **Stage.** Switch `day_absence_student_cells_enabled`, default on. The cells path declares the home zones stage and the home-area key, requires `P_TAET` in the enriched persons (refused, naming the switch), runs the deviation guard per cell, counts the adults without a readable `P_TAET` as a rate with a warn share (a guard against a broken column; no readable code at all raises), and adds `absence_cell` to the frame. Mechanics, config keys and the diagnostics names: the stage record `braunschweig.synthesis.day_absence.absence_stage`.
4. **Token.** ON: the cells helpers and the CRLF-neutral digests of the three SrV tables the cells read enter the stage token. OFF: the module list of the code before the switch.

## Rejected options

- **One rate per adult band (ADR-0110 as it stood).** The students would be thinned at the rate of their band while the balance and the in-commuters use `a(area)`, so the students of the day per site would not add up (design, section 5a: the model's students before Phase 4 are away at the band's rate). Rejected.
- **Students at the band rate, only the other adults at the non-student rate.** The same inconsistency for the students; and the difference of the students' away share between the two areas passes the area rule (`srv2023_student_area_rule.csv`, row `away_all_day`, columns `difference_in_se` and `area_value`), so one rate for both areas is not what SrV measures. Rejected.
- **Cells per Kreis.** ASSUMPTION: a Kreis cell would hold only a part of the base of the surrounding area (column `n_base_surrounding` of the `away_all_day` row), too few respondents for the minimum the area rule asks for per share (design assumptions A14 and A20, [ADR-0211](ADR-0211-students-lower-the-employed-target-and-get-their-own-education-entries.md) Decision 2). The committed table holds no base per Kreis and none was computed, so this reason is not measured. The areas are the finest split the table supports. Rejected.

## Consequences

- The draw now depends on `P_TAET` and the home Kreis, and the stage gains the edge home zones to day absence on the cells path. Production outputs change: the students by area, the non-students at their own rate. The OFF path (`day_absence_student_cells_enabled: false`) is byte-identical to the code before (tested against stored digests of the output of commit `c12cb6d9`).
- **OFF token:** the module list of the OFF token is unchanged, but the semantic digest of `braunschweig/synthesis/day_absence/absence.py` changed and the new switch is declared on both paths, so a cached day-absence output is recomputed once even with the switch off; the recomputed output is identical.
- With the day absence on and the cells off, students are thinned at their band rate; the student flows refuse that configuration ([ADR-0213](ADR-0213-students-are-placed-by-the-enrollment-balance.md)).

## Assumptions

Design assumptions A24 and A25 as stated in Decisions 1 and 2, and the warn share of the unreadable `P_TAET` (a guard, not a target; config comment).

## Evidence

Feature record `docs/registry/features/day_absence_student_cells.yml` (tests, OFF byte-identity, fallback rate); the decisive test of Decision 1 is `test_an_adult_of_unknown_status_is_drawn_at_the_band_rate_of_all_persons` in `tests/test_day_absence_cells.py`. A run manifest of the wiring run follows.
