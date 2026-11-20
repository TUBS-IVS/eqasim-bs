# Population validation - new

- Source: `~/popsim100_20261120/output` (run_output)
- Git commit: `unknown`
- Persons: 1,129,395 | Households: 558,179 | Vehicles: 2,305,705

## Headline quality by family

       family  mean_srmse  mean_abs_delta_pp  n_controls
       census    0.041613           0.532665           3
 distribution    0.004294           0.120720           1
mid_household    0.073361           0.985064           2
   mid_person    0.143951           2.532146           4

## Interpretation

### Where are we good

- **bev_share** (very good, mean |delta| 0.12 pp, SRMSE 0.004)
- **sex** (very good, mean |delta| 0.22 pp, SRMSE 0.004)
- **age_group** (very good, mean |delta| 0.51 pp, SRMSE 0.043)
- **household_size** (very good, mean |delta| 0.87 pp, SRMSE 0.077)
- **bicycles_per_hh** (very good, mean |delta| 0.98 pp, SRMSE 0.080)
- **cars_per_hh** (very good, mean |delta| 0.99 pp, SRMSE 0.067)
- **employment_status** (good, mean |delta| 1.41 pp, SRMSE 0.161)
- **pt_ticket_type** (good, mean |delta| 1.65 pp, SRMSE 0.201)
- **driving_license_type** (good, mean |delta| 2.83 pp, SRMSE 0.119)

### What can be improved

- **employment** (acceptable, mean |delta| 4.24 pp, SRMSE 0.095)

### Possible causes

- **employment**: The synthetic employment rate is anchored to OFFICIAL statistics, not to MiD P9 (popsim_mid: Zensus 2022 kreis_erwerbsstatus levels + the employment_grid age shape; simple_ipf: GENESIS 13111). P9 is an INDEPENDENT cross-check with two known caveats: its per-Kreis rate is survey-noisy (MiD sample is a few hundred to ~2000 per Kreis), and the census anchor follows the Zensus ILO employment concept while P9 is self-reported employment extent, so a small definitional offset is expected. (P9's former 'in_ausbildung'-counted-as-not-employed mismatch no longer applies: since issue #169 the employment_target loader counts apprentices as employed, matching the realized flag.) Raking to P9 would overfit survey noise and is intentionally avoided.

## Minor-employment plausibility (issue #96 guard)

- Employed share among under-15s: 0.00% (0/156,445), bound 0.50% -- ok

## Trip coherence (donor activity chains vs MiD)

- Mobility rate: 83.8% (MiD P36_1 80.8%, |delta| 3.0 pp)
- Purpose distribution SRMSE vs MiD W1 (4 scored purposes): 0.284
- [KPI] work-trip participation gap employed - not-employed: 60.6 pp (higher = matching gives employed persons commute diaries)

Scored purpose shares (realised vs W1, re-normalised over the four unambiguous purposes):

  - arbeit: 26.1% vs 18.1% (|delta| 8.0 pp)
  - ausbildung: 13.0% vs 8.3% (|delta| 4.7 pp)
  - einkauf: 17.5% vs 22.2% (|delta| 4.7 pp)
  - freizeit: 33.0% vs 40.3% (|delta| 7.2 pp)
  - begleitung: 10.4% vs 11.1% (|delta| 0.8 pp)

_Modal split is not shown: the synthesis trips.csv carries no transport mode (written only by the MATSim mode-choice run), and donor-inherited modes would be French-biased - see step 3 (MiD donor) in the matching-optimization concept._
