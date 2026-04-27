# Coding Conventions

> Conventions observed across the Braunschweig pipeline. Refactor Phase 3 will enforce some of these via linting; currently documentation only.

## Core Sections (Required)

### 1) Naming Rules

| Item | Rule | Example | Evidence |
|------|------|---------|----------|
| **Files** | `snake_case.py` | [household_size.py](braunschweig/data/census/household_size.py), [pendler_detailed.py](braunschweig/data/ba/pendler_detailed.py) | All source files in [braunschweig/](braunschweig/), [bavaria/](bavaria/), [synthesis/](synthesis/) |
| **Modules** | dotted path mirrors directory structure | `braunschweig.data.census.household_size`, `bavaria.ipf.model` | [config_local_braunschweig.yml](config_local_braunschweig.yml#L6-L8) stage names |
| **Functions/methods** | `snake_case`; private/internal prefixed with `_` | `_load_region_distribution()`, `_synthesise_intra_kreis()` | [braunschweig/data/census/household_size.py](braunschweig/data/census/household_size.py#L68), [braunschweig/gravity/model.py](braunschweig/gravity/model.py#L71, #L93) |
| **Classes** | `PascalCase` (rare; most stages are modules, not classes) | `[TODO]` discover example | [TODO] |
| **Constants/env vars** | `UPPER_SNAKE_CASE` | `SIZE_BINS`, `AGE_STRATA`, `MAX_IPF_ITERATIONS`, `IPF_TOLERANCE` | [braunschweig/data/census/household_size.py](braunschweig/data/census/household_size.py#L18-L25) |
| **Synpp stage names** | Dotted path; no hyphens or underscores in stage name itself | `braunschweig.gravity.model` (file `braunschweig/gravity/model.py` exports via `configure()` + `execute()`) | [config_local_braunschweig.yml](config_local_braunschweig.yml) |
| **DataFrame columns** | `snake_case`; ID columns suffixed `_id` | `commune_id`, `household_id`, `person_id`, `weight`, `working_age`, `lower_age`, `upper_age`, `sex` | [braunschweig/data/census/household_size.py](braunschweig/data/census/household_size.py#L50-L60) |

### 2) Formatting and Linting

- **Formatter**: None currently enforced. Suggested: `black` with line length 100.
- **Linter**: None currently enforced. Suggested: `pylint` or `flake8`.
- **Most relevant rules**:
  - Docstrings: Google style (triple-quoted, Parameter/Returns/Raises sections) for all public functions and modules. See exemplars in [braunschweig/gravity/model.py](braunschweig/gravity/model.py#L1-L27) (module docstring), [braunschweig/data/census/household_size.py](braunschweig/data/census/household_size.py#L1-L25) (module docstring).
  - Type hints: Optional but encouraged for function signatures (no static type checking configured yet; `[TODO]` add `mypy`).
  - Line length: Soft target 100 chars (follow existing code).
  - Imports: Always `from __future__ import annotations` at module top (enables Python 3.10 generic syntax). Standard library, then third-party, then local imports, grouped by blank lines.
- **Run commands**: `[TODO]` add `make format` / `make lint` targets when CI is set up.

### 3) Import and Module Conventions

- **Import grouping/order**:
  ```python
  from __future__ import annotations  # Python 3.10+ generic syntax

  import os                            # stdlib
  import pathlib
  import sys

  import numpy as np                   # third-party
  import pandas as pd
  import scipy.stats
  import geopandas as gpd

  # local imports (none for pure-function stages; synpp context injected)
  ```
  See exemplar: [braunschweig/data/census/household_size.py](braunschweig/data/census/household_size.py#L19-L26).

- **Alias vs relative import policy**: 
  - Relative imports not used (all absolute module paths).
  - No path aliases configured (all imports resolve via package tree).
  - Synpp stages do NOT import each other directly; they depend via `context.stage()` (Dependency Injection via DAG).

- **Public exports/barrel policy**: 
  - No `__all__` barrel exports. Synpp discovers stages via `configure()` function.
  - Each stage module is standalone; import it directly if used outside synpp.

### 4) Error and Logging Conventions

- **Error strategy by layer**:
  - **Data loaders** ([braunschweig/data/](braunschweig/data/)): Raise `RuntimeError` or `ValueError` with diagnostic context (file path, row count, schema mismatch). Examples: [braunschweig/data/census/household_size.py](braunschweig/data/census/household_size.py#L70) raises if no rows found.
  - **IPF/Gravity**: Assert post-conditions (e.g. marginal totals match targets per BUG-009). Raise `AssertionError` with details if violated.
  - **Synthesis/Location**: Warn (via `print()` or later `logging`) if fallback applied (e.g. no work location found, use home location). Raise only if fundamental data is missing.
  - **Output**: Raise if output path does not exist or write fails (file permission, disk full).

- **Logging style and required context fields**: 
  - No logging framework currently configured. Stages use `print()` or return logs as stdout.
  - Suggested future logging keys: `stage_name`, `scope`, `row_count`, `execution_time_sec`.
  - Example future log line: `[braunschweig.gravity.model] Calibrated OD matrix: 1200 Gemeinde pairs, IPF converged in 8 iterations, R² vs BA flows = 0.91`.

- **Sensitive-data redaction rules**: 
  - No PII in logs. Aggregate counts only (no person-level IDs in output except in final CSV).
  - File paths acceptable (all data is public Zensus / BA / MiD).

### 5) Testing Conventions

- **Test file naming/location rule**: Co-located with source, in [tests/](tests/) directory. Pattern: `test_<module_name>.py`. Examples: [tests/test_braunschweig_data.py](tests/test_braunschweig_data.py), [tests/test_hh_size_margin.py](tests/test_hh_size_margin.py).

- **Mocking strategy norm**: 
  - Unit tests use dependency injection: mock data frames passed as function arguments (no global state).
  - Integration tests use `pytest` fixtures (see [tests/testdata.py](tests/testdata.py)) to create temporary cache/output directories.
  - No `@patch` decorator mocking; prefer composable, pure functions.

- **Coverage expectation**: [TODO] set threshold (suggested: 80% for braunschweig/*, 60% for legacy bavaria/). Current coverage unknown; baseline to be recorded in Phase 1.

### 6) Evidence

- [braunschweig/gravity/model.py](braunschweig/gravity/model.py#L1-L50) — exemplar docstring, imports, naming
- [braunschweig/data/census/household_size.py](braunschweig/data/census/household_size.py) — exemplar module structure, constants, functions
- [tests/test_braunschweig_data.py](tests/test_braunschweig_data.py#L1-L30) — exemplar test file location + naming
- [config_local_braunschweig.yml](config_local_braunschweig.yml) — stage name format

## Known Convention Violations (to clean up in refactor)

- **Language mix**: Docstrings and comments are a mix of English and German. Refactor Phase 3 standardizes on English.
- **Docstring inconsistency**: Not all functions have docstrings; some are sparse. Phase 3 adds comprehensive docstrings per Google style.
- **Type hints absent**: No `mypy` configured. Phase 3 adds type hints to function signatures.

---
