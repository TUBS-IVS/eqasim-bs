# STACK

> **Focus**: commute / gravity / IPF / household pipeline (Bavaria base + Braunschweig overrides).

## Languages & runtimes
- Python 3.10 (`environment.yml`, conda env `eqasim`)
- Java (eqasim-java) — **out of scope**, read-only.

## Frameworks
- `synpp` — pipeline orchestrator (stage DAG). All BS code is expressed as synpp stages.
- `eqasim` — synthesis framework providing population/spatial/trip stages under `synthesis.*` and `bavaria.*`.

## Calibration-relevant dependencies
- `pandas 1.5.3`, `numpy 1.23.5` (frozen by env)
- `geopandas 1.0.1` (spatial joins of homes/workplaces to Kreise)
- `matplotlib` (validation plots)

## Reference data sources (already loaded)
- Zensus 2022 — population, employment, household-size table 5000H-2001 (`data/census/braunschweig/`)
- BA Pendleratlas 2025 — Kreis-pair SvB flows (`braunschweig.data.census.pendler`)
- MiD 2023 ZGB regional CSVs (`data/hts/mid/`, loader `braunschweig.data.mid.references`)
- INKAR / Sirene-equivalents (income tables) — out of scope here.

## Validation harness
- `scripts/validate_bs_10pct/**` — produces 17 PNGs + HTML + JSON from the 10 % cache. **English** since 2026-04-25.

## Evidence
- [environment.yml](environment.yml)
- [bavaria/__init__.py](bavaria/__init__.py), [braunschweig/__init__.py](braunschweig/__init__.py)
- [braunschweig/data/census/household_size.py](braunschweig/data/census/household_size.py)
- [scripts/validate_bs_10pct/__main__.py](scripts/validate_bs_10pct/__main__.py)

## Out of scope (CON-001 / CON-002)
- `bavaria/**` — read-only.
- `eqasim-java` — read-only. Mode-choice constants (R-E) are deferred.
