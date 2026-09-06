# Commute day state -- Phase A measurement

Parameters: detour_factor=1.3, max_unmatched_home_share=0.05, max_unresolved_destination_share=0.05, edge_tolerance_km=5.0, sampling_rate=1.0, output_subdir=analysis/commute_day_state_phase_a, commute_day_state_enabled=True, check_1_tolerance_pp=3.0, max_states_outside_employed_share=0.05
Generated at: 2026-09-05T20:53:01.480976+00:00

Measurement only: the model is compared to a committed reference, which is NOT a
validation against observed behaviour and decides nothing.

All person and worker counts below (every n_* column) are SAMPLE counts at sampling_rate = 1.0000; they are NOT expanded to the full population. Shares and deltas are unaffected by the sampling rate.

## Work participation of employed persons (model vs SrV 2023)

Comparable quantity: share_work_trip. The model has no day state, so its
share_no_work_trip corresponds to the SUM of the SrV home-office and neither shares.

| code | n_employed | share_work_trip | SrV share_work_trip | delta (pp) | SrV share_home_office_day | SrV n |
|---|---|---|---|---|---|---|
| 03101 | 126048 | 0.493 | 0.628 | -13.45 | 0.178 | 2268 |
| 03102 | 45482 | 0.500 | 0.714 | -21.37 | 0.059 | 663 |
| 03103 | 56093 | 0.510 | n/a | n/a | n/a | 0 |
| 03151 | 86850 | 0.460 | 0.644 | -18.39 | 0.179 | 1521 |
| 03153 | 59402 | 0.476 | 0.692 | -21.60 | 0.078 | 935 |
| 03154 | 42386 | 0.474 | 0.667 | -19.33 | 0.121 | 754 |
| 03157 | 66039 | 0.456 | 0.612 | -15.59 | 0.147 | 1067 |
| 03158 | 58125 | 0.483 | 0.660 | -17.71 | 0.150 | 808 |
| zgb | 540425 | 0.481 | 0.651 | -16.99 | 0.142 | 8016 |

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

## Check 1 (ADR-0104): reporting-day states vs SrV -- tolerance +/- 3 pp on the regional aggregate only (ASSUMPTION, pre-registered)

DENOMINATOR: every model share below, and every SrV share it is compared to, is a share of EMPLOYED
persons with a ZGB home Kreis -- the universe SrV asked the home-office question in. It is NOT a share
of the model's workers. The employed persons WITHOUT an assigned workplace, for whom the model draws no
state at all, are reported as share_no_workplace, so the four model shares sum to 1 over the employed;
n_workers is a COUNT of the employed persons that do have an assigned workplace, never a denominator.

The +/- 3 pp band was chosen a priori in the 2026-09-04 design and recorded in ADR-0104; it is NOT
derived from any committed source. It is applied to the ZGB aggregate ONLY. The per-Kreis rows of
commute_day_state_shares.csv are REPORTED, never gated: the per-Kreis SrV cells rest on 663-2,268
persons under a stratified PSU design and are assumption-grade for a full Kreis.

ASSUMPTION -- state correspondence: at_workplace <-> SrV work trip, home <-> SrV full home-office day,
absent <-> SrV neither. SrV's "neither" is a residual category (employed, no work trip, no full
home-office day), so its correspondence with a modelled away-from-the-region state is an
interpretation, not a definitional identity.

ZGB employed persons: 540425 (sample count); of them 267194 with an assigned workplace and therefore a drawn state.
A further 37705 worker(s) with a ZGB home Kreis have an assigned workplace but are NOT flagged employed
by synthesis.population.enriched (column n_workers_not_employed). They are a UNIVERSE DIFFERENCE between the model's
worker cohort and the employed cohort SrV surveyed -- not a join failure -- and are excluded from every
share in this section; they are reported so the gap between the two cohorts stays visible.

| quantity (share of employed persons) | model | SrV 2023 | delta (pp) | +/- 3 pp |
|---|---|---|---|---|
| at_workplace vs SrV work trip | 0.481 | 0.651 | -17.01 | outside |
| home vs SrV full home-office day | 0.008 | 0.142 | -13.35 | outside |
| absent vs SrV neither | 0.005 | 0.207 | -20.20 | outside |
| employed without a work trip vs SrV remainder | 0.519 | 0.349 | 16.99 | outside |
| no_workplace (employed, no assigned workplace) | 0.506 | n/a | n/a | not compared |

The no_workplace row has NO SrV counterpart: it is part of the model's own remainder, reported so the
four model shares can be read as the partition of the employed universe that they are.
