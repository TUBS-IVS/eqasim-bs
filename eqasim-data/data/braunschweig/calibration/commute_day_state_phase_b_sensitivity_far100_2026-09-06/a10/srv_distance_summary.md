# SrV primary-distance baseline

Parameters: detour_factor=1.3, emd_threshold=0.08, min_persons=200, aggregate_requires_min_persons=True, max_unmatched_home_share=0.05, warn_unmatched_destination_share=0.3, sensitivity_thresholds=[0.06, 0.08, 0.1], sampling_rate=1.0, commute_day_state_enabled=True
Generated at: 2026-09-06T15:37:31.478236+00:00

Model = realised euclidean home->activity distance x detour factor; reference = SrV 2023
(GIS routed, person-level, GEWICHT_W_ZENSUS, shrunk shares). Classification per the
pre-registered rule (braunschweig.calibration.decision).

## Worker universe of the work comparison (ADR-0104 ruling R5)

commute_by_kreis.csv is measured over the 296599 of 304900 workers with an assigned workplace
(97.28%) whose drawn reporting-day state is 'at_workplace', i.e. who actually
commuted on the simulated day. That is the universe the SrV reference bands are defined on (persons with a
work trip on the reporting day); a worker drawn to 'home' or 'absent' made no work trip today.
0 worker(s) have no state row at all and are excluded as unknown.
This reporting-day universe is applied to the WHOLE work frame before any output is derived from it, so it also
governs commute_quantiles_model.csv and the sensitivity variants (sensitivity_cells.csv / sensitivity.csv) -- not only the
pre-registered commute_by_kreis.csv comparison above.

commute_by_kreis_all_assigned.csv carries the SAME comparison over ALL 304900 workers with an assigned workplace
(the universe measured before this ruling). It is a diagnostic: the decisions in decisions.json are the
reporting-day ones.

## Work (per scope)
- **all**: build = True -- build: gap (EMD > 0.08 and > noise floor) in decisive cell(s) ['03157', '03158'] (Kreis with >= 200 reference persons, or the aggregate)
- **inter**: build = True -- build: gap (EMD > 0.08 and > noise floor) in decisive cell(s) ['03153', '03157', '03158'] (Kreis with >= 200 reference persons, or the aggregate)
- **intra**: build = False -- do not build: no gap in any decisive cell (4 Kreise with >= 200 persons; aggregate decisive with n=1950)

| scope | code | n_model | n_ref | EMD | noise floor | class |
|---|---|---|---|---|---|---|
| all | 03101 | 71042 | 1272 | 0.038 | 0.021 | ok |
| all | 03102 | 26431 | 387 | 0.020 | 0.037 | ok |
| all | 03103 | 33352 | 1659 | 0.076 | 0.019 | ok |
| all | 03151 | 44883 | 849 | 0.028 | 0.040 | ok |
| all | 03153 | 32515 | 570 | 0.079 | 0.024 | ok |
| all | 03154 | 22927 | 430 | 0.041 | 0.039 | ok |
| all | 03157 | 33926 | 581 | 0.081 | 0.027 | gap |
| all | 03158 | 31522 | 454 | 0.090 | 0.029 | gap |
| all | zgb | 296599 | 4543 | 0.037 | 0.012 | ok |
| inter | 03101 | 23562 | 353 | 0.058 | 0.025 | ok |
| inter | 03102 | 10643 | 156 | 0.036 | 0.041 | ok |
| inter | 03103 | 6763 | 509 | 0.079 | 0.022 | ok |
| inter | 03151 | 32892 | 670 | 0.043 | 0.036 | ok |
| inter | 03153 | 16331 | 295 | 0.137 | 0.028 | gap |
| inter | 03154 | 16374 | 348 | 0.050 | 0.038 | ok |
| inter | 03157 | 24047 | 430 | 0.100 | 0.024 | gap |
| inter | 03158 | 22994 | 341 | 0.081 | 0.029 | gap |
| inter | zgb | 153607 | 2593 | 0.069 | 0.013 | ok |
| intra | 03101 | 47480 | 919 | 0.010 | 0.010 | ok |
| intra | 03102 | 15788 | 231 | 0.020 | 0.025 | ok |
| intra | 03103 | 26589 | 1150 | 0.031 | 0.010 | ok |
| intra | 03151 | 11991 | 179 | 0.013 | 0.021 | ok |
| intra | 03153 | 16184 | 275 | 0.021 | 0.015 | ok |
| intra | 03154 | 6553 | 82 | 0.026 | 0.015 | ok |
| intra | 03157 | 9879 | 151 | 0.011 | 0.015 | ok |
| intra | 03158 | 8528 | 113 | 0.030 | 0.022 | ok |
| intra | zgb | 142992 | 1950 | 0.011 | 0.008 | ok |

## Education (per level)
- **kindergarten**: build = False -- do not build: no gap in any decisive cell (1 Kreise with >= 200 persons; aggregate decisive with n=647)
- **grundschule**: build = True -- build: gap (EMD > 0.08 and > noise floor) in decisive cell(s) ['zgb'] (Kreis with >= 200 reference persons, or the aggregate)
- **sekundar_1**: build = False -- do not build: no gap in any decisive cell (2 Kreise with >= 200 persons; aggregate decisive with n=718)
- **upper_secondary**: build = False -- do not build: no gap in any decisive cell (0 Kreise with >= 200 persons; aggregate decisive with n=360)
- **university**: build = UNDECIDABLE -- not decidable: no cell reaches the >= 200-person floor (aggregate n=142) and no decisive gap exists

| level | code | n_model | n_ref | EMD | noise floor | class |
|---|---|---|---|---|---|---|
| kindergarten | 03101 | 6920 | 188 | 0.030 | 0.037 | ok |
| kindergarten | 03102 | 3864 | 53 | 0.107 | 0.063 | gap |
| kindergarten | 03103 | 4464 | 241 | 0.011 | 0.032 | ok |
| kindergarten | 03151 | 7357 | 140 | 0.057 | 0.048 | ok |
| kindergarten | 03153 | 3131 | 68 | 0.177 | 0.056 | gap |
| kindergarten | 03154 | 3050 | 37 | 0.068 | 0.067 | ok |
| kindergarten | 03157 | 5031 | 94 | 0.092 | 0.047 | gap |
| kindergarten | 03158 | 4180 | 67 | 0.086 | 0.067 | gap |
| kindergarten | zgb | 37997 | 647 | 0.067 | 0.020 | ok |
| grundschule | 03101 | 8563 | 143 | 0.080 | 0.037 | gap |
| grundschule | 03102 | 4178 | 57 | 0.026 | 0.068 | ok |
| grundschule | 03103 | 4456 | 200 | 0.068 | 0.033 | ok |
| grundschule | 03151 | 6833 | 107 | 0.157 | 0.059 | gap |
| grundschule | 03153 | 3464 | 57 | 0.126 | 0.071 | gap |
| grundschule | 03154 | 2569 | 55 | 0.211 | 0.049 | gap |
| grundschule | 03157 | 5272 | 97 | 0.152 | 0.055 | gap |
| grundschule | 03158 | 4234 | 55 | 0.225 | 0.058 | gap |
| grundschule | zgb | 39569 | 571 | 0.116 | 0.021 | gap |
| sekundar_1 | 03101 | 9395 | 204 | 0.016 | 0.038 | ok |
| sekundar_1 | 03102 | 4518 | 75 | 0.059 | 0.058 | ok |
| sekundar_1 | 03103 | 5072 | 279 | 0.028 | 0.033 | ok |
| sekundar_1 | 03151 | 7529 | 126 | 0.040 | 0.053 | ok |
| sekundar_1 | 03153 | 4429 | 70 | 0.086 | 0.059 | gap |
| sekundar_1 | 03154 | 3487 | 79 | 0.063 | 0.092 | ok |
| sekundar_1 | 03157 | 6455 | 93 | 0.039 | 0.057 | ok |
| sekundar_1 | 03158 | 4779 | 71 | 0.093 | 0.060 | gap |
| sekundar_1 | zgb | 45664 | 718 | 0.025 | 0.023 | ok |
| upper_secondary | 03101 | 4485 | 92 | 0.060 | 0.057 | ok |
| upper_secondary | 03102 | 1941 | 28 | 0.147 | 0.109 | gap |
| upper_secondary | 03103 | 2371 | 120 | 0.070 | 0.053 | ok |
| upper_secondary | 03151 | 2514 | 76 | 0.060 | 0.101 | ok |
| upper_secondary | 03153 | 1378 | 26 | 0.084 | 0.080 | gap |
| upper_secondary | 03154 | 1236 | 40 | 0.176 | 0.134 | gap |
| upper_secondary | 03157 | 2463 | 57 | 0.122 | 0.073 | gap |
| upper_secondary | 03158 | 1733 | 41 | 0.162 | 0.063 | gap |
| upper_secondary | zgb | 18121 | 360 | 0.074 | 0.033 | ok |
| university | 03101 | 10586 | 74 | 0.130 | 0.090 | gap |
| university | 03102 | 2924 | 10 | 0.240 | 0.019 | gap |
| university | 03103 | 3926 | 84 | 0.482 | 0.096 | gap |
| university | 03151 | 4813 | 17 | 0.293 | 0.048 | gap |
| university | 03153 | 3674 | 8 | 0.247 | 0.279 | within_noise |
| university | 03154 | 2480 | 7 | 0.350 | 0.286 | gap |
| university | 03157 | 4730 | 13 | 0.295 | 0.028 | gap |
| university | 03158 | 4279 | 13 | 0.123 | 0.220 | within_noise |
| university | zgb | 37412 | 142 | 0.202 | 0.083 | gap |

## Sensitivity (not pre-registered)

The verdicts below are DIAGNOSTICS, not the pre-registered decision: they measure
how far the result could move if the reference's two documented caveats (polygon-external
destinations, GIS-invalid tail) were resolved differently, and how sensitive it is to the
EMD threshold. The pre-registered verdicts are the ones in the two sections above.

### Reference variants

- **inter_zgb** (model scope `inter_zgb`): build = False -- do not build: no gap in any decisive cell (7 Kreise with >= 200 persons; aggregate decisive with n=2301)
- **all_gis_fallback** (model scope `all`): build = True -- build: gap (EMD > 0.08 and > noise floor) in decisive cell(s) ['03103', '03158'] (Kreis with >= 200 reference persons, or the aggregate)
- **inter_gis_fallback** (model scope `inter`): build = True -- build: gap (EMD > 0.08 and > noise floor) in decisive cell(s) ['03153', '03157'] (Kreis with >= 200 reference persons, or the aggregate)

| variant | code | n_model | n_ref | EMD | noise floor | class |
|---|---|---|---|---|---|---|
| inter_zgb | 03101 | 17478 | 310 | 0.015 | 0.023 | ok |
| inter_zgb | 03102 | 8189 | 135 | 0.029 | 0.040 | ok |
| inter_zgb | 03103 | 4781 | 445 | 0.035 | 0.021 | ok |
| inter_zgb | 03151 | 28386 | 600 | 0.016 | 0.038 | ok |
| inter_zgb | 03153 | 10837 | 246 | 0.048 | 0.029 | ok |
| inter_zgb | 03154 | 14483 | 336 | 0.040 | 0.033 | ok |
| inter_zgb | 03157 | 15891 | 344 | 0.045 | 0.028 | ok |
| inter_zgb | 03158 | 20418 | 330 | 0.056 | 0.023 | ok |
| inter_zgb | zgb | 120463 | 2301 | 0.019 | 0.013 | ok |
| all_gis_fallback | 03101 | 71042 | 1419 | 0.032 | 0.019 | ok |
| all_gis_fallback | 03102 | 26431 | 466 | 0.019 | 0.032 | ok |
| all_gis_fallback | 03103 | 33352 | 1885 | 0.081 | 0.017 | gap |
| all_gis_fallback | 03151 | 44883 | 962 | 0.026 | 0.036 | ok |
| all_gis_fallback | 03153 | 32515 | 641 | 0.075 | 0.026 | ok |
| all_gis_fallback | 03154 | 22927 | 492 | 0.031 | 0.036 | ok |
| all_gis_fallback | 03157 | 33926 | 677 | 0.072 | 0.026 | ok |
| all_gis_fallback | 03158 | 31522 | 517 | 0.083 | 0.033 | gap |
| all_gis_fallback | zgb | 296599 | 5174 | 0.033 | 0.012 | ok |
| inter_gis_fallback | 03101 | 23562 | 398 | 0.055 | 0.022 | ok |
| inter_gis_fallback | 03102 | 10643 | 187 | 0.038 | 0.040 | ok |
| inter_gis_fallback | 03103 | 6763 | 585 | 0.079 | 0.021 | ok |
| inter_gis_fallback | 03151 | 32892 | 759 | 0.046 | 0.032 | ok |
| inter_gis_fallback | 03153 | 16331 | 332 | 0.130 | 0.028 | gap |
| inter_gis_fallback | 03154 | 16374 | 401 | 0.046 | 0.035 | ok |
| inter_gis_fallback | 03157 | 24047 | 503 | 0.092 | 0.023 | gap |
| inter_gis_fallback | 03158 | 22994 | 389 | 0.075 | 0.028 | ok |
| inter_gis_fallback | zgb | 153607 | 2969 | 0.064 | 0.012 | ok |

### EMD threshold sweep

| kind | name | threshold | build | undecidable | gap codes |
|---|---|---|---|---|---|
| scope | all | 0.06 | True | False | 03103;03153;03157;03158 |
| scope | inter | 0.06 | True | False | 03103;03153;03157;03158;zgb |
| scope | intra | 0.06 | False | False | - |
| level | kindergarten | 0.06 | True | False | zgb |
| level | grundschule | 0.06 | True | False | 03103;zgb |
| level | sekundar_1 | 0.06 | False | False | - |
| level | upper_secondary | 0.06 | True | False | zgb |
| level | university | 0.06 | False | True | - |
| variant | inter_zgb | 0.06 | False | False | - |
| variant | all_gis_fallback | 0.06 | True | False | 03103;03153;03157;03158 |
| variant | inter_gis_fallback | 0.06 | True | False | 03103;03153;03157;03158;zgb |
| scope | all | 0.08 | True | False | 03157;03158 |
| scope | inter | 0.08 | True | False | 03153;03157;03158 |
| scope | intra | 0.08 | False | False | - |
| level | kindergarten | 0.08 | False | False | - |
| level | grundschule | 0.08 | True | False | zgb |
| level | sekundar_1 | 0.08 | False | False | - |
| level | upper_secondary | 0.08 | False | False | - |
| level | university | 0.08 | False | True | - |
| variant | inter_zgb | 0.08 | False | False | - |
| variant | all_gis_fallback | 0.08 | True | False | 03103;03158 |
| variant | inter_gis_fallback | 0.08 | True | False | 03153;03157 |
| scope | all | 0.10 | False | False | - |
| scope | inter | 0.10 | True | False | 03153;03157 |
| scope | intra | 0.10 | False | False | - |
| level | kindergarten | 0.10 | False | False | - |
| level | grundschule | 0.10 | True | False | zgb |
| level | sekundar_1 | 0.10 | False | False | - |
| level | upper_secondary | 0.10 | False | False | - |
| level | university | 0.10 | False | True | - |
| variant | inter_zgb | 0.10 | False | False | - |
| variant | all_gis_fallback | 0.10 | False | False | - |
| variant | inter_gis_fallback | 0.10 | True | False | 03153 |

Known quirk: the ZGB aggregate row's n_model includes persons whose home Kreis could not be resolved (see the module docstring); on the committed 100% baseline this is exactly one worker, so sum(kreis n_model) == aggregate n_model - 1 for the work 'all' scope -- expected, not a join defect.
