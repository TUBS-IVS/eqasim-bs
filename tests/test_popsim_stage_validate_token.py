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
  submodules AND the other first-party helper modules the stage imports at module
  level, so a future edit that DROPS coverage fails loudly here instead of
  silently re-opening the gap,
- the DEFERRED (function-level) first-party dependencies named in
  ``_DEFERRED_HELPER_MODULE_NAMES`` -- including the
  ``braunschweig.synthesis.population.enriched`` package, enumerated one level
  deep -- are all importable and every one of them really contributes to the
  digest; they are direct dependencies of the stage's result (``control_spec``
  owns the control catalog), so a name that silently stopped being hashed would
  re-open the trap for it,
- every submodule DISCOVERED on disk under those four packages is covered by ONE
  of the two mechanisms, so a helper ADDED later cannot stay silently uncovered
  (the literal expectation lists only catch removals),
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
# stage's result live in the adapters, so all of them must be covered. Since #287
# the ENTD adapter is a facade over seven siblings, and those siblings -- not
# ``entd`` itself -- now hold that behaviour, so they belong here too.
EXPECTED_SOURCES_MODULE_NAMES = (
    "braunschweig.popsim.sources",
    "braunschweig.popsim.sources.base",
    "braunschweig.popsim.sources.entd",
    "braunschweig.popsim.sources.entd_attributes",
    "braunschweig.popsim.sources.entd_diary_matching",
    "braunschweig.popsim.sources.entd_donor",
    "braunschweig.popsim.sources.entd_schema",
    "braunschweig.popsim.sources.entd_seed",
    "braunschweig.popsim.sources.entd_trips",
    "braunschweig.popsim.sources.entd_vocabulary",
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
# covered, because the stage uses them as plain function libraries WITHOUT
# declaring the dependency via ``context.stage(...)``: synpp's own stage hash for
# them therefore never reaches this stage, and no declared DAG edge propagates it
# either, so this token is the ONLY mechanism that can see a change in them.
# ``household_size`` is imported at MODULE level for ``kreis_household_stats``, so
# it is covered as a module OBJECT in ``_HELPER_MODULES``; ``enriched`` is imported
# at FUNCTION level for ``_apply_housing_tenure``, so it is covered by dotted NAME
# in ``_DEFERRED_HELPER_MODULE_NAMES``. Named explicitly so a THIRD synpp stage
# cannot silently join the token: adding one has to be a visible edit to this
# tuple, justified on the same two grounds.
UNDECLARED_STAGE_LIBRARY_MODULE_NAMES = (
    "braunschweig.data.census.household_size",
    "braunschweig.synthesis.population.enriched",
)

# The ``enriched`` package enumerated ONE level deep: its stage ``__init__`` plus
# every submodule. ``inspect.getsource`` of a package returns only its
# ``__init__``, and the function the popsim stage actually calls,
# ``_apply_housing_tenure``, lives in ``enriched.housing_tenure`` -- so covering the
# package alone would hash the facade and leave an edit to the tenure helper
# invisible to the token. Every entry is covered by dotted NAME (the package is
# imported at function level only): a dotted-name entry enumerates a package one
# level deep just as module objects do, one literal entry per submodule. Written
# out literally for the same reason as the lists above.
EXPECTED_ENRICHED_MODULE_NAMES = (
    "braunschweig.synthesis.population.enriched",
    "braunschweig.synthesis.population.enriched.availability",
    "braunschweig.synthesis.population.enriched.base",
    "braunschweig.synthesis.population.enriched.economic_status",
    "braunschweig.synthesis.population.enriched.housing_tenure",
    "braunschweig.synthesis.population.enriched.income_distribution",
    "braunschweig.synthesis.population.enriched.vehicle_ownership",
)

# The DEFERRED (function-level) first-party direct dependencies, covered by dotted
# NAME in ``_DEFERRED_HELPER_MODULE_NAMES`` and imported lazily inside
# ``validate()``. Written out literally, again as an independent statement of what
# MUST be covered: dropping one from the stage tuple cannot hide by also
# disappearing from this list. The ``enriched`` names are appended from the literal
# tuple above rather than repeated here (one fact, one place); the result is still
# an explicit literal list, in the stage tuple's own dotted-path order.
EXPECTED_DEFERRED_HELPER_MODULE_NAMES = (
    "braunschweig.data.mid.tenure_by_income",
    "braunschweig.parallelism",
    "braunschweig.popsim.control_spec",
    "braunschweig.popsim.employment_grid",
    "braunschweig.popsim.folders",
    "braunschweig.popsim.kreis_attribute_control",
    "braunschweig.popsim.placement_income",
    "braunschweig.popsim.zensus_employment_age",
) + EXPECTED_ENRICHED_MODULE_NAMES

# Packages whose submodules must ALL appear in _HELPER_MODULES. Discovered
# dynamically (see test_helper_modules_cover_every_discovered_submodule) so a
# helper module ADDED later cannot stay unlisted. Enumerated one level deep only,
# matching the token's own documented bound.
DYNAMICALLY_ENUMERATED_PACKAGE_NAMES = (
    "braunschweig.popsim.stage",
    "braunschweig.popsim.mid",
    "braunschweig.popsim.sources",
    "braunschweig.synthesis.population.enriched",
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


def _covered_module_names() -> set[str]:
    """Dotted names of every module the token hashes, via EITHER mechanism.

    ``_HELPER_MODULES`` holds module objects (module-level imports),
    ``_DEFERRED_HELPER_MODULE_NAMES`` dotted strings (function-level imports). Both
    end up in the same digest, so a test that asks "is this module covered at all?"
    must look at the union; tests that pin WHICH mechanism covers a given module
    inspect the individual tuples instead.
    """
    return {module.__name__ for module in stage._HELPER_MODULES}.union(
        stage._DEFERRED_HELPER_MODULE_NAMES
    )


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
    """The digest must be md5 over both listed sets, in tuple order.

    ``_HELPER_MODULES`` (module objects) first, then
    ``_DEFERRED_HELPER_MODULE_NAMES`` (dotted names, imported lazily). The
    expectation is recomputed FROM those two tuples, so this pins the hashing rule
    and the deterministic ITERATION ORDER only -- a set or ``dir()`` based order
    would make the token vary between processes -- not which modules are covered.
    The covered set is pinned by the literal expectation lists and the discovery
    test below.
    """
    expected = hashlib.md5()
    for module in stage._HELPER_MODULES:
        expected.update(inspect.getsource(module).encode("utf-8"))
    for module_name in stage._DEFERRED_HELPER_MODULE_NAMES:
        expected.update(
            inspect.getsource(importlib.import_module(module_name)).encode("utf-8")
        )

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
# the deferred (function-level) first-party dependencies
# ---------------------------------------------------------------------------

def test_deferred_helper_modules_are_all_importable():
    """Every deferred name must import and expose retrievable source.

    ``validate()`` resolves these lazily at RUN time; if one cannot be imported it
    raises rather than skipping the module, so an unimportable name here would
    break every run instead of quietly shrinking the token. Catch that at test
    time.
    """
    for module_name in stage._DEFERRED_HELPER_MODULE_NAMES:
        module = importlib.import_module(module_name)
        assert module.__name__ == module_name
        assert inspect.getsource(module).strip(), f"no source for {module_name}"


@pytest.mark.parametrize("target_module_name", EXPECTED_DEFERRED_HELPER_MODULE_NAMES)
def test_validate_token_changes_when_a_deferred_helper_source_changes(
        monkeypatch, target_module_name):
    """A changed DEFERRED dependency's source must change the token too.

    These are function-level imports, so they were absent from the module-level
    set the token was originally built from -- ``control_spec`` in particular owns
    the control catalog, so an edit there changes the stage's controls without
    changing this file. Parametrised over EVERY name, so a single one falling out
    of the hashed loop fails here rather than silently reusing stale cached output.
    """
    baseline_token = stage.validate(None)
    target_module = importlib.import_module(target_module_name)

    real_getsource = inspect.getsource

    def fake_getsource(object_):
        if object_ is target_module:
            return real_getsource(object_) + "\n# simulated deferred helper edit\n"
        return real_getsource(object_)

    monkeypatch.setattr(inspect, "getsource", fake_getsource)
    changed_token = stage.validate(None)

    assert changed_token != baseline_token
    assert len(changed_token) == 32

    monkeypatch.undo()
    assert stage.validate(None) == baseline_token


def test_deferred_helper_module_names_are_exactly_the_expected_names():
    """The deferred set must match the literal expectation, in the same order.

    Order matters because the digest is order-dependent, and the covered SET
    matters because each entry is a direct dependency of the stage's result. An
    addition or a removal therefore has to be a visible edit to BOTH tuples.
    """
    assert stage._DEFERRED_HELPER_MODULE_NAMES == EXPECTED_DEFERRED_HELPER_MODULE_NAMES


def test_deferred_names_do_not_duplicate_the_module_level_helpers():
    """No module may be covered twice, once per mechanism.

    A name in both tuples would be hashed twice and, worse, would hide a mistake
    about WHICH mechanism covers it (module object vs lazy dotted name).
    """
    listed = {module.__name__ for module in stage._HELPER_MODULES}
    overlap = sorted(listed.intersection(stage._DEFERRED_HELPER_MODULE_NAMES))

    assert not overlap, (
        "modules covered by BOTH _HELPER_MODULES and _DEFERRED_HELPER_MODULE_NAMES: "
        f"{overlap}"
    )
    assert len(stage._DEFERRED_HELPER_MODULE_NAMES) == len(
        set(stage._DEFERRED_HELPER_MODULE_NAMES)
    ), "duplicate entries in _DEFERRED_HELPER_MODULE_NAMES"


def test_validate_raises_naming_the_module_when_a_deferred_import_fails(monkeypatch):
    """A broken deferred dependency must be LOUD, never silently unhashed.

    Swallowing the failure would drop that module from the token exactly when its
    code is broken, i.e. keep the stale cached output alive at the worst possible
    moment (CLAUDE.md: no silent fallbacks). The raised message must name the
    module so the failure is diagnosable without a debugger.
    """
    target_module_name = stage._DEFERRED_HELPER_MODULE_NAMES[0]
    real_import_module = importlib.import_module

    def fake_import_module(name, *args, **kwargs):
        if name == target_module_name:
            raise ImportError("simulated broken deferred dependency")
        return real_import_module(name, *args, **kwargs)

    monkeypatch.setattr(importlib, "import_module", fake_import_module)

    with pytest.raises(RuntimeError) as error_info:
        stage.validate(None)

    assert target_module_name in str(error_info.value)
    assert isinstance(error_info.value.__cause__, ImportError)


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


def test_the_undeclared_stage_libraries_are_covered_by_one_of_the_two_mechanisms():
    """The two undeclared synpp-stage libraries must be covered.

    Both are synpp stages used here as plain function libraries whose dependency
    ``configure()`` does NOT declare, so neither synpp's own stage hashing nor a
    declared DAG edge propagates their source into this stage's cache key. If one
    is dropped from BOTH tuples, an edit to ``kreis_household_stats`` or
    ``_apply_housing_tenure`` silently reuses stale output -- fail loudly here.
    Which tuple covers which is fixed by the import site and asserted separately
    below, so this test only pins that neither can fall out of the token entirely.
    """
    covered = _covered_module_names()

    missing = [
        name for name in UNDECLARED_STAGE_LIBRARY_MODULE_NAMES if name not in covered
    ]
    assert not missing, (
        "undeclared synpp-stage libraries missing from both _HELPER_MODULES and "
        f"_DEFERRED_HELPER_MODULE_NAMES: {missing}"
    )


def test_household_size_is_covered_as_a_module_object_and_enriched_by_name():
    """Each undeclared library must sit in the tuple its import site dictates.

    ``braunschweig.data.census.household_size`` is imported at MODULE level (for
    ``kreis_household_stats``), so it belongs in ``_HELPER_MODULES``.
    ``braunschweig.synthesis.population.enriched`` is imported at FUNCTION level
    only (``_apply_housing_tenure_parity``), so it belongs in
    ``_DEFERRED_HELPER_MODULE_NAMES``: adding seven module-level imports purely to
    obtain module objects covers not one extra line of source. This pins the
    placement so those imports cannot creep back in unnoticed. (Note that the
    ``enriched`` package IS loaded during this package's import anyway, via
    ``braunschweig.popsim.sources.entd``, so the placement is about a single
    consistent rule -- import site decides the tuple -- not about import cost.)
    """
    module_objects = {module.__name__ for module in stage._HELPER_MODULES}

    assert "braunschweig.data.census.household_size" in module_objects
    assert "braunschweig.synthesis.population.enriched" not in module_objects
    assert not [
        name for name in module_objects
        if name.startswith("braunschweig.synthesis.population.enriched.")
    ], "enriched submodules must be covered by dotted NAME, not as module objects"
    assert (
        "braunschweig.synthesis.population.enriched"
        in stage._DEFERRED_HELPER_MODULE_NAMES
    )


def test_deferred_helper_modules_cover_the_enriched_package_one_level_deep():
    """The ``enriched`` package must be covered ``__init__`` + submodules.

    ``inspect.getsource`` of a package yields only its ``__init__``, while the
    function the popsim stage calls -- ``_apply_housing_tenure`` -- lives in
    ``enriched.housing_tenure``. Covering the package alone therefore hashes the
    facade and leaves an edit to the tenure sampling (or to any sibling reached
    through the facade) invisible to the token, which is the same trap the ``mid``
    and ``sources`` packages are enumerated one level deep to avoid. The
    enumeration works identically through dotted names -- one literal entry per
    submodule -- which is why the deferral does not have to be given up for it.
    """
    listed = list(stage._DEFERRED_HELPER_MODULE_NAMES)

    missing = [
        name for name in EXPECTED_ENRICHED_MODULE_NAMES if name not in listed
    ]
    assert not missing, (
        f"enriched modules missing from _DEFERRED_HELPER_MODULE_NAMES: {missing}"
    )


def test_every_discovered_submodule_is_covered_by_one_of_the_two_mechanisms():
    """A helper module ADDED to a covered package must also be covered.

    The literal expectation lists above catch a module being REMOVED from the
    token; they cannot catch a NEW submodule that is never added to it (the token
    would then silently miss it and the trap re-opens for that module only).
    ``pkgutil.iter_modules`` discovers what is actually on disk, so adding a
    submodule to any of these packages without covering it -- as a module object or
    as a dotted name, whichever its import site dictates -- fails here.
    """
    covered = _covered_module_names()

    uncovered = []
    for package_name in DYNAMICALLY_ENUMERATED_PACKAGE_NAMES:
        discovered = _discovered_submodule_names(package_name)
        assert discovered, f"no submodules discovered under {package_name}"
        uncovered.extend(name for name in discovered if name not in covered)

    assert not uncovered, (
        "submodules discovered on disk but absent from both _HELPER_MODULES and "
        "_DEFERRED_HELPER_MODULE_NAMES (add them explicitly to the right tuple): "
        f"{sorted(uncovered)}"
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
    that declared edge, so covering it here would double-cover it and add cache
    churn without closing any gap -- this assertion keeps such a module out. BOTH
    tuples are checked, because the mechanism a module is covered by follows its
    import site, so a declared stage could just as easily slip into the deferred
    names as into the module objects.

    The two exceptions in ``UNDECLARED_STAGE_LIBRARY_MODULE_NAMES`` are allowed
    precisely because that propagation does NOT happen for them: they are used as
    plain function libraries and are not declared dependencies, so the token is
    the only mechanism that can see them (see
    ``test_the_undeclared_stage_libraries_are_covered_by_one_of_the_two_mechanisms``).
    They are named explicitly rather than waved through by a predicate, so a THIRD
    synpp stage joining the token has to be justified in a visible edit here
    instead of slipping in silently.
    """
    covered_modules = [module for module in stage._HELPER_MODULES] + [
        importlib.import_module(name)
        for name in stage._DEFERRED_HELPER_MODULE_NAMES
    ]
    stages = [
        module.__name__ for module in covered_modules
        if hasattr(module, "configure") and hasattr(module, "execute")
    ]

    unexpected = [
        name for name in stages
        if name not in UNDECLARED_STAGE_LIBRARY_MODULE_NAMES
    ]

    assert not unexpected, (
        "synpp stages must not be covered by the token unless they are an "
        f"undeclared library dependency: {unexpected}"
    )


def test_helper_modules_has_no_duplicates():
    """A module listed twice would double-hash it and hide a coverage mistake."""
    listed = [module.__name__ for module in stage._HELPER_MODULES]

    assert len(listed) == len(set(listed)), f"duplicate entries in _HELPER_MODULES: {listed}"
