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

**The gap this split left open has since been closed (issue #289).** At the time
of the split, `_HELPER_MODULES` covered only the 5 siblings the split extracted
(`attraction_vector`, `balancing`, `base`, `kreis_calibration`, `od`), while the
4 pre-existing siblings `friction.py`, `production_mass.py`, `taz_margins.py`
and `verbindungen_anchor.py` (default ON, ADR-0068) were behaviour dependencies
sitting outside the hash. The split did not create that gap — the stage had no
`validate()` hook at all before it — and did not widen it.

Those four are now covered by **dotted name** in
`_DEFERRED_HELPER_MODULE_NAMES`, resolved inside `validate()` at run time,
because every one of them is imported lazily inside a function; binding them at
module level would have changed the stage's import timing, and in
`production_mass`'s case would have turned its deferred back-import of
`_GEMBAND_COLUMN_NAMES` from this facade into an import-time cycle. The token
therefore folds in **nine** modules by two mechanisms.

`distance_matrix_taz.py` remains deliberately uncovered: it is its own synpp
stage, hashed by synpp in its own right, so folding its source in here would
double-count it rather than close a gap. `tests/test_gravity_validate_token.py`
pins both tuples literally, enumerates the package from disk so a newly added
module cannot stay unlisted, and asserts that `distance_matrix_taz` really is
still a stage — otherwise its exclusion would silently become a gap.

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
