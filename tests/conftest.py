"""Shared pytest fixtures for eqasim-bs test suite."""
from __future__ import annotations

import logging

import pytest


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
