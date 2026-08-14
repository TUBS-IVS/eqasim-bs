"""The gravity stage's synpp validation token must cover its package helpers.

``braunschweig.gravity.model`` is a synpp stage, and synpp's ``get_stage_hash``
hashes only the stage module's OWN source. Every other module in
``braunschweig/gravity/`` whose source shapes this stage's result must therefore
be folded into ``validate()``, or an edit to it silently reuses stale cached
stage output on a partial rerun -- the pipeline runs, the tests pass, the result
is wrong.

Two mechanisms exist and both are checked here:

* ``_HELPER_MODULES`` -- module objects, for the siblings this file already
  imports at module level (the five extracted by the #267 split).
* ``_DEFERRED_HELPER_MODULE_NAMES`` -- dotted names, for the siblings reached
  only through function-level imports, resolved at run time so their deferral
  is preserved.

The literal expectations below catch a module being REMOVED from the token. The
dynamic discovery test catches the opposite and more dangerous case: a module
ADDED to the package and never covered, which is exactly how the ENTD siblings
slipped out of the popsim stage's token (issue #290, PR #296).
"""
import hashlib
import importlib
import inspect
import pkgutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

model = importlib.import_module("braunschweig.gravity.model")

# The five siblings extracted from the stage by the #267 split, written out
# literally (never derived from the tuple under test, which would make the
# assertion vacuous).
EXPECTED_HELPER_MODULE_NAMES = (
    "braunschweig.gravity.attraction_vector",
    "braunschweig.gravity.balancing",
    "braunschweig.gravity.base",
    "braunschweig.gravity.kreis_calibration",
    "braunschweig.gravity.od",
)

# The four pre-existing package siblings the stage reaches through
# function-level imports.
EXPECTED_DEFERRED_MODULE_NAMES = (
    "braunschweig.gravity.friction",
    "braunschweig.gravity.production_mass",
    "braunschweig.gravity.taz_margins",
    "braunschweig.gravity.verbindungen_anchor",
)

# Modules in the package that must NOT be in the token, each for a stated
# reason. ``distance_matrix_taz`` is its own synpp stage: synpp hashes it in its
# own right, so folding its source in here would double-count it rather than
# close a gap. ``model`` is the stage module itself, already covered by
# ``get_stage_hash``.
DELIBERATELY_EXCLUDED_MODULE_NAMES = {
    "braunschweig.gravity.distance_matrix_taz",
    "braunschweig.gravity.model",
}

HEX_DIGITS = set("0123456789abcdef")


def _covered_module_names():
    """Every module name the token folds in, by either mechanism."""
    covered = {module.__name__ for module in model._HELPER_MODULES}
    covered.update(model._DEFERRED_HELPER_MODULE_NAMES)
    return covered


def _discovered_module_names():
    """Dotted names of every direct submodule of ``braunschweig.gravity``.

    ``pkgutil.iter_modules`` reports what is actually on disk, one level deep,
    mirroring the bound the token documents for itself.
    """
    package = importlib.import_module("braunschweig.gravity")
    return {
        f"braunschweig.gravity.{info.name}"
        for info in pkgutil.iter_modules(package.__path__)
    }


def test_helper_modules_tuple_holds_the_five_extracted_siblings():
    assert tuple(module.__name__ for module in model._HELPER_MODULES) == \
        EXPECTED_HELPER_MODULE_NAMES


def test_deferred_names_tuple_holds_the_four_pre_existing_siblings():
    assert model._DEFERRED_HELPER_MODULE_NAMES == EXPECTED_DEFERRED_MODULE_NAMES


def test_helper_modules_is_a_tuple_in_a_fixed_order():
    """A set or a dir()-derived list would make the token order-dependent."""
    assert isinstance(model._HELPER_MODULES, tuple)
    assert isinstance(model._DEFERRED_HELPER_MODULE_NAMES, tuple)


def test_token_is_a_deterministic_md5_hex_digest():
    first = model.validate(None)
    second = model.validate(None)
    assert first == second
    assert len(first) == 32
    assert set(first) <= HEX_DIGITS


def test_token_folds_in_every_covered_module_and_nothing_else():
    """Recompute the digest independently from the two tuples.

    This is the assertion that would fail if ``validate()`` were changed to skip
    a module, hash something twice, or hash in a different order.
    """
    digest = hashlib.md5()
    for module in model._HELPER_MODULES:
        digest.update(inspect.getsource(module).encode("utf-8"))
    for module_name in model._DEFERRED_HELPER_MODULE_NAMES:
        source = inspect.getsource(importlib.import_module(module_name))
        digest.update(source.encode("utf-8"))
    assert model.validate(None) == digest.hexdigest()


def test_every_discovered_submodule_is_covered_or_deliberately_excluded():
    """A module ADDED to the package must be covered, or excluded on purpose.

    The literal expectations above catch a removal; only enumerating what is on
    disk catches an addition that nobody folded in. That failure mode is not
    hypothetical: the ENTD siblings sat outside the popsim stage's token this way
    until PR #296.
    """
    uncovered = sorted(
        _discovered_module_names()
        - _covered_module_names()
        - DELIBERATELY_EXCLUDED_MODULE_NAMES
    )
    assert not uncovered, (
        "modules found in braunschweig/gravity/ that neither the token covers "
        "nor DELIBERATELY_EXCLUDED_MODULE_NAMES names: "
        f"{uncovered}. Add each to _HELPER_MODULES (module object, if this file "
        "imports it at module level) or to _DEFERRED_HELPER_MODULE_NAMES (dotted "
        "name, if it is imported lazily) -- or, if it genuinely cannot affect "
        "this stage's result, list it as excluded WITH the reason."
    )


def test_excluded_modules_still_exist_and_distance_matrix_taz_is_a_stage():
    """The exclusions must stay justified, not merely inherited.

    If ``distance_matrix_taz`` ever stopped being a synpp stage, excluding it
    would silently become a coverage gap instead of a correct decision.
    """
    for module_name in DELIBERATELY_EXCLUDED_MODULE_NAMES:
        assert module_name in _discovered_module_names(), (
            f"{module_name} is listed as deliberately excluded but no longer "
            "exists in the package; drop the stale exclusion."
        )

    taz_stage = importlib.import_module("braunschweig.gravity.distance_matrix_taz")
    assert callable(getattr(taz_stage, "configure", None))
    assert callable(getattr(taz_stage, "execute", None))


def test_a_deferred_module_that_cannot_be_imported_raises_not_skips():
    """Silently skipping a broken dependency would keep stale output alive."""
    original = model._DEFERRED_HELPER_MODULE_NAMES
    model._DEFERRED_HELPER_MODULE_NAMES = original + (
        "braunschweig.gravity.this_module_does_not_exist",
    )
    try:
        with pytest.raises(RuntimeError, match="this_module_does_not_exist"):
            model.validate(None)
    finally:
        model._DEFERRED_HELPER_MODULE_NAMES = original
