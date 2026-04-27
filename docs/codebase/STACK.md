# Technology Stack

> **Focus**: commute/gravity/IPF/household pipeline (Bavaria base + Braunschweig overrides). This is the production stack; experimental branches may differ.

## Core Sections (Required)

### 1) Runtime Summary

| Area | Value | Evidence |
|------|-------|----------|
| Primary language | Python 3.10.10 | [environment.yml](environment.yml#L25) |
| Runtime + version | Python 3.10.10 (conda) | [environment.yml](environment.yml) |
| Package manager | pip + conda | [environment.yml](environment.yml#L2-L4) |
| Module/build system | synpp 1.5.1 (DAG orchestrator) | [environment.yml](environment.yml#L31) |

### 2) Production Frameworks and Dependencies

| Dependency | Version | Role in system | Evidence |
|------------|---------|----------------|----------|
| `synpp` | 1.5.1 | Pipeline DAG orchestration; all stages registered via `configure(context)` and `execute(context)` | [environment.yml](environment.yml#L31) |
| `pandas` | 1.5.3 | DataFrames for all data processing and marginal IPF | [environment.yml](environment.yml#L7) |
| `geopandas` | 1.0.1 | Spatial joins for home/workplace location to Kreise | [environment.yml](environment.yml#L9) |
| `numpy` | 1.23.5 | Array operations, IPF iterations, gravity weighting | [environment.yml](environment.yml#L8) |
| `scipy` | 1.10.1 | Numerical methods (distance matrix, optimization) | [environment.yml](environment.yml#L6) |
| `shapely` | 2.0.6 | Geometry operations for spatial intersections | [environment.yml](environment.yml#L13) |
| `fiona` | 1.10.1 | Read/write GeoPackage and shapefile formats | [environment.yml](environment.yml#L20) |
| `pyrosm` | 0.6.2 | OSM landuse and POI parsing | [environment.yml](environment.yml#L23) |
| `scikit-learn` | 1.2.2 | KDTree for nearest-neighbor location assignment | [environment.yml](environment.yml#L10) |
| `bhepop2` | 2.0.0 | Household income inference via Bayesian microsimulation | [environment.yml](environment.yml#L32) |

### 3) Development Toolchain

| Tool | Purpose | Evidence |
|------|---------|----------|
| `pytest` | 7.2.2 | Unit and integration test runner | [environment.yml](environment.yml#L18), [.github/workflows/tests.yml](.github/workflows/tests.yml) |
| `mock` | 5.1.0 | Test doubles for external data sources | [environment.yml](environment.yml#L19) |
| `Java` | 17+ (corretto) | MATSim simulation runtime (read-only in this cycle) | [.github/workflows/tests.yml](.github/workflows/tests.yml#L18) |
| `Maven` | 3.8+ | Build Java MATSim scenario writer | [.github/workflows/tests.yml](.github/workflows/tests.yml) |
| `osmosis` | 0.48.2 | OSM PBF filtering and data extraction | [.github/workflows/tests.yml](.github/workflows/tests.yml#L20-L41) |

### 4) Key Commands

```bash
# Install environment
conda env create -f environment.yml

# Run pipeline (1% dev config)
python -m synpp config_local_braunschweig.yml

# Run 10% baseline
python -m synpp config_local_braunschweig_10pct.yml

# Run 25% for MATSim
python -m synpp config_local_braunschweig_25pct.yml

# Run all tests
pytest tests/ -v

# Run tests with coverage
pytest tests/ --cov=braunschweig --cov-report=term-missing

# Validation harness (produces 17 plots + HTML + JSON)
python -m scripts.validate_bs_10pct
```

### 5) Environment and Config

- **Config sources**: [config_local_braunschweig.yml](config_local_braunschweig.yml), [config_local_braunschweig_10pct.yml](config_local_braunschweig_10pct.yml), [config_local_braunschweig_25pct.yml](config_local_braunschweig_25pct.yml)
- **Required env vars**: `MKL_CBWR=AUTO` (numpy MKL optimization), conda env `ile-de-france` (per [environment.yml](environment.yml#L1))
- **Key config keys** (per [config_local_braunschweig.yml](config_local_braunschweig.yml)):
  - `sampling_rate`: 0.01 (1%) / 0.10 (10%) / 0.25 (25%)
  - `random_seed`: 1234 (reproducibility seed for IPF + RNG streams)
  - `data_path`: eqasim-data/data (input data root)
  - `output_path`: eqasim-data/output_bs (synthesis CSV + GeoPackage)
  - `working_directory`: eqasim-data/cache_bs (synpp stage cache, content-hashed)
  - `processes`: 8 (parallel workers for stage execution)
  - `gravity_slope`: -0.065 (ZGB-specific IDF calibration)
  - `gravity_constant`: -2.4
  - `gravity_diagonal`: 1.0
  - `bavaria.ipf.use_household_size_margin`: true
  - `bavaria.ipf.use_household_type_margin`: true
  - `bavaria.ipf.use_employment_margin`: true
- **Deployment/runtime constraints**:
  - Java memory requirement: `java_memory: 32G` (MATSim scenario writer)
  - Linux/Windows/macOS support (CI validates both via GitHub Actions)
  - Conda environment activation required for reproducibility

### 6) Evidence

- [environment.yml](environment.yml) — package manifest with pinned versions
- [config_local_braunschweig.yml](config_local_braunschweig.yml) — main config with gravity, IPF, data paths
- [.github/workflows/tests.yml](.github/workflows/tests.yml) — CI/CD pipeline (pytest + MATSim setup)
- [setup.py](setup.py) — [TODO] verify existence for package metadata

## Extended Sections (Optional)

### Full Dependency Taxonomy by Category

**Data I/O**:
- `pandas`, `geopandas`, `fiona`, `pyarrow` (CSV/GeoPackage/HDF5 serialization)
- `openpyxl`, `xlrd` (Excel reading for INKAR tables)
- `py7zr` (7z decompression for Zensus archives)

**Numerics & Spatial**:
- `numpy`, `scipy`, `scikit-learn` (arrays, linear algebra, nearest-neighbor)
- `shapely` (geometry operations)
- `pyrosm` (OSM parsing)

**Utilities**:
- `tqdm` (progress bars for long-running loops)
- `matplotlib`, `palettable` (validation plotting)
- `statsmodels` (statistical inference, OLS for gravity fitting)
- `requests` (HTTP downloads in data loaders)

**Testing & Mocking**:
- `pytest` (test framework)
- `mock` (test doubles)

**Java Interop**:
- Java 17 Corretto runtime (MATSim, eqasim-java classes)
- Maven 3.8+ (build MATSim JAR dependencies)

---
