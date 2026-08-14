# `braunschweig/popsim/stage/` package split

## What it is

The synpp producer stage for `population.method == popsim_mid`
(`configure`/`execute`/`validate`). It runs the popsim_mid chain (prepared
cells -> control totals -> complete-household MiD seed -> PopulationSim per
1 km batch, run as a subprocess -> cell-disjoint merge) and expands the merged
result into the full eqasim persons frame.

## Split shape and import path

Package conversion: the flat module (~1900 lines, per PR #284) — itself the
renamed legacy `stage.py` — became a package. `__init__.py` (2136 lines) is
the synpp stage and re-exports every name its 6 submodules define, plus the
module-level first-party helper bindings `validate()` hashes; `execute()`
itself is decomposed into 26 named private orchestration steps (verified:
`grep -c '^def _' braunschweig/popsim/stage/__init__.py`). Submodules:
`batch_cache` (work-dir batch cache invalidation: config-signature guard +
stale `batch_*` folder purge), `cell_attributes` (per-cell ARS/RegioStaR7 join
+ Kreis-code derivation), `config_keys` (every `KEY_*` config-key constant, a
LEAF module with no intra-package imports), `controls_builder`
(`controls.csv` frame assembly + aggregation-map / source-column / per-Kreis
person-total helpers), `source_resolution` (donor-source resolution + active
KREIS attribute-control entries), `tilt_columns` (income spatial-tilt
cell-column selection, issue #136).

The import path is unchanged: `braunschweig.popsim.stage` resolves to the
package's `__init__.py`.

## Cache / `validate()` consequences

The stage gained a `validate()` hook it never had before, closing the
"helper-only source change is invisible to `get_stage_hash`" trap for this
stage. Its coverage is intentionally wide, and is documented **canonically
and exhaustively in the docstring of `validate()` itself**
(`braunschweig/popsim/stage/__init__.py`) — that docstring states it is the
single source of truth for the covered/uncovered boundary and should be read
in place of any summary elsewhere in the package or its docs.

Summary only (see the docstring for the precise, current list, since this
boundary has already changed once — see below): the package's own 6
submodules; the whole `braunschweig.popsim.mid` package one level deep (its
`__init__` plus all 8 submodules — see `popsim-mid-split.md`); the whole
`braunschweig.popsim.sources` donor-adapter package one level deep
(`base`, `entd`, `mid`, and — since PR #296 — `entd`'s own 7 siblings; see
`entd-source-split.md`); several other non-stage first-party helper modules
imported at module level; and, covered by dotted name (imported lazily inside
`validate()` because their only import site in this package is inside a
function body), the deferred function-level dependencies, including the
`synthesis.population.enriched` package one level deep and
`braunschweig.data.census.household_size` — two synpp stages this stage calls
as plain libraries without declaring them as synpp dependencies, so this
token is the only mechanism that can see a source change in either. The
transitive surface beyond this explicit, one-level-deep enumeration is
deliberately **not** covered.

First-run effect: this stage gained a validation token it never had, so the
first run after merge recomputes it and everything downstream once; every run
after that is cache-stable.

## Standing rules

- Every module extracted from this package, and every first-party helper
  module this stage depends on directly (whether imported at module level or
  inside a function body), **must** be added to `_HELPER_MODULES` or
  `_DEFERRED_HELPER_MODULE_NAMES` respectively in
  `braunschweig/popsim/stage/__init__.py` — see that file's `validate()`
  docstring for the exact criterion (a module qualifies when this stage calls
  it as a library AND it is not among the stage dependencies `configure()`
  declares).
- A dedicated test, `test_every_discovered_submodule_is_covered_by_one_of_the_two_mechanisms`
  (`tests/test_popsim_stage_validate_token.py`), dynamically discovers every
  submodule on disk under the covered packages and fails if one is present in
  neither tuple — this is the guard that makes a missed coverage update
  visible rather than silent.
- **PR #296 is a concrete example of this rule catching a real gap after the
  fact.** PR #287 split `braunschweig/popsim/sources/entd.py` into a facade
  plus 7 siblings; PR #284 (this split) added the dynamic-discovery guard
  above. Each PR was verified against a `main` that did not yet contain the
  other, so neither PR's own review could see that, once both were merged,
  the 7 new ENTD siblings would sit on disk but in neither tuple. PR #296
  added them to `_HELPER_MODULES` to close the gap; until then, an edit to
  one of the 7 ENTD siblings alone would have silently reused stale cached
  popsim-stage output on a partial rerun.

## PR / issue reference

PR #284 (`refactor/split-popsim-stage`), part of the collective
oversized-module backlog issue #267. Related follow-up:
PR #296 (`fix/popsim-token-covers-entd-siblings`).
