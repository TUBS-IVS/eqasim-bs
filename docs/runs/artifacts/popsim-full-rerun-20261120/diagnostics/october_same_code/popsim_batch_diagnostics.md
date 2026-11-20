# PopulationSim batch diagnostics

- work directory: `/home/felix/prod100_20261031/popsim_work`
- batches: 60
- seed groups: 1 (de13d57636a9: 60 batch(es))
- repository commit: `1041849c243c71859c95275447a24363ee1aa358`

## Reuse (region rows)

| scope | seed_group | subject | n_synthetic_households | n_seed_households | n_distinct_used | distinct_share_of_seed | kish_n_eff | max_copies | top100_share | top1000_share | top5000_share |
|---|---|---|---|---|---|---|---|---|---|---|---|
| region | de13d57636a9 | de13d57636a9 | 558274 | 2.181e+05 | 15248 | 0.06991 | 1293 | 3796 | 0.2066 | 0.5702 | 0.8906 |

## Weight classes

| seed_group | weight_class | n_seed | seed_weight_share | synthetic_share | difference_pp |
|---|---|---|---|---|---|
| de13d57636a9 | <1 | 158006 | 0.2759 | 0.006228 | -26.97 |
| de13d57636a9 | 1-2 | 33521 | 0.2139 | 0.07753 | -13.63 |
| de13d57636a9 | 2-3 | 11603 | 0.1296 | 0.1047 | -2.486 |
| de13d57636a9 | 3-5 | 8792 | 0.1535 | 0.2196 | 6.612 |
| de13d57636a9 | 5-8 | 4027 | 0.1144 | 0.2379 | 12.36 |
| de13d57636a9 | 8+ | 2148 | 0.1128 | 0.354 | 24.12 |

## Control coverage

| seed_group | group | seed_table | exhaustive | n_categories | n_universe | n_uncovered | n_multi_hit | seed_weight_share | synthetic_share | ratio |
|---|---|---|---|---|---|---|---|---|---|---|
| de13d57636a9 | age_sex | persons | True | 22 | 485709 | 880 | 0 | 0.002032 | 0.01465 | 7.21 |
| de13d57636a9 | employed_grid | persons | True | 10 | 230762 | 371 | 0 | 0.001893 | 0.008133 | 4.297 |
| de13d57636a9 | trip_class | persons | True | 4 | 485709 | 0 | 0 | 0 | 0 |  |
| de13d57636a9 | employment_status | persons | True | 7 | 422056 | 0 | 0 | 0 | 0 |  |
| de13d57636a9 | pt_ticket_group4 | persons | True | 4 | 422056 | 0 | 0 | 0 | 0 |  |
| de13d57636a9 | leisure_participation | persons | True | 2 | 485709 | 0 | 0 | 0 | 0 |  |
| de13d57636a9 | escort_participation | persons | True | 2 | 485709 | 0 | 0 | 0 | 0 |  |
| de13d57636a9 | work_by_employment | persons | True | 4 | 422056 | 0 | 0 | 0 | 0 |  |
| de13d57636a9 | education_band | persons | True | 6 | 485709 | 0 | 0 | 0 | 0 |  |
| de13d57636a9 | schulabschluss | persons | False | 3 | 417802 | 23185 | 0 | 0.05839 | 0.1336 | 2.288 |
| de13d57636a9 | beruflabschluss | persons | False | 3 | 417802 | 39986 | 0 | 0.09071 | 0.04784 | 0.5274 |
| de13d57636a9 | employed | persons | False | 1 | 485709 | 254894 | 0 | 0.5303 | 0.5306 | 1.001 |
| de13d57636a9 | household_size | households | True | 6 | 218097 | 0 | 0 | 0 | 0 |  |
| de13d57636a9 | household_type | households | True | 4 | 163844 | 0 | 0 | 0 | 0 |  |
| de13d57636a9 | tenure | households | True | 2 | 218097 | 5320 | 0 | 0.03504 | 0.003493 | 0.09968 |
| de13d57636a9 | building_type | households | True | 3 | 218097 | 8322 | 0 | 0.06185 | 3.941e-05 | 0.0006371 |
| de13d57636a9 | own_cars | households | True | 4 | 218097 | 0 | 0 | 0 | 0 |  |
| de13d57636a9 | own_bikes | households | True | 5 | 218097 | 0 | 0 | 0 | 0 |  |
| de13d57636a9 | economic_status | households | True | 5 | 218097 | 0 | 0 | 0 | 0 |  |
| de13d57636a9 | number_of_cars | households | True | 4 | 218097 | 0 | 0 | 0 | 0 |  |
| de13d57636a9 | number_of_bicycles | households | True | 5 | 218097 | 0 | 0 | 0 | 0 |  |
| de13d57636a9 | has_ebike | households | True | 2 | 218097 | 0 | 0 | 0 | 0 |  |

## Resolved seed records by raw code

(no rows)

## Control universe

| group | geography | target_sum | household_total | ratio |
|---|---|---|---|---|
| household_size | ZENSUS100m | 5.58e+05 | 5.582e+05 | 0.9997 |
| tenure | ZENSUS100m | 5.582e+05 | 5.582e+05 | 1 |
| building_type | ZENSUS100m | 6.098e+05 | 5.582e+05 | 1.093 |
| own_cars | ZENSUS1km | 5.581e+05 | 5.582e+05 | 0.9999 |
| own_bikes | ZENSUS1km | 5.583e+05 | 5.582e+05 | 1 |
| economic_status | KREIS | 5.582e+05 | 5.582e+05 | 1 |
| number_of_cars | KREIS | 5.582e+05 | 5.582e+05 | 1 |
| number_of_bicycles | KREIS | 5.582e+05 | 5.582e+05 | 1 |
| has_ebike | KREIS | 5.582e+05 | 5.582e+05 | 1 |

## Control fit per geography (region)

| scope | control | geography | importance | target_sum | synthetic_sum | abs_error | bias | wape |
|---|---|---|---|---|---|---|---|---|
| region_geography | ALL | ZENSUS100m |  | 4.273e+06 | 4.2e+06 | 8.438e+05 | -0.01719 | 0.1975 |
| region_geography | ALL | ZENSUS1km |  | 1.675e+06 | 1.675e+06 | 6.667e+04 | 0.0001851 | 0.03981 |
| region_geography | ALL | KREIS |  | 1.205e+07 | 1.206e+07 | 1.643e+05 | 0.0006276 | 0.01363 |

## Solver paths per level (summed over batches)

`non_simultaneous_rate` = `non_simultaneous / n_parents`: parents whose final weights did not come from the simultaneous solution (sequential, single feasible sub zone, or all-smart-rounded).

| level | n_parents | first_optimal | first_feasible | first_infeasible | first_not_solved | retry_optimal | retry_feasible | retry_infeasible | retry_not_solved | sequential_fallback | no_infeasible_subzones | single_feasible_subzone | all_subzones_infeasible | infeasible_subzones | non_simultaneous | non_simultaneous_rate |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| KREIS | 60 | 60 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| ZENSUS1km | 172 | 123 | 0 | 38 | 11 | 23 | 0 | 2 | 7 | 26 | 17 | 0 | 0 | 44 | 26 | 0.1512 |
| ZENSUS100m | 2233 | 911 | 0 | 1299 | 23 | 566 | 0 | 403 | 11 | 631 | 331 | 114 | 11 | 1675 | 756 | 0.3386 |

## Input consistency (20 largest shares)

`share` = contradiction `amount` / `base` of the check; Kreis rows compare the KREIS control table with the grid tables, cell rows are contradictions inside the 100 m cells (`braunschweig.popsim.input_consistency`); `ratio_reported` rows (survey ratio, no bound) are in `input_consistency.csv` only.

| batch | scope | key | check | cells | amount | base | share |
|---|---|---|---|---|---|---|---|
| batch_031 | kreis | 03158 | employed | 1 | 3.866 | 1 | 3.866 |
| batch_031 | kreis | 03158 | persons_all | 1 | 6.01 | 4 | 1.503 |
| batch_031 | kreis | 03158 | persons_14plus_high | 1 | 4.776 | 4 | 1.194 |
| batch_031 | kreis | 03158 | persons_18plus | 1 | 4.422 | 4 | 1.106 |
| batch_033 | kreis | 03158 | cars_0 | 1 | 2.852 | 3 | 0.9505 |
| batch_041 | kreis | 03103 | cars_0 | 1 | 27.46 | 30 | 0.9153 |
| batch_050 | kreis | 03103 | cars_0 | 1 | 68.48 | 77 | 0.8893 |
| batch_043 | kreis | 03157 | cars_0 | 1 | 7.05 | 8 | 0.8813 |
| batch_038 | kreis | 03101 | cars_0 | 1 | 60.14 | 73 | 0.8238 |
| batch_051 | kreis | 03103 | cars_0 | 1 | 59.71 | 73 | 0.818 |
| batch_040 | kreis | 03103 | cars_0 | 1 | 60.36 | 77 | 0.7839 |
| batch_036 | kreis | 03151 | persons_0_17 | 1 | 6.033 | 8 | 0.7541 |
| batch_000 | cell | all | building_type_partition | 509 | 4770 | 6629 | 0.7196 |
| batch_048 | kreis | 03154 | cars_0 | 1 | 27.31 | 38 | 0.7187 |
| batch_041 | kreis | 03154 | cars_0 | 1 | 20.59 | 29 | 0.7101 |
| batch_020 | kreis | 03102 | cars_0 | 1 | 16.29 | 24 | 0.6789 |
| batch_038 | kreis | 03103 | cars_0 | 1 | 41.37 | 64 | 0.6464 |
| batch_025 | kreis | 03157 | persons_0_17 | 1 | 22.94 | 36 | 0.6372 |
| batch_024 | kreis | 03158 | cars_0 | 1 | 37.57 | 59 | 0.6367 |
| batch_037 | kreis | 03101 | cars_0 | 1 | 94.1 | 149 | 0.6316 |

## Household composition

Synthetic households with the members of their seed household; adult = age >= 18. Definitions: `braunschweig.analysis.popsim_batch_diagnostics.composition`.

| batch | households | households_with_minor | share_households_with_minor | one_adult_share_among_minor_households | persons_0_17_per_minor_household | share_persons_0_9_with_one_adult | share_persons_10_14_with_one_adult | minor_households_without_adult |
|---|---|---|---|---|---|---|---|---|
| batch_000 | 6724 | 955 | 0.142 | 0.2869 | 1.403 | 0.2265 | 0.224 | 3 |
| batch_001 | 6377 | 1317 | 0.2065 | 0.268 | 1.159 | 0.3004 | 0.3399 | 0 |
| batch_002 | 8710 | 1764 | 0.2025 | 0.2976 | 1.165 | 0.329 | 0.3842 | 0 |
| batch_003 | 9402 | 2139 | 0.2275 | 0.2903 | 1.104 | 0.3735 | 0.3875 | 0 |
| batch_004 | 10899 | 2547 | 0.2337 | 0.2878 | 1.087 | 0.386 | 0.3307 | 0 |
| batch_005 | 10457 | 2444 | 0.2337 | 0.277 | 1.079 | 0.361 | 0.3297 | 0 |
| batch_006 | 6713 | 1826 | 0.272 | 0.1731 | 1.026 | 0.2035 | 0.2054 | 0 |
| batch_007 | 5758 | 1646 | 0.2859 | 0.1501 | 1.019 | 0.2008 | 0.1192 | 0 |
| batch_008 | 6460 | 1908 | 0.2954 | 0.2055 | 1.057 | 0.2704 | 0.279 | 0 |
| batch_009 | 10295 | 2795 | 0.2715 | 0.2365 | 1.292 | 0.283 | 0.2737 | 0 |
| batch_010 | 6792 | 2137 | 0.3146 | 0.2475 | 1.157 | 0.3053 | 0.3592 | 0 |
| batch_011 | 4694 | 1472 | 0.3136 | 0.1977 | 1.132 | 0.2392 | 0.339 | 0 |
| batch_012 | 7319 | 2139 | 0.2923 | 0.2221 | 1.194 | 0.2505 | 0.2933 | 0 |
| batch_013 | 7106 | 2461 | 0.3463 | 0.1894 | 1.174 | 0.2441 | 0.2929 | 0 |
| batch_014 | 10919 | 3511 | 0.3215 | 0.2293 | 1.171 | 0.3094 | 0.2979 | 0 |
| batch_015 | 12169 | 3395 | 0.279 | 0.2457 | 1.222 | 0.2864 | 0.3129 | 0 |
| batch_016 | 11203 | 3106 | 0.2772 | 0.2254 | 1.18 | 0.2549 | 0.2644 | 0 |
| batch_017 | 16613 | 4058 | 0.2443 | 0.2829 | 1.315 | 0.2924 | 0.3016 | 0 |
| batch_018 | 10568 | 3072 | 0.2907 | 0.2028 | 1.179 | 0.2331 | 0.263 | 0 |
| batch_019 | 8270 | 2440 | 0.295 | 0.2086 | 1.203 | 0.2386 | 0.2562 | 0 |
| batch_020 | 7496 | 2573 | 0.3432 | 0.1741 | 1.107 | 0.2462 | 0.2627 | 0 |
| batch_021 | 8621 | 2901 | 0.3365 | 0.1493 | 1.068 | 0.2349 | 0.2195 | 0 |
| batch_022 | 11245 | 2954 | 0.2627 | 0.215 | 1.156 | 0.2494 | 0.2892 | 0 |
| batch_023 | 11710 | 3365 | 0.2874 | 0.2672 | 1.104 | 0.3455 | 0.3746 | 0 |
| batch_024 | 10016 | 3070 | 0.3065 | 0.2459 | 1.093 | 0.3184 | 0.339 | 0 |
| batch_025 | 16641 | 4722 | 0.2838 | 0.2736 | 1.08 | 0.376 | 0.3533 | 0 |
| batch_026 | 13533 | 3568 | 0.2637 | 0.2466 | 1.212 | 0.2617 | 0.3294 | 0 |
| batch_027 | 10165 | 2678 | 0.2635 | 0.227 | 1.193 | 0.2679 | 0.355 | 0 |
| batch_028 | 26906 | 5164 | 0.1919 | 0.2734 | 1.356 | 0.2515 | 0.2968 | 6 |
| batch_029 | 6992 | 2457 | 0.3514 | 0.1518 | 1.103 | 0.2065 | 0.2587 | 0 |
| batch_030 | 25649 | 4699 | 0.1832 | 0.2628 | 1.405 | 0.2042 | 0.2339 | 14 |
| batch_031 | 13484 | 3129 | 0.2321 | 0.2336 | 1.225 | 0.2377 | 0.2935 | 0 |
| batch_032 | 13464 | 3438 | 0.2553 | 0.2248 | 1.175 | 0.2511 | 0.2995 | 0 |
| batch_033 | 7040 | 2038 | 0.2895 | 0.184 | 1.117 | 0.2118 | 0.267 | 0 |
| batch_034 | 9359 | 2920 | 0.312 | 0.1997 | 1.137 | 0.2671 | 0.3398 | 0 |
| batch_035 | 9841 | 3034 | 0.3083 | 0.2238 | 1.164 | 0.2736 | 0.3398 | 0 |
| batch_036 | 12115 | 3638 | 0.3003 | 0.2653 | 1.164 | 0.3406 | 0.3491 | 0 |
| batch_037 | 7268 | 2514 | 0.3459 | 0.1822 | 1.103 | 0.2202 | 0.2409 | 2 |
| batch_038 | 6630 | 2506 | 0.378 | 0.1732 | 1.082 | 0.2336 | 0.2394 | 0 |
| batch_039 | 6135 | 2079 | 0.3389 | 0.1621 | 1.125 | 0.1806 | 0.2971 | 0 |
| batch_040 | 5416 | 1933 | 0.3569 | 0.1686 | 1.098 | 0.1919 | 0.2841 | 0 |
| batch_041 | 5496 | 1943 | 0.3535 | 0.1688 | 1.154 | 0.2022 | 0.2698 | 0 |
| batch_042 | 7160 | 2479 | 0.3462 | 0.1815 | 1.134 | 0.2253 | 0.2658 | 0 |
| batch_043 | 11205 | 3961 | 0.3535 | 0.2068 | 1.13 | 0.273 | 0.2775 | 0 |
| batch_044 | 8593 | 2566 | 0.2986 | 0.1988 | 1.211 | 0.2079 | 0.3084 | 0 |
| batch_045 | 14526 | 3501 | 0.241 | 0.2745 | 1.327 | 0.2683 | 0.3046 | 0 |
| batch_046 | 14882 | 3628 | 0.2438 | 0.2745 | 1.328 | 0.2526 | 0.2837 | 0 |
| batch_047 | 6625 | 2273 | 0.3431 | 0.1777 | 1.15 | 0.2248 | 0.2773 | 0 |
| batch_048 | 10149 | 2912 | 0.2869 | 0.2301 | 1.232 | 0.2587 | 0.3146 | 0 |
| batch_049 | 7999 | 2569 | 0.3212 | 0.2199 | 1.17 | 0.2452 | 0.2903 | 0 |
| batch_050 | 8859 | 3173 | 0.3582 | 0.1759 | 1.172 | 0.2283 | 0.2769 | 0 |
| batch_051 | 9047 | 2822 | 0.3119 | 0.213 | 1.245 | 0.2625 | 0.2985 | 0 |
| batch_052 | 6279 | 2462 | 0.3921 | 0.1413 | 1.124 | 0.1927 | 0.2304 | 0 |
| batch_053 | 5593 | 2170 | 0.388 | 0.1452 | 1.133 | 0.1946 | 0.192 | 0 |
| batch_054 | 4890 | 1616 | 0.3305 | 0.1801 | 1.209 | 0.2207 | 0.3197 | 0 |
| batch_055 | 4324 | 1588 | 0.3673 | 0.1486 | 1.14 | 0.1726 | 0.2331 | 0 |
| batch_056 | 4949 | 1903 | 0.3845 | 0.1713 | 1.159 | 0.2462 | 0.3031 | 1 |
| batch_057 | 3556 | 1211 | 0.3406 | 0.2048 | 1.121 | 0.2312 | 0.3145 | 0 |
| batch_058 | 4349 | 1327 | 0.3051 | 0.2404 | 1.228 | 0.2745 | 0.3082 | 0 |
| batch_059 | 2619 | 789 | 0.3013 | 0.2548 | 1.207 | 0.2914 | 0.4057 | 0 |

## Warnings

| check | subject | value | threshold |
|---|---|---|---|
| weight_class_gap | <1 | -26.97 | 5 |
| weight_class_gap | 1-2 | -13.63 | 5 |
| weight_class_gap | 3-5 | 6.612 | 5 |
| weight_class_gap | 5-8 | 12.36 | 5 |
| weight_class_gap | 8+ | 24.12 | 5 |
| coverage_ratio | age_sex | 7.21 | 2 |
| coverage_ratio | employed_grid | 4.297 | 2 |
| coverage_ratio | schulabschluss | 2.288 | 2 |
| coverage_ratio | tenure | 0.09968 | 0.5 |
| coverage_ratio | building_type | 0.0006371 | 0.5 |
| universe_ratio | building_type/ZENSUS100m | 1.093 | 0.02 |
| sequential_fallback_rate | ZENSUS1km | 0.1512 | 0.05 |
| sequential_fallback_rate | ZENSUS100m | 0.3386 | 0.05 |
