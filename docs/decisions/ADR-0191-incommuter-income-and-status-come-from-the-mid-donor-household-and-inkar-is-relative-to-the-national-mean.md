# ADR-0191 · 2026-11-11 · A worker in-commuter carries the household of its MiD donor, drawn weighted, by commute length and by the income level of its origin Kreis; the INKAR Kreis scale is relative to the population-weighted national mean

- **Status:** accepted (implemented on branch `fix/audit-b8-b10-incommuter-income`; no model run with the change yet, so its effect on in-commuter timing, incomes, economic status and fleet is not measured)
- **Numbering:** 0191 checked free across all branches and the open PRs on 2026-11-11 (highest record anywhere: ADR-0190).
- **Issue:** none (repository audit 2026-11-11, work package B, findings B8 = P10-F1 and B10 = P6-F8 / P6-F9).
- **Relates to:** ADR-0029 (Einpendler injection), ADR-0069 (placement_income: a donor keeps its own MiD income), ADR-0120 (in-commuter donor demographics), ADR-0099 (in-commuter PT ticket), ADR-0116 (the model's weekday universe).
- **Owner decisions (2026-11-11, in this order):** derive the in-commuter income and status from the MiD data instead of labelling the constants; give the in-commuter a conceptual household like the residents; draw the donors weighted; use the INKAR level of the origin Kreis for the draw instead of scaling the income afterwards; use weekday donors only; match donors by commute length, with a minimum donor pool of 1000 instead of a fixed distance edge. The INKAR scale relative to the national mean (decision 1) follows from the national donor pool.

## Context

- **B8.** `braunschweig.data.inkar.household_income :: _load` documents the per-Kreis `scale` as "relative to the national mean", but divided by the UNWEIGHTED mean of the Kreis rows. The INKAR export (`E_Haushaltseinkommen.xls`) has no Bund row. The indicator is disposable household income per inhabitant, so the national value is the population-weighted mean of the Kreis values. Residents are not affected while `income_kreis_control` is on, because `braunschweig.popsim.income_kreis_control :: build_kreis_income_targets` renormalises the scale within the region.
- **B10.** `braunschweig.synthesis.incommuters` gave every worker in-commuter the monthly household income `3000 EUR x INKAR scale x lognormal(sigma 0.20)` and mapped it to the economic status with the thresholds 0.7 / 0.9 / 1.1 / 1.3 x 3000. None of the four numbers has a source. The status selects the fleet segment of the car-mode in-commuters. PT in-commuters got `number_of_cars = 1`, and every in-commuter household (workers and students) got `car_availability = "all"`.
- Every worker in-commuter already copies commute timing, age and sex from a MiD 2023 B1 donor (`braunschweig.data.hts.mid_donor`): an employed person with a leg arriving at work, drawn uniformly from the national sample over all reporting days. The donor's household record carries the income bracket, the MiD economic status, the household size and the ages of its members; the person record carries the person weight and the reporting day; the trip record the leg length.

## Decision

1. **National reference (B8).** `scale = einkommen_eur / national_mean`, where `national_mean` is the mean of the INKAR Kreis values weighted with the DESTATIS 12411-0018 Kreis population (data record `destatis_population_kreis`). A Kreis without population raises.
2. **Conceptual household.** A worker in-commuter is the one simulated member of a household that is not simulated. It carries its donor household: income (a draw within the donor's `hheink_gr1` bracket, as for residents, ADR-0069), `oek_status`, household size (`H_GR`) and OECD-modified consumption units from the household interview ages (`HP_ALTER_1..6`, computed with the upstream `data.hts.hts.calculate_consumption_units`). `high_income` follows the resident rule of `braunschweig.popsim.income`. The income is used as drawn; nothing is multiplied onto it.
3. **Donor pool.** Employed MiD persons with a leg arriving at work that have a household record, a reporting day in the model's weekday universe (`braunschweig.popsim.trips.WEEKDAY_DIARY_KERNWO`), a positive person weight `P_GEW` and a commute length (the first leg between home and work, the rule of `braunschweig.popsim.commute_distance`). Every exclusion is counted by reason.
4. **Draw by commute length.** An agent draws from the donors whose commute length is nearest to its own (in log distance); the window grows until its Kish effective size reaches the minimum pool (config `braunschweig.incommuters.donor_min_effective_pool`). The agent's commute is the detour factor times the straight line from its origin to its workplace; the origin is its real home where the real-origin step placed one, otherwise the population-weighted centre of the Gemeinden of its origin Kreis, because such an agent keeps its gate or station as home.
5. **Draw by Kreis income level.** Within the window the donor weights `P_GEW` are tilted exponentially in the household income per person, so that the mean of the drawn donors equals the window's weighted mean times the INKAR scale of the origin Kreis. This is the distribution closest to the weighted window (minimum relative entropy) that meets the mean, the principle of `braunschweig.popsim.income_kreis_control`. Fallbacks, all counted in the `donor_draw` report of the stage output: an origin Kreis without an INKAR scale and a target outside the incomes of the window are drawn untilted; a window that cannot reach the minimum pool is the whole pool.
6. **Cars by mode.** Worker in-commuters: `number_of_cars` 1 for car mode, 0 for PT; the household `car_availability` equals the person's. Student in-commuters: the household `car_availability` follows the mode as the person attributes already did.
7. **Student in-commuters (method unchanged).** They keep a flat household income, now the config key `braunschweig.student_incommuters.household_income_eur` (ASSUMPTION), a one-person household and the uniform draw over all reporting days. Moving them to the donor construct is a separate follow-up.

### Assumptions that remain (marked in the code)

- Commuter households of a Kreis deviate from the commuter households with a similar commute as all inhabitants of that Kreis deviate from the national mean (INKAR).
- The donor pool is national, not regional.
- The minimum pool, the rounding of the agent commute to steps of about 5 %, and the Kreis population centre as the origin of agents without a real home point.
- The income within a MiD bracket is uniform (Pareto above 7000 EUR), as for residents.
- Household size 6 stands for six and more (MiD top code); a member without a valid age counts as 14 or older.
- The population weights are from 31.12.2024, the INKAR income from the latest INKAR year.

### Rejected alternatives

- **Keep the unweighted mean and fix only the documentation (B8).** The donors are national, so their relative factor has to be national too.
- **Re-export INKAR with the Bund row (B8).** Exact and same-year, but needs a new manual download; the weighted mean reproduces the Bund value up to the population year.
- **Label the four constants ASSUMPTION and make them configurable.** The owner preferred the data the donor already brings.
- **Multiply the donor income by the INKAR scale.** Income, status and household size would no longer belong to one real household, and the status would not follow the Kreis level.
- **A uniform draw over all reporting days.** Person weights exist, and a weekend working day does not describe a weekday commuter.
- **A fixed commute edge at 20 km with two donor classes.** Arbitrary edge; the window adapts to where the survey has donors.
- **A soft tilt on the commute length instead of a window.** The drawn donors' commutes spread far too widely around the target to be a match.
- **Donors matched to the commute mode, or regional donor pools.** The mode can still switch after the draw (timetable check, re-balancing), and a regional pool is smaller while the Kreis level is already met by the tilt.
- **Number of cars from the donor's `H_ANZAUTO`.** The donor is not matched to the mode, so a car-mode agent could get a household without a car.

## Consequences

- **Outputs change.** The INKAR stage changes, so every stage downstream of it is recomputed (popsim included; with `income_kreis_control` on the resident incomes are expected to stay the same up to floating point). Worker in-commuters get other donors: their commute timing, incomes, economic status, household size, `high_income` and therefore the fleet segment mix change; PT in-commuters lose their car attribute. The mode of an in-commuter is fixed by the outside tour, so no direct mode-choice effect is expected; this is not measured. Where the scale is used absolutely (the enriched path `braunschweig/synthesis/population/enriched/income_distribution.py :: _apply_inkar_income_scale`, and `braunschweig/popsim/income.py` with `income_kreis_control` off), every resident `household_income_eur` shifts by the constant ratio of the unweighted to the weighted national mean.
- **Fewer distinct donors.** The weighted, distance-matched pool is smaller in effective size than the uniform pool, so more agents share one donor's working times. The size of this effect on the departure profile is not measured.
- **Household attributes of in-commuters.** `household_size` and `consumption_units` of a worker in-commuter now describe its donor household, not the number of simulated members. No reader of `consumption_units` exists outside popsim; whether an analysis reading `household_size` of the merged population filters the in-commuters has not been traced for every analysis module.
- **Not validated.** No run exists yet; the effect must be measured in a run manifest before any claim.

## Evidence

- Code and tests on the implementing branch (`tests/test_inkar_household_income.py`, `tests/test_cordon_donor_draw.py`, `tests/test_mid_donor.py`, `tests/test_incommuters.py`, `tests/test_student_incommuters_stage.py`).
- No run manifest yet.
