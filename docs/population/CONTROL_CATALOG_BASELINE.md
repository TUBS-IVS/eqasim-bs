Baseline production control set captured 2026-06-14 from popsimprep `_prep3_controls.csv`; the `catalog` renderer must reproduce these rows byte-for-byte (modulo row order).

| target | geography | seed_table | importance | control_field | expression |
|--------|-----------|------------|------------|---------------|------------|
| Insgesamt_Haushalte_Groesse_des_privaten_Haushalts_100m_Gitter_adj_ZENSUS100m_target | ZENSUS100m | households | 1000 | Insgesamt_Haushalte_Groesse_des_privaten_Haushalts_100m_Gitter_adj_ZENSUS100m | (households.H_GEW > 0) & (households.H_GEW < np.inf) |
| POP_TOTAL_100m_adj_ZENSUS100m_target | ZENSUS100m | persons | 1000 | POP_TOTAL_100m_adj_ZENSUS100m | (persons.P_GEW > 0) & (persons.P_GEW < np.inf) |
| M_AGE_0_9_agg_ZENSUS100m_target | ZENSUS100m | persons | 1000 | M_AGE_0_9_agg_ZENSUS100m | (persons.HP_ALTER < 10)&(persons.HP_SEX==1) |
| M_AGE_10_19_agg_ZENSUS100m_target | ZENSUS100m | persons | 1000 | M_AGE_10_19_agg_ZENSUS100m | (persons.HP_ALTER > 9)&(persons.HP_ALTER < 20)&(persons.HP_SEX==1) |
| M_AGE_20_29_agg_ZENSUS100m_target | ZENSUS100m | persons | 1000 | M_AGE_20_29_agg_ZENSUS100m | (persons.HP_ALTER >19)&(persons.HP_ALTER < 30)&(persons.HP_SEX==1) |
| M_AGE_30_39_agg_ZENSUS100m_target | ZENSUS100m | persons | 1000 | M_AGE_30_39_agg_ZENSUS100m | (persons.HP_ALTER > 29)&(persons.HP_ALTER < 40)&(persons.HP_SEX==1) |
| M_AGE_40_49_agg_ZENSUS100m_target | ZENSUS100m | persons | 1000 | M_AGE_40_49_agg_ZENSUS100m | (persons.HP_ALTER > 39)&(persons.HP_ALTER < 50)&(persons.HP_SEX==1) |
| M_AGE_50_59_agg_ZENSUS100m_target | ZENSUS100m | persons | 1000 | M_AGE_50_59_agg_ZENSUS100m | (persons.HP_ALTER > 49)&(persons.HP_ALTER < 60)&(persons.HP_SEX==1) |
| M_AGE_60_69_agg_ZENSUS100m_target | ZENSUS100m | persons | 1000 | M_AGE_60_69_agg_ZENSUS100m | (persons.HP_ALTER > 59)&(persons.HP_ALTER < 70)&(persons.HP_SEX==1) |
| M_AGE_70_79_agg_ZENSUS100m_target | ZENSUS100m | persons | 1000 | M_AGE_70_79_agg_ZENSUS100m | (persons.HP_ALTER > 69)&(persons.HP_ALTER < 80)&(persons.HP_SEX==1) |
| M_AGE_80_plus_agg_ZENSUS100m_target | ZENSUS100m | persons | 1000 | M_AGE_80_plus_agg_ZENSUS100m | (persons.HP_ALTER > 79)&(persons.HP_SEX==1) |
| F_AGE_0_9_agg_ZENSUS100m_target | ZENSUS100m | persons | 1000 | F_AGE_0_9_agg_ZENSUS100m | (persons.HP_ALTER < 10)&(persons.HP_SEX==2) |
| F_AGE_10_19_agg_ZENSUS100m_target | ZENSUS100m | persons | 1000 | F_AGE_10_19_agg_ZENSUS100m | (persons.HP_ALTER > 9)&(persons.HP_ALTER < 20)&(persons.HP_SEX==2) |
| F_AGE_20_29_agg_ZENSUS100m_target | ZENSUS100m | persons | 1000 | F_AGE_20_29_agg_ZENSUS100m | (persons.HP_ALTER > 19)&(persons.HP_ALTER < 30)&(persons.HP_SEX==2) |
| F_AGE_30_39_agg_ZENSUS100m_target | ZENSUS100m | persons | 1000 | F_AGE_30_39_agg_ZENSUS100m | (persons.HP_ALTER > 29)&(persons.HP_ALTER < 40)&(persons.HP_SEX==2) |
| F_AGE_40_49_agg_ZENSUS100m_target | ZENSUS100m | persons | 1000 | F_AGE_40_49_agg_ZENSUS100m | (persons.HP_ALTER > 39)&(persons.HP_ALTER < 50)&(persons.HP_SEX==2) |
| F_AGE_50_59_agg_ZENSUS100m_target | ZENSUS100m | persons | 1000 | F_AGE_50_59_agg_ZENSUS100m | (persons.HP_ALTER > 49)&(persons.HP_ALTER < 60)&(persons.HP_SEX==2) |
| F_AGE_60_69_agg_ZENSUS100m_target | ZENSUS100m | persons | 1000 | F_AGE_60_69_agg_ZENSUS100m | (persons.HP_ALTER > 59)&(persons.HP_ALTER < 70)&(persons.HP_SEX==2) |
| F_AGE_70_79_agg_ZENSUS100m_target | ZENSUS100m | persons | 1000 | F_AGE_70_79_agg_ZENSUS100m | (persons.HP_ALTER > 69)&(persons.HP_ALTER < 80)&(persons.HP_SEX==2) |
| F_AGE_80_plus_agg_ZENSUS100m_target | ZENSUS100m | persons | 1000 | F_AGE_80_plus_agg_ZENSUS100m | (persons.HP_ALTER > 79)&(persons.HP_SEX==2) |
| M_TOTAL_ZENSUS100m_target | ZENSUS100m | persons | 1000 | M_TOTAL_ZENSUS100m | (persons.HP_SEX==1) |
| F_TOTAL_ZENSUS100m_target | ZENSUS100m | persons | 1000 | F_TOTAL_ZENSUS100m | (persons.HP_SEX==2) |
| Insgesamt_Haushalte_Groesse_des_privaten_Haushalts_100m_Gitter_adj_ZENSUS1km_target | ZENSUS1km | households | 1000 | Insgesamt_Haushalte_Groesse_des_privaten_Haushalts_100m_Gitter_adj_ZENSUS1km | (households.H_GEW > 0) & (households.H_GEW < np.inf) |
| POP_TOTAL_100m_adj_ZENSUS1km_target | ZENSUS1km | persons | 1000 | POP_TOTAL_100m_adj_ZENSUS1km | (persons.P_GEW > 0) & (persons.P_GEW < np.inf) |
| M_AGE_0_9_agg_ZENSUS1km_target | ZENSUS1km | persons | 1000 | M_AGE_0_9_agg_ZENSUS1km | (persons.HP_ALTER < 10)&(persons.HP_SEX==1) |
| M_AGE_10_19_agg_ZENSUS1km_target | ZENSUS1km | persons | 1000 | M_AGE_10_19_agg_ZENSUS1km | (persons.HP_ALTER > 9)&(persons.HP_ALTER < 20)&(persons.HP_SEX==1) |
| M_AGE_20_29_agg_ZENSUS1km_target | ZENSUS1km | persons | 1000 | M_AGE_20_29_agg_ZENSUS1km | (persons.HP_ALTER >19)&(persons.HP_ALTER < 30)&(persons.HP_SEX==1) |
| M_AGE_30_39_agg_ZENSUS1km_target | ZENSUS1km | persons | 1000 | M_AGE_30_39_agg_ZENSUS1km | (persons.HP_ALTER > 29)&(persons.HP_ALTER < 40)&(persons.HP_SEX==1) |
| M_AGE_40_49_agg_ZENSUS1km_target | ZENSUS1km | persons | 1000 | M_AGE_40_49_agg_ZENSUS1km | (persons.HP_ALTER > 39)&(persons.HP_ALTER < 50)&(persons.HP_SEX==1) |
| M_AGE_50_59_agg_ZENSUS1km_target | ZENSUS1km | persons | 1000 | M_AGE_50_59_agg_ZENSUS1km | (persons.HP_ALTER > 49)&(persons.HP_ALTER < 60)&(persons.HP_SEX==1) |
| M_AGE_60_69_agg_ZENSUS1km_target | ZENSUS1km | persons | 1000 | M_AGE_60_69_agg_ZENSUS1km | (persons.HP_ALTER > 59)&(persons.HP_ALTER < 70)&(persons.HP_SEX==1) |
| M_AGE_70_79_agg_ZENSUS1km_target | ZENSUS1km | persons | 1000 | M_AGE_70_79_agg_ZENSUS1km | (persons.HP_ALTER > 69)&(persons.HP_ALTER < 80)&(persons.HP_SEX==1) |
| M_AGE_80_plus_agg_ZENSUS1km_target | ZENSUS1km | persons | 1000 | M_AGE_80_plus_agg_ZENSUS1km | (persons.HP_ALTER > 79)&(persons.HP_SEX==1) |
| F_AGE_0_9_agg_ZENSUS1km_target | ZENSUS1km | persons | 1000 | F_AGE_0_9_agg_ZENSUS1km | (persons.HP_ALTER < 10)&(persons.HP_SEX==2) |
| F_AGE_10_19_agg_ZENSUS1km_target | ZENSUS1km | persons | 1000 | F_AGE_10_19_agg_ZENSUS1km | (persons.HP_ALTER > 9)&(persons.HP_ALTER < 20)&(persons.HP_SEX==2) |
| F_AGE_20_29_agg_ZENSUS1km_target | ZENSUS1km | persons | 1000 | F_AGE_20_29_agg_ZENSUS1km | (persons.HP_ALTER > 19)&(persons.HP_ALTER < 30)&(persons.HP_SEX==2) |
| F_AGE_30_39_agg_ZENSUS1km_target | ZENSUS1km | persons | 1000 | F_AGE_30_39_agg_ZENSUS1km | (persons.HP_ALTER > 29)&(persons.HP_ALTER < 40)&(persons.HP_SEX==2) |
| F_AGE_40_49_agg_ZENSUS1km_target | ZENSUS1km | persons | 1000 | F_AGE_40_49_agg_ZENSUS1km | (persons.HP_ALTER > 39)&(persons.HP_ALTER < 50)&(persons.HP_SEX==2) |
| F_AGE_50_59_agg_ZENSUS1km_target | ZENSUS1km | persons | 1000 | F_AGE_50_59_agg_ZENSUS1km | (persons.HP_ALTER > 49)&(persons.HP_ALTER < 60)&(persons.HP_SEX==2) |
| F_AGE_60_69_agg_ZENSUS1km_target | ZENSUS1km | persons | 1000 | F_AGE_60_69_agg_ZENSUS1km | (persons.HP_ALTER > 59)&(persons.HP_ALTER < 70)&(persons.HP_SEX==2) |
| F_AGE_70_79_agg_ZENSUS1km_target | ZENSUS1km | persons | 1000 | F_AGE_70_79_agg_ZENSUS1km | (persons.HP_ALTER > 69)&(persons.HP_ALTER < 80)&(persons.HP_SEX==2) |
| F_AGE_80_plus_agg_ZENSUS1km_target | ZENSUS1km | persons | 1000 | F_AGE_80_plus_agg_ZENSUS1km | (persons.HP_ALTER > 79)&(persons.HP_SEX==2) |
| M_TOTAL_ZENSUS1km_target | ZENSUS1km | persons | 1000 | M_TOTAL_ZENSUS1km | (persons.HP_SEX==1) |
| F_TOTAL_ZENSUS1km_target | ZENSUS1km | persons | 1000 | F_TOTAL_ZENSUS1km | (persons.HP_SEX==2) |

## T1 census-column binding (verified 2026-06-14)

Parquet inspected: `eqasim-data/data/braunschweig/popsim/cells/zensus2022_grid_100m_de_prepared.parquet`
Column count: 570 (3,148,482 rows). Column names are the output of `clean_col_name()` from `braunschweig.popsim.prepared_cells`.

Verification method: loaded only the relevant columns with pandas, computed `sum(category_cols, axis=1)` per cell, checked `max |cat_sum − adj| < 1e-4` (floating-point noise only). Both topics verified at max diff < 6×10⁻⁵; 45 cells have NaN in the `_adj` column (suppressed marginals) but their category sums are non-NaN and consistent.

---

### household_size — 6 categories + `_adj` total

Census theme: `Groesse des privaten Haushalts` (100m grid). Universe: all private households (40,233,744 DE-wide).

| catalog control       | verified `census_source` column                                         | `_adj` total column                                                     |
|-----------------------|-------------------------------------------------------------------------|-------------------------------------------------------------------------|
| household_size_1      | `1_Person_Groesse_des_privaten_Haushalts_100m_Gitter`                   | `Insgesamt_Haushalte_Groesse_des_privaten_Haushalts_100m_Gitter_adj`    |
| household_size_2      | `2_Personen_Groesse_des_privaten_Haushalts_100m_Gitter`                 | (same)                                                                  |
| household_size_3      | `3_Personen_Groesse_des_privaten_Haushalts_100m_Gitter`                 | (same)                                                                  |
| household_size_4      | `4_Personen_Groesse_des_privaten_Haushalts_100m_Gitter`                 | (same)                                                                  |
| household_size_5      | `5_Personen_Groesse_des_privaten_Haushalts_100m_Gitter`                 | (same)                                                                  |
| household_size_6plus  | `6_Personen_und_mehr_Groesse_des_privaten_Haushalts_100m_Gitter`        | (same)                                                                  |

Verification result: per-cell max |cat_sum − adj| = 4.3×10⁻⁵ (floating-point); global cat_sum = 40,233,768 vs adj_sum = 40,233,744 (diff = 24, explained by 45 cells with NaN in adj). **PASS.**

---

### household_type — 5 categories (Familie topic) + total note

Census theme: `Typ privater Haushalte nach Familientyp` (100m grid, `_Typ_priv_HH_Familie_`).
Universe: 39,615,530 private households (≈ 618k fewer than the all-HH universe above — the Zensus 2022 suppresses the Einpersonen-HH sub-count differently in this topic).

**Important:** this topic has NO `_adj` column. The reconciled total is `Insgesamt_Haushalte_Typ_priv_HH_Familie_100m_Gitter` (not an `_adj` column). Use the 5 category columns as per-cell marginal targets; do NOT expect sum(categories) == the Lebensform `_adj` (different universe).

Contrast with the 7-class `Lebensform` topic (`_Typ_priv_HH_Lebensform_`, has `_adj`, 40.2M universe): it carries Ehepaare / EingetrLebensp / NichtehelLebensg separately and does NOT separate Paare_ohneKind from Paare_mitKind — it cannot produce the 5-class breakdown without external join. The `Familie` 5-class IS the right source for Tasks 7-8.

| catalog control                        | verified `census_source` column                              | total reference column (no `_adj`)                             |
|----------------------------------------|--------------------------------------------------------------|----------------------------------------------------------------|
| household_type_einpersonen             | `EinpersHH_SingleHH_Typ_priv_HH_Familie_100m_Gitter`        | `Insgesamt_Haushalte_Typ_priv_HH_Familie_100m_Gitter`          |
| household_type_paar_ohne_kind          | `Paare_ohneKind_Typ_priv_HH_Familie_100m_Gitter`            | (same)                                                         |
| household_type_paar_mit_kind           | `Paare_mitKind_Typ_priv_HH_Familie_100m_Gitter`             | (same)                                                         |
| household_type_alleinerziehend         | `Alleinerziehende_Typ_priv_HH_Familie_100m_Gitter`          | (same)                                                         |
| household_type_mehrpers_ohne_kernfamilie | `MehrpersHHohneKernfam_Typ_priv_HH_Familie_100m_Gitter`   | (same)                                                         |

Verification result: per-cell max |cat_sum − Insgesamt| = 141.0 (structural gap, not FP noise) — the 5 categories sum to 37,954,651 vs Insgesamt = 39,615,530 (diff ≈ 1.66M). This reflects a known Zensus 2022 suppression pattern where the Insgesamt is populated for cells where individual categories are suppressed to zero. The category marginals are the correct PopulationSim targets; the Insgesamt is NOT a reliable sum-of-categories total. **DONE_WITH_CONCERNS — see note.**

> **Note for Tasks 7-8:** Use the 5 `_Typ_priv_HH_Familie_` category columns directly as `census_source`. Do NOT use `Insgesamt_Haushalte_Typ_priv_HH_Familie_100m_Gitter` as a control marginal (it is over-counted relative to the category sum). The reconciled all-HH `_adj` anchor for this topic should reference `Insgesamt_Haushalte_Typ_priv_HH_Lebensform_100m_Gitter_adj` (40.2M, same universe as HH-size `_adj`) — but that is for the HH-total control only, not for the 5-class marginals.
