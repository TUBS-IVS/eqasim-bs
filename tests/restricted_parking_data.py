"""Shared access to the restricted parking data files that are NOT part of the repository.

Three files of the parking cost zone release rest on sources that are not cleared for redistribution (their data
records say ``redistribution: restricted``): the zone polygons, the resident parking districts and the garage dataset.
They stay on disk under ``eqasim-data/data/braunschweig/parking/`` like the other raw data (the folder is ignored by
git) and are available on request (GitHub issue in TUBS-IVS/eqasim-bs). A checkout without them must SKIP every test
that reads them, with one explicit reason, instead of failing; tests on synthetic fixtures are unaffected.

All tests reach the files through this module (one place, no copy per test file):

* ``committed_parking_path(name)``: path of a file of the parking folder; skips when ``name`` is restricted and absent.
* ``require_restricted_parking_files()``: skips unless all three files are present (for tests that hand the whole data
  root to a stage or a script, which then reads the files itself).
* ``lf_sha256(path)``: SHA-256 of the LF-normalised content, the form of the pin that each data record states
  (docs/registry/data/parking_*_2026.yml, storage.notes) so that a recipient can verify the files; the record owns
  the value, tests/test_restricted_parking_data.py compares it with the working file.

Test-support switch (documented for the "files absent" check, never needed in a normal run): the environment variable
``EQASIM_BS_RESTRICTED_PARKING_DIR`` moves the lookup of the restricted files to another folder, e.g. an empty scratch
folder, so absence can be simulated without touching the real files. Tests that pass the whole real data root to a
stage still read that root once they have passed the presence check, so the switch is meant for the empty case.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PARKING_DIR = REPO_ROOT / "eqasim-data" / "data" / "braunschweig" / "parking"
LOOKUP_DIR_ENVIRONMENT_VARIABLE = "EQASIM_BS_RESTRICTED_PARKING_DIR"

RESTRICTED_FILES = ("parking_zones_2026.geojson", "parking_resident_districts_2026.geojson",
                    "parking_garages_2026.geojson")
SKIP_REASON = ("restricted parking data, not in the repository; available on request "
               "(GitHub issue in TUBS-IVS/eqasim-bs)")


def lf_sha256(path: Path) -> str:
    """SHA-256 of the file content with CRLF folded to LF (a Windows checkout may rewrite line endings)."""
    return hashlib.sha256(Path(path).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def _restricted_dir() -> Path:
    override = os.environ.get(LOOKUP_DIR_ENVIRONMENT_VARIABLE)
    return Path(override) if override else PARKING_DIR


def committed_parking_path(name: str) -> Path:
    """Path of ``name`` in the parking folder; a restricted file that is absent skips the calling test."""
    if name in RESTRICTED_FILES:
        path = _restricted_dir() / name
        if not path.is_file():
            pytest.skip(SKIP_REASON)
        return path
    return PARKING_DIR / name


def require_restricted_parking_files() -> None:
    """Skip the calling test unless all three restricted files are present."""
    for name in RESTRICTED_FILES:
        committed_parking_path(name)
