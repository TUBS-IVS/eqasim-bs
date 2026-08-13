"""Tests for the ``braunschweig.popsim.stage`` synpp cache-validation token.

The popsim stage had NO ``validate()`` hook before issue #267. synpp's
``get_stage_hash`` hashes only the stage module's OWN source, so a change
confined to a helper -- one of the ``stage`` package's submodules, or one of the
``braunschweig.popsim.mid`` modules that actually build the seed, controls and
batch folders -- left the stage hash untouched and the stale cached stage output
was silently reused. That is the documented synpp helper trap and a
scientific-correctness hazard, so these tests pin the properties the hook must
have:

- the token is a STABLE 32-char lowercase md5 hex digest (a stable token is what
  keeps the cache usable at all),
- the token CHANGES when the source of ANY listed helper changes (otherwise the
  hook does not close the trap),
- ``_HELPER_MODULES`` really covers the ``braunschweig.popsim.mid`` package, the
  ``braunschweig.popsim.sources`` donor adapters, the stage package's own
  submodules AND the other first-party helper modules the stage imports, so a
  future edit that DROPS coverage fails loudly here instead of silently
  re-opening the gap,
- every submodule DISCOVERED on disk under those three packages is listed, so a
  helper ADDED later cannot stay silently uncovered (the literal expectation
  lists only catch removals),
- and the only synpp stages listed are the two documented UNDECLARED library
  dependencies: a stage this one declares via ``context.stage(...)`` is hashed by
  synpp and propagated through that declared edge, so listing it would only add
  churn, whereas an undeclared one is reached by no other mechanism at all.

The "source changed" case is simulated by monkeypatching ``inspect.getsource``
for ONE listed module; no file on disk is written or modified, and nothing
depends on external data, so the tests are deterministic and side-effect free.
"""

from __future__ import annotations

import hashlib
import importlib
import inspect
import pkgutil

import pytest

from braunschweig.popsim import stage

# The eight submodules of the mid package plus its ``__init__``. Written out
# literally (not derived from _HELPER_MODULES) so this list is an independent
# statement of what MUST be covered: a coverage gap in the stage cannot hide by
# also disappearing from the expectation.
EXPECTED_MID_MODULE_NAMES = (
    "braunschweig.popsim.mid",
    "braunschweig.popsim.mid.batch_folders",
    "braunschweig.popsim.mid.control_cells",
    "braunschweig.popsim.mid.csv_format",
    "braunschweig.popsim.mid.donor",
    "braunschweig.popsim.mid.donor_stratification",
    "braunschweig.popsim.mid.kreis_controls",
    "braunschweig.popsim.mid.participation",
    "braunschweig.popsim.mid.seed_loading",
)

EXPECTED_STAGE_SUBMODULE_NAMES = (
    "braunschweig.popsim.stage.batch_cache",
    "braunschweig.popsim.stage.cell_attributes",
    "braunschweig.popsim.stage.config_keys",
    "braunschweig.popsim.stage.controls_builder",
    "braunschweig.popsim.stage.source_resolution",
    "braunschweig.popsim.stage.tilt_columns",
)

# The donor-adapter package enumerated ONE level deep: its registry ``__init__``
# plus every adapter submodule. The ``__init__`` alone is only the name -> adapter
# registry; the seed build, donor loading and attribute mapping that shape the
# stage's result live in the adapters, so all four must be covered.
EXPECTED_SOURCES_MODULE_NAMES = (
    "braunschweig.popsim.sources",
    "braunschweig.popsim.sources.base",
    "braunschweig.popsim.sources.entd",
    "braunschweig.popsim.sources.mid",
)

# The remaining first-party, NON-STAGE modules the stage package imports at
# module level (directly or, for ``sources``, via a submodule). Also written out
# literally, for the same reason as the lists above.
EXPECTED_OTHER_HELPER_MODULE_NAMES = (
    "braunschweig.data.mid.income_by_size",
    "braunschweig.data.mid.income_by_status",
    "braunschweig.popsim.assembly",
    "braunschweig.popsim.batch",
    "braunschweig.popsim.income",
    "braunschweig.popsim.income_kreis_control",
    "braunschweig.popsim.income_spatial_tilt",
    "braunschweig.popsim.plausibility",
    "braunschweig.popsim.prepared_cells",
)

# The two modules that ARE synpp stages (``configure`` + ``execute``) yet MUST be
# listed, because the stage uses them as plain function libraries WITHOUT
# declaring the dependency via ``context.stage(...)``: synpp's own stage hash for
# them therefore never reaches this stage, and no declared DAG edge propagates it
# either, so this token is the ONLY mechanism that can see a change in them.
# ``household_size`` is imported at module level for ``kreis_household_stats``;
# ``enriched`` at function level for ``_apply_housing_tenure``. Named explicitly
# so a THIRD synpp stage cannot silently join the token: adding one has to be a
# visible edit to this tuple, justified on the same two grounds.
UNDECLARED_STAGE_LIBRARY_MODULE_NAMES = (
    "braunschweig.data.census.household_size",
    "braunschweig.synthesis.population.enriched",
)

# Packages whose submodules must ALL appear in _HELPER_MODULES. Discovered
# dynamically (see test_helper_modules_cover_every_discovered_submodule) so a
# helper module ADDED later cannot stay unlisted. Enumerated one level deep only,
# matching the token's own documented bound.
DYNAMICALLY_ENUMERATED_PACKAGE_NAMES = (
    "braunschweig.popsim.stage",
    "braunschweig.popsim.mid",
    "braunschweig.popsim.sources",
)

HEX_DIGITS = set("0123456789abcdef")


def _discovered_submodule_names(package_name: str) -> list[str]:
    """Dotted names of every direct submodule of ``package_name``.

    Uses ``pkgutil.iter_modules`` on the package's ``__path__``, i.e. one level
    deep only (no recursion), mirroring how ``_HELPER_MODULES`` bounds itself.
    Dynamic enumeration is deliberate HERE and forbidden in the token itself:
    the token must stay an explicit literal list so every coverage change is a
    visible diff, while this test needs discovery precisely to catch a module
    that was added to a package but never added to that list.
    """
    package = importlib.import_module(package_name)
    return [
        f"{package_name}.{info.name}"
        for info in pkgutil.iter_modules(package.__path__)
    ]


# ---------------------------------------------------------------------------
# token shape and stability
# ---------------------------------------------------------------------------

def test_validate_returns_stable_lowercase_md5_hex():
    """Two calls must return the SAME 32-char lowercase hex digest.

    An unstable token would devalidate the cached stage output on every run,
    which is as wrong as never devalidating it.
    """
    first = stage.validate(None)
    second = stage.validate(None)

    assert first == second
    assert len(first) == 32
    assert set(first) <= HEX_DIGITS


def test_validate_hashes_exactly_the_listed_modules_in_order():
    """The digest must be md5 over ``_HELPER_MODULES`` sources, in tuple order.

    The expectation is recomputed FROM ``_HELPER_MODULES``, so this pins the
    hashing rule and the deterministic ITERATION ORDER only -- a set or ``dir()``
    based order would make the token vary between processes -- not which modules
    are covered. The covered set is pinned by the literal expectation lists and
    the discovery test below.
    """
    expected = hashlib.md5()
    for module in stage._HELPER_MODULES:
        expected.update(inspect.getsource(module).encode("utf-8"))

    assert stage.validate(None) == expected.hexdigest()


# ---------------------------------------------------------------------------
# the token must react to a helper source change
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("target_module_name", (
    "braunschweig.popsim.stage.controls_builder",  # this package's submodule
    "braunschweig.popsim.mid.donor",               # a mid helper module
    "braunschweig.popsim.mid.seed_loading",        # the biggest mid helper
    "braunschweig.popsim.mid",                     # the mid package __init__
))
def test_validate_token_changes_when_a_listed_helper_source_changes(
        monkeypatch, target_module_name):
    """A changed helper source must change the token (the helper trap).

    ``inspect.getsource`` is monkeypatched for exactly ONE listed module and
    delegates to the real implementation for all others, so this simulates an
    edit to that helper without touching any file on disk.
    """
    baseline_token = stage.validate(None)
    target_module = next(
        module for module in stage._HELPER_MODULES
        if module.__name__ == target_module_name
    )

    real_getsource = inspect.getsource

    def fake_getsource(object_):
        if object_ is target_module:
            return real_getsource(object_) + "\n# simulated helper edit\n"
        return real_getsource(object_)

    monkeypatch.setattr(inspect, "getsource", fake_getsource)
    changed_token = stage.validate(None)

    assert changed_token != baseline_token
    assert len(changed_token) == 32

    monkeypatch.undo()
    assert stage.validate(None) == baseline_token


# ---------------------------------------------------------------------------
# coverage of the listed helper modules
# ---------------------------------------------------------------------------

def test_helper_modules_cover_the_mid_package():
    """Every ``braunschweig.popsim.mid`` module must be hashed.

    The mid package holds the seed / donor / control / batch-folder logic
    ``execute()`` orchestrates, so dropping it from ``_HELPER_MODULES`` would
    re-open the cache-correctness gap this hook exists to close.
    """
    listed = [module.__name__ for module in stage._HELPER_MODULES]

    missing = [name for name in EXPECTED_MID_MODULE_NAMES if name not in listed]
    assert not missing, f"mid modules missing from _HELPER_MODULES: {missing}"


def test_helper_modules_cover_this_packages_submodules():
    """Every submodule of the stage package must be hashed as well."""
    listed = [module.__name__ for module in stage._HELPER_MODULES]

    missing = [
        name for name in EXPECTED_STAGE_SUBMODULE_NAMES if name not in listed
    ]
    assert not missing, f"stage submodules missing from _HELPER_MODULES: {missing}"


def test_helper_modules_cover_the_sources_package_one_level_deep():
    """The donor-adapter package must be covered ``__init__`` + submodules.

    Listing only ``braunschweig.popsim.sources`` covers the small name -> adapter
    registry but NOT the adapters themselves, where the seed build, donor loading
    and attribute mapping live (``sources.mid`` is the default MiD path). An edit
    confined to an adapter would then leave the token unchanged and the stale
    cached stage output would be reused -- exactly the trap the hook closes.
    """
    listed = [module.__name__ for module in stage._HELPER_MODULES]

    missing = [
        name for name in EXPECTED_SOURCES_MODULE_NAMES if name not in listed
    ]
    assert not missing, f"sources modules missing from _HELPER_MODULES: {missing}"


def test_helper_modules_cover_the_undeclared_stage_libraries():
    """The two undeclared synpp-stage libraries must be covered.

    Both are synpp stages used here as plain function libraries whose dependency
    ``configure()`` does NOT declare, so neither synpp's own stage hashing nor a
    declared DAG edge propagates their source into this stage's cache key. If one
    is dropped from ``_HELPER_MODULES``, an edit to ``kreis_household_stats`` or
    ``_apply_housing_tenure`` silently reuses stale output -- fail loudly here.
    """
    listed = [module.__name__ for module in stage._HELPER_MODULES]

    missing = [
        name for name in UNDECLARED_STAGE_LIBRARY_MODULE_NAMES if name not in listed
    ]
    assert not missing, (
        f"undeclared synpp-stage libraries missing from _HELPER_MODULES: {missing}"
    )


def test_helper_modules_cover_every_discovered_submodule():
    """A helper module ADDED to a covered package must also be listed.

    The literal expectation lists above catch a module being REMOVED from
    ``_HELPER_MODULES``; they cannot catch a NEW submodule that is never added to
    it (the token would then silently miss it and the trap re-opens for that
    module only). ``pkgutil.iter_modules`` discovers what is actually on disk, so
    adding a submodule to any of these packages without listing it fails here.
    """
    listed = {module.__name__ for module in stage._HELPER_MODULES}

    unlisted = []
    for package_name in DYNAMICALLY_ENUMERATED_PACKAGE_NAMES:
        discovered = _discovered_submodule_names(package_name)
        assert discovered, f"no submodules discovered under {package_name}"
        unlisted.extend(name for name in discovered if name not in listed)

    assert not unlisted, (
        "submodules discovered on disk but absent from _HELPER_MODULES "
        f"(add them explicitly to the tuple): {sorted(unlisted)}"
    )


def test_helper_modules_cover_the_other_first_party_helpers():
    """The non-stage first-party helpers imported at module level must be hashed.

    ``execute()`` / ``configure()`` also depend on module-level imports outside
    this package and outside ``braunschweig.popsim.mid`` (the income tables, the
    persons assembly, the PopulationSim batch runner, the income helpers, the
    plausibility checks, the prepared-cells loader, the donor-source registry).
    A change confined to any of them must devalidate the cached stage output, so
    dropping one from ``_HELPER_MODULES`` has to fail here.
    """
    listed = [module.__name__ for module in stage._HELPER_MODULES]

    missing = [
        name for name in EXPECTED_OTHER_HELPER_MODULE_NAMES if name not in listed
    ]
    assert not missing, f"first-party helpers missing from _HELPER_MODULES: {missing}"


def test_helper_modules_contains_no_synpp_stage():
    """Only the two documented undeclared stage libraries may be synpp stages.

    A synpp stage whose dependency this stage DECLARES (``context.stage(...)`` in
    ``configure``) is hashed by synpp from its own source and propagated through
    that declared edge, so listing it here would double-cover it and add cache
    churn without closing any gap -- this assertion keeps such a module out.

    The two exceptions in ``UNDECLARED_STAGE_LIBRARY_MODULE_NAMES`` are allowed
    precisely because that propagation does NOT happen for them: they are used as
    plain function libraries and are not declared dependencies, so the token is
    the only mechanism that can see them (see
    ``test_helper_modules_cover_the_undeclared_stage_libraries``). They are named
    explicitly rather than waved through by a predicate, so a THIRD synpp stage
    joining ``_HELPER_MODULES`` has to be justified in a visible edit here
    instead of slipping in silently.
    """
    stages = [
        module.__name__ for module in stage._HELPER_MODULES
        if hasattr(module, "configure") and hasattr(module, "execute")
    ]

    unexpected = [
        name for name in stages
        if name not in UNDECLARED_STAGE_LIBRARY_MODULE_NAMES
    ]

    assert not unexpected, (
        "synpp stages must not be listed in _HELPER_MODULES unless they are an "
        f"undeclared library dependency: {unexpected}"
    )


def test_helper_modules_has_no_duplicates():
    """A module listed twice would double-hash it and hide a coverage mistake."""
    listed = [module.__name__ for module in stage._HELPER_MODULES]

    assert len(listed) == len(set(listed)), f"duplicate entries in _HELPER_MODULES: {listed}"
