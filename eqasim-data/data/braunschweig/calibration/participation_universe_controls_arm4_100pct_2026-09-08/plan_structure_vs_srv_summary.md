# Plan structure: synthetic population vs SrV 2023

VALIDATION REFERENCE, not a calibration target: no synthesis or location stage reads the SrV plan-structure table, and this stage writes report files only.

Parameters: srv_universe=at_home_zero, output_subdir=analysis/plan_structure_vs_srv, sampling_rate=1.0, max_unmatched_home_share=0.05
Generated at: 2026-09-08T07:14:09.231065+00:00
Pipeline commit: 30cde0a1+dirty
Reference: eqasim-data/data/braunschweig/srv/srv2023_plan_structure_reference.csv

## Comparison universe

Head-to-head Kreise (SrV-surveyed): 03101, 03102, 03151, 03153, 03154, 03157, 03158.
Model persons in scope: 1002871 of 1130516 (0.8871); 127642 person(s) live in a Kreis SrV does not survey (03103, reported model-only in by_kreis.csv) and 3 (0.0000) have no resolvable home Kreis (the stage raises above 0.05).

Deltas: percentage points for share metrics (mobility_rate, share_*, participation_*, purpose_share_*, dep_hour_share_*), a plain difference otherwise. The n_persons_* and n_work_activities_measured rows are the two sides' sample sizes, not a gap: SrV is an expanded survey of 18,223 respondents, the model side a (possibly sampled) synthetic population.

## Headline (segment 'all')

| metric | model | srv | delta | n_srv |
| --- | ---: | ---: | ---: | ---: |
| mobility_rate | 0.8872 | 0.8437 | 4.35 | 18223 |
| trips_per_person | 3.2463 | 3.1865 | 0.06 | 18223 |
| trips_per_mobile_person | 3.6590 | 3.7767 | -0.12 | 18223 |
| nonhome_acts_per_mobile | 2.1366 | 2.1816 | -0.05 | 18223 |
| tours_per_mobile | 1.5224 | 1.5951 | -0.07 | 18223 |
| share_mobile_first_from_home | 1.0000 | 0.9781 | 2.19 | 18223 |
| share_mobile_last_to_home | 1.0000 | 0.9776 | 2.24 | 18223 |
| share_mobile_odd_trip_count | 0.2863 | 0.2325 | 5.38 | 18223 |
| share_trips_home_based | 0.8086 | 0.8449 | -3.63 | 18223 |
| share_trips_followed_by_same_purpose | 0.1024 | 0.0453 | 5.71 | 18223 |
| share_trips_0 | 0.1128 | 0.1563 | -4.35 | 18223 |
| share_trips_1 | 0.0008 | 0.0058 | -0.50 | 18223 |
| share_trips_2 | 0.3392 | 0.3142 | 2.50 | 18223 |
| participation_work | 0.3491 | 0.3316 | 1.75 | 18223 |
| participation_education | 0.1754 | 0.1761 | -0.07 | 18223 |
| participation_shop | 0.2175 | 0.2794 | -6.19 | 18223 |
| participation_leisure | 0.3767 | 0.3832 | -0.65 | 18223 |
| participation_escort | 0.1010 | 0.0992 | 0.18 | 18223 |
| participation_other | 0.2533 | 0.1712 | 8.22 | 18223 |
| purpose_share_work | 0.1334 | 0.1239 | 0.94 | 18223 |
| purpose_share_education | 0.0681 | 0.0610 | 0.71 | 18223 |
| purpose_share_shop | 0.0795 | 0.1105 | -3.09 | 18223 |
| purpose_share_leisure | 0.1488 | 0.1647 | -1.59 | 18223 |
| purpose_share_escort | 0.0473 | 0.0502 | -0.28 | 18223 |
| purpose_share_other | 0.1069 | 0.0671 | 3.98 | 18223 |
| purpose_share_home | 0.4161 | 0.4226 | -0.66 | 18223 |
| mean_work_activity_h | 5.7957 | 6.2904 | -0.49 | 18223 |

## Accepted deviations (closed plans by decision)

Every synthetic day is a closed home-based chain by construction (issue #367), so the model cannot reproduce the SrV shares below. They are accepted deviations, not defects.

| deviation | model | srv | delta_pp | note |
| --- | ---: | ---: | ---: | --- |
| open_end_days | 0.0000 | 0.0224 | -2.24 | closed plans by decision (issue #367; ADR in the stage record's decisions) |
| open_start_days | 0.0000 | 0.0219 | -2.19 | closed plans by decision (issue #367; ADR in the stage record's decisions) |
| single_trip_days | 0.0008 | 0.0058 | -0.50 | closed plans by decision (issue #367; ADR in the stage record's decisions) |

## Synthetic home closure (modelling assumption)

Measured over the whole model trip table (every Kreis), not only the head-to-head universe: the closure is a property of the trip builder.

| metric | value |
| --- | ---: |
| n_trips | 3662925.0000 |
| n_trips_synthetic_closure | 228278.0000 |
| share_trips_synthetic_closure | 0.0623 |
| n_persons | 1001503.0000 |
| n_persons_closed | 228278.0000 |
| share_persons_closed | 0.2279 |
| n_home_trips | 1524395.0000 |
| n_home_trips_synthetic | 228278.0000 |
| share_home_trips_synthetic | 0.1497 |
| closure_column_present | 1.0000 |

## By home Kreis

A model-only Kreis (not surveyed by SrV) carries n/a for srv and delta.

### mobility_rate

| segment | model | srv | delta | n_srv |
| --- | ---: | ---: | ---: | ---: |
| kreis_03101 | 0.9007 | 0.8533 | 4.74 | 4519 |
| kreis_03102 | 0.8802 | 0.8519 | 2.83 | 1794 |
| kreis_03103 | 0.8754 | n/a | n/a | n/a |
| kreis_03151 | 0.8773 | 0.8359 | 4.15 | 3574 |
| kreis_03153 | 0.8786 | 0.8324 | 4.62 | 2258 |
| kreis_03154 | 0.8860 | 0.8423 | 4.37 | 1761 |
| kreis_03157 | 0.8776 | 0.8212 | 5.63 | 2446 |
| kreis_03158 | 0.9006 | 0.8599 | 4.07 | 1871 |

### trips_per_person

| segment | model | srv | delta | n_srv |
| --- | ---: | ---: | ---: | ---: |
| kreis_03101 | 3.3701 | 3.2884 | 0.08 | 4519 |
| kreis_03102 | 3.0667 | 3.0524 | 0.01 | 1794 |
| kreis_03103 | 3.1908 | n/a | n/a | n/a |
| kreis_03151 | 3.1753 | 3.1187 | 0.06 | 3574 |
| kreis_03153 | 3.2113 | 3.1789 | 0.03 | 2258 |
| kreis_03154 | 3.2370 | 3.1863 | 0.05 | 1761 |
| kreis_03157 | 3.2165 | 3.1076 | 0.11 | 2446 |
| kreis_03158 | 3.3237 | 3.2521 | 0.07 | 1871 |

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

