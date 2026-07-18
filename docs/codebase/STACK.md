# STACK

> **Refreshed 2026-07-18.** Stack essentials: Python 3.10.10 (conda env `eqasim`),
> `synpp 1.6.2`, pandas 1.5.3 / numpy 1.23.5 / scipy 1.10.1, geopandas 1.0.1 /
> shapely 2.0.6 / fiona 1.10.1 / pyrosm 0.6.2, `carla` chainsolvers pinned to git
> `d8d8ae7`, PopulationSim. Java MATSim/eqasim side upgraded to **eqasim-java 2.2.0**
> (branch `main`) via the `eqasim-java-bs` fork; **building it now requires JDK 25**
> (upstream bumped `maven.compiler` 21 -> 25). Current feature state: see
> ARCHITECTURE.md banner.

Technology stack for `eqasim-bs`, the synthetic-population pipeline for the
Großraum Braunschweig (ZGB-8) region. Verified from `environment.yml`, the
config files, the CI workflow, and the source tree. Document scope is the
**Python / synpp** pipeline; the MATSim/eqasim **Java** side is invoked as an
external, cached toolchain (see "Java / MATSim side" below).

## Language and runtime

- **Python 3.10** (`environment.yml` pins `python=3.10.10`). README and AGENTS.md
  both state Python 3.10 via miniforge.
- **Conda environment.** Created from `environment.yml`. Note a naming divergence:
  the env file declares `name: ile-de-france` (inherited from upstream) and the
  CI workflow activates `ile-de-france`, but README/AGENTS.md/CLAUDE.md instruct
  contributors to use the env named `eqasim`. See CONCERNS.md. `[ASK USER]`
  whether the canonical local env name is `eqasim` or `ile-de-france`.
- Pipeline entry point: `python -m synpp <config>.yml` (README §4; AGENTS.md
  "Day-to-day commands").

## Pipeline framework

- **synpp 1.6.2** (`environment.yml` pip section) — content-hashed DAG runner.
  Pipeline stages are dotted Python module paths with `configure(context)` /
  `execute(context)` functions (verified in
  `braunschweig/synthesis/locations/education_gravity.py`, lines 239/310).

## Key Python libraries (pinned in `environment.yml`)

Scientific / data:
- `pandas=1.5.3`, `numpy=1.23.5`, `scipy=1.10.1`, `scikit-learn=1.2.2`,
  `statsmodels=0.14.3`, `numba=0.56.4`
- `pytables=3.9.2` (HDF5), `pyarrow=16.1.0` (Parquet)

Geospatial:
- `geopandas=1.0.1`, `shapely=2.0.6`, `fiona=1.10.1`, `pyogrio=0.10.0`,
  `pyrosm=0.6.2` (OSM PBF reader)

I/O and utilities:
- `openpyxl=3.1.0`, `xlrd=2.0.1`, `xlwt=1.3.0` (Excel), `py7zr=0.20.8`,
  `requests=2.32.3`, `tqdm=4.65.0`, `matplotlib=3.7.1`, `palettable=3.3.0`,
  `sqlite=3.49.1`, `mock=5.1.0`

Pip-only:
- `synpp==1.6.2`, `bhepop2==2.0.0` (income synthesis)

PopulationSim (popsim branch only): **not** part of this env. The popsim
workflows run `populationsim` as a **subprocess in its own `uv`-managed
environment** (`uv_path` / `popsimprep_dir` config keys,
`braunschweig/popsim/batch.py`) — deliberate isolation because the eqasim env's
BLAS/LAPACK is broken and PopulationSim pins different pandas/numpy versions.

Note: `geopy` is referenced in the task brief but is **not** pinned in
`environment.yml`. School geocoding is described in
`eqasim-data/data/braunschweig/schools/README.md` as using OSM Nominatim; the
geocoding dependency is `[TODO]` to confirm (may be invoked via `requests` or an
unpinned import in a `scripts/` geocoder).

## Dev tooling

- **pytest 7.2.2** (`environment.yml`). Tests live in `tests/` (see TESTING.md).
- **No linter/formatter config** found in the repo root (scan: "No linting or
  formatting config files found"). No `pyproject.toml`, `setup.cfg`, `setup.py`,
  `pytest.ini`, or `conftest.py` present (verified by directory listing).
- CI: GitHub Actions (`.github/workflows/tests.yml`, `data.yml`) and a legacy
  `.travis.yml`. The tests workflow runs `pytest tests/` on ubuntu + windows and
  sets up Java (Corretto 17) + Maven + osmosis.

## Java / MATSim side

- The pipeline's `matsim.output` stage builds a MATSim scenario via the **eqasim
  Java** toolchain. synpp downloads and caches the Java sources under
  `eqasim-data/cache_*/matsim.runtime.eqasim*` and `…pt2matsim*` (these are
  nested git checkouts, gitignored — AGENTS.md, `.gitignore`).
- **eqasim-java 2.2.0** (branch `main`, commit `ab938aaac`) via the
  `eqasim-java-bs` fork (`matsim/runtime/eqasim.py:8-10` DEFAULT_EQASIM_VERSION /
  BRANCH / COMMIT). Upstream v2.2.0 bumped `maven.compiler` source/target 21 -> 25,
  so **building the jar now requires JDK 25** (Temurin 25 installed on felix and
  locally; the felix + local run configs point `java_home` / `java_binary` at it).
  The Maven runtime honours a new `java_home` config key
  (`matsim/runtime/maven.py`) that exports JAVA_HOME for the build subprocess.
- CI (`.github/workflows/tests.yml`) still installs **Java 17 (Corretto)** for the
  Python test suite — that is unchanged and sufficient for pytest, but NOT for
  building eqasim-java 2.2.0 (which needs JDK 25); the Java jar build is exercised
  on felix / locally, not in the Python CI.
- The Java MATSim package is `org.eqasim.braunschweig.*` in the fork (renamed from
  `bavaria`), though several config files and `matsim/simulation/prepare.py` still
  reference `org.eqasim.bavaria.*` entry-point class paths (see CONCERNS.md).
- External binaries configured per-machine in the config:
  `osmosis_binary`, `osmconvert_binary` (Windows paths in
  `config_local_braunschweig.yml`).

## Evidence

- `environment.yml`
- `config_local_braunschweig.yml` (entry `run:`, `processes`, binaries)
- `.github/workflows/tests.yml` (Java 17, conda env `ile-de-france`, `pytest tests/`)
- `README.md` §2 (env), §4 (run command)
- `AGENTS.md` "Environment", "Day-to-day commands"
- `braunschweig/synthesis/locations/education_gravity.py` (synpp `configure`/`execute`)
- `eqasim-data/data/braunschweig/schools/README.md` (geocoding via Nominatim)

---

## Cross-repo addendum: population-synthesis refactor (popsimprep)

Added 2026-06-08 for the three-workflow population-generation refactor
(`population.method ∈ {simple_ipf_open, popsim_open, popsim_mid}`). The refactor
folds a **second repository** into the quaSIM population stage:
a sibling `popsimprep` checkout (`../popsimprep`).

### popsimprep stack (the new `popsim_*` workflows)

- **Python 3.11** (Docker base `python:3.11`; `pyproject.toml requires-python>=3.11`),
  `uv`-managed (`uv.lock`, `uv run populationsim`). eqasim-bs is Python **3.10**
  conda — a **version gap to reconcile** (`[ASK USER]`: one env or two).
- **PopulationSim ≥ 0.10.0** (activitysim-family synthesizer) — the core new
  dependency. Needs a real BLAS/LAPACK (Fortran balancers); the popsimprep Docker
  installs `gfortran libopenblas0-pthread`. This **directly collides** with the
  known broken reference-BLAS in the eqasim conda env (CONCERNS.md / memory
  `eqasim-env-lapack-broken`). Evidence: `popsimprep/pyproject.toml`,
  `popsimprep/docker/dev.Dockerfile`.
- Shared data stack overlaps eqasim-bs but at **newer pins**: pandas≥2.2 (vs
  1.5.3), numpy<3 (vs 1.23.5), pyarrow≥18 (vs 16.1), geopandas≥1.0 (matches),
  scipy≥1.14 (vs 1.10), unidecode, python-dotenv. A merged env must satisfy both.
- `populationsim` is invoked as a **CLI subprocess** (`uv run populationsim -w <folder>`),
  not as a library — see `popsimprep/batch_run_popsim.py:285`.

### Implication for the merged stack

The cleanest integration is likely a **separate PopulationSim environment**
invoked as a subprocess from the quaSIM synpp stage (mirrors how eqasim-bs already
shells out to the Java/osmosis toolchain), rather than importing PopulationSim
into the 3.10 eqasim env. `[ASK USER]` to confirm the env strategy.

Evidence: `popsimprep/pyproject.toml`, `popsimprep/docker/dev.Dockerfile`,
`popsimprep/uv.lock`, `popsimprep/batch_run_popsim.py`.
