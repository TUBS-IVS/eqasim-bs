# ARCHITECTURE

> Focus: how commute flows, gravity calibration and HH-size IPF compose. Bavaria↔Braunschweig override map is the core artefact.

## Pipeline DAG (calibration-relevant slice)

```
bavaria.data.census.{population, employment, licenses}
            │
            ▼
   bavaria.ipf.prepare
            │
            ▼
   bavaria.ipf.model           ← person-level IPF (sex × age × Kreis)
            │
            ▼
  bavaria.ipf.attributed       ← attaches commune_id, working-age flag

   ┌────────┴────────────────────────────┐
   ▼                                     ▼
bavaria.gravity.distance_matrix   braunschweig.data.census.pendler
   │                                     │
   ▼                                     │
bavaria.gravity.model  ◄─── IDF SLOPE/CONST ───┘
   │
   ▼
braunschweig.gravity.model   ← wraps bavaria, IPF-calibrates on BA Pendler,
   │                           injects intra-Kreis + outbound EXT flows
   ▼
braunschweig.locations.work  (uses EXT centroids from braunschweig.data.external_workplaces)
   │
   ▼
synthesis.population.spatial.locations.{home,work,education,secondary}
   │
   ▼
synthesis.population.{trips, activities}
   │
   ▼
braunschweig.synthesis.spatial.commute_distance   ← MiD P13 override (post-gravity)
   │
   ▼
matsim XML output  (population.xml.gz)
```

## Bavaria → Braunschweig overrides

| Concern | Bavaria stage | Braunschweig override / addition | File |
|---|---|---|---|
| Population margin | `bavaria.data.census.population` | replaced by Zensus 2022 BS | [braunschweig/data/census/population.py](braunschweig/data/census/population.py) |
| Employment margin | `bavaria.data.census.employment` | replaced for ZGB-8 | [braunschweig/data/census/employment.py](braunschweig/data/census/employment.py) |
| HH-size margin | `bavaria.data.census.household_size` | replaced by Zensus 5000H-2001 (5+ merged) | [braunschweig/data/census/household_size.py](braunschweig/data/census/household_size.py) |
| Commuter OD reference | (none) | new BA Pendleratlas Kreis-pair loader | [braunschweig/data/census/pendler.py](braunschweig/data/census/pendler.py) |
| External workplaces | (none) | new EXT Kreis centroids ≥50 SvB | [braunschweig/data/external_workplaces.py](braunschweig/data/external_workplaces.py) |
| Gravity model | `bavaria.gravity.model` (IDF params) | wrapped + IPF-calibrated on BA + EXT injection | [braunschweig/gravity/model.py](braunschweig/gravity/model.py) |
| Workplace locations | `bavaria.locations.work` | extended to draw from EXT pool | [braunschweig/locations/work.py](braunschweig/locations/work.py) |
| Commute distance | (none) | MiD P13 override after gravity | [braunschweig/synthesis/spatial/commute_distance.py](braunschweig/synthesis/spatial/commute_distance.py) |

**Key design rule (CON-003)**: every BS override is a synpp stage that
either *replaces* a Bavaria data stage (same name in BS namespace) or
*wraps* a Bavaria computation stage by consuming it and emitting a new
DataFrame with identical schema. No monkey-patching.

## Data flow for the four pain points

| Pain point | Code path that produces it | Code path that consumes / measures it |
|---|---|---|
| Commute OD volumes | `braunschweig.gravity.model._calibrate` → `_append_outbound_flows` | `scripts.validate_bs_10pct.metrics.commute_od_kreis` |
| Internal commute distance | gravity matrix + EXT injection + MiD P13 override | `metrics.commute_distance_summary` |
| HH-size distribution | `braunschweig.data.census.household_size.execute` → `bavaria.synthesis.population.enriched` HH-size IPF | `metrics.household_size_per_kreis` |
| Trip-purpose mix | `synthesis.population.activities.execute` (assigns `purpose = preceding_purpose`, last activity = `following_purpose`) | `metrics.purpose_mix` (counts `following_purpose`) |

## Evidence
- [braunschweig/gravity/model.py](braunschweig/gravity/model.py) lines 41-46 (configure stages)
- [bavaria/gravity/model.py](bavaria/gravity/model.py) lines 9-11 (DEFAULT_SLOPE / CONSTANT / DIAGONAL)
- [synthesis/population/activities.py](synthesis/population/activities.py) lines 22, 35 (purpose assignment)
- [braunschweig/data/census/household_size.py](braunschweig/data/census/household_size.py) lines 28-35 (SIZE_BINS, 5+ merged)
