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

**Known gap (found by the #290 audit, corrected here).** An earlier version of
this note claimed this package "has no external siblings outside its own
boundary, so there is no equivalent known gap". That was wrong. `_HELPER_MODULES`
covers the 13 in-package submodules, but the stage module imports four
first-party modules from **outside** the package that are not in the tuple and
therefore outside the token: `braunschweig.calibration.secondary_measurement`
and `synthesis.population.spatial.secondary.problems` (module level),
`braunschweig.synthesis.locations.escort_links` and `braunschweig.parallelism`
(inside functions). Editing any of them does not devalidate this stage.

The submodules in turn import further first-party modules of their own
(`braunschweig.popsim.trips`, `braunschweig.popsim.shop_subtype`,
`braunschweig.popsim.purpose_subtype`, `braunschweig.data.building_potential_attach`,
`synthesis.population.spatial.secondary.{components,rda}` and others), which are
equally outside the token. The token's boundary is one level deep by design, the
same boundary `braunschweig/popsim/stage/`'s `validate()` docstring states for
itself — so this gap is a property of that boundary, not of this split.

See `synpp-helper-hash-audit.md` for the repo-wide picture; closing gaps of this
kind is tracked separately because doing so devalidates additional cached output.

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
