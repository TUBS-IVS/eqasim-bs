"""Shared pytest fixtures for eqasim-bs test suite."""
from __future__ import annotations

import logging
from pathlib import Path

import pytest


def popsim_stage_package_source_text() -> str:
    """Concatenated source of the ``braunschweig.popsim.stage`` package.

    ``braunschweig/popsim/stage.py`` was converted into a package
    (``braunschweig/popsim/stage/__init__.py`` plus submodules) while this
    split was carried out. Several tests pin behaviour by grepping the stage
    module's *source text* rather than importing and exercising it; those
    tests originally hard-coded the single-file path
    ``Path("braunschweig/popsim/stage.py")``, which raises
    ``FileNotFoundError`` now that the file is a directory.

    Reading the whole package instead of a single module keeps the pin
    resilient to further splits: any later task that moves a name between
    ``stage/__init__.py`` and one of its submodules (or introduces a new
    submodule) cannot silently break -- or silently weaken -- a source-text
    assertion, because the concatenation is always a superset of what the
    single ``stage.py`` file used to contain. Positive assertions ("pattern X
    appears") keep working because X still appears *somewhere* in the
    package; negative assertions ("pattern Y is absent") become *stricter*,
    since Y must now be absent from every submodule, not just one file.
    """
    from braunschweig.popsim import stage

    package_dir = Path(stage.__file__).resolve().parent
    module_paths = [Path(stage.__file__).resolve()]
    module_paths.extend(
        sorted(
            path
            for path in package_dir.glob("*.py")
            if path.resolve() not in module_paths
        )
    )
    return "\n".join(path.read_text(encoding="utf-8") for path in module_paths)


@pytest.fixture(autouse=True)
def _clean_root_logger_eqasim_handlers():
    """Bookend eqasim-tagged root-logger handlers around each test.

    setup_logging() is idempotent within a single call-pair, but the root logger
    persists across tests in the same process.  Any _eqasim_console / _eqasim_file
    handler left by a previous test would make idempotency checks vacuous; and the
    handler's file could be in the previous test's tmp_path (already closed/deleted).

    We remove only eqasim-tagged handlers before the test, and again after, so that
    each test that calls setup_logging() starts with a clean slate.  Pytest's own
    log-capture handlers (_FileHandler → /dev/null, LogCaptureHandler) are left
    untouched so caplog fixtures and live-logging keep working.
    """
    root = logging.getLogger()

    def _drop_eqasim():
        for h in list(root.handlers):
            if getattr(h, "_eqasim_console", False) or getattr(h, "_eqasim_file", False):
                h.close()
                root.removeHandler(h)

    _drop_eqasim()   # clean up from any previous test
    yield
    _drop_eqasim()   # clean up after this test
