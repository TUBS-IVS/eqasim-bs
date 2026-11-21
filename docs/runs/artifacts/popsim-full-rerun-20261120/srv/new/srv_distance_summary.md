# SrV primary-distance baseline

Parameters: detour_factor=1.3, emd_threshold=0.08, min_persons=200, aggregate_requires_min_persons=True, max_unmatched_home_share=0.05, warn_unmatched_destination_share=0.3, sensitivity_thresholds=[0.06, 0.08, 0.1], sampling_rate=1.0, commute_day_state_enabled=True
Generated at: 2026-11-20T14:56:00.142426+00:00

Model = realised euclidean home->activity distance x detour factor; reference = SrV 2023
(GIS routed, person-level, GEWICHT_W_ZENSUS, shrunk shares). Classification per the
pre-registered rule (braunschweig.calibration.decision).

## Worker universe of the work comparison (ADR-0104 ruling R5)

commute_by_kreis.csv is measured over the 381638 of 394425 workers with an assigned workplace
(96.76%) whose drawn reporting-day state is 'at_workplace', i.e. who actually
commuted on the simulated day. That is the universe the SrV reference bands are defined on (persons with a
work trip on the reporting day); a worker drawn to 'home' or 'absent' made no work trip today.
0 worker(s) have no state row at all and are excluded as unknown.
This reporting-day universe is applied to the WHOLE work frame before any output is derived from it, so it also
governs commute_quantiles_model.csv and the sensitivity variants (sensitivity_cells.csv / sensitivity.csv) -- not only the
pre-registered commute_by_kreis.csv comparison above.

commute_by_kreis_all_assigned.csv carries the SAME comparison over ALL 394425 workers with an assigned workplace
(the universe measured before this ruling). It is a diagnostic: the decisions in decisions.json are the
reporting-day ones.

## Work (per scope)
- **all**: build = True -- build: gap (EMD > 0.08 and > noise floor) in decisive cell(s) ['03158'] (Kreis with >= 200 reference persons, or the aggregate)
- **inter**: build = True -- build: gap (EMD > 0.08 and > noise floor) in decisive cell(s) ['03103', '03153', '03157', '03158'] (Kreis with >= 200 reference persons, or the aggregate)
- **intra**: build = False -- do not build: no gap in any decisive cell (4 Kreise with >= 200 persons; aggregate decisive with n=1950)

| scope | code | n_model | n_ref | EMD | noise floor | class |
|---|---|---|---|---|---|---|
| all | 03101 | 90757 | 1272 | 0.031 | 0.021 | ok |
| all | 03102 | 33186 | 387 | 0.036 | 0.037 | ok |
| all | 03103 | 42665 | 1659 | 0.075 | 0.019 | ok |
| all | 03151 | 58181 | 849 | 0.027 | 0.040 | ok |
| all | 03153 | 41764 | 570 | 0.074 | 0.024 | ok |
| all | 03154 | 29720 | 430 | 0.043 | 0.039 | ok |
| all | 03157 | 44114 | 581 | 0.079 | 0.027 | ok |
| all | 03158 | 41250 | 454 | 0.091 | 0.029 | gap |
| all | zgb | 381638 | 4543 | 0.035 | 0.012 | ok |
| inter | 03101 | 29987 | 353 | 0.063 | 0.025 | ok |
| inter | 03102 | 13220 | 156 | 0.065 | 0.041 | ok |
| inter | 03103 | 8637 | 509 | 0.086 | 0.022 | gap |
| inter | 03151 | 42763 | 670 | 0.040 | 0.036 | ok |
| inter | 03153 | 20795 | 295 | 0.130 | 0.028 | gap |
| inter | 03154 | 21154 | 348 | 0.053 | 0.038 | ok |
| inter | 03157 | 31171 | 430 | 0.099 | 0.024 | gap |
| inter | 03158 | 30053 | 341 | 0.082 | 0.029 | gap |
| inter | zgb | 197781 | 2593 | 0.064 | 0.013 | ok |
| intra | 03101 | 60770 | 919 | 0.013 | 0.010 | ok |
| intra | 03102 | 19966 | 231 | 0.024 | 0.025 | ok |
| intra | 03103 | 34028 | 1150 | 0.028 | 0.010 | ok |
| intra | 03151 | 15418 | 179 | 0.012 | 0.021 | ok |
| intra | 03153 | 20969 | 275 | 0.022 | 0.015 | ok |
| intra | 03154 | 8566 | 82 | 0.020 | 0.015 | ok |
| intra | 03157 | 12943 | 151 | 0.012 | 0.015 | ok |
| intra | 03158 | 11197 | 113 | 0.023 | 0.022 | ok |
| intra | zgb | 183857 | 1950 | 0.009 | 0.008 | ok |

## Education (per level)
- **kindergarten**: build = False -- do not build: no gap in any decisive cell (1 Kreise with >= 200 persons; aggregate decisive with n=647)
- **grundschule**: build = True -- build: gap (EMD > 0.08 and > noise floor) in decisive cell(s) ['zgb'] (Kreis with >= 200 reference persons, or the aggregate)
- **sekundar_1**: build = False -- do not build: no gap in any decisive cell (2 Kreise with >= 200 persons; aggregate decisive with n=718)
- **upper_secondary**: build = True -- build: gap (EMD > 0.08 and > noise floor) in decisive cell(s) ['zgb'] (Kreis with >= 200 reference persons, or the aggregate)
- **university**: build = UNDECIDABLE -- not decidable: no cell reaches the >= 200-person floor (aggregate n=142) and no decisive gap exists

| level | code | n_model | n_ref | EMD | noise floor | class |
|---|---|---|---|---|---|---|
| kindergarten | 03101 | 8682 | 188 | 0.055 | 0.037 | ok |
| kindergarten | 03102 | 4321 | 53 | 0.060 | 0.063 | ok |
| kindergarten | 03103 | 5345 | 241 | 0.080 | 0.032 | ok |
| kindergarten | 03151 | 7427 | 140 | 0.038 | 0.048 | ok |
| kindergarten | 03153 | 4231 | 68 | 0.065 | 0.056 | ok |
| kindergarten | 03154 | 3439 | 37 | 0.039 | 0.067 | ok |
| kindergarten | 03157 | 5884 | 94 | 0.047 | 0.047 | ok |
| kindergarten | 03158 | 4091 | 67 | 0.039 | 0.067 | ok |
| kindergarten | zgb | 43420 | 647 | 0.021 | 0.020 | ok |
| grundschule | 03101 | 7885 | 143 | 0.076 | 0.037 | ok |
| grundschule | 03102 | 4042 | 57 | 0.023 | 0.068 | ok |
| grundschule | 03103 | 4802 | 200 | 0.070 | 0.033 | ok |
| grundschule | 03151 | 6751 | 107 | 0.144 | 0.059 | gap |
| grundschule | 03153 | 3407 | 57 | 0.148 | 0.071 | gap |
| grundschule | 03154 | 3211 | 55 | 0.180 | 0.049 | gap |
| grundschule | 03157 | 5331 | 97 | 0.146 | 0.055 | gap |
| grundschule | 03158 | 4024 | 55 | 0.185 | 0.058 | gap |
| grundschule | zgb | 39453 | 571 | 0.106 | 0.021 | gap |
| sekundar_1 | 03101 | 11417 | 204 | 0.016 | 0.038 | ok |
| sekundar_1 | 03102 | 5716 | 75 | 0.062 | 0.058 | ok |
| sekundar_1 | 03103 | 6420 | 279 | 0.027 | 0.033 | ok |
| sekundar_1 | 03151 | 9594 | 126 | 0.040 | 0.053 | ok |
| sekundar_1 | 03153 | 5825 | 70 | 0.078 | 0.059 | ok |
| sekundar_1 | 03154 | 4585 | 79 | 0.069 | 0.092 | ok |
| sekundar_1 | 03157 | 7795 | 93 | 0.036 | 0.057 | ok |
| sekundar_1 | 03158 | 6265 | 71 | 0.096 | 0.060 | gap |
| sekundar_1 | zgb | 57617 | 718 | 0.027 | 0.023 | ok |
| upper_secondary | 03101 | 5107 | 92 | 0.052 | 0.057 | ok |
| upper_secondary | 03102 | 2460 | 28 | 0.170 | 0.109 | gap |
| upper_secondary | 03103 | 2881 | 120 | 0.087 | 0.053 | gap |
| upper_secondary | 03151 | 3945 | 76 | 0.098 | 0.101 | within_noise |
| upper_secondary | 03153 | 2003 | 26 | 0.084 | 0.080 | gap |
| upper_secondary | 03154 | 1512 | 40 | 0.184 | 0.134 | gap |
| upper_secondary | 03157 | 3184 | 57 | 0.148 | 0.073 | gap |
| upper_secondary | 03158 | 2439 | 41 | 0.189 | 0.063 | gap |
| upper_secondary | zgb | 23531 | 360 | 0.088 | 0.033 | gap |
| university | 03101 | 10962 | 74 | 0.117 | 0.090 | gap |
| university | 03102 | 3263 | 10 | 0.194 | 0.019 | gap |
| university | 03103 | 4020 | 84 | 0.270 | 0.096 | gap |
| university | 03151 | 6054 | 17 | 0.157 | 0.048 | gap |
| university | 03153 | 2547 | 8 | 0.134 | 0.279 | within_noise |
| university | 03154 | 1261 | 7 | 0.193 | 0.286 | within_noise |
| university | 03157 | 4517 | 13 | 0.183 | 0.028 | gap |
| university | 03158 | 3000 | 13 | 0.124 | 0.220 | within_noise |
| university | zgb | 35624 | 142 | 0.115 | 0.083 | gap |

## Sensitivity (not pre-registered)

The verdicts below are DIAGNOSTICS, not the pre-registered decision: they measure
how far the result could move if the reference's two documented caveats (polygon-external
destinations, GIS-invalid tail) were resolved differently, and how sensitive it is to the
EMD threshold. The pre-registered verdicts are the ones in the two sections above.

### Reference variants

- **inter_zgb** (model scope `inter_zgb`): build = False -- do not build: no gap in any decisive cell (7 Kreise with >= 200 persons; aggregate decisive with n=2301)
- **all_gis_fallback** (model scope `all`): build = True -- build: gap (EMD > 0.08 and > noise floor) in decisive cell(s) ['03158'] (Kreis with >= 200 reference persons, or the aggregate)
- **inter_gis_fallback** (model scope `inter`): build = True -- build: gap (EMD > 0.08 and > noise floor) in decisive cell(s) ['03103', '03153', '03157'] (Kreis with >= 200 reference persons, or the aggregate)

| variant | code | n_model | n_ref | EMD | noise floor | class |
|---|---|---|---|---|---|---|
| inter_zgb | 03101 | 22223 | 310 | 0.042 | 0.023 | ok |
| inter_zgb | 03102 | 10202 | 135 | 0.069 | 0.040 | ok |
| inter_zgb | 03103 | 6090 | 445 | 0.027 | 0.021 | ok |
| inter_zgb | 03151 | 37186 | 600 | 0.016 | 0.038 | ok |
| inter_zgb | 03153 | 13787 | 246 | 0.032 | 0.029 | ok |
| inter_zgb | 03154 | 18729 | 336 | 0.043 | 0.033 | ok |
| inter_zgb | 03157 | 20595 | 344 | 0.046 | 0.028 | ok |
| inter_zgb | 03158 | 26659 | 330 | 0.055 | 0.023 | ok |
| inter_zgb | zgb | 155471 | 2301 | 0.013 | 0.013 | ok |
| all_gis_fallback | 03101 | 90757 | 1419 | 0.029 | 0.019 | ok |
| all_gis_fallback | 03102 | 33186 | 466 | 0.034 | 0.032 | ok |
| all_gis_fallback | 03103 | 42665 | 1885 | 0.080 | 0.017 | ok |
| all_gis_fallback | 03151 | 58181 | 962 | 0.024 | 0.036 | ok |
| all_gis_fallback | 03153 | 41764 | 641 | 0.070 | 0.026 | ok |
| all_gis_fallback | 03154 | 29720 | 492 | 0.032 | 0.036 | ok |
| all_gis_fallback | 03157 | 44114 | 677 | 0.070 | 0.026 | ok |
| all_gis_fallback | 03158 | 41250 | 517 | 0.085 | 0.033 | gap |
| all_gis_fallback | zgb | 381638 | 5174 | 0.030 | 0.012 | ok |
| inter_gis_fallback | 03101 | 29987 | 398 | 0.060 | 0.022 | ok |
| inter_gis_fallback | 03102 | 13220 | 187 | 0.058 | 0.040 | ok |
| inter_gis_fallback | 03103 | 8637 | 585 | 0.085 | 0.021 | gap |
| inter_gis_fallback | 03151 | 42763 | 759 | 0.044 | 0.032 | ok |
| inter_gis_fallback | 03153 | 20795 | 332 | 0.123 | 0.028 | gap |
| inter_gis_fallback | 03154 | 21154 | 401 | 0.049 | 0.035 | ok |
| inter_gis_fallback | 03157 | 31171 | 503 | 0.091 | 0.023 | gap |
| inter_gis_fallback | 03158 | 30053 | 389 | 0.076 | 0.028 | ok |
| inter_gis_fallback | zgb | 197781 | 2969 | 0.059 | 0.012 | ok |

### EMD threshold sweep

| kind | name | threshold | build | undecidable | gap codes |
|---|---|---|---|---|---|
| scope | all | 0.06 | True | False | 03103;03153;03157;03158 |
| scope | inter | 0.06 | True | False | 03101;03103;03153;03157;03158;zgb |
| scope | intra | 0.06 | False | False | - |
| level | kindergarten | 0.06 | True | False | 03103 |
| level | grundschule | 0.06 | True | False | 03103;zgb |
| level | sekundar_1 | 0.06 | False | False | - |
| level | upper_secondary | 0.06 | True | False | zgb |
| level | university | 0.06 | False | True | - |
| variant | inter_zgb | 0.06 | False | False | - |
| variant | all_gis_fallback | 0.06 | True | False | 03103;03153;03157;03158 |
| variant | inter_gis_fallback | 0.06 | True | False | 03103;03153;03157;03158 |
| scope | all | 0.08 | True | False | 03158 |
| scope | inter | 0.08 | True | False | 03103;03153;03157;03158 |
| scope | intra | 0.08 | False | False | - |
| level | kindergarten | 0.08 | False | False | - |
| level | grundschule | 0.08 | True | False | zgb |
| level | sekundar_1 | 0.08 | False | False | - |
| level | upper_secondary | 0.08 | True | False | zgb |
| level | university | 0.08 | False | True | - |
| variant | inter_zgb | 0.08 | False | False | - |
| variant | all_gis_fallback | 0.08 | True | False | 03158 |
| variant | inter_gis_fallback | 0.08 | True | False | 03103;03153;03157 |
| scope | all | 0.10 | False | False | - |
| scope | inter | 0.10 | True | False | 03153 |
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
