# `braunschweig/analysis/simwrapper/spatial_export.py` sibling-module split

## What it is

The SimWrapper spatial-export driver: `export_spatial()`, the registry-based
function wired into `braunschweig/analysis/simwrapper/export.py`'s `main()`,
which builds the geometry-consuming SimWrapper dashboard layers (choropleths,
point clouds, sankeys, spatial demand hexagons).

## Split shape and import path

Sibling-module split, **not** a package conversion. `spatial_export.py`
(currently 252 lines; 1593 lines before the split, per PR #286) is the
facade: docstring, imports, `LOGGER`, the re-export blocks, and
`export_spatial()` itself. Content moved into 6 sibling modules inside the
**same** package — `braunschweig/analysis/simwrapper/` already had its own
`__init__.py` before this split, so no `git mv` and no import-path change was
needed at all: `braunschweig.analysis.simwrapper.spatial_export` resolves
exactly as it did before.

| Module | Lines (current) | Content |
|---|---|---|
| `socio.py` | 473 | Socio-demographic Kreis layer, economic-status ordinal mapping |
| `fleet.py` | 458 | Fleet points/choropleth layer, brand and powertrain mixes |
| `commuter_tabs.py` | 246 | Commuter + student-commuter SimWrapper tabs |
| `behaviour.py` | 171 | Purpose-to-mode sankey + per-Kreis car-share scatter layer |
| `trip_demand.py` | 161 | Spatial demand layer (trip origin/destination hexagons), purpose-to-mode aggregation |
| `geo_layers.py` | 160 | Geometry-consuming writers: xytime point-cloud CSV, Kreis choropleth GeoJSON |

Two sibling names deliberately do not match their nearest existing neighbour,
because the package was already crowded with related names: `geo_layers.py`
is distinct from the pre-existing `writers.py` (pure dashboard-card/CSV/YAML
builders that never touch geometry or a CRS), and `commuter_tabs.py` is
distinct from the pre-existing `commuters.py` (a pure OD-matrix analysis
library) and `student_commuters.py` (pure aggregation + plain-CSV writer) —
`commuter_tabs.py` is the presentation layer that calls into both and builds
the SimWrapper dashboard cards.

No sibling imports the facade back; sibling-to-sibling imports are used
instead where one needs another's helper (e.g. `behaviour.py` imports
`trip_demand._purpose_to_mode` directly). Every name a sibling defines is
re-exported through the facade so external imports of
`braunschweig.analysis.simwrapper.spatial_export` (tests, `export.py`) keep
working unchanged.

## Cache / `validate()` consequences

`spatial_export.py` is not itself a synpp stage — the stage is
`braunschweig.analysis.simwrapper_export`, which calls `export.py`'s `main()`,
which in turn calls `spatial_export.export_spatial()` — so there is no
`configure`/`execute`/`validate()` and no cache-invalidation question for this
split at all. It is a pure module reorganisation, cache-neutral and
behaviour-neutral by construction.

## Standing rules

None specific to cache coverage (this module is not hashed by any synpp
`validate()` token). The one behavioural continuity requirement from the
split: the fallback-rate log line this module must carry (mandatory per
CLAUDE.md's "no silent fallbacks" rule) moved into the `fleet.py` sibling
verbatim — see `docs/registry/features/simwrapper_export.yml`'s
`fallback_rate` entry and `fleet.py`'s "no-match (fallback signal)" log line.

## PR / issue reference

PR #286 (`refactor/split-spatial-export`), part of the collective
oversized-module backlog issue #267.
