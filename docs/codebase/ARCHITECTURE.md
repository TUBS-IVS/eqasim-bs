# Architecture

> Focus: how commute flows, gravity calibration, and HH-size IPF compose. Bavaria↔Braunschweig override map is the core artefact. **Data flow from Zensus margins → IPF → gravity calibration → location synthesis → MATSim output.**

## Core Sections (Required)

### 1) Architectural Style

- **Primary style**: **Feature-driven DAG orchestration** (synpp) + **layer-based decomposition** (data → synthesis → output).
- **Why this classification**: Synpp's content-hashed stage DAG allows deterministic, cacheable composition of region-specific data loaders (Braunschweig) with generic synthesis steps (Bavaria/eqasim). Each stage is an atomic, pure function: `execute(context) -> DataFrame` (or tuple of DataFrames). No global mutable state; outputs are deterministic given config + seed.
- **Primary constraints**:
  1. **Bavaria inheritance**: Many core stages live in [bavaria/](bavaria/) and are authored against Bavaria's geography/schema. BS overrides must match their output schema exactly to avoid downstream breakage (CON-003).
  2. **RNG determinism**: All stochastic steps (IPF, sampling, assignment) must consume seeded RNG streams keyed by stage name + config seed. Changing stage execution order breaks reproducibility (BUG-005, BUG-008).
  3. **External calibration**: Gravity model is calibrated to BA Pendleratlas Kreis-level flows, not fitted to Gemeinde-level data. This drives the IPF calibration step as a necessary gap-fill.

### 2) System Flow

```text
Input: Zensus 2022 (5000H-2001, 1000A-2081) + BA Pendler + MiD 2023 ZGB + INKAR
                 │
                 ▼
         [data loaders]
    braunschweig.data.census.*
    braunschweig.data.mid.*
    braunschweig.data.ba.pendler
    braunschweig.data.inkar.*
                 │
                 ▼
    [per-Gemeinde person/household marginals + empirical OD flows]
                 │
                 ├─────────────────────┬─────────────────────┐
                 ▼                     ▼                     ▼
       bavaria.ipf.prepare   braunschweig.data.        braunschweig.data.
                             external_workplaces       census.employment
                 │                     │                     │
                 ▼                     ▼                     ▼
       bavaria.ipf.model         [EXT centroids]    [Wohnort totals per Kreis]
       [Iterative Proportional    [EXT pool ≥50     [For intra-Kreis
        Fitting: sex×age×Kreis]    SvB]             flow synthesis]
                 │
                 ▼
       bavaria.ipf.attributed
       [attached: commune_id, working_age flag, household_id]
                 │
                 ├─────────────────────────────┐
                 ▼                             ▼
    braunschweig.gravity.        synthesis.population.spatial.
    model                        locations.{home,work,education}
    [wraps bavaria.gravity,      [draws destination commune via
     IPF-calibrates on BA,       OD matrix, assigns address via
     injects EXT flows]          density-weighted sampling]
                 │
                 ▼
    synthesis.population.trips
    synthesis.population.activities
    [generates trip legs + activity chains]
                 │
                 ▼
    braunschweig.synthesis.spatial.
    commute_distance
    [MiD P13 distance override for commute legs]
                 │
                 ▼
    matsim.output
    [MATSim XML population; events XML if simulation.output enabled]
```

**Four-step loop**: (1) Load marginals + OD reference, (2) IPF person-level weights, (3) Gravity-calibrated location synthesis, (4) MATSim output.

### 3) Layer/Module Responsibilities

| Layer or module | Owns | Must not own | Evidence |
|-----------------|------|--------------|----------|
| **Input layer** (`braunschweig.data.*`) | Schema normalization, validation, per-scope filtering (ZGB-8 Kreise), ID zero-fill, type casting, encoding fixes | Synthesis logic; downstream dependencies. Free to add custom margins/loaders. | [braunschweig/data/census/household_size.py](braunschweig/data/census/household_size.py), [braunschweig/data/ba/pendler_detailed.py](braunschweig/data/ba/pendler_detailed.py) |
| **IPF layer** (`bavaria.ipf.*` + wrappers) | Sex/age/Kreis stratification, marginal constraint setup, convergence iteration, person replication (stochastic rounding). | Region-specific data sources; location choice. | [bavaria/ipf/model.py](bavaria/ipf/model.py), [bavaria/ipf/attributed.py](bavaria/ipf/attributed.py) |
| **Gravity layer** (`braunschweig.gravity.*`) | Distance-based OD probabilities (Gemeinde pairs), IPF rescaling to match BA Kreis flows, external commuter injection. | HH-size sampling; location assignment. | [braunschweig/gravity/model.py](braunschweig/gravity/model.py#L41-L150) |
| **Location synthesis** (`synthesis.population.spatial.locations.*` + `braunschweig.locations.*`) | Draw destination communes via gravity OD, assign home/work/education addresses via density weighting. | Gravity calibration; IPF. | [braunschweig/locations/work.py](braunschweig/locations/work.py) |
| **Trip + activity layer** (`synthesis.population.{trips,activities}`) | Generate trip legs connecting activities; assign purposes + modes. | Location choice; density sampling. | [synthesis/population/activities.py](synthesis/population/activities.py#L20-L35) |
| **Post-synthesis layer** (`braunschweig.synthesis.*` + `synthesis.synthesis.*`) | HH-type draw, vehicle assignment, income sampling, **commute distance override**. | All upstream layers. | [braunschweig/synthesis/spatial/commute_distance.py](braunschweig/synthesis/spatial/commute_distance.py) |
| **Output layer** (`matsim.output`) | Serialize synthesis DataFrame to MATSim XML, produce population.xml.gz. | Synthesis logic. | [matsim/scenario/population.py](matsim/scenario/population.py) |

### 4) Reused Patterns

| Pattern | Where found | Why it exists |
|---------|-------------|---------------|
| **Synpp stage DAG** | All modules: `configure()` + `execute()` exports | Pure-function, cacheable composition; deterministic caching via content-hashing |
| **DataFrame I/O** | All stages return pandas DataFrames or tuples of DataFrames | Standard schema contract across Bavaria/Braunschweig boundary; allows lazy computation |
| **IPF Iterative Proportional Fitting** | [bavaria/ipf/model.py](bavaria/ipf/model.py#L100-L200) | Enforce marginal constraints (sex×age, household size) while preserving correlation structure (co-location, income) |
| **Gravity model** | [bavaria/gravity/model.py](bavaria/gravity/model.py) + [braunschweig/gravity/model.py](braunschweig/gravity/model.py) | Synthesize Gemeinde-pair OD flows from population + employment + distance; calibrate to BA reference |
| **Density-weighted location sampling** | [bavaria/locations/home.py](bavaria/locations/home.py), [braunschweig/locations/work.py](braunschweig/locations/work.py) | Draw from candidate location pool (residential/commercial buildings) proportional to capacity; preserves spatial heterogeneity |
| **Stochastic rounding** | [synthesis/population/sampled.py](synthesis/population/sampled.py#L37-L39) | Expand fractional person-weights into integer household member counts; preserve totals |
| **RNG seeding by stage name** | [braunschweig/synthesis/spatial/commute_distance.py](braunschweig/synthesis/spatial/commute_distance.py#L162) | Ensure runs with same seed + same stage order produce identical results (reproducibility) |

### 5) Known Architectural Risks

- **Bavaria coupling (CON-001, CON-003)**: Many core stages are in [bavaria/](bavaria/). If we need to fix a bug there (e.g. BUG-002 household member grouping in [bavaria/synthesis/population/sampled.py](bavaria/synthesis/population/sampled.py)), we must either (a) patch Bavaria (upstream sync) or (b) override in BS. Refactor Phase 3 will extract region-neutral code to `eqasim_common/` to break this coupling.
  
- **Java/Python boundary fragility (CON-002)**: MATSim simulation consumes `population.xml.gz` produced by Python. Mode-choice parameters live in Java (`org.eqasim.bavaria.routing.Modes`). If we want to tune mode utilities for ZGB-8 (e.g. fix BUG-E-001, the −10 pp bike bias), we must rebuild Java. Currently out of scope (Decision D-1c).
  
- **RNG non-reproducibility (BUG-005)**: Hardcoded seed offsets (e.g. `91731` in [braunschweig/synthesis/population/enriched.py](braunschweig/synthesis/population/enriched.py#L162)) differ across modules. If synpp stage execution order changes, RNG state drifts → different vehicle/income assignments. Fix: use consistent offset or derive from stage hash.
  
- **Stochastic IPF convergence tolerance (BUG-009)**: IPF convergence check only validates that factors stay within tolerance, not that final weights satisfy all margin targets. Infeasible problems can spuriously "converge" to suboptimal points. No post-convergence assertion.
  
- **Cache invalidation cascade (not documented as bug)**: If a single input data file (e.g. Zensus CSV path) changes, all downstream stages must re-run. Synpp's content-hashing detects this, but re-running a 10% synthesis takes ~4 hours on a local laptop. High cost of iteration.

### 6) Evidence

- [braunschweig/gravity/model.py](braunschweig/gravity/model.py) — wraps bavaria + IPF calibration
- [bavaria/ipf/model.py](bavaria/ipf/model.py) — core IPF solver
- [bavaria/gravity/model.py](bavaria/gravity/model.py#L9-L11) — DEFAULT_SLOPE / CONSTANT / DIAGONAL
- [synthesis/population/activities.py](synthesis/population/activities.py#L22, #L35) — purpose assignment
- [braunschweig/data/census/household_size.py](braunschweig/data/census/household_size.py#L28-L35) — SIZE_BINS structure
- [config_local_braunschweig.yml](config_local_braunschweig.yml#L6-L8) — stage `run:` list and `aliases:` map

---
