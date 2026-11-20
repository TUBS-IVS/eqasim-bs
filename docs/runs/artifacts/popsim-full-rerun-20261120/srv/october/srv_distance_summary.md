# SrV primary-distance baseline

Parameters: detour_factor=1.3, emd_threshold=0.08, min_persons=200, aggregate_requires_min_persons=True, max_unmatched_home_share=0.05, warn_unmatched_destination_share=0.3, sensitivity_thresholds=[0.06, 0.08, 0.1], sampling_rate=1.0, commute_day_state_enabled=True
Generated at: 2026-10-31T23:00:50.657872+00:00

Model = realised euclidean home->activity distance x detour factor; reference = SrV 2023
(GIS routed, person-level, GEWICHT_W_ZENSUS, shrunk shares). Classification per the
pre-registered rule (braunschweig.calibration.decision).

## Worker universe of the work comparison (ADR-0104 ruling R5)

commute_by_kreis.csv is measured over the 377271 of 389000 workers with an assigned workplace
(96.98%) whose drawn reporting-day state is 'at_workplace', i.e. who actually
commuted on the simulated day. That is the universe the SrV reference bands are defined on (persons with a
work trip on the reporting day); a worker drawn to 'home' or 'absent' made no work trip today.
0 worker(s) have no state row at all and are excluded as unknown.
This reporting-day universe is applied to the WHOLE work frame before any output is derived from it, so it also
governs commute_quantiles_model.csv and the sensitivity variants (sensitivity_cells.csv / sensitivity.csv) -- not only the
pre-registered commute_by_kreis.csv comparison above.

commute_by_kreis_all_assigned.csv carries the SAME comparison over ALL 389000 workers with an assigned workplace
(the universe measured before this ruling). It is a diagnostic: the decisions in decisions.json are the
reporting-day ones.

## Work (per scope)
- **all**: build = True -- build: gap (EMD > 0.08 and > noise floor) in decisive cell(s) ['03157', '03158'] (Kreis with >= 200 reference persons, or the aggregate)
- **inter**: build = True -- build: gap (EMD > 0.08 and > noise floor) in decisive cell(s) ['03103', '03153', '03157', '03158'] (Kreis with >= 200 reference persons, or the aggregate)
- **intra**: build = False -- do not build: no gap in any decisive cell (4 Kreise with >= 200 persons; aggregate decisive with n=1950)

| scope | code | n_model | n_ref | EMD | noise floor | class |
|---|---|---|---|---|---|---|
| all | 03101 | 88348 | 1272 | 0.042 | 0.021 | ok |
| all | 03102 | 35006 | 387 | 0.029 | 0.037 | ok |
| all | 03103 | 39674 | 1659 | 0.075 | 0.019 | ok |
| all | 03151 | 58653 | 849 | 0.026 | 0.040 | ok |
| all | 03153 | 41320 | 570 | 0.075 | 0.024 | ok |
| all | 03154 | 28795 | 430 | 0.043 | 0.039 | ok |
| all | 03157 | 43388 | 581 | 0.080 | 0.027 | gap |
| all | 03158 | 42087 | 454 | 0.091 | 0.029 | gap |
| all | zgb | 377271 | 4543 | 0.039 | 0.012 | ok |
| inter | 03101 | 29536 | 353 | 0.056 | 0.025 | ok |
| inter | 03102 | 13947 | 156 | 0.056 | 0.041 | ok |
| inter | 03103 | 8062 | 509 | 0.083 | 0.022 | gap |
| inter | 03151 | 43017 | 670 | 0.042 | 0.036 | ok |
| inter | 03153 | 20685 | 295 | 0.129 | 0.028 | gap |
| inter | 03154 | 20424 | 348 | 0.054 | 0.038 | ok |
| inter | 03157 | 30636 | 430 | 0.100 | 0.024 | gap |
| inter | 03158 | 30628 | 341 | 0.082 | 0.029 | gap |
| inter | zgb | 196935 | 2593 | 0.067 | 0.013 | ok |
| intra | 03101 | 58812 | 919 | 0.019 | 0.010 | ok |
| intra | 03102 | 21059 | 231 | 0.021 | 0.025 | ok |
| intra | 03103 | 31612 | 1150 | 0.029 | 0.010 | ok |
| intra | 03151 | 15636 | 179 | 0.010 | 0.021 | ok |
| intra | 03153 | 20635 | 275 | 0.023 | 0.015 | ok |
| intra | 03154 | 8371 | 82 | 0.022 | 0.015 | ok |
| intra | 03157 | 12752 | 151 | 0.008 | 0.015 | ok |
| intra | 03158 | 11459 | 113 | 0.023 | 0.022 | ok |
| intra | zgb | 180336 | 1950 | 0.010 | 0.008 | ok |

## Education (per level)
- **kindergarten**: build = False -- do not build: no gap in any decisive cell (1 Kreise with >= 200 persons; aggregate decisive with n=647)
- **grundschule**: build = True -- build: gap (EMD > 0.08 and > noise floor) in decisive cell(s) ['zgb'] (Kreis with >= 200 reference persons, or the aggregate)
- **sekundar_1**: build = False -- do not build: no gap in any decisive cell (2 Kreise with >= 200 persons; aggregate decisive with n=718)
- **upper_secondary**: build = False -- do not build: no gap in any decisive cell (0 Kreise with >= 200 persons; aggregate decisive with n=360)
- **university**: build = UNDECIDABLE -- not decidable: no cell reaches the >= 200-person floor (aggregate n=142) and no decisive gap exists

| level | code | n_model | n_ref | EMD | noise floor | class |
|---|---|---|---|---|---|---|
| kindergarten | 03101 | 8662 | 188 | 0.027 | 0.037 | ok |
| kindergarten | 03102 | 4321 | 53 | 0.104 | 0.063 | gap |
| kindergarten | 03103 | 5250 | 241 | 0.011 | 0.032 | ok |
| kindergarten | 03151 | 7517 | 140 | 0.051 | 0.048 | ok |
| kindergarten | 03153 | 4296 | 68 | 0.161 | 0.056 | gap |
| kindergarten | 03154 | 3527 | 37 | 0.072 | 0.067 | ok |
| kindergarten | 03157 | 5937 | 94 | 0.094 | 0.047 | gap |
| kindergarten | 03158 | 4154 | 67 | 0.084 | 0.067 | gap |
| kindergarten | zgb | 43664 | 647 | 0.062 | 0.020 | ok |
| grundschule | 03101 | 7926 | 143 | 0.078 | 0.037 | ok |
| grundschule | 03102 | 4294 | 57 | 0.027 | 0.068 | ok |
| grundschule | 03103 | 4773 | 200 | 0.069 | 0.033 | ok |
| grundschule | 03151 | 7454 | 107 | 0.154 | 0.059 | gap |
| grundschule | 03153 | 3576 | 57 | 0.127 | 0.071 | gap |
| grundschule | 03154 | 3381 | 55 | 0.191 | 0.049 | gap |
| grundschule | 03157 | 5788 | 97 | 0.156 | 0.055 | gap |
| grundschule | 03158 | 4419 | 55 | 0.186 | 0.058 | gap |
| grundschule | zgb | 41611 | 571 | 0.110 | 0.021 | gap |
| sekundar_1 | 03101 | 11308 | 204 | 0.018 | 0.038 | ok |
| sekundar_1 | 03102 | 5536 | 75 | 0.054 | 0.058 | ok |
| sekundar_1 | 03103 | 6451 | 279 | 0.033 | 0.033 | ok |
| sekundar_1 | 03151 | 9246 | 126 | 0.040 | 0.053 | ok |
| sekundar_1 | 03153 | 5648 | 70 | 0.083 | 0.059 | gap |
| sekundar_1 | 03154 | 4399 | 79 | 0.067 | 0.092 | ok |
| sekundar_1 | 03157 | 7576 | 93 | 0.033 | 0.057 | ok |
| sekundar_1 | 03158 | 6021 | 71 | 0.091 | 0.060 | gap |
| sekundar_1 | zgb | 56185 | 718 | 0.025 | 0.023 | ok |
| upper_secondary | 03101 | 4917 | 92 | 0.057 | 0.057 | ok |
| upper_secondary | 03102 | 2203 | 28 | 0.150 | 0.109 | gap |
| upper_secondary | 03103 | 2902 | 120 | 0.079 | 0.053 | ok |
| upper_secondary | 03151 | 3121 | 76 | 0.090 | 0.101 | within_noise |
| upper_secondary | 03153 | 1846 | 26 | 0.079 | 0.080 | ok |
| upper_secondary | 03154 | 1495 | 40 | 0.152 | 0.134 | gap |
| upper_secondary | 03157 | 2938 | 57 | 0.132 | 0.073 | gap |
| upper_secondary | 03158 | 2105 | 41 | 0.167 | 0.063 | gap |
| upper_secondary | zgb | 21527 | 360 | 0.079 | 0.033 | ok |
| university | 03101 | 11215 | 74 | 0.126 | 0.090 | gap |
| university | 03102 | 3320 | 10 | 0.200 | 0.019 | gap |
| university | 03103 | 4131 | 84 | 0.319 | 0.096 | gap |
| university | 03151 | 6196 | 17 | 0.173 | 0.048 | gap |
| university | 03153 | 2506 | 8 | 0.149 | 0.279 | within_noise |
| university | 03154 | 1160 | 7 | 0.234 | 0.286 | within_noise |
| university | 03157 | 4573 | 13 | 0.186 | 0.028 | gap |
| university | 03158 | 3122 | 13 | 0.123 | 0.220 | within_noise |
| university | zgb | 36223 | 142 | 0.129 | 0.083 | gap |

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
| inter_zgb | 03101 | 21571 | 310 | 0.030 | 0.023 | ok |
| inter_zgb | 03102 | 10672 | 135 | 0.056 | 0.040 | ok |
| inter_zgb | 03103 | 5698 | 445 | 0.034 | 0.021 | ok |
| inter_zgb | 03151 | 37341 | 600 | 0.017 | 0.038 | ok |
| inter_zgb | 03153 | 13749 | 246 | 0.036 | 0.029 | ok |
| inter_zgb | 03154 | 18003 | 336 | 0.043 | 0.033 | ok |
| inter_zgb | 03157 | 20258 | 344 | 0.047 | 0.028 | ok |
| inter_zgb | 03158 | 27151 | 330 | 0.054 | 0.023 | ok |
| inter_zgb | zgb | 154443 | 2301 | 0.015 | 0.013 | ok |
| all_gis_fallback | 03101 | 88348 | 1419 | 0.038 | 0.019 | ok |
| all_gis_fallback | 03102 | 35006 | 466 | 0.027 | 0.032 | ok |
| all_gis_fallback | 03103 | 39674 | 1885 | 0.080 | 0.017 | ok |
| all_gis_fallback | 03151 | 58653 | 962 | 0.025 | 0.036 | ok |
| all_gis_fallback | 03153 | 41320 | 641 | 0.071 | 0.026 | ok |
| all_gis_fallback | 03154 | 28795 | 492 | 0.032 | 0.036 | ok |
| all_gis_fallback | 03157 | 43388 | 677 | 0.071 | 0.026 | ok |
| all_gis_fallback | 03158 | 42087 | 517 | 0.084 | 0.033 | gap |
| all_gis_fallback | zgb | 377271 | 5174 | 0.033 | 0.012 | ok |
| inter_gis_fallback | 03101 | 29536 | 398 | 0.053 | 0.022 | ok |
| inter_gis_fallback | 03102 | 13947 | 187 | 0.050 | 0.040 | ok |
| inter_gis_fallback | 03103 | 8062 | 585 | 0.083 | 0.021 | gap |
| inter_gis_fallback | 03151 | 43017 | 759 | 0.045 | 0.032 | ok |
| inter_gis_fallback | 03153 | 20685 | 332 | 0.122 | 0.028 | gap |
| inter_gis_fallback | 03154 | 20424 | 401 | 0.050 | 0.035 | ok |
| inter_gis_fallback | 03157 | 30636 | 503 | 0.092 | 0.023 | gap |
| inter_gis_fallback | 03158 | 30628 | 389 | 0.076 | 0.028 | ok |
| inter_gis_fallback | zgb | 196935 | 2969 | 0.062 | 0.012 | ok |

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
| scope | inter | 0.08 | True | False | 03103;03153;03157;03158 |
| scope | intra | 0.08 | False | False | - |
| level | kindergarten | 0.08 | False | False | - |
| level | grundschule | 0.08 | True | False | zgb |
| level | sekundar_1 | 0.08 | False | False | - |
| level | upper_secondary | 0.08 | False | False | - |
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
