# Student flows: acceptance table and diagnostics

Status: measurement of the synthesis, not validation (the acceptance rule is the one exception, A17). Counts the synthesis (cached population and in-commuter agents), not a simulation.

- Verdict: **FAIL** (rule ASSUMPTION A17: balance >= 200 persons and |deviation| > 5.0%; 39 row(s) judged)
- sampling rate 1.0, absence mode cells, e 0.3706, f_eff 0.3218
- The student flows are not used for results on a population that fails the population rows (population rows failing: 16).

## Rows over the bound

| section | key | model | balance | deviation share |
|---|---|---|---|---|
| students_population | 03101 | 10180.0 | 12331.3 | -17.4% |
| students_population | 03102 | 2605.0 | 2073.1 | +25.7% |
| students_population | 03103 | 2940.0 | 2560.3 | +14.8% |
| students_population | 03151 | 2904.0 | 2060.8 | +40.9% |
| students_population | 03153 | 2821.0 | 3328.6 | -15.3% |
| students_population | 03154 | 1492.0 | 1585.9 | -5.9% |
| students_population | 03158 | 2067.0 | 2483.5 | -16.8% |
| students_population | total | 27213.0 | 28678.5 | -5.1% |
| students_of_day | 03101 | 3855.0 | 4172.1 | -7.6% |
| students_of_day | 03102 | 806.0 | 635.4 | +26.9% |
| students_of_day | 03103 | 1014.0 | 831.2 | +22.0% |
| students_of_day | 03151 | 1007.0 | 631.6 | +59.4% |
| students_of_day | 03153 | 661.0 | 1020.2 | -35.2% |
| students_of_day | 03154 | 260.0 | 486.1 | -46.5% |
| students_of_day | 03157 | 726.0 | 691.1 | +5.0% |
| students_of_day | 03158 | 660.0 | 761.2 | -13.3% |

## SrV distance rows

- Usable (at least 30 respondents, ASSUMPTION A14, and a comparison with a finite EMD): 03101, all (`all` is the SrV total row 03ZGB)
- Reported only (fewer respondents or no comparison, a difference there says little): 03102, 03103, 03151, 03153, 03154, 03157, 03158

## Fallback rates

- student status (Task 5): primary 199645/199645, fallback 0 (0.0%)
- absence cells P_TAET (Task 6): primary 941359/942846, fallback 1487 (0.2%)
- university plan rule, present (Task 9): primary 8748/8989, fallback 241 (2.7%)
- university plan rule, absent (Task 9): primary 0/0, fallback 0 (0.0%)
- legacy decay, Kreis without a row of T (Task 10): primary 8989/8989, fallback 0 (0.0%)
- in-commuter commute length (Task 11): primary 827/827, fallback 0 (0.0%)
- in-commuter home rule (Task 11): primary 826/827, fallback 1 (0.1%)

## Skipped

- mikrozensus_distance_bands: no committed Mikrozensus table of the students' education distance: braunschweig/mikrozensus holds the commute distance and mode of the employed only

## Files

- `student_acceptance_table.csv`
- `student_realised_flows.csv`
- `student_distance_bands.csv`
- `student_destination_shares.csv`
- `student_allotment_by_kreis.csv`
- `student_education_leg_by_status.csv`
- `student_routed_level_by_status.csv`
- `student_non_students_moved.csv`
- `student_incommuter_commute_length.csv`
- `student_flows_summary.md`
