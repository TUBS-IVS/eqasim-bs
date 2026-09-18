# Plan structure: synthetic population vs SrV 2023

VALIDATION REFERENCE, not a calibration target: no synthesis or location stage reads the SrV plan-structure table, and this stage writes report files only.

Parameters: srv_universe=at_home_zero, output_subdir=analysis/plan_structure_vs_srv, sampling_rate=1.0, max_unmatched_home_share=0.05, trips_view=final, day_absence_enabled=True
Generated at: 2026-09-09T17:23:55.525619+00:00
Pipeline commit: 9184f594+dirty
Reference: eqasim-data/data/braunschweig/srv/srv2023_plan_structure_reference.csv

## Comparison universe

Reporting-day trips view: final (day_absence_enabled=True); 57017 model person(s) marked away from home by the general day-absence draw, 0 of them excluded from the model side under universe 'at_home_zero'.
Head-to-head Kreise (SrV-surveyed): 03101, 03102, 03151, 03153, 03154, 03157, 03158.
Model persons in scope: 1002871 of 1130516 (0.8871); 127642 person(s) live in a Kreis SrV does not survey (03103, reported model-only in by_kreis.csv) and 3 (0.0000) have no resolvable home Kreis (the stage raises above 0.05).

Deltas: percentage points for share metrics (mobility_rate, share_*, participation_*, purpose_share_*, dep_hour_share_*), a plain difference otherwise. The n_persons_* and n_work_activities_measured rows are the two sides' sample sizes, not a gap: SrV is an expanded survey of 18,223 respondents, the model side a (possibly sampled) synthetic population.

## Headline (segment 'all')

| metric | model | srv | delta | n_srv |
| --- | ---: | ---: | ---: | ---: |
| mobility_rate | 0.8378 | 0.8437 | -0.59 | 18223 |
| trips_per_person | 3.0611 | 3.1865 | -0.13 | 18223 |
| trips_per_mobile_person | 3.6537 | 3.7767 | -0.12 | 18223 |
| nonhome_acts_per_mobile | 2.1327 | 2.1816 | -0.05 | 18223 |
| tours_per_mobile | 1.5210 | 1.5951 | -0.07 | 18223 |
| share_mobile_first_from_home | 1.0000 | 0.9781 | 2.19 | 18223 |
| share_mobile_last_to_home | 1.0000 | 0.9776 | 2.24 | 18223 |
| share_mobile_odd_trip_count | 0.2855 | 0.2325 | 5.30 | 18223 |
| share_trips_home_based | 0.8092 | 0.8449 | -3.57 | 18223 |
| share_trips_followed_by_same_purpose | 0.1025 | 0.0453 | 5.71 | 18223 |
| share_trips_0 | 0.1622 | 0.1563 | 0.59 | 18223 |
| share_trips_1 | 0.0008 | 0.0058 | -0.51 | 18223 |
| share_trips_2 | 0.3211 | 0.3142 | 0.69 | 18223 |
| participation_work | 0.3199 | 0.3316 | -1.17 | 18223 |
| participation_education | 0.1696 | 0.1761 | -0.65 | 18223 |
| participation_shop | 0.2059 | 0.2794 | -7.35 | 18223 |
| participation_leisure | 0.3566 | 0.3832 | -2.66 | 18223 |
| participation_escort | 0.0960 | 0.0992 | -0.32 | 18223 |
| participation_other | 0.2401 | 0.1712 | 6.89 | 18223 |
| purpose_share_work | 0.1296 | 0.1239 | 0.56 | 18223 |
| purpose_share_education | 0.0698 | 0.0610 | 0.88 | 18223 |
| purpose_share_shop | 0.0799 | 0.1105 | -3.05 | 18223 |
| purpose_share_leisure | 0.1492 | 0.1647 | -1.54 | 18223 |
| purpose_share_escort | 0.0478 | 0.0502 | -0.24 | 18223 |
| purpose_share_other | 0.1074 | 0.0671 | 4.03 | 18223 |
| purpose_share_home | 0.4163 | 0.4226 | -0.64 | 18223 |
| mean_work_activity_h | 5.7971 | 6.2904 | -0.49 | 18223 |

## Accepted deviations (closed plans by decision)

Every synthetic day is a closed home-based chain by construction (issue #367), so the model cannot reproduce the SrV shares below. They are accepted deviations, not defects.

| deviation | model | srv | delta_pp | note |
| --- | ---: | ---: | ---: | --- |
| open_end_days | 0.0000 | 0.0224 | -2.24 | closed plans by decision (issue #367; ADR in the stage record's decisions) |
| open_start_days | 0.0000 | 0.0219 | -2.19 | closed plans by decision (issue #367; ADR in the stage record's decisions) |
| single_trip_days | 0.0008 | 0.0058 | -0.51 | closed plans by decision (issue #367; ADR in the stage record's decisions) |

## Synthetic home closure (modelling assumption)

Measured over the whole model trip table (every Kreis), not only the head-to-head universe: the closure is a property of the trip builder.

| metric | value |
| --- | ---: |
| n_trips | 3455325.0000 |
| n_trips_synthetic_closure | 214642.0000 |
| share_trips_synthetic_closure | 0.0621 |
| n_persons | 945920.0000 |
| n_persons_closed | 214642.0000 |
| share_persons_closed | 0.2269 |
| n_home_trips | 1438610.0000 |
| n_home_trips_synthetic | 214642.0000 |
| share_home_trips_synthetic | 0.1492 |
| closure_column_present | 1.0000 |

## By home Kreis

A model-only Kreis (not surveyed by SrV) carries n/a for srv and delta.

### mobility_rate

| segment | model | srv | delta | n_srv |
| --- | ---: | ---: | ---: | ---: |
| kreis_03101 | 0.8494 | 0.8533 | -0.39 | 4519 |
| kreis_03102 | 0.8334 | 0.8519 | -1.85 | 1794 |
| kreis_03103 | 0.8281 | n/a | n/a | n/a |
| kreis_03151 | 0.8308 | 0.8359 | -0.51 | 3574 |
| kreis_03153 | 0.8258 | 0.8324 | -0.65 | 2258 |
| kreis_03154 | 0.8375 | 0.8423 | -0.47 | 1761 |
| kreis_03157 | 0.8284 | 0.8212 | 0.71 | 2446 |
| kreis_03158 | 0.8515 | 0.8599 | -0.84 | 1871 |

### trips_per_person

| segment | model | srv | delta | n_srv |
| --- | ---: | ---: | ---: | ---: |
| kreis_03101 | 3.1748 | 3.2884 | -0.11 | 4519 |
| kreis_03102 | 2.9022 | 3.0524 | -0.15 | 1794 |
| kreis_03103 | 3.0199 | n/a | n/a | n/a |
| kreis_03151 | 3.0028 | 3.1187 | -0.12 | 3574 |
| kreis_03153 | 3.0115 | 3.1789 | -0.17 | 2258 |
| kreis_03154 | 3.0547 | 3.1863 | -0.13 | 1761 |
| kreis_03157 | 3.0295 | 3.1076 | -0.08 | 2446 |
| kreis_03158 | 3.1385 | 3.2521 | -0.11 | 1871 |

### share_mobile_last_to_home

| segment | model | srv | delta | n_srv |
| --- | ---: | ---: | ---: | ---: |
| kreis_03101 | 1.0000 | 0.9724 | 2.76 | 4519 |
| kreis_03102 | 1.0000 | 0.9713 | 2.87 | 1794 |
| kreis_03103 | 1.0000 | n/a | n/a | n/a |
| kreis_03151 | 1.0000 | 0.9850 | 1.50 | 3574 |
| kreis_03153 | 1.0000 | 0.9834 | 1.66 | 2258 |
| kreis_03154 | 1.0000 | 0.9801 | 1.99 | 1761 |
| kreis_03157 | 1.0000 | 0.9814 | 1.86 | 2446 |
| kreis_03158 | 1.0000 | 0.9794 | 2.06 | 1871 |

