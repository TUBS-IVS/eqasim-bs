# `braunschweig/gravity/model.py` sibling-module split

## What it is

The work/education gravity-distribution stage: the doubly-constrained
Furness/IPF distance-decay model (per-RegioStaR-7 slope), calibrated to BA
Pendleratlas Kreis-level commuter flows. Aliased from `data.od.weighted`.

## Split shape and import path

Sibling-module split, **not** a package conversion (unlike the `enriched` /
`secondary_chainsolvers` stage packages). `model.py` (currently 425 lines;
1483 lines before the split, per PR #288) stays the sole synpp stage
(`configure`/`execute`/`validate`) and the import path
`braunschweig.gravity.model` for every consumer. Sections extracted from it
live in 5 new sibling modules inside the **already-existing**
`braunschweig/gravity/` package, alongside the pre-existing siblings
`friction.py`, `production_mass.py`, `taz_margins.py`,
`verbindungen_anchor.py` and `distance_matrix_taz.py`:

- `attraction_vector.py` (190 lines) — destination attraction: the
  employees-at-workplace headcount and the flag-gated sector-aware
  establishment-density tilt
- `balancing.py` (237 lines) — the doubly-constrained Furness/IPF balancing
  loop (`evaluate_gravity`) and per-origin RegioStaR-7 friction-slope
  resolution
- `od.py` (335 lines) — the pure work-OD gravity computation
  (`compute_work_od`) and the BA Gemeindedaten establishment-count reader
- `kreis_calibration.py` (371 lines) — the BA-Pendleratlas Kreis-level IPF
  calibration (`_calibrate`), the zone-to-Kreis mapping, intra-Kreis flow
  synthesis and outbound-flow injection to external Kreise
- `base.py` (275 lines) — the inherited eqasim-bavaria base gravity execution
  (`_execute_gravity_base`) plus its IDF-derived default friction parameters

`model.py` re-exports every name its siblings define — except each sibling's
own `logger` object where one exists (`od.logger` and
`kreis_calibration.logger` are bound to the literal
`"braunschweig.gravity.model"` name, resolving to the same `logging.Logger`
instance via Python's name-keyed logger cache) — so external imports (the
pipeline, calibration scripts, tests) keep working unchanged.

## Cache / `validate()` consequences

The split gave this stage a `validate()` hook it never had before. synpp's
`get_stage_hash` hashes only the stage module's own source
(`inspect.getsource` of `model.py`), never its siblings', so without
`validate()` a change confined to a sibling would silently reuse the stale
cached stage output on a partial rerun. The new `validate()` folds the 5
new siblings' sources into an md5 token via `_HELPER_MODULES`. Because the
stage had no token before this split, the first run after this change has no
stored token to compare against and therefore recomputes this stage and
everything downstream of it once — a deliberate, one-off cost, not a bug.
Subsequent runs devalidate correctly on sibling-only edits.

**Known gap, not created by this split, and not yet closed:**
`_HELPER_MODULES` today covers only the 5 siblings this split extracted
(`attraction_vector`, `balancing`, `base`, `kreis_calibration`, `od`) — verified
current as of this writing. The 4 pre-existing siblings `friction.py`,
`production_mass.py`, `taz_margins.py` and `verbindungen_anchor.py` (default
ON, ADR-0068) are also behaviour dependencies of the `model.py` stage
(`execute()` reads them, some via lazy imports) but are **not** in that
tuple. This split did not create that gap — the stage had no `validate()`
hook at all before it, so those 4 were already outside the hash — and did not
widen it. Closing it (adding them to `_HELPER_MODULES`) is a separate,
behaviour-affecting cache change and is not documented here as done. (The
entd-source split, `entd-source-split.md`, shows what closing an analogous
gap later looks like — see PR #296 there.)

## Standing rules

- Every new sibling module added under `braunschweig/gravity/` **must** be
  appended to the `_HELPER_MODULES` tuple in `model.py`. A sibling missing
  from that tuple is invisible to `get_stage_hash`/`validate()`, so its
  changes silently reuse stale cached output on a partial rerun — exactly the
  failure this split's `validate()` hook exists to prevent. (This rule
  previously existed only in a gitignored scratch gate under
  `.superpowers/sdd/2026-08-14-split-gravity-model/`; this note is its
  durable home.)

## PR / issue reference

PR #288 (`refactor/split-gravity`), part of the collective oversized-module
backlog issue #267.
