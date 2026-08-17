"""Tests for lazy adapter resolution in the popsim source registry (issue #292).

``braunschweig.popsim.sources`` used to import BOTH adapter modules
(``mid.py`` and ``entd.py``, plus, after the #287 split, ``entd.py``'s seven
sibling modules) at package-import time, so merely importing the registry --
or calling ``get_source("mid")`` on the Braunschweig production (MiD-only)
path -- also loaded the ENTD adapter and everything it depends on. This file
pins the fix: the registry now resolves each adapter's module on FIRST USE
(``_REGISTRY`` stores ``"module:ClassName"`` dotted strings, not classes), so
importing the package or requesting one source never imports the other.

The "does X get imported" checks run in a SUBPROCESS with a clean
interpreter and inspect ``sys.modules`` there. Checking ``sys.modules`` in the
running pytest process would be unreliable: other tests in the same session
may have already imported the ENTD adapter for their own purposes (e.g.
tests/test_popsim_sources.py, tests/test_popsim_open_entd.py), which would
make an in-process assertion pass or fail depending on test order rather than
on what THIS import actually does. A subprocess sidesteps that entirely.

Note on scope (see the docstring of ``braunschweig.popsim.sources`` and
``docs/codebase/notes/popsim-sources-lazy-registry.md``): this laziness does
NOT change the popsim_mid production path, because
``braunschweig.popsim.stage`` imports the ``entd`` adapter and its seven
siblings at ITS OWN module level anyway, to cover them in its synpp
cache-validation token. These tests exercise ``braunschweig.popsim.sources``
in isolation (never importing ``braunschweig.popsim.stage`` first), which is
exactly the condition under which the laziness has an effect -- e.g. for
``braunschweig.popsim.trips_stage``, which imports ``sources`` inside its
``execute()`` function body.
"""

from __future__ import annotations

import subprocess
import sys

import pytest


def _run_subprocess_check(code: str) -> str:
    """Run ``code`` in a fresh interpreter and return its stdout, raising on failure.

    A clean ``sys.executable`` subprocess is used (not the running pytest
    process) so ``sys.modules`` reflects ONLY what ``code`` itself imports,
    unaffected by whatever other test modules already imported earlier in
    this session.
    """
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, (
        f"subprocess failed (exit {result.returncode}):\n"
        f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
    )
    return result.stdout


# ---------------------------------------------------------------------------
# Importing the registry must not import a non-selected adapter.
# ---------------------------------------------------------------------------

def test_importing_sources_package_imports_no_adapter_module():
    """``import braunschweig.popsim.sources`` alone must not import either
    adapter module (``mid`` or ``entd``), nor any of the seven ENTD siblings.

    This is the core regression this issue guards against: before the fix,
    the package ``__init__`` imported both ``mid.py`` and ``entd.py`` (and,
    transitively, everything ``entd.py`` imports) unconditionally.
    """
    code = (
        "import sys\n"
        "from braunschweig.popsim import sources\n"
        "adapter_modules = [\n"
        "    m for m in sys.modules\n"
        "    if m.startswith('braunschweig.popsim.sources.')\n"
        "    and m != 'braunschweig.popsim.sources.base'\n"
        "]\n"
        "print('ADAPTER_MODULES=' + ','.join(sorted(adapter_modules)))\n"
    )
    stdout = _run_subprocess_check(code)
    line = next(l for l in stdout.splitlines() if l.startswith("ADAPTER_MODULES="))
    imported = line[len("ADAPTER_MODULES="):]
    assert imported == "", (
        f"importing braunschweig.popsim.sources imported adapter submodule(s): "
        f"{imported!r} (expected none -- adapters must resolve lazily)"
    )


def test_importing_sources_package_imports_base_only():
    """The ONE adapter-package submodule that MUST still be eager is ``base``
    (the shared ``PopsimSource`` Protocol interface, per the issue)."""
    code = (
        "import sys\n"
        "from braunschweig.popsim import sources\n"
        "assert 'braunschweig.popsim.sources.base' in sys.modules\n"
        "print('OK')\n"
    )
    stdout = _run_subprocess_check(code)
    assert "OK" in stdout


# ---------------------------------------------------------------------------
# get_source("mid") must not import the ENTD adapter.
# ---------------------------------------------------------------------------

def test_get_source_mid_does_not_import_entd():
    code = (
        "import sys\n"
        "from braunschweig.popsim import sources\n"
        "src = sources.get_source('mid')\n"
        "entd_modules = [m for m in sys.modules if m.startswith('braunschweig.popsim.sources.entd')]\n"
        "print('NAME=' + src.name)\n"
        "print('ENTD_MODULES=' + ','.join(sorted(entd_modules)))\n"
        "print('MID_IMPORTED=' + str('braunschweig.popsim.sources.mid' in sys.modules))\n"
    )
    stdout = _run_subprocess_check(code)
    lines = dict(l.split("=", 1) for l in stdout.splitlines() if "=" in l)
    assert lines["NAME"] == "mid"
    assert lines["ENTD_MODULES"] == "", (
        f"get_source('mid') imported ENTD module(s): {lines['ENTD_MODULES']!r}"
    )
    assert lines["MID_IMPORTED"] == "True"


# ---------------------------------------------------------------------------
# get_source("entd") must work and return an EntdSource.
# ---------------------------------------------------------------------------

def test_get_source_entd_returns_entd_source():
    code = (
        "from braunschweig.popsim import sources\n"
        "from braunschweig.popsim.sources.entd import EntdSource\n"
        "src = sources.get_source('entd')\n"
        "assert isinstance(src, EntdSource), type(src)\n"
        "assert src.name == 'entd'\n"
        "print('OK')\n"
    )
    stdout = _run_subprocess_check(code)
    assert "OK" in stdout


def test_get_source_entd_does_not_import_mid_adapter():
    """Requesting 'entd' must not import the MiD adapter module either
    (symmetry check: laziness must not be one-directional)."""
    code = (
        "import sys\n"
        "from braunschweig.popsim import sources\n"
        "sources.get_source('entd')\n"
        "print('MID_IMPORTED=' + str('braunschweig.popsim.sources.mid' in sys.modules))\n"
    )
    stdout = _run_subprocess_check(code)
    lines = dict(l.split("=", 1) for l in stdout.splitlines() if "=" in l)
    assert lines["MID_IMPORTED"] == "False"


# ---------------------------------------------------------------------------
# Error paths: unknown name -> ValueError; planned-but-unimplemented -> NotImplementedError.
# In-process (no subprocess needed: these only check exception behaviour, not
# what gets imported).
# ---------------------------------------------------------------------------

def test_unknown_source_raises_value_error_with_message():
    from braunschweig.popsim import sources

    with pytest.raises(ValueError) as excinfo:
        sources.get_source("does_not_exist")
    message = str(excinfo.value)
    assert "does_not_exist" in message
    assert "mid" in message and "entd" in message


def test_planned_source_raises_not_implemented_error(monkeypatch):
    """A name registered in ``_PLANNED`` (but not ``_REGISTRY``) must raise
    ``NotImplementedError`` with a message naming it, exactly as before the
    lazy-resolution change (the ``_PLANNED`` branch is untouched by this fix).
    """
    from braunschweig.popsim import sources

    monkeypatch.setattr(sources, "_PLANNED", {"srv"})
    with pytest.raises(NotImplementedError) as excinfo:
        sources.get_source("srv")
    message = str(excinfo.value)
    assert "srv" in message
    assert "planned" in message.lower()


# ---------------------------------------------------------------------------
# MidSource / EntdSource must still resolve as package attributes.
# ---------------------------------------------------------------------------

def test_mid_source_attribute_resolves_lazily():
    from braunschweig.popsim import sources
    from braunschweig.popsim.sources.mid import MidSource as DirectMidSource

    assert sources.MidSource is DirectMidSource


def test_entd_source_attribute_resolves_lazily():
    from braunschweig.popsim import sources
    from braunschweig.popsim.sources.entd import EntdSource as DirectEntdSource

    assert sources.EntdSource is DirectEntdSource


def test_mid_source_attribute_access_does_not_import_entd():
    """Accessing ``sources.MidSource`` specifically must not import the ENTD
    adapter (the two lazy attributes must not be coupled)."""
    code = (
        "import sys\n"
        "from braunschweig.popsim import sources\n"
        "_ = sources.MidSource\n"
        "entd_modules = [m for m in sys.modules if m.startswith('braunschweig.popsim.sources.entd')]\n"
        "print('ENTD_MODULES=' + ','.join(sorted(entd_modules)))\n"
    )
    stdout = _run_subprocess_check(code)
    line = next(l for l in stdout.splitlines() if l.startswith("ENTD_MODULES="))
    assert line == "ENTD_MODULES=", (
        f"accessing sources.MidSource imported ENTD module(s): {line!r}"
    )


def test_unknown_attribute_still_raises_attribute_error():
    from braunschweig.popsim import sources

    with pytest.raises(AttributeError):
        sources.not_a_real_attribute
