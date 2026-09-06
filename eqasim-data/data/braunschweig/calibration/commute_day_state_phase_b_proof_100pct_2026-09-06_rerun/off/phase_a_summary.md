# Commute day state -- Phase A measurement

Parameters: detour_factor=1.3, max_unmatched_home_share=0.05, max_unresolved_destination_share=0.05, edge_tolerance_km=5.0, sampling_rate=1.0, output_subdir=analysis/commute_day_state_phase_a, commute_day_state_enabled=False, check_1_tolerance_pp=3.0, max_states_outside_employed_share=0.05
Generated at: 2026-09-05T20:39:51.433554+00:00

Measurement only: the model is compared to a committed reference, which is NOT a
validation against observed behaviour and decides nothing.

All person and worker counts below (every n_* column) are SAMPLE counts at sampling_rate = 1.0000; they are NOT expanded to the full population. Shares and deltas are unaffected by the sampling rate.

## Work participation of employed persons (model vs SrV 2023)

Comparable quantity: share_work_trip. The model has no day state, so its
share_no_work_trip corresponds to the SUM of the SrV home-office and neither shares.

| code | n_employed | share_work_trip | SrV share_work_trip | delta (pp) | SrV share_home_office_day | SrV n |
|---|---|---|---|---|---|---|
| 03101 | 126048 | 0.505 | 0.628 | -12.27 | 0.178 | 2268 |
| 03102 | 45482 | 0.508 | 0.714 | -20.55 | 0.059 | 663 |
| 03103 | 56093 | 0.519 | n/a | n/a | n/a | 0 |
| 03151 | 86850 | 0.473 | 0.644 | -17.12 | 0.179 | 1521 |
| 03153 | 59402 | 0.498 | 0.692 | -19.43 | 0.078 | 935 |
| 03154 | 42386 | 0.487 | 0.667 | -17.99 | 0.121 | 754 |
| 03157 | 66039 | 0.473 | 0.612 | -13.87 | 0.147 | 1067 |
| 03158 | 58125 | 0.495 | 0.660 | -16.56 | 0.150 | 808 |
| zgb | 540425 | 0.494 | 0.651 | -15.67 | 0.142 | 8016 |

## Assigned commute-distance classes (ZGB, all destinations)

| distance_class | n_workers | share |
|---|---|---|
| lt10 | 141352 | 0.464 |
| 10_25 | 71528 | 0.235 |
| 25_50 | 54311 | 0.178 |
| 50_100 | 21515 | 0.071 |
| 100_200 | 5235 | 0.017 |
| gt200 | 10959 | 0.036 |

## External destinations vs BA Kreis centroids

- Worker-weighted share of EXT workers whose model distance class equals the Kreis-centroid distance class: 0.826
- (home, destination) Kreis pairs measured: 646
- EXT workers measured: 39150

## Class-edge fragility

- Share of workers whose distance lies within the configured tolerance of a commute-distance class edge: 0.426
