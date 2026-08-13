"""Tests for the ``braunschweig.popsim.stage`` synpp cache-validation token.

The popsim stage had NO ``validate()`` hook before issue #267. synpp's
``get_stage_hash`` hashes only the stage module's OWN source, so a change
confined to a helper -- one of the ``stage`` package's submodules, or one of the
``braunschweig.popsim.mid`` modules that actually build the seed, controls and
batch folders -- left the stage hash untouched and the stale cached stage output
was silently reused. That is the documented synpp helper trap and a
scientific-correctness hazard, so these tests pin the three properties the hook
must have:

- the token is a STABLE 32-char lowercase md5 hex digest (a stable token is what
  keeps the cache usable at all),
- the token CHANGES when the source of ANY listed helper changes (otherwise the
  hook does not close the trap),
- ``_HELPER_MODULES`` really covers the ``braunschweig.popsim.mid`` package, so a
  future edit that drops mid coverage fails loudly here instead of silently
  re-opening the gap.

The "source changed" case is simulated by monkeypatching ``inspect.getsource``
for ONE listed module; no file on disk is written or modified, and nothing
depends on external data, so the tests are deterministic and side-effect free.
"""

from __future__ import annotations

import hashlib
import inspect

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

HEX_DIGITS = set("0123456789abcdef")


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

    Recomputing the expected digest independently pins BOTH the covered set and
    the deterministic iteration order (a set or ``dir()`` based order would make
    the token vary between processes).
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


def test_helper_modules_has_no_duplicates():
    """A module listed twice would double-hash it and hide a coverage mistake."""
    listed = [module.__name__ for module in stage._HELPER_MODULES]

    assert len(listed) == len(set(listed)), f"duplicate entries in _HELPER_MODULES: {listed}"
