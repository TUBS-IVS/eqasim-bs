# `braunschweig/popsim/mid/` package split

## What it is

The popsim_mid orchestration helper package: cells -> control totals -> seed
-> `batch_folders.py`'s greedy 1-km-atomic bin-packing and per-batch
PopulationSim run -> `merge.py`'s cell-disjoint merge. It is called directly
from `braunschweig/popsim/stage/` (the synpp producer stage) and is **not**
itself a synpp stage.

## Split shape and import path

Package conversion: the flat module (~1900 lines, per PR #271) became a
package. `__init__.py` is a pure facade — imports, the `MID_SEED_COLUMNS`
alias and re-export blocks only, no logic — plus 8 submodules:
`batch_folders` (batch **folder** assembly + the PopulationSim runner —
distinct from the parent `braunschweig/popsim/batch.py`, which bin-packs cells
**into** batches; `batch_folders` writes each batch's run-folder contents and
invokes that runner), `control_cells` (control-cell loading, ZGB filtering,
control totals), `csv_format` (MiD CSV field-separator detection), `donor`
(MiD donor attribute + Wege/trip table loading), `donor_stratification`
(RegioStaR donor stratification, Phase 4B), `kreis_controls` (Tier-3 KREIS
control tables + per-batch apportionment), `participation` (participation-
control seed derivation), `seed_loading` (consistent MiD seed load +
completed-donor projection).

The import path is unchanged: `braunschweig.popsim.mid` resolves to the
package's `__init__.py`.

## Rename forced by a name collision

The `donor_stratification` submodule was named `stratum.py` until this split
renamed it, to end an exact-filename collision with the pre-existing,
unrelated-in-content top-level `braunschweig/popsim/stratum.py` (Phase-4A
stratum-KEY mapping, e.g. `cell_urban_class_from_rs7`). The two modules cover
the same feature area (RegioStaR donor stratification) but are distinct:
`braunschweig/popsim/stratum.py` is Phase-4A; `braunschweig/popsim/mid/donor_stratification.py`
is Phase-4B (dominant-stratum derivation + seed filtering).

## Cache / `validate()` consequences

`mid` is a plain helper library, not a synpp stage: it has no
`configure`/`execute`/`validate()` of its own, so this split alone did not
devalidate any cache entry (cache-neutral by construction — no synpp stage
content-hashed `mid`'s source directly, before or after).

The pre-existing gap this left open — no synpp stage content-hashed `mid`'s
source at all, so editing `mid` alone silently reused a stale cached stage
output on a partial rerun — was **closed** by the separate `popsim/stage/`
split (PR #284; see `popsim-stage-split.md`), whose new `validate()` hashes
this whole package one level deep (its `__init__` plus all 8 submodules) as
part of a wider validation token.

## Standing rules

- Every submodule extracted from this package **must** be listed in
  `braunschweig/popsim/stage/__init__.py`'s `_HELPER_MODULES` tuple — `mid`
  has no validation token of its own, so coverage is entirely delegated to
  the stage package that consumes it.
- Facade re-export / monkeypatch surface: same shape as `enriched` (see
  `enriched-split.md`) — submodules import each other directly where they
  need a sibling's name (e.g. `batch_folders.py` imports from
  `control_cells`, `kreis_controls` and `donor_stratification`;
  `seed_loading.py` imports from `csv_format`, `donor` and `participation`;
  `donor.py` imports from `csv_format`) rather than through the facade, so a
  patch aimed at a re-exported name must target the owning submodule, not
  `braunschweig.popsim.mid` itself.

## PR / issue reference

PR #271 (`refactor/split-popsim-mid`), part of the collective oversized-module
backlog issue #267.
