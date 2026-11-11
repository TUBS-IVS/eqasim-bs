# ADR-0191 · 2026-11-11 · In-commuter income and economic status come from the MiD donor household; the INKAR Kreis scale is relative to the population-weighted national mean

- **Status:** accepted (implemented on branch `fix/audit-b8-b10-incommuter-income`; no model run with the change yet, so its effect on in-commuter incomes, economic status and fleet is not measured)
- **Numbering:** 0191 checked free across all branches and the open PRs on 2026-11-11 (highest record anywhere: ADR-0190).
- **Issue:** none (repository audit 2026-11-11, work package B, findings B8 = P10-F1 and B10 = P6-F8 / P6-F9).
- **Relates to:** ADR-0029 (Einpendler injection), ADR-0069 (placement_income: a donor keeps its own MiD income), ADR-0120 (in-commuter donor demographics), ADR-0099 (in-commuter PT ticket).
- **Owner decisions (2026-11-11):** derive the in-commuter income and economic status from the MiD data in this PR instead of only labelling the constants; INKAR scale relative to the national mean via the population-weighted mean over the Kreise (option A of the kickoff); car attributes follow the fixed commute mode.

## Context

- **B8.** `braunschweig.data.inkar.household_income :: _load` documents the per-Kreis `scale` as "relative to the national mean", but divided by the UNWEIGHTED mean of the 400 Kreis rows. The INKAR export (`E_Haushaltseinkommen.xls`, Ausgabe 2024, latest year 2021) has no Bund row. The indicator is disposable household income per inhabitant (EUR/EW/month), so the national value is the population-weighted mean of the Kreis values. Residents are not affected while `income_kreis_control` is on, because `braunschweig.popsim.income_kreis_control :: build_kreis_income_targets` renormalises the scale within the region. The scale enters the in-commuter income directly and the legacy midpoint scaling (`income_kreis_control` off, enriched path).
- **B10.** `braunschweig.synthesis.incommuters` gave every in-commuter the monthly household income `3000 EUR x INKAR scale x lognormal(sigma 0.20)` and mapped it to the economic status with the thresholds 0.7 / 0.9 / 1.1 / 1.3 x 3000. None of the four numbers has a source (the 3000 came in as a constant with the first in-commuter builder). The economic status selects the fleet segment of the car-mode in-commuters. PT in-commuters got `number_of_cars = 1`, and every in-commuter household (workers and students) got `car_availability = "all"`.
- Every in-commuter already copies its commute timing, age and sex from a MiD 2023 B1 donor (`braunschweig.data.hts.mid_donor`: an employed person with a home-to-work trip, national sample). The donor's household carries `hheink_gr1` (net household income, 15 brackets), `oek_status` (MiD economic status, 1-5) and `H_GR`.

## Decision

1. **National reference (B8).** `scale = einkommen_eur / national_mean`, where `national_mean` is the mean of the INKAR Kreis values weighted with the DESTATIS 12411-0018 Kreis population (config `braunschweig.destatis_population_path`, the file of data record `destatis_population_kreis`). Every INKAR Kreis must have a population; a missing one raises (no fallback for the reference itself). The stage logs the weighted and the unweighted mean.
2. **Donor household income and status (B10).** `braunschweig.data.hts.mid_donor` maps each donor household with the resident popsim mappers: `oek_status` → `economic_status` (`attributes.map_economic_status`), `hheink_gr1` → income bracket label (`attributes.map_household_income`); item non-response is imputed from the valid pool of the same household-size group and RegioStaR7, the rate logged by `missing.resolve`. Each donor gets a continuous EUR value within its bracket (`placement_income.draw_own_income_eur`: uniform in closed brackets, truncated Pareto in the open top bracket, as for residents), on its own RNG stream so the existing donor columns keep their bytes. The donor columns `household_income_eur` and `economic_status` travel with the donor.
3. **Origin Kreis tilt.** `incommuters.draw_incommuter_income` returns `donor household_income_eur x INKAR scale(origin Kreis)`. An origin Kreis missing in INKAR keeps the donor income unscaled (scale 1.0, counted, warned above 5 %, as before). The jitter (sigma 0.20) and the base 3000 EUR are removed for the worker in-commuters. A donor without income or status raises.
4. **Economic status.** The fleet draw of the car-mode in-commuters uses the donor's `economic_status`; the thresholds 0.7 / 0.9 / 1.1 / 1.3 are removed. `high_income` follows the resident rule `household_income_eur >= HIGH_INCOME_THRESHOLD_EUR` (5000 EUR, `braunschweig.popsim.income`).
5. **Cars by mode.** Worker in-commuters: `number_of_cars` 1 for car mode, 0 for PT; the household `car_availability` equals the person's (`all` / `none`). Student in-commuters: the household `car_availability` follows the mode as the person attributes already did.
6. **Student in-commuters (unchanged method).** They keep a flat household income, now the config key `braunschweig.student_incommuters.household_income_eur` (default 3000.0, marked ASSUMPTION). Deriving it from their own MiD donor household is a separate follow-up.

### Assumptions that remain (marked in the code)

- The donor household's income and status stand for the in-commuter's household; the injected agent itself is a one-person household (`household_size = 1`), so income and size describe different constructs.
- The income within a MiD bracket is uniform (Pareto above 7000 EUR), the same assumption as for residents (ADR-0069).
- The INKAR scale shifts the EUR value but not the economic status; the status stays the donor's.
- Donors are drawn unweighted from the national MiD worker pool (unchanged; `H_GEW` is not used).
- The population weights are from 31.12.2024, the INKAR income from 2021.

### Rejected alternatives

- **Keep the unweighted mean and fix only the documentation (B8 option C).** Keeps the reference construct wrong; the donor income is national, so its relative factor has to be national too.
- **Re-export INKAR with the Bund row (B8 option B).** Exact and same-year, but needs a new manual download; the weighted mean reproduces the Bund value up to the population year.
- **Label the four constants ASSUMPTION and make them configurable.** The owner preferred deriving them from the data that the donor already brings.
- **Draw the income from the MiD bracket table P(bracket | household size = 1).** That table covers all one-person households (retired people included) and gives no status; the donor is the actual employed commuter.
- **Number of cars from the donor's `H_ANZAUTO`.** A car-mode agent could get zero cars; the mode is fixed by the outside tour, so the car count follows the mode.

## Consequences

- **Outputs change.** The INKAR stage changes, so every stage downstream of it is recomputed (popsim included; with `income_kreis_control` on the resident incomes are expected to stay the same up to floating point, because the constant factor cancels in the renormalisation). Worker in-commuters get new incomes, economic status, `high_income` and therefore a different fleet segment mix; PT in-commuters lose their car attribute. The mode of an in-commuter is fixed by the outside tour, so no direct mode-choice effect is expected; this is not measured.
- **Not validated.** No run exists yet; the size of the effect must be measured in a run manifest before any claim.

## Evidence

- Code and tests on the implementing branch (`tests/test_inkar_household_income.py`, `tests/test_mid_donor.py`, `tests/test_incommuters.py`, `tests/test_population_income_attribute.py`, `tests/test_student_incommuters_stage.py`).
- No run manifest yet.
