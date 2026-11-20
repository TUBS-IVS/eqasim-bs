# Population validation - old

- Source: `~/prod100_20261031/output` (run_output)
- Git commit: `1041849c243c71859c95275447a24363ee1aa358`
- Persons: 1,130,422 | Households: 558,274 | Vehicles: 2,311,830

## Headline quality by family

       family  mean_srmse  mean_abs_delta_pp  n_controls
       census    0.087159           0.967263           3
 distribution    0.004235           0.131841           1
mid_household    0.078941           1.050004           2
   mid_person    0.105779           1.670236           4

## Interpretation

### Where are we good

- **bev_share** (very good, mean |delta| 0.13 pp, SRMSE 0.004)
- **sex** (very good, mean |delta| 0.18 pp, SRMSE 0.004)
- **age_group** (very good, mean |delta| 0.80 pp, SRMSE 0.068)
- **employment_status** (very good, mean |delta| 0.84 pp, SRMSE 0.084)
- **cars_per_hh** (very good, mean |delta| 1.00 pp, SRMSE 0.068)
- **bicycles_per_hh** (good, mean |delta| 1.10 pp, SRMSE 0.090)
- **pt_ticket_type** (good, mean |delta| 1.47 pp, SRMSE 0.197)
- **household_size** (good, mean |delta| 1.92 pp, SRMSE 0.190)
- **employment** (good, mean |delta| 2.09 pp, SRMSE 0.045)
- **driving_license_type** (good, mean |delta| 2.28 pp, SRMSE 0.097)

### What can be improved


### Possible causes


## Minor-employment plausibility (issue #96 guard)

- Employed share among under-15s: 0.01% (18/155,992), bound 0.50% -- ok

## Trip coherence (donor activity chains vs MiD)

- Mobility rate: 83.8% (MiD P36_1 80.8%, |delta| 3.0 pp)
- Purpose distribution SRMSE vs MiD W1 (4 scored purposes): 0.292
- [KPI] work-trip participation gap employed - not-employed: 61.0 pp (higher = matching gives employed persons commute diaries)

Scored purpose shares (realised vs W1, re-normalised over the four unambiguous purposes):

  - arbeit: 26.4% vs 18.1% (|delta| 8.3 pp)
  - ausbildung: 13.1% vs 8.3% (|delta| 4.8 pp)
  - einkauf: 17.8% vs 22.2% (|delta| 4.5 pp)
  - freizeit: 32.7% vs 40.3% (|delta| 7.6 pp)
  - begleitung: 10.0% vs 11.1% (|delta| 1.1 pp)

_Modal split is not shown: the synthesis trips.csv carries no transport mode (written only by the MATSim mode-choice run), and donor-inherited modes would be French-biased - see step 3 (MiD donor) in the matching-optimization concept._
