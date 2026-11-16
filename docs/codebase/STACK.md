# STACK

> **Refreshed 2026-07-18.** Stack essentials: Python 3.10.10 (conda env `eqasim`),
> `synpp 1.6.2`, pandas 1.5.3 / numpy 1.23.5 / scipy 1.10.1, geopandas 1.0.1 /
> shapely 2.0.6 / fiona 1.10.1 / pyrosm 0.6.2, `carla` chainsolvers pinned to git
> `d8d8ae7`, PopulationSim. The Java MATSim/eqasim side comes from the
> `eqasim-java-bs` fork; its version is owned by the Java pom (see "Java / MATSim
> side"); **building it requires JDK 25**. Current feature state: see the
> ARCHITECTURE.md banner (generated views under `docs/generated/`).

Technology stack for `eqasim-bs`, the synthetic-population pipeline for the
Großraum Braunschweig (ZGB-8) region. Verified from `environment.yml`, the
config files, the CI workflow, and the source tree. Document scope is the
**Python / synpp** pipeline; the MATSim/eqasim **Java** side is invoked as an
external, cached toolchain (see "Java / MATSim side" below).

## Language and runtime

- **Python 3.10** (`environment.yml` pins `python=3.10.10`). README and AGENTS.md
  both state Python 3.10 via miniforge.
- **Conda environment `eqasim`.** Linux/WSL reproduces the captured server
  conda/pip snapshot; Windows installs its generated lock. `environment.yml`
  is the Windows source specification, not a command to re-solve production.
  See [environment installation and ownership](notes/reproducible-environment.md).
- Pipeline entry point: `python scripts/run_synpp.py <config>.yml [<overlay>.yml]`, which installs
  the deterministic stage-hash patch (ADR-0105) before synpp builds the stage graph; a plain
  `python -m synpp` run is unsupported (README §4; AGENTS.md "Day-to-day commands").

## Pipeline framework

- **synpp 1.6.2** (`environment.yml` pip section) — content-hashed DAG runner.
  Pipeline stages are dotted Python module paths with `configure(context)` /
  `execute(context)` functions (verified in
  `braunschweig/synthesis/locations/education_gravity.py`).

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

- **pytest 9.1.1** effective runtime. Linux uses the captured server conda/pip
  snapshot; Windows uses its generated lock. See
  [environment ownership](notes/reproducible-environment.md).
- `scripts/run_tests.py` is the shared entry point; `pytest.ini` owns discovery
  and pipeline markers, and `tests/conftest.py` owns shared fixtures.
- GitHub Actions runs the regression suite on Linux and Windows using the
  committed environments. The metadata-only documentation workflow is a separate
  gate. The obsolete inherited Travis configuration has been retired.
- Agents follow [the required verification gate](TESTING.md#required-agent-verification-gate).

## Java / MATSim side

- The pipeline's `matsim.output` stage builds a MATSim scenario via the **eqasim
  Java** toolchain. synpp downloads and caches the Java sources under
  `eqasim-data/cache_*/matsim.runtime.eqasim*` and `…pt2matsim*` (these are
  nested git checkouts, gitignored — AGENTS.md, `.gitignore`).
- **eqasim-java** comes from the `eqasim-java-bs` fork. The jar version is owned
  by the Java pom, not by a Python constant: see
  [ADR-0098](../decisions/ADR-0098-the-java-pom-owns-the-jar-version-not-a-python-constant.md).
  `DEFAULT_EQASIM_VERSION` / `DEFAULT_EQASIM_BRANCH` / `DEFAULT_EQASIM_COMMIT` in
  `matsim/runtime/eqasim.py` are the fallback when the pom cannot be
  read (logged), and also the defaults of the prebuilt-jar and legacy-clone paths and of the
  `eqasim_branch` / `eqasim_commit` config keys, so an edit is not log-only; do not copy a version number into prose. The Java build needs **JDK 25**
  (the run configs point `java_home` / `java_binary` at it).
  The Maven runtime honours a new `java_home` config key
  (`matsim/runtime/maven.py`) that exports JAVA_HOME for the build subprocess.
- The Python regression CI does not install Java/Maven or run real-data pipeline
  tests. Explicit pipeline checks require JDK 25, Maven and the input preflight;
  they are separate from the regression gate.
- The Java MATSim package is `org.eqasim.braunschweig.*` in the fork (renamed from
  `bavaria`); the entry-point class paths in `matsim/simulation/run.py` and
  `matsim/simulation/prepare.py` use it.
- External binaries configured per-machine in the config:
  `osmosis_binary`, `osmconvert_binary` (Windows paths in
  `configs/fixtures/config_local_braunschweig.yml`; Linux paths in
  `configs/base_bs.yml`).

## Evidence

- `environment.yml`
- `configs/fixtures/config_local_braunschweig.yml` (entry `run:`, `processes`, binaries)
- `.github/workflows/tests.yml` (locked Linux/Windows environments, shared runner)
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
  not as a library — see `popsimprep/batch_run_popsim.py`.

### Implication for the merged stack

The cleanest integration is likely a **separate PopulationSim environment**
invoked as a subprocess from the quaSIM synpp stage (mirrors how eqasim-bs already
shells out to the Java/osmosis toolchain), rather than importing PopulationSim
into the 3.10 eqasim env. `[ASK USER]` to confirm the env strategy.

Evidence: `popsimprep/pyproject.toml`, `popsimprep/docker/dev.Dockerfile`,
`popsimprep/uv.lock`, `popsimprep/batch_run_popsim.py`.
