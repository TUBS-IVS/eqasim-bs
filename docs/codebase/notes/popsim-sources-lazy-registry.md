# `braunschweig/popsim/sources/__init__.py` lazy adapter resolution

## What it is

The popsim donor-source registry (`_REGISTRY`, `get_source`) maps a short
source name (`"mid"`, `"entd"`) to a `PopsimSource` adapter instance. Before
issue #292, `_REGISTRY` stored the imported CLASSES, which meant the package
`__init__` unconditionally imported both `mid.py` and `entd.py` at
package-import time — and, after the #287 split, `entd.py`'s seven sibling
modules too, since `entd.py` imports all of them at its own module level.

## The change (issue #292)

`_REGISTRY` now stores `"module:ClassName"` dotted strings instead of
imported classes, and `get_source()` resolves the string (`importlib`,
`getattr`) only when that specific source is requested, via the new
`_resolve_adapter_class` helper. `PopsimSource` (the shared Protocol every
adapter implements) stays eagerly imported from `base.py`, as required — it
carries no adapter-specific dependencies.

`MidSource` and `EntdSource` stay reachable as public attributes of the
package (`sources.MidSource`, `from braunschweig.popsim.sources import
EntdSource`) via a module-level `__getattr__` (PEP 562): the lookup itself
triggers the one-time import of the owning adapter module. This mirrors the
existing pattern in `braunschweig/popsim/stage/__init__.py` (worker-state
globals), `braunschweig/synthesis/locations/secondary_chainsolvers/__init__.py`
(same pattern) and `braunschweig/data/census/household_income.py`
(`CLASS_MIDPOINT_EUR`, lazy because it needs a configured `data_path`).

`get_source`'s behaviour, its two exceptions (`ValueError` for an unknown
name, `NotImplementedError` for a `_PLANNED`-but-unregistered name) and their
messages are unchanged; `__all__` is unchanged.

## Scope: this does NOT make the production popsim_mid path lazier

This is the finding this note exists to keep visible. `_REGISTRY`'s laziness
only has an effect for a caller that imports `braunschweig.popsim.sources`
WITHOUT importing `braunschweig.popsim.stage` first. On the actual
Braunschweig production path, it does not:

`braunschweig/popsim/stage/__init__.py` imports `braunschweig.popsim.sources`
one level deep at ITS OWN module level — `base`, `entd`, `mid` plus the seven
`entd_*` siblings — specifically so their source text can be
`inspect.getsource`'d into the stage's synpp cache-validation token
(`validate()`, `_HELPER_MODULES`; see `popsim-stage-split.md`). That coverage
was added by PR #296 after the #290 audit found the gap, and
`inspect.getsource` needs a module OBJECT, not a dotted name, to hash it — a
lazy dotted-string entry cannot serve that purpose. So importing
`braunschweig.popsim.stage` (which the popsim_mid producer stage always is)
still imports the ENTD adapter and all its siblings, regardless of this
registry's laziness. Verified empirically: a subprocess that imports
`braunschweig.popsim.stage` shows all ten `sources` submodules in
`sys.modules` before and after this change.

The consumers that DO benefit are call sites that import
`braunschweig.popsim.sources` on their own, never via `stage`:

- `braunschweig/popsim/trips_stage.py` — imports `sources` inside its
  `execute()` function body (not at module level), so a `trips_stage`-only
  import path (or a test that imports it without `stage`) no longer drags in
  the ENTD adapter.
- `tests/test_employment_status_seed_derivation.py` — imports
  `braunschweig.popsim.sources` at module level and never imports
  `braunschweig.popsim.stage`.

## Deliberately not done here

Converting `braunschweig/popsim/stage/__init__.py`'s `_HELPER_MODULES` module
objects to deferred dotted names (mirroring `_DEFERRED_HELPER_MODULE_NAMES`)
would let the stage itself defer the ENTD import for `source="mid"` runs,
delivering the issue's full promise on the production path. That is a
separate, deliberate change: it edits the stage's own cache-validation
token construction, which is a synpp cache-devalidation event of its own and
needs to be reviewed as such rather than folded into a "make the registry
lazy" change. Left for maintainers to decide as a follow-up.

## Cache consequence of this change itself

`braunschweig/popsim/sources/__init__.py` is itself one of the modules
`braunschweig.popsim.stage`'s `validate()` hashes (via the `sources` /
`_sources_base` / `_sources_mid` / `_sources_entd*` module-level imports), so
editing this file's source text moves the popsim_mid stage's cache token —
a one-time cache devalidation, exactly like any other edit to a covered
helper. Measured directly (`stage.validate(DummyContext())` before/after
this change, same interpreter, same `braunschweig` checkout):

- before: `43126fb45b16194e0c72d17acb96f7e3`
- after: `5fc7e12dbf90ed972299e5ec12557a13`

## Tests

`tests/test_popsim_source_registry_lazy.py` pins: importing the package
imports no adapter submodule (subprocess, clean `sys.modules`); `base` alone
stays eager; `get_source("mid")` does not import `entd`, and vice versa;
`MidSource`/`EntdSource` still resolve identically to their direct-module
counterparts; the two error paths keep their exception type and message
content.

## PR / issue reference

Issue #292, found while reviewing #287 (#267 module 6).
