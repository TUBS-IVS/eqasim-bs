# `braunschweig/analysis/dashboard/build_dashboard.py` sibling-module split

## What it is

The run dashboard: a CLI script that collects eqasim and MATSim run metrics,
compares them against MiD reference tables, and renders a self-contained HTML
page per run. Invoked directly, not from the synpp DAG (see
`docs/features/run-analysis.md`).

## Split shape and import path

Sibling-module split, **not** a package conversion.
`braunschweig/analysis/dashboard/` already had its own `__init__.py`, so the
split needed no `git mv` and changed **no import path at all** —
`braunschweig.analysis.dashboard.build_dashboard` resolves exactly as before.
The whole class of path-reference breakage a package conversion risks (stale
absolute imports, config references, notebook paths) therefore never arose.

`build_dashboard.py` (1601 lines before the split, per PR #285) stays the
facade: docstring, imports, re-export blocks, `render_dashboard()` and `main()`
(the CLI entry point). The extracted content lives in seven siblings:

| Module | Lines | Content |
|---|---|---|
| `html_template.py` | 873 | the dashboard's HTML/CSS/JS template literal (`HTML_TEMPLATE`) |
| `run_metrics.py` | 270 | eqasim + MATSim run metrics, sim-output discovery, sample-rate detection |
| `spatial_metrics.py` | 190 | VG250/ZGB Kreis classification, time-of-day, per-Kreis and OD metrics |
| `mid_reference.py` | 186 | MiD reference tables, km-band binning, earth-mover distance |
| `run_records.py` | 101 | run-record assembly, writing, collection |
| `comparisons.py` | 95 | model-vs-reference comparison table |
| `paths.py` | 30 | leaf: `REPO_ROOT`, `DASHBOARD_DIR`, `RUNS_DIR` anchors |

The facade itself is 147 lines. Line counts are `wc -l` at the time of writing
and drift with edits; the shape (facade + these seven siblings) is the durable
fact.

The import graph is acyclic with `paths` and `html_template` as leaves. **No
sibling imports the facade.**

## Cache / `validate()` consequences

None. `build_dashboard.py` is not a synpp stage — it has no
`configure`/`execute`/`validate` — so there is no cache token to gain, lose or
maintain, and no stage hash that this package's source participates in. The
split is cache-neutral and behaviour-neutral by construction.

This is the opposite situation from `braunschweig/gravity/model.py` and
`braunschweig/popsim/stage/`, where an equivalent split *did* require a
`validate()` token because those modules are stages (see
`gravity-model-split.md`, `popsim-stage-split.md`).

## Standing rules

- The facade re-exports **every** public and `_private` name the siblings
  define, because external modules import private names through it — e.g.
  `braunschweig/analysis/simwrapper/export.py` imports `_load_zgb_kreise` and
  `spatial_export.py` imports `_find_sim_output`. Removing a private name from
  the facade breaks those consumers even though nothing "public" changed.
- No sibling may import the facade back.
- `HTML_TEMPLATE` is a large string literal where a silent character-level
  change would alter rendered output without failing any test. Its
  byte-identity was verified explicitly as part of the split, and any future
  move of it must be verified the same way rather than reviewed by eye.

## Known duplication (resolved)

`spatial_metrics.py` used to locate and read the VG250 archive independently
of `braunschweig/analysis/spatial.py`, and the two disagreed about what a
missing archive means (one raised, the other returned `None` with no log
line). Not addressed by this split (a verbatim relocation) -- tracked as
issue #293 and fixed separately: `spatial_metrics.py`'s `VG250_ZIP`,
`VG250_CACHE` and `_ensure_vg250` now delegate to the single shared loader
in `spatial.py` (`load_vg250_layer` / `_resolve_vg250_gpkg`), which keeps
each caller's original failure mode (raise for the analysis/validation path,
`None` + a logged `warning` for the dashboard) but makes both decisions
explicit in one place instead of an accident of which module is imported.
See `spatial.py`'s module docstring for the strict/tolerant contract.

## PR / issue reference

PR #285 (`refactor/split-dashboard`), part of the collective oversized-module
backlog issue #267.
