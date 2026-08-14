# `braunschweig/synthesis/locations/secondary_chainsolvers/` package split

## What it is

The chainsolvers-based secondary-location assignment stage: a drop-in
replacement for the upstream `synthesis.population.spatial.secondary.locations`
stage that delegates point placement to the external
[chainsolvers](https://github.com/TUBS-IVS/chainsolvers) package (carla solver,
default distance-based scoring) instead of eqasim's `GravityChainSolver`. It
produces the same `(df_locations, df_convergence)` output schema as the legacy
stage, wired in via the synpp alias
`synthesis.population.spatial.secondary.locations: braunschweig.synthesis.locations.secondary_chainsolvers`.

## Split shape and import path

Package conversion: the flat module (5327 lines, per issue #266) became a
package. `__init__.py` (1181 lines) is the synpp stage itself
(`configure`/`execute`/`validate`) and re-exports every name its 13 sibling
submodules define: `activity_types`, `candidate_columns`, `candidates`,
`srv_candidates`, `distance_sampling`, `deciders`, `srv_location_types`,
`escort`, `plans`, `fallback`, `parallel_solving`, `results`, `reporting`.

The import path is unchanged: `braunschweig.synthesis.locations.secondary_chainsolvers`
resolves to the package's `__init__.py` exactly as it resolved to the flat
module before, so the synpp alias needed no change.

## Cache / `validate()` consequences

The pre-split flat module had **no `validate()` hook at all** (verified: the
last pre-split revision, `git show 9ea23800:braunschweig/synthesis/locations/secondary_chainsolvers.py`,
defines only `configure`/`execute`). The split added one: `_HELPER_MODULES` in
`__init__.py` lists all 13 submodules, and `validate()` hashes their combined
source into the synpp validation token, so a helper-only edit now correctly
recomputes the stage instead of silently reusing stale cached output. Being a
brand-new token, the first run after this change recomputes the stage and
everything downstream of it once; every run after that is cache-stable.

This package has no external siblings outside its own boundary (unlike
`braunschweig/gravity/`), so there is no equivalent "known gap" of
pre-existing modules left uncovered.

## Standing rules

- Every submodule extracted from or added to this package **must** be
  appended to the `_HELPER_MODULES` tuple in `__init__.py`; a submodule
  missing from that tuple is invisible to `validate()`, so its changes
  silently reuse stale cached output on a partial rerun.
- Facade re-export / monkeypatch surface: as in the `enriched` package (see
  `enriched-split.md`), most submodule names are re-exported through
  `__init__.py` for backward compatibility, and siblings that need each
  other's names import them directly, submodule-to-submodule (e.g.
  `srv_location_types.py` imports from `deciders.py`; `candidates.py` imports
  from `srv_location_types.py`; `plans.py` imports from `distance_sampling.py`
  and `srv_location_types.py`) rather than through the facade. A test or patch
  aimed at a re-exported name must target the owning submodule attribute, not
  the facade, for the same reason documented for `enriched`. Unlike
  `enriched`, this package's own `pandas`/`numpy`/`geopandas` imports are
  bound directly at the top of `__init__.py` (not re-exported from a
  submodule), so the specific `enriched.pd`-style trap does not apply to
  `pd`/`np`/`gpd` here.

## PR / issue reference

PR #268 (`refactor/split-secondary-chainsolvers`), closing issue #266 — the
dedicated split issue that predates, and is referenced by, the collective
oversized-module backlog issue #267.
