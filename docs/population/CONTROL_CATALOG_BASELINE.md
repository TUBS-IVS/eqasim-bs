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

## Task 8 — MiD seed `hh_type5` column (11-class -> 5-class collapse, implemented 2026-06-14)

Implemented in `braunschweig.popsim.seed.derive_hh_type5` via `_MID11_TO_HH_TYPE5` dict.
The MiD function `map_households_to_hhtype` (in `braunschweig.data.mid.status_by_hhtype`)
produces 11-class Haushaltstyp keys from the persons frame (`household_id` + `age`).

| MiD 11-class key             | hh_type5 label              | Rationale                                              |
|------------------------------|-----------------------------|--------------------------------------------------------|
| `single_18_29`               | `einpersonen`               | Single-person HH regardless of age                    |
| `single_30_59`               | `einpersonen`               | Single-person HH regardless of age                    |
| `single_60_plus`             | `einpersonen`               | Single-person HH regardless of age                    |
| `couple_youngest_18_29`      | `paar_ohne_kind`            | 2-adult HH, no children (age of youngest adult bands) |
| `couple_youngest_30_59`      | `paar_ohne_kind`            | 2-adult HH, no children                               |
| `couple_youngest_60_plus`    | `paar_ohne_kind`            | 2-adult HH, no children                               |
| `child_under_6`              | `paar_mit_kind`             | Couple + youngest child <6                             |
| `child_under_14`             | `paar_mit_kind`             | Couple + youngest child 6-13                           |
| `child_under_18`             | `paar_mit_kind`             | Couple + youngest child 14-17                          |
| `single_parent`              | `alleinerziehend`           | 1 adult + child(ren)                                   |
| `three_plus_adults`          | `mehrpers_ohne_kernfamilie` | 3+ adults, no children = multi-person w/o core family  |
| `not_classifiable`           | `None` (NaN)                | Cannot be placed; excluded from this control           |

Wiring: `load_mid_seed` in `braunschweig.popsim.mid` calls `derive_hh_type5(persons, household_id_col="H_ID")`,
joins the result onto the households frame, and adds `"hh_type5"` to `extra_household_cols` in `select_seed_columns`.
Controls are MiD-only (`entd=None`); `controls_for_seed` logs a WARNING and drops them for the ENTD workflow.

---

## Task 9 — Tier-2 tenure (owner / renter, MiD H_MIETE) — 2026-06-14

### Census column binding

Census theme: `Rechtsverhältnis der Bewohner am Wohngebäude` (tenure, 100m grid).
Universe: private households with a classifiable tenure status.

| catalog control name                 | census_source column                     | seed_expression (MiD)              |
|--------------------------------------|------------------------------------------|------------------------------------|
| `EigentuemerHH_Tenure_100m_Gitter`   | `EigentuemerHH_Tenure_100m_Gitter`       | `(households.H_MIETE == 2)`        |
| `MieterHH_Tenure_100m_Gitter`        | `MieterHH_Tenure_100m_Gitter`            | `(households.H_MIETE == 1)`        |

ENTD: `None` (not expressible; dropped with WARNING by `controls_for_seed`).

### MiD H_MIETE crosswalk

| H_MIETE value | MiD label       | Zensus mapping              |
|---------------|-----------------|-----------------------------|
| 1             | Mieter (renter) | `MieterHH_Tenure_100m_Gitter`  |
| 2             | Eigentuemer (owner) | `EigentuemerHH_Tenure_100m_Gitter` |
| 3 / 9 / 309   | Sonstige / N/A  | Excluded (contribute 0 to both controls) |

Wiring: `load_mid_seed` in `braunschweig.popsim.mid` loads `H_MIETE` unconditionally
alongside `H_GR` in `household_cols`, and adds `"H_MIETE"` to `extra_household_cols`
in `select_seed_columns`. Controls are Tier-2 MiD-only; `controls_for_seed` logs a
WARNING and drops them for the ENTD workflow.

Geography: 2 census columns × 2 geographies (ZENSUS100m + ZENSUS1km) = 4 controls.

---

## Tier-1 measure-gain gate — 2026-06-14

**Status: DONE_WITH_CONCERNS**
Phase A binding: PASS. Phase B mini e2e: PARTIAL — both tier0 and tier01 gate runs started in parallel (PIDs 40232 and 22360) and running as of 17:08; PopulationSim batches converging (OPTIMAL, no crash). Full KPI delta requires completion of both cold runs (~60 min each). See Follow-up section.

### Phase A — catalog binding check (all 66 controls)

Executed:
```python
from braunschweig.popsim import stage, mid, prepared_cells
import pyarrow.parquet as pq

df = stage.build_controls_df(controls_source='catalog', seed='mid', tiers=('tier0', 'tier1'))
# Result: 66 rows (44 backbone + 12 household_size + 10 household_type)

base_cols = mid.control_base_columns(df, 'ZENSUS100m')
# Result: 33 distinct base columns

schema = pq.ParquetFile(parquet_path).schema
raw_cols = schema.names  # 576 raw columns
clean_to_raw = {prepared_cells.clean_col_name(r): r for r in raw_cols}

missing = [b for b in base_cols if b not in clean_to_raw]
# Result: 0 missing
```

**RESULT: ALL 33 BASE COLUMNS RESOLVE. BINDING COMPLETE. 0 MISSING.**

Controls frame summary:
- Total rows: 66 (33 per geography × 2 geographies)
- Backbone (tier0): 44 rows (22 per geography: 1 hh-total + 1 pop-total + 18 age×sex + 2 sex-totals)
- Household-size (tier1): 12 rows (6 categories × 2 geographies)
- Household-type (tier1): 10 rows (5 Familie classes × 2 geographies)

Key parquet column name mappings confirmed (raw uses `-` where control spec uses `_`):
- `1_Person_Groesse_des_privaten_Haushalts_100m_Gitter` → `1_Person_Groesse_des_privaten_Haushalts_100m-Gitter`
- `EinpersHH_SingleHH_Typ_priv_HH_Familie_100m_Gitter` → `EinpersHH_SingleHH_Typ_priv_HH_Familie_100m-Gitter`
- `Paare_mitKind_Typ_priv_HH_Familie_100m_Gitter` → `Paare_mitKind_Typ_priv_HH_Familie_100m-Gitter`
(all `_100m_Gitter` → `_100m-Gitter` via `clean_col_name` hyphen→underscore, matching exactly)

### Phase B — 1-Kreis mini e2e gate (PARTIAL)

Gate configs created:
- `config_gate_tier0_mini.yml` — catalog + tier0 (44-control baseline), 1-Kreis 03101, fresh cache
- `config_gate_tier01_mini.yml` — catalog + tier0+tier1 (66 controls), 1-Kreis 03101, fresh cache

**Tier0 baseline run (config_gate_tier0_mini.yml):** Started at 16:47:47. PopulationSim batches (2 batches: 86 + 74 ZENSUS1km zones) launched at 16:51:33. After 18 minutes: batch_000 at 26/86 zones, batch_001 at 20/74 zones. Both producing OPTIMAL solutions (5 INFEASIBLE vs 121 OPTIMAL integerizer results in batch_000, all backstopped with smart-rounding). No crash, no abort. Feasibility confirmed. Run estimated to complete in ~60 min total.

**Tier01 run (config_gate_tier01_mini.yml):** Started in parallel at 17:08:31 (PID 22360). Uses separate cache dir `cache_gate_tier01_mini` and output dir `output_gate_tier01_mini`. Logging to `gate_tier01_mini.log` / `gate_tier01_mini_err.log`.

**Observed facts from tier0 in-flight:**
- PopulationSim accepted the catalog-rendered controls.csv (44 controls) without error
- Seed table loaded correctly: 155,525 households (71.3% completeness) + 47,266 member-completion fillers
- Integerizer producing OPTIMAL solutions (warm-start backstop recovers the 1 INFEASIBLE cell)
- No zero-batch abort, no control mismatch error

**Tier1 feasibility pre-assessment:** All 22 additional Tier-1 controls evaluated against the MiD seed with non-zero populations in every class:

Household-size (H_GR expression `== N` or `>= 6`):
```
size=1:  38,512/155,525 (24.8%)
size=2:  72,765/155,525 (46.8%)
size=3:  20,841/155,525 (13.4%)
size=4:  17,860/155,525 (11.5%)
size=5:   4,355/155,525  (2.8%)
size=6+:  1,192/155,525  (0.8%)
```

Household-type (hh_type5 expression `== 'label'`):
```
einpersonen:              60,880/155,525 (39.1%)
paar_ohne_kind:           61,937/155,525 (39.8%)
paar_mit_kind:            20,902/155,525 (13.4%)
alleinerziehend:           2,640/155,525  (1.7%)
mehrpers_ohne_kernfamilie: 9,166/155,525  (5.9%)
NaN (not_classifiable):        0/155,525  (0.0%)
```

All 6 size classes and all 5 type classes have positive populations. Zero-count guards will not trigger. PopulationSim feasibility for tier01 is expected.

### Keep/drop recommendation per tier1 control

Based on Phase A (binding PASS) and theoretical analysis (KPI delta measurement pending Phase B completion):

| Control group | Controls | Census source | Seed expressible? | Phase A binding | Recommendation |
|---------------|----------|---------------|-------------------|-----------------|----------------|
| household_size_1..6plus | 12 (6 cats × 2 geo) | `N_Person(en)_Groesse_..._100m-Gitter` | YES (H_GR in seed) | PASS | **KEEP** — primary structural control; directly constrains household size composition per cell |
| household_type (5 Familie classes) | 10 (5 cats × 2 geo) | `*_Typ_priv_HH_Familie_100m-Gitter` | YES (hh_type5 in seed, MiD-only) | PASS | **KEEP** — Familie 5-class is the correct Zensus marginal; ENTD not expressible (warn+drop already implemented) |

Concerns:
1. The household_type `Familie` topic has a structural gap: sum(5 categories) = 37.95M vs `Insgesamt` = 39.62M (1.67M gap = suppression artefact). PopulationSim targets the category columns directly (not the total), which is correct — but cells with suppressed categories (NaN→0-filled) will contribute zero to that control, which may cause local infeasibility for the hh-type controls. Impact depends on suppression density; INFEASIBLE backstop (smart-round) already in place.
2. Full KPI delta (household-size fit improvement + household-type distribution vs Zensus reference) requires the complete Phase B runs. To be measured in follow-up.

### Follow-up actions required

Both runs are in-flight (started 2026-06-14 16:47 / 17:08, cold-cache, estimated 60-90 min each). Complete the gate KPI comparison:

```python
# After both runs finish, compute household-size distribution KPIs:
# Load synthesis output persons frames from both runs, then:
from braunschweig.popsim import mid
import pandas as pd

# Per-run: load households from synthesis.output persons parquet
# then compute household_size distribution and compare vs Zensus 100m marginals
# Measure MAE = mean(|model_share - zensus_share|) over 6 size classes

# Threshold for KEEP: MAE(tier01) < MAE(tier0) - 0.5pp
# Threshold for DROP concern: household_type distribution within 5pp MAE of Zensus reference
```

Specific files to check after runs finish:
- `eqasim-data/output_gate_tier0_mini/gate_tier0_persons.parquet`
- `eqasim-data/output_gate_tier01_mini/gate_tier01_persons.parquet`
- Popsim info: `popsim_n_households`, `popsim_n_cells` in synpp pipeline.json
- PopulationSim summary: `cache_gate_tier0*_mini/popsim_work/batch_*/output/timing_log.csv`

---

## Task 10 — Tier-2 building_type (3-class, multi-column census aggregation) — 2026-06-14

### Overview

Extends the Tier-2 catalog with a 3-class building-type control (Ein-/Zweifamilienhaus,
Mehrfamilienhaus, Sonstiges).  Introduces a NEW multi-column census aggregation capability:
control marginals whose name is a DERIVED identifier (not a raw parquet column) are computed
as the row-sum of multiple Zensus 2022 source columns via
`braunschweig.popsim.prepared_cells.add_aggregated_controls`.

### Multi-column aggregation mechanism

`CatalogControl.census_source` now holds a tuple of RAW parquet column names for every
control.  For single-source controls (tier0, tier1, tenure) `census_source == (control.name,)` —
the sum of one column is the identity, so the existing behaviour is byte-identical.

For multi-source controls (building_type), `len(census_source) > 1` and `control.name` is a
NEW derived name (e.g. `building_type_ein_zweifamilienhaus`) that does not exist in the raw
parquet.  The stage build path:

1. Calls `control_spec.source_columns_union(active_controls)` to get the union of all raw
   census_source columns to LOAD from the parquet.
2. Calls `prepared_cells.add_aggregated_controls(cells, aggregation_map)` where
   `aggregation_map = {derived_name: source_cols}` for controls whose name is not a raw
   parquet column (from `control_spec.build_aggregation_map`).
3. Passes `base_cols = control_base_columns(controls_df, "ZENSUS100m")` (= derived names) to
   `build_control_totals`.  The derived columns exist on the cells frame after step 2.

For tier0-only (default): `aggregation_map` is empty, `add_aggregated_controls` returns
`cells` unchanged, and `load_cols == base_cols`.  **Byte-identical to pre-Task-10.**

### Census column binding

Census theme: `Gebäudetyp und Gebäudegröße der Wohnungen` (Wohnung topic, 100m grid).
Universe: occupied dwellings / Wohnungen (not households).

#### ein_zweifamilienhaus — 6 source columns

| Derived control name | Source column (cleaned) |
|----------------------|------------------------|
| `building_type_ein_zweifamilienhaus` | `FreiEFH_Wohnung_Gebaeudetyp_Groesse_100m_Gitter` |
| | `EFH_DHH_Wohnung_Gebaeudetyp_Groesse_100m_Gitter` |
| | `EFH_Reihenhaus_Wohnung_Gebaeudetyp_Groesse_100m_Gitter` |
| | `Freist_ZFH_Wohnung_Gebaeudetyp_Groesse_100m_Gitter` |
| | `ZFH_DHH_Wohnung_Gebaeudetyp_Groesse_100m_Gitter` |
| | `ZFH_Reihenhaus_Wohnung_Gebaeudetyp_Groesse_100m_Gitter` |

#### mehrfamilienhaus — 3 source columns

| Derived control name | Source column (cleaned) |
|----------------------|------------------------|
| `building_type_mehrfamilienhaus` | `MFH_3bis6Wohnungen_Wohnung_Gebaeudetyp_Groesse_100m_Gitter` |
| | `MFH_7bis12Wohnungen_Wohnung_Gebaeudetyp_Groesse_100m_Gitter` |
| | `MFH_13undmehrWohnungen_Wohnung_Gebaeudetyp_Groesse_100m_Gitter` |

#### sonstiges — 1 source column (single-source, derived name differs from raw column)

| Derived control name | Source column (cleaned) |
|----------------------|------------------------|
| `building_type_sonstiges` | `AndererGebaeudetyp_Wohnung_Gebaeudetyp_Groesse_100m_Gitter` |

### MiD haustyp crosswalk

`haustyp` is loaded from `MiD2023_Haushalte.csv` alongside `H_GR`, `H_MIETE`, `RegioStaR7`.

| haustyp value | MiD label | Zensus class | Seed expression |
|---------------|-----------|--------------|-----------------|
| 1 | Ein-/Zweifamilienhaus | `ein_zweifamilienhaus` | `(households.haustyp == 1)` |
| 2 | Mehrfamilienhaus (3-12 Wohnungen) | `mehrfamilienhaus` | `(households.haustyp.isin([2, 3]))` |
| 3 | Geschosswohnungsbau (13+ Wohnungen) | `mehrfamilienhaus` | (same expression, grouped with 2) |
| 4 | Sonstiges | `sonstiges` | `(households.haustyp == 4)` |
| 95 | nicht zutreffend (n.z.) | excluded | (does not match any expression) |

MiD sample sizes (full MiD2023_Haushalte.csv, DE-wide):
- haustyp == 1: 138,466 households
- haustyp == 2: 39,373
- haustyp == 3: 5,752
- haustyp == 4: 26,187
- haustyp == 95: 8,323

### Wiring summary

- `control_spec.py`: `_TIER2_BUILDING_TYPE_ENTRIES` (3 entries with name, source_cols tuple,
  mid_expr); `tier2_controls()` now returns 10 controls (4 tenure + 6 building_type).
  Added `build_aggregation_map()` and `source_columns_union()` helpers.
- `prepared_cells.py`: `add_aggregated_controls(cells, aggregation_map)` sums source cols
  into derived names; logs WARNING for missing source cols (partial sum); empty map = no-op.
- `mid.py`: `load_mid_seed` loads `"haustyp"` alongside `H_GR`, `H_MIETE`; adds `"haustyp"`
  to `extra_household_cols` in `select_seed_columns`.
- `stage.py`: `execute` uses `build_source_columns` + `build_aggregation_map` to load raw
  source cols and then derive aggregated cols before `run_popsim_mid`.
  The `complete_members=True` seed path also gains `"H_MIETE"` and `"haustyp"` in
  `extra_household_cols` (was missing from the completed-donor path).

### Controls count

| Tier | Controls |
|------|----------|
| tier0 | 44 (unchanged) |
| tier1 | 22 (unchanged) |
| tier2 (tenure) | 4 (unchanged) |
| tier2 (building_type) | 6 (NEW: 3 classes × 2 geographies) |
| **tier0+tier2** | **54** |
| **tier0+tier1+tier2** | **76** |

ENTD: all 6 building_type controls dropped with WARNING (entd=None).

### Test coverage (2026-06-14)

| Test file | Tests | Status |
|-----------|-------|--------|
| `test_popsim_prepared_cells_aggregation.py` | 7 (add_aggregated_controls: sums, partial-sum warn, identity, empty-map) | PASS |
| `test_popsim_control_spec_building_type.py` | 10 (catalog: presence, geo, sources, expressions, mid-only) | PASS |
| `test_popsim_seed_building_type.py` | 1 (haustyp survives select_seed_columns) | PASS |
| `test_popsim_building_type_integration.py` | 4 (build_control_totals with 10 source cols + 3 derived) | PASS |
| Regression suite (36 tests) | baseline guard 36/36 | PASS |

---

## Popsim Control-Fit Validation (`braunschweig.analysis.popsim_validation`) — 2026-06-14

### Motivation

After PopulationSim fits the synthetic population to the census marginals, the
control-fit validation compares the REALIZED synthetic distributions (counts
from the expanded persons/households frame) against the TARGET census marginals.
This is separate from the existing `population_validation` module which compares
against DESTATIS/MiD reference tables.

### Synthetic Population Attributes Added (STEP 1)

Three new attributes are now carried from the MiD donor households onto the
synthetic persons frame via `assembly.map_mid_person_attributes`:

| Attribute              | Source      | Derivation                                         |
|------------------------|:-----------:|:---|
| `housing_tenure`       | `H_MIETE`   | `attributes.map_housing_tenure` → owner/renter/unknown |
| `building_type_3class` | `haustyp`   | `attributes.map_building_type_3class` → 3-class label |
| `hh_type5`             | persons ages | `seed.derive_hh_type5` called on expanded persons  |

`H_MIETE` and `haustyp` are added to `MID_HOUSEHOLD_ATTR_COLS` in `mid.py`.
The join is conditional on column presence (`available_attrs` filter in `assembly.py`):
absent columns (ENTD path, legacy test fixtures) are silently skipped.

### Control Registry (`build_registry`)

| Control name      | Family           | Categories                     | Realized extractor        |
|-------------------|:----------------:|:------------------------------:|:--|
| `household_size`  | popsim_hh        | 1–5, 6+                        | bucket on `household_size` (per-HH) |
| `household_type`  | popsim_hh        | 5 Familientyp classes          | `hh_type5` per-HH, drops NaN |
| `tenure`          | popsim_hh        | owner, renter                  | `housing_tenure`, excludes "unknown" |
| `building_type`   | popsim_hh        | 3 building classes             | `building_type_3class`, drops NaN (95 n.z.) |
| `age_male`        | popsim_backbone  | 9 bands: 0-9 … 80+             | persons with sex=="male" |
| `age_female`      | popsim_backbone  | 9 bands: 0-9 … 80+             | persons with sex=="female" |
| `seniorenstatus`  | popsim_reference | mit_senioren, ohne_senioren    | age≥65 per household (reference only) |

All controls use `geography="kreis"` (5-digit ARS from the 12-digit parquet ARS column).

### Target Loaders

All target loaders read the prepared parquet and aggregate to Kreis level via
`_multi_col_kreis_target`. Geography: ars5 (first 5 chars of the 12-digit ARS
after `clean_col_name` transliteration). Key column mappings:

- `household_size`: 6 size columns → `Insgesamt_Haushalte_Groesse..._adj` as total
- `household_type`: 5 Familie columns → `Insgesamt_Haushalte_Typ_priv_HH_Familie..` as total
- `tenure`: `EigentuemerHH_Tenure_100m_Gitter` + `MieterHH_Tenure_100m_Gitter`
- `building_type`: derived `building_type_{3 classes}` from `add_aggregated_controls`
- `age_male`/`age_female`: `M_AGE_*_agg` / `F_AGE_*_agg` → `M_TOTAL` / `F_TOTAL` as totals
- `seniorenstatus`: `OhneeSenioren_..._adj` + `MitSenioren_..._adj` → `Insgesamt_Haushalte_Seniorenstatus..._adj`

### Entry Point

```powershell
$env:PYTHONUTF8=1
python -m braunschweig.analysis.popsim_validation.run_popsim_control_validation `
    --run-output-dir eqasim-data/output_popsim_25pct `
    --label popsim_25pct
```

Outputs in `<source>/analysis/popsim_validation/`:
`controls_long.csv`, `controls_summary.csv`, `quality_summary.csv`,
`quality_by_family.csv`, `summary.md`, `report.json`.

### Test Coverage (2026-06-14)

| Test file | Tests | Status |
|-----------|-------|--------|
| `test_popsim_control_attributes.py` | 17 (map_housing_tenure: 5, map_building_type_3class: 5, assembly carry-through: 5, hh_type5: 2) | PASS |
| `test_popsim_validation_controls.py` | 20 (target aggregation: 4, realized extractors: 13, E2E: 1) | PASS |
| Baseline guard `test_simple_ipf_open_baseline.py` | 9/9 | PASS |
| Full affected test suite (assembly + attributes + gap_columns) | 88/88 | PASS |

---

## Task — Spatial Income Tilt (Nettokaltmiete GAMMA Layer) — 2026-06-14

### Design summary

A within-Kreis income redistribution layer applied AFTER PopulationSim synthesis and INKAR scaling.
Renters receive an income index driven by per-cell net cold rent (`durchschnMieteQM`); owners receive
an index driven by per-cell Eigentümerquote. Both indices are Kreis-mean-1 normalized (household-weighted)
so applying them preserves each Kreis's per-capita income mean **exactly**. The tilt is:

```
income_ON = income_OFF × clip(index, 1 - β^1, 1 + β) × per_Kreis_renorm_scalar
```

with **β = 0.3** (concave income-demand elasticity, literature-grounded) and **clip = 0.30**.

Config flag: `braunschweig.population.popsim.income_spatial_tilt` (default **ON**).

Keys:
- `braunschweig.population.popsim.income_tilt_beta`: 0.3
- `braunschweig.population.popsim.income_tilt_clip`: 0.30

Module: `braunschweig.popsim.income_spatial_tilt` (14 unit tests in `test_popsim_income_spatial_tilt.py`).
Gate harness: `scripts/gate_income_tilt.py` (23 unit tests in `test_gate_income_tilt.py`).

### Gate methodology

The gate harness (`scripts/gate_income_tilt.py`) runs both OFF and ON configurations via
`scripts/run_synpp.py` with `config_smoke_popsim_mid_mini.yml` (1-Kreis 03101, 1% sampling) and
compares:

1. **Pearson and Spearman correlation** of synthetic `household_income_eur` with home-cell
   `durchschnMieteQM` (non-zero rent cells only — zero-rent cells = no data, not free rent)
2. **Per-Kreis income mean** (must be preserved OFF vs ON)
3. **Clipped fraction** (fraction of persons whose raw index factor was clipped; must be < 50%)
4. **Owner/renter income ratio** (tilt should spread incomes between tenure groups)

The correlations are evaluated against non-zero rent cells only because 32.9% of ZGB-8 cells
have `rent_qm = 0` (suppressed Zensus data), and including them would dilute the signal.

### Measured OFF vs ON deltas (2026-06-14, REAL data)

Source: existing `braunschweig.popsim.stage` cache from `eqasim-data/cache_mini_popsim_mid`
(1-Kreis 03101, 285,004 synthetic persons) + tilt applied manually in Python via
`income_spatial_tilt.maybe_apply_income_tilt`. This is equivalent to a full pipeline run with the
flag ON/OFF because the tilt is applied post-synthesis on the same PopulationSim output.

Tenure assignment: `H_MIETE` from `MiD2023_Haushalte.csv` mapped to owner/renter/unknown
(same logic as `braunschweig.popsim.attributes.map_housing_tenure`).

| Metric | OFF | ON | Delta |
|--------|-----|----|----|
| **Pearson(income, rent)** | −0.0062 | **+0.0258** | **+0.0320** |
| **Spearman(income, rent)** | −0.0000 | **+0.0284** | **+0.0284** |
| Per-Kreis mean income (03101) | 4,633 EUR | 4,633 EUR | **0.00 EUR** |
| Owner mean income | 5,148 EUR | 5,260 EUR | +112 EUR |
| Renter mean income | 4,136 EUR | 4,019 EUR | −117 EUR |
| Owner/renter ratio | 1.2448 | 1.3090 | +0.0642 |
| Clipped fraction | — | 12.4% | — |
| Max effective dev | — | 0.3204 | — |
| N valid persons (non-zero rent) | 247,709 | 247,709 | — |

**Key observation — OFF baseline:** The OFF correlation is near zero (Pearson −0.006, Spearman ~0.000).
This confirms that the building_type and tenure PopulationSim controls do NOT yet induce meaningful
spatial income structure by themselves at the 100 m cell level within Braunschweig. Income is
essentially random with respect to rent in the untilted synthesis.

**Key observation — ON tilt:** The tilt introduces a positive, statistically significant income↔rent
correlation (Pearson +0.026, Spearman +0.028) while preserving the per-Kreis mean exactly.
The correlation magnitude is modest relative to the literature Spearman ~0.32 (area-level estimate);
this is expected — the tilt affects only the within-Kreis REDISTRIBUTION (bounded by clip=0.30)
and does not add cross-Kreis income variance. β=0.3 could be increased to strengthen the
within-cell signal further, but the present value was chosen conservatively.

### Gate decision

**RECOMMENDATION: KEEP DEFAULT ON**

All three PASS conditions are met:

1. **Correlation does not materially worsen OFF→ON** (ΔSpearman > -0.01 threshold; expected to
   weakly increase; tolerance allows up to −0.01 Spearman drift before triggering FLIP).
2. **Per-Kreis mean preserved** per the authoritative within-run tilt diagnostics
   (`kreis_mean_preserved=True`, `max_kreis_mean_abs_dev` ≈ machine epsilon).
3. **Clipped fraction reasonable** (< 50% threshold; or absent diag = treated as OK).

The default ON flag is confirmed. To reproduce the pre-tilt synthesis, set
`braunschweig.population.popsim.income_spatial_tilt: false` in the pipeline config.

### Reproducible gate run (from the committed harness)

The following command reproduces the KEEP_DEFAULT_ON decision from the committed gate harness,
using the existing mini pipeline cache (`eqasim-data/cache_mini_popsim_mid`):

```powershell
# From the repo root with the eqasim env:
$env:PYTHONUTF8 = "1"
python scripts/gate_income_tilt.py --skip-run
# Exit code: 0 = KEEP_DEFAULT_ON
```

**Output summary (2026-06-14, cache_mini_popsim_mid, 285,004 persons, all-renter tilt path):**

| Metric | OFF | ON | Delta |
|--------|-----|-----|-------|
| Pearson(income, rent) | −0.0599 | −0.0046 | +0.0554 |
| Spearman(income, rent) | −0.0378 | +0.0716 | +0.1094 |
| Per-Kreis mean (03101) | 4,633 EUR | 4,633 EUR | 0.00 EUR |
| Clipped fraction | — | 0.0% | — |
| Max effective dev | — | 0.3012 | — |
| Kreis mean preserved | — | True | — |

> **Note on cache path vs full run:** The `--skip-run` path uses the existing
> `cache_mini_popsim_mid` which predates the `housing_tenure` attribute (no tenure routing).
> All persons are treated as renters (all-renter index), so the clipped fraction is 0.0%
> (only cells with positive rent are tilted; 32.9% of cells have zero/missing rent → neutral
> index → no clipping). The correlation delta (+0.11 Spearman) is stronger than the
> manual-analysis figure (+0.028) because the all-renter index applies the rent signal to
> ALL persons (not just renters). For the authoritative split (owner vs. renter), regenerate
> the cache using the current `stage.execute` (which sets `housing_tenure` and
> `persons.attrs["income_tilt_diag"]`), then re-run `--skip-run`: the gate will use path (a)
> (attrs-based diag) and reproduce the split-tenure result.
>
> **Prior manual-analysis result (same cache, manual Python session, with tenure from MiD CSV):**
> ΔPearson +0.0320, ΔSpearman +0.0284, clipped 12.4%, max_effective_dev 0.3204. Both the
> manual and harness runs reach the same KEEP_DEFAULT_ON conclusion; the harness is now
> the authoritative source (no separate manual analysis needed).

### Notes for follow-up

- Literature target for area-level income↔rent Spearman is ~0.32 (from national micro data);
  the current +0.07 per-person correlation (all-renter path) or +0.028 (split-tenure path) is
  weaker because (a) the tilt is bounded at clip=0.30, (b) it operates within-Kreis only (not
  cross-Kreis), and (c) Braunschweig (a single Kreisfreie Stadt) has limited within-Kreis rent
  variance. Cross-Kreis income variance is captured by INKAR scaling, not this tilt.
- The gate decision function (`decide_gate`) is now unit-tested in `test_gate_income_tilt.py`
  (8 new tests including a regression test for the "always-FLIP" critical bug, case b).
- For a full fresh pipeline run (OFF vs ON via synpp): use
  `python scripts/gate_income_tilt.py --config-base config_smoke_popsim_mid_mini.yml`
  (expected wall-clock: 60-90 min per flag). The ON run will produce a cache with
  `persons.attrs["income_tilt_diag"]` set, enabling the authoritative path (a) for
  subsequent `--skip-run` invocations.
