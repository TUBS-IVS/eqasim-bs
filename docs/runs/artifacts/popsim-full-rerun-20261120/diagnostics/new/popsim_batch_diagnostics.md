# PopulationSim batch diagnostics

- work directory: `~/popsim100_20261120/popsim_work`
- batches: 60
- seed groups: 1 (b69248d9b030: 60 batch(es))
- repository commit: `1041849c243c71859c95275447a24363ee1aa358`

## Reuse (region rows)

| scope | seed_group | subject | n_synthetic_households | n_seed_households | n_distinct_used | distinct_share_of_seed | kish_n_eff | max_copies | top100_share | top1000_share | top5000_share |
|---|---|---|---|---|---|---|---|---|---|---|---|
| region | b69248d9b030 | b69248d9b030 | 558179 | 2.181e+05 | 107940 | 0.4949 | 1.602e+04 | 624 | 0.04784 | 0.1665 | 0.3593 |

## Weight classes

| seed_group | weight_class | n_seed | seed_weight_share | synthetic_share | difference_pp |
|---|---|---|---|---|---|
| b69248d9b030 | <1 | 158006 | 0.2759 | 0.3055 | 2.957 |
| b69248d9b030 | 1-2 | 33521 | 0.2139 | 0.2161 | 0.2276 |
| b69248d9b030 | 2-3 | 11603 | 0.1296 | 0.124 | -0.5544 |
| b69248d9b030 | 3-5 | 8792 | 0.1535 | 0.1491 | -0.4353 |
| b69248d9b030 | 5-8 | 4027 | 0.1144 | 0.105 | -0.94 |
| b69248d9b030 | 8+ | 2148 | 0.1128 | 0.1002 | -1.255 |

## Control coverage

| seed_group | group | seed_table | exhaustive | n_categories | n_universe | n_uncovered | n_multi_hit | seed_weight_share | synthetic_share | ratio |
|---|---|---|---|---|---|---|---|---|---|---|
| b69248d9b030 | age_sex | persons | True | 22 | 485709 | 0 | 0 | 0 | 0 |  |
| b69248d9b030 | employed_grid | persons | True | 10 | 230762 | 0 | 0 | 0 | 0 |  |
| b69248d9b030 | trip_class | persons | True | 4 | 485709 | 0 | 0 | 0 | 0 |  |
| b69248d9b030 | employment_status | persons | True | 7 | 422056 | 0 | 0 | 0 | 0 |  |
| b69248d9b030 | pt_ticket_group4 | persons | True | 4 | 422056 | 0 | 0 | 0 | 0 |  |
| b69248d9b030 | leisure_participation | persons | True | 2 | 485709 | 0 | 0 | 0 | 0 |  |
| b69248d9b030 | escort_participation | persons | True | 2 | 485709 | 0 | 0 | 0 | 0 |  |
| b69248d9b030 | work_by_employment | persons | True | 4 | 422056 | 0 | 0 | 0 | 0 |  |
| b69248d9b030 | education_band | persons | True | 6 | 485709 | 0 | 0 | 0 | 0 |  |
| b69248d9b030 | schulabschluss | persons | False | 3 | 417802 | 23185 | 0 | 0.05839 | 0.1299 | 2.225 |
| b69248d9b030 | beruflabschluss | persons | False | 3 | 417802 | 39986 | 0 | 0.09071 | 0.03096 | 0.3413 |
| b69248d9b030 | employed | persons | False | 1 | 485709 | 254894 | 0 | 0.5303 | 0.5195 | 0.9796 |
| b69248d9b030 | household_size | households | True | 6 | 218097 | 0 | 0 | 0 | 0 |  |
| b69248d9b030 | household_minor | households | False | 2 | 218097 | 169869 | 0 | 0.7773 | 0.8084 | 1.04 |
| b69248d9b030 | tenure | households | True | 2 | 218097 | 0 | 0 | 0 | 0 |  |
| b69248d9b030 | building_type | households | True | 3 | 218097 | 0 | 0 | 0 | 0 |  |
| b69248d9b030 | own_cars | households | True | 4 | 218097 | 0 | 0 | 0 | 0 |  |
| b69248d9b030 | own_bikes | households | True | 5 | 218097 | 0 | 0 | 0 | 0 |  |
| b69248d9b030 | economic_status | households | True | 5 | 218097 | 0 | 0 | 0 | 0 |  |
| b69248d9b030 | number_of_cars | households | True | 4 | 218097 | 0 | 0 | 0 | 0 |  |
| b69248d9b030 | number_of_bicycles | households | True | 5 | 218097 | 0 | 0 | 0 | 0 |  |
| b69248d9b030 | has_ebike | households | True | 2 | 218097 | 0 | 0 | 0 | 0 |  |

## Resolved seed records by raw code

| seed_group | attribute | raw_codes | seed_table | n_records | seed_weight_share | synthetic_share | ratio |
|---|---|---|---|---|---|---|---|
| b69248d9b030 | HP_SEX_raw | 3,9 | persons | 880 | 0.002032 | 0.003628 | 1.785 |
| b69248d9b030 | H_MIETE_raw | not 1,2 | households | 5320 | 0.03504 | 0.02743 | 0.7829 |
| b69248d9b030 | haustyp_raw | 95 | households | 8322 | 0.06185 | 0.05424 | 0.8769 |

## Control universe

| group | geography | target_sum | household_total | ratio |
|---|---|---|---|---|
| household_size | ZENSUS100m | 5.58e+05 | 5.582e+05 | 0.9997 |
| tenure | ZENSUS100m | 5.582e+05 | 5.582e+05 | 1 |
| building_type | ZENSUS100m | 5.581e+05 | 5.582e+05 | 0.9999 |
| own_cars | ZENSUS1km | 5.581e+05 | 5.582e+05 | 0.9999 |
| own_bikes | ZENSUS1km | 5.583e+05 | 5.582e+05 | 1 |
| economic_status | KREIS | 5.582e+05 | 5.582e+05 | 1 |
| number_of_cars | KREIS | 5.581e+05 | 5.582e+05 | 0.9999 |
| number_of_bicycles | KREIS | 5.583e+05 | 5.582e+05 | 1 |
| has_ebike | KREIS | 5.582e+05 | 5.582e+05 | 1 |

## Control fit per geography (region)

| scope | control | geography | importance | target_sum | synthetic_sum | abs_error | bias | wape |
|---|---|---|---|---|---|---|---|---|
| region_geography | ALL | ZENSUS100m |  | 3.904e+06 | 3.905e+06 | 4.77e+05 | 0.0001278 | 0.1222 |
| region_geography | ALL | ZENSUS1km |  | 1.781e+06 | 1.781e+06 | 1e+04 | 0.0002852 | 0.005617 |
| region_geography | ALL | KREIS |  | 1.205e+07 | 1.205e+07 | 2.515e+04 | -0.0003246 | 0.002087 |

## Solver paths per level (summed over batches)

`non_simultaneous_rate` = `non_simultaneous / n_parents`: parents whose final weights did not come from the simultaneous solution (sequential, single feasible sub zone, or all-smart-rounded).

| level | n_parents | first_optimal | first_feasible | first_infeasible | first_not_solved | retry_optimal | retry_feasible | retry_infeasible | retry_not_solved | sequential_fallback | no_infeasible_subzones | single_feasible_subzone | all_subzones_infeasible | infeasible_subzones | non_simultaneous | non_simultaneous_rate |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| KREIS | 60 | 0 | 60 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| ZENSUS1km | 172 | 3 | 169 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| ZENSUS100m | 2233 | 244 | 1989 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |

## Input consistency (20 largest shares)

`share` = contradiction `amount` / `base` of the check; Kreis rows compare the KREIS control table with the grid tables, cell rows are contradictions inside the 100 m cells (`braunschweig.popsim.input_consistency`); `ratio_reported` rows (survey ratio, no bound) are in `input_consistency.csv` only.

| batch | scope | key | check | cells | amount | base | share |
|---|---|---|---|---|---|---|---|
| batch_031 | kreis | 03158 | persons_all | 1 | 6.01 | 4 | 1.503 |
| batch_031 | kreis | 03158 | persons_14plus_high | 1 | 4.802 | 4 | 1.201 |
| batch_031 | kreis | 03158 | persons_18plus | 1 | 4.474 | 4 | 1.118 |
| batch_021 | kreis | 03102 | persons_all | 1 | 4.994 | 14 | 0.3567 |
| batch_030 | kreis | 03157 | persons_0_17 | 1 | 1.68 | 6 | 0.28 |
| batch_036 | kreis | 03151 | persons_0_17 | 1 | 2.197 | 8 | 0.2746 |
| batch_021 | kreis | 03102 | persons_14plus_high | 1 | 3.676 | 14 | 0.2626 |
| batch_021 | kreis | 03102 | persons_18plus | 1 | 3.155 | 14 | 0.2253 |
| batch_032 | kreis | 03154 | persons_0_17 | 1 | 0.8124 | 4 | 0.2031 |
| batch_059 | cell | all | persons_above_size_maximum | 161 | 949 | 5730 | 0.1656 |
| batch_059 | cell | all | persons_below_size_minimum | 182 | 864 | 5730 | 0.1508 |
| batch_011 | cell | all | persons_above_size_maximum | 282 | 1547 | 1.029e+04 | 0.1504 |
| batch_057 | cell | all | persons_above_size_maximum | 236 | 1184 | 7945 | 0.149 |
| batch_054 | cell | all | persons_above_size_maximum | 265 | 1610 | 1.089e+04 | 0.1479 |
| batch_057 | cell | all | persons_below_size_minimum | 251 | 1143 | 7945 | 0.1439 |
| batch_055 | cell | all | persons_above_size_maximum | 246 | 1419 | 9978 | 0.1422 |
| batch_058 | cell | all | persons_above_size_maximum | 242 | 1244 | 9159 | 0.1358 |
| batch_058 | cell | all | persons_below_size_minimum | 254 | 1234 | 9159 | 0.1347 |
| batch_055 | cell | all | persons_below_size_minimum | 253 | 1306 | 9978 | 0.1309 |
| batch_001 | cell | all | persons_above_size_maximum | 247 | 1475 | 1.166e+04 | 0.1265 |

## Household composition

Synthetic households with the members of their seed household; adult = age >= 18. Definitions: `braunschweig.analysis.popsim_batch_diagnostics.composition`.

| batch | households | households_with_minor | share_households_with_minor | one_adult_share_among_minor_households | persons_0_17_per_minor_household | share_persons_0_9_with_one_adult | share_persons_10_14_with_one_adult | minor_households_without_adult |
|---|---|---|---|---|---|---|---|---|
| batch_000 | 6657 | 696 | 0.1046 | 0.2945 | 1.606 | 0.2477 | 0.3041 | 0 |
| batch_001 | 6377 | 868 | 0.1361 | 0.2765 | 1.767 | 0.2356 | 0.3051 | 2 |
| batch_002 | 8695 | 1181 | 0.1358 | 0.2964 | 1.76 | 0.2627 | 0.3061 | 1 |
| batch_003 | 9402 | 1352 | 0.1438 | 0.3107 | 1.677 | 0.2705 | 0.3398 | 0 |
| batch_004 | 10899 | 1707 | 0.1566 | 0.2882 | 1.712 | 0.2468 | 0.3186 | 0 |
| batch_005 | 10457 | 1633 | 0.1562 | 0.2964 | 1.678 | 0.266 | 0.3222 | 0 |
| batch_006 | 6713 | 1221 | 0.1819 | 0.199 | 1.706 | 0.1729 | 0.2275 | 1 |
| batch_007 | 5757 | 1086 | 0.1886 | 0.2357 | 1.68 | 0.1929 | 0.2767 | 0 |
| batch_008 | 6460 | 1181 | 0.1828 | 0.2252 | 1.631 | 0.1789 | 0.2799 | 0 |
| batch_009 | 10295 | 1785 | 0.1734 | 0.2325 | 1.757 | 0.2093 | 0.2458 | 0 |
| batch_010 | 6792 | 1417 | 0.2086 | 0.2484 | 1.87 | 0.2185 | 0.2849 | 1 |
| batch_011 | 4694 | 915 | 0.1949 | 0.1945 | 1.774 | 0.1507 | 0.2421 | 0 |
| batch_012 | 7319 | 1384 | 0.1891 | 0.2225 | 1.713 | 0.1969 | 0.2519 | 1 |
| batch_013 | 7106 | 1752 | 0.2466 | 0.1895 | 1.962 | 0.16 | 0.2269 | 1 |
| batch_014 | 10919 | 2362 | 0.2163 | 0.2218 | 1.897 | 0.1949 | 0.252 | 2 |
| batch_015 | 12169 | 2335 | 0.1919 | 0.2445 | 1.794 | 0.2224 | 0.2789 | 1 |
| batch_016 | 11203 | 2111 | 0.1884 | 0.2099 | 1.754 | 0.1804 | 0.2515 | 1 |
| batch_017 | 16613 | 2868 | 0.1726 | 0.265 | 1.768 | 0.2274 | 0.2774 | 1 |
| batch_018 | 10568 | 2028 | 0.1919 | 0.1958 | 1.699 | 0.1736 | 0.2404 | 1 |
| batch_019 | 8270 | 1650 | 0.1995 | 0.1879 | 1.801 | 0.1665 | 0.2289 | 0 |
| batch_020 | 7496 | 1782 | 0.2377 | 0.1897 | 1.762 | 0.1626 | 0.2484 | 0 |
| batch_021 | 8621 | 1902 | 0.2206 | 0.1709 | 1.605 | 0.1421 | 0.2174 | 0 |
| batch_022 | 11245 | 1984 | 0.1764 | 0.2011 | 1.638 | 0.18 | 0.2376 | 0 |
| batch_023 | 11710 | 2236 | 0.1909 | 0.2715 | 1.706 | 0.2492 | 0.3233 | 0 |
| batch_024 | 10016 | 1965 | 0.1962 | 0.2504 | 1.673 | 0.2332 | 0.2881 | 1 |
| batch_025 | 16629 | 3510 | 0.2111 | 0.2872 | 1.685 | 0.2611 | 0.3558 | 0 |
| batch_026 | 13533 | 2593 | 0.1916 | 0.2322 | 1.747 | 0.2011 | 0.2897 | 1 |
| batch_027 | 10165 | 1820 | 0.179 | 0.2126 | 1.696 | 0.1954 | 0.2478 | 0 |
| batch_028 | 26906 | 3844 | 0.1429 | 0.23 | 1.636 | 0.1958 | 0.3027 | 1 |
| batch_029 | 6992 | 1701 | 0.2433 | 0.1434 | 1.748 | 0.1249 | 0.1893 | 0 |
| batch_030 | 25649 | 3632 | 0.1416 | 0.2252 | 1.616 | 0.1897 | 0.2965 | 0 |
| batch_031 | 13484 | 2202 | 0.1633 | 0.1971 | 1.68 | 0.1656 | 0.2545 | 0 |
| batch_032 | 13464 | 2398 | 0.1781 | 0.2098 | 1.673 | 0.1772 | 0.2607 | 2 |
| batch_033 | 7040 | 1415 | 0.201 | 0.1717 | 1.655 | 0.153 | 0.2048 | 0 |
| batch_034 | 9359 | 1910 | 0.2041 | 0.2031 | 1.804 | 0.1692 | 0.2838 | 1 |
| batch_035 | 9841 | 2117 | 0.2151 | 0.2409 | 1.842 | 0.2142 | 0.3049 | 0 |
| batch_036 | 12115 | 2406 | 0.1986 | 0.2502 | 1.701 | 0.2187 | 0.3097 | 0 |
| batch_037 | 7268 | 1653 | 0.2274 | 0.1676 | 1.697 | 0.1485 | 0.2275 | 0 |
| batch_038 | 6630 | 1637 | 0.2469 | 0.1668 | 1.752 | 0.1477 | 0.2036 | 0 |
| batch_039 | 6135 | 1423 | 0.2319 | 0.168 | 1.713 | 0.1575 | 0.2162 | 0 |
| batch_040 | 5416 | 1278 | 0.236 | 0.1479 | 1.702 | 0.1284 | 0.1818 | 0 |
| batch_041 | 5496 | 1293 | 0.2353 | 0.1624 | 1.707 | 0.1476 | 0.2087 | 0 |
| batch_042 | 7160 | 1625 | 0.227 | 0.1618 | 1.746 | 0.142 | 0.2064 | 0 |
| batch_043 | 11205 | 2762 | 0.2465 | 0.1879 | 1.887 | 0.1649 | 0.229 | 0 |
| batch_044 | 8593 | 1780 | 0.2071 | 0.1865 | 1.731 | 0.1532 | 0.2461 | 0 |
| batch_045 | 14526 | 2403 | 0.1654 | 0.2551 | 1.677 | 0.2229 | 0.2844 | 0 |
| batch_046 | 14882 | 2541 | 0.1707 | 0.2432 | 1.752 | 0.2138 | 0.2694 | 1 |
| batch_047 | 6625 | 1475 | 0.2226 | 0.1675 | 1.855 | 0.1453 | 0.2159 | 0 |
| batch_048 | 10149 | 1939 | 0.1911 | 0.1913 | 1.739 | 0.17 | 0.2154 | 1 |
| batch_049 | 7999 | 1730 | 0.2163 | 0.1913 | 1.723 | 0.1657 | 0.2248 | 0 |
| batch_050 | 8859 | 2158 | 0.2436 | 0.1867 | 1.824 | 0.1608 | 0.2247 | 0 |
| batch_051 | 9047 | 1814 | 0.2005 | 0.2018 | 1.907 | 0.1844 | 0.2285 | 1 |
| batch_052 | 6279 | 1694 | 0.2698 | 0.1405 | 1.821 | 0.1247 | 0.1898 | 0 |
| batch_053 | 5593 | 1407 | 0.2516 | 0.15 | 1.828 | 0.1255 | 0.1898 | 2 |
| batch_054 | 4890 | 1049 | 0.2145 | 0.1792 | 1.832 | 0.1702 | 0.2111 | 1 |
| batch_055 | 4324 | 1001 | 0.2315 | 0.1678 | 1.796 | 0.1412 | 0.2361 | 0 |
| batch_056 | 4949 | 1180 | 0.2384 | 0.1661 | 1.968 | 0.1462 | 0.2148 | 1 |
| batch_057 | 3556 | 824 | 0.2317 | 0.1748 | 1.725 | 0.1661 | 0.208 | 0 |
| batch_058 | 4349 | 838 | 0.1927 | 0.2375 | 1.87 | 0.2105 | 0.2725 | 1 |
| batch_059 | 2619 | 506 | 0.1932 | 0.2589 | 1.98 | 0.2425 | 0.3116 | 1 |

## Warnings

| check | subject | value | threshold |
|---|---|---|---|
| coverage_ratio | schulabschluss | 2.225 | 2 |
| coverage_ratio | beruflabschluss | 0.3413 | 0.5 |
