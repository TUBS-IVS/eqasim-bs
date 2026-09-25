"""Shared pytest fixtures for eqasim-bs test suite."""
from __future__ import annotations

import copy
import io
import logging
import sys
import types
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


_COMMITTED_DATA_PATH = str(Path(__file__).resolve().parents[1] / "eqasim-data" / "data")


@pytest.fixture(scope="session")
def committed_fleet_sampler():
    """One FleetSampler over the committed KBA tables for the whole session.

    ``sample_fleet`` re-applies every per-call setting to the sampler it is given,
    so a reused sampler draws exactly what a fresh one would; the OFF-path goldens
    in test_fleet_sampling_de and test_fleet_consistency_e2e pin that.
    """
    from braunschweig.synthesis.vehicles import fleet_sampling_de as fs

    return fs.FleetSampler.from_data_path(_COMMITTED_DATA_PATH)


@pytest.fixture(scope="session")
def _default_fleet_sample_once(committed_fleet_sampler):
    from braunschweig.synthesis.vehicles import fleet_sampling_de as fs
    from tests.fleet_frames import make_fleet_cars

    return fs.sample_fleet(make_fleet_cars(), _COMMITTED_DATA_PATH, random_seed=42,
                           sampler=committed_fleet_sampler)


@pytest.fixture(scope="session")
def _default_fleet_sample_legacy_once(committed_fleet_sampler):
    from braunschweig.synthesis.vehicles import fleet_sampling_de as fs
    from tests.fleet_frames import make_fleet_cars

    return fs.sample_fleet(make_fleet_cars(), _COMMITTED_DATA_PATH, random_seed=42,
                           sampler=committed_fleet_sampler, consistency_v2=False)


@pytest.fixture
def default_fleet_sample(_default_fleet_sample_once):
    """``(df_spec, df_types, summary)`` of the default consistency-v2 draw of
    ``make_fleet_cars()`` (32,000 cars, random_seed=42), sampled once per session;
    each test gets its own copies."""
    df_spec, df_types, summary = _default_fleet_sample_once
    return df_spec.copy(), df_types.copy(), copy.deepcopy(summary)


@pytest.fixture
def default_fleet_sample_legacy(_default_fleet_sample_legacy_once):
    """``(df_spec, df_types)`` of the same frame and seed on the legacy path
    (``consistency_v2=False``), sampled once per session; copies per test."""
    df_spec, df_types = _default_fleet_sample_legacy_once
    return df_spec.copy(), df_types.copy()


# The upstream MATSim writers wrap every output file in an io.BufferedWriter with a 2 GiB
# buffer, a throughput choice for 100 % populations. Windows commits that memory up front,
# so parallel test workers (pytest -n) writing at the same time run out of it (MemoryError).
# Tests cap the buffer at 16 MiB through a module-local ``io`` stand-in: buffering decides
# when bytes are flushed, never which bytes are written, and the production modules stay
# untouched (their source is part of the synpp stage hashes). Nothing is imported here, so
# the metadata-only documentation workflow (no pandas) still loads this conftest.
_MATSIM_WRITER_MODULES = (
    "matsim.scenario.facilities", "matsim.scenario.households",
    "matsim.scenario.population", "matsim.scenario.vehicles",
)
_TEST_WRITE_BUFFER_BYTES = 16 * 1024 ** 2


def _capped_buffered_writer(raw, buffer_size=io.DEFAULT_BUFFER_SIZE):
    return io.BufferedWriter(raw, buffer_size=min(buffer_size, _TEST_WRITE_BUFFER_BYTES))


_CAPPED_IO = types.SimpleNamespace(
    **{name: getattr(io, name) for name in dir(io) if not name.startswith("__")})
_CAPPED_IO.BufferedWriter = _capped_buffered_writer


@pytest.fixture(autouse=True)
def _cap_matsim_writer_buffers(monkeypatch):
    for name in _MATSIM_WRITER_MODULES:
        module = sys.modules.get(name)
        if module is not None:
            monkeypatch.setattr(module, "io", _CAPPED_IO)


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
