# `braunschweig/popsim/sources/entd.py` sibling-module split

## What it is

`EntdSource`: the ENTD (French Enquete Nationale Transports et Deplacements)
donor-adapter for the `popsim_open` workflow — the Protocol implementation
(`sources/base.py`) that builds the PopulationSim seed, maps ENTD persons to
the eqasim/MiD attribute schema, and builds the trips frame, as an alternative
donor to `sources/mid.py`.

## Split shape and import path

Sibling-module split, **not** a package conversion (unlike `mid/` or the
`enriched`/`secondary_chainsolvers` stage packages). `entd.py` (currently 652
lines; 1487 lines before the split, per PR #287) stays a single module:
`sources/` gained no `entd/` subdirectory and no `__init__.py`, so the import
path `braunschweig.popsim.sources.entd` is unchanged. `entd.py` remains a
**delegating class facade**: `EntdSource`'s full public surface — 8 method
names and signatures (`seed_columns`, `built_seed_columns`, `build_seed`,
`map_person_attributes`, `build_trips`, `load_donor`, `donor_stratum`,
`cell_stratum`) — stays defined on the class in `entd.py`.

Content moved into 7 siblings in the same package, in two different
relationships to the facade (the code's own docstring distinguishes these; it
is not accurate to describe every sibling as merely "one-line delegation"):

- **Re-exports** — `entd_vocabulary` (205 lines: ENTD constants/column
  vocabularies, seed-column mapping, income-class -> MiD label lookup,
  PT-ticket defaults), `entd_schema` (59 lines: column-presence validation,
  ENTD -> MiD donor-schema rename) and `entd_diary_matching` (263 lines:
  diary-donor chain matching for trip-less persons) hold constants/helpers
  that already had bare module-level names before the split; each is
  imported into `entd.py` under its original name, so external imports of
  `braunschweig.popsim.sources.entd` keep resolving those names unchanged.
- **Delegation targets** — `entd_seed` (292 lines: seed building),
  `entd_attributes` (360 lines: person-attribute mapping, the largest ENTD
  mapper), `entd_trips` (295 lines: trip building) and `entd_donor` (158
  lines: donor loading + donor/cell stratum derivation) hold the 8
  `EntdSource` method bodies, which were only ever class-method bodies before
  the split (never bare module-level names); each is imported under a private
  leading-underscore alias and is the one-line internal target of the
  matching `EntdSource` method. `EntdSource` remains the sole public entry
  point for all 8.

This body-move was safe because `EntdSource` carries no instance state
(`self` never appears inside a method body in the pre-split module), so
nothing needed to be threaded through an instance when the bodies moved out.
Every sibling binds its logger to the literal facade name
(`logging.getLogger("braunschweig.popsim.sources.entd")`, not `__name__`), so
`LogRecord.name` values are unchanged by the split.

Public-surface parity (the 8 method names and signatures staying stable) was
verified during PR #287's development with a one-off namespace-comparison
script; that script lived in a gitignored scratch directory and is not a
persistent repo artifact or CI gate today.

## Cache / `validate()` consequences

`entd.py` is not itself a synpp stage, so this split had no `validate()` of
its own to gain or lose. At the time of PR #287, no synpp stage
content-hashed `entd.py`'s internals beyond the facade file itself, so the
split was cache-neutral by construction.

**That has since changed.** PR #296 (`fix/popsim-token-covers-entd-siblings`)
extended `braunschweig/popsim/stage/`'s `validate()` token
(`_HELPER_MODULES`, see `popsim-stage-split.md`) to cover all 7 ENTD siblings
introduced here, closing a gap the two PRs' merge order had left open (each
was verified against a `main` that did not yet contain the other, so neither
PR's own review saw the combination). **Today, editing any of the 7 ENTD
siblings — or `entd.py` itself — does devalidate the popsim-stage cache**,
because `braunschweig.popsim.stage` is the consumer that hashes this
package's source; nothing in `entd.py`/its siblings hashes it directly.

## Standing rules

- Every sibling added under `braunschweig/popsim/sources/` for the ENTD
  adapter must be added to `braunschweig/popsim/stage/__init__.py`'s
  `_HELPER_MODULES` tuple (see the standing rule and the
  `test_every_discovered_submodule_is_covered_by_one_of_the_two_mechanisms`
  guard documented in `popsim-stage-split.md`) — this module has no cache
  token of its own to maintain.

## PR / issue reference

PR #287 (`refactor/split-entd`), part of the collective oversized-module
backlog issue #267. Cache-coverage follow-up: PR #296
(`fix/popsim-token-covers-entd-siblings`).
