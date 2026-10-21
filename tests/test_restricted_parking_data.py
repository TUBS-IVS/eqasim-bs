"""The three restricted parking files stay out of git, and the tests that need them skip cleanly without them.

Issue #436, ADR-0140 (Consequences): the zone polygons, the resident parking districts and the garage dataset rest on
sources that are not cleared for redistribution. They stay on disk (``eqasim-data/`` is ignored) and are available on
request. ``tests/restricted_parking_data.py`` is the one place that the other tests use to reach them.
"""
from __future__ import annotations

import re
import subprocess

import pytest
import yaml

from tests import restricted_parking_data as restricted

REPO_ROOT = restricted.REPO_ROOT
RECORD_NAMES = {"parking_zones_2026.geojson": "parking_zones_2026",
                "parking_resident_districts_2026.geojson": "parking_resident_districts_2026",
                "parking_garages_2026.geojson": "parking_garages_2026"}


def _git(*arguments: str) -> subprocess.CompletedProcess:
    result = subprocess.run(["git", *arguments], cwd=REPO_ROOT, capture_output=True, text=True)
    if result.returncode == 128:       # git's "fatal": not a git checkout (a source copy), so the index cannot be asked
        pytest.skip("not a git checkout")
    return result


def _skip_message(call) -> str:
    with pytest.raises(pytest.skip.Exception) as skipped:
        call()
    return str(skipped.value)


@pytest.mark.parametrize("name", restricted.RESTRICTED_FILES)
def test_an_absent_restricted_file_skips_with_the_reason_that_names_the_request_route(name, tmp_path, monkeypatch):
    monkeypatch.setenv(restricted.LOOKUP_DIR_ENVIRONMENT_VARIABLE, str(tmp_path))
    message = _skip_message(lambda: restricted.parking_data_path(name))
    assert message == ("restricted parking data, not in the repository; available on request "
                       "(GitHub issue in TUBS-IVS/eqasim-bs)")


def test_a_present_restricted_file_is_returned_and_an_unrestricted_file_never_skips(tmp_path, monkeypatch):
    monkeypatch.setenv(restricted.LOOKUP_DIR_ENVIRONMENT_VARIABLE, str(tmp_path))
    (tmp_path / "parking_zones_2026.geojson").write_text("{}", encoding="utf-8")
    assert restricted.parking_data_path("parking_zones_2026.geojson") == tmp_path / "parking_zones_2026.geojson"
    # the tariff table is committed: its path is the repository path, whatever the lookup folder says
    assert restricted.parking_data_path("parking_tariffs_2026.csv") == restricted.PARKING_DIR / "parking_tariffs_2026.csv"


def test_the_whole_set_is_required_by_the_stage_level_tests(tmp_path, monkeypatch):
    monkeypatch.setenv(restricted.LOOKUP_DIR_ENVIRONMENT_VARIABLE, str(tmp_path))
    for name in restricted.RESTRICTED_FILES[:-1]:
        (tmp_path / name).write_text("{}", encoding="utf-8")
    assert "available on request" in _skip_message(restricted.require_restricted_parking_files)
    (tmp_path / restricted.RESTRICTED_FILES[-1]).write_text("{}", encoding="utf-8")
    restricted.require_restricted_parking_files()


def test_the_shared_message_names_the_record_the_route_and_the_expected_path(tmp_path):
    from braunschweig.parking import restricted_inputs

    assert set(restricted_inputs.RESTRICTED_RECORDS) == set(restricted.RESTRICTED_FILES)
    path = tmp_path / "parking_garages_2026.geojson"
    with pytest.raises(FileNotFoundError) as error:
        restricted_inputs.require_restricted_file("parking_garages_2026", "garage dataset", path)
    message = str(error.value)
    assert message.startswith("garage dataset does not exist") and str(path) in message
    assert "not distributed in the repository" in message and "available on request" in message
    assert "TUBS-IVS/eqasim-bs" in message and "docs/registry/data/parking_garages_2026.yml" in message
    path.write_text("{}", encoding="utf-8")
    assert restricted_inputs.require_restricted_file("parking_garages_2026", "garage dataset", path) == path


def test_the_content_hash_ignores_the_line_ending_of_the_checkout(tmp_path):
    lf, crlf = tmp_path / "lf.geojson", tmp_path / "crlf.geojson"
    lf.write_bytes(b'{"a": 1}\n{"b": 2}\n')
    crlf.write_bytes(b'{"a": 1}\r\n{"b": 2}\r\n')
    assert restricted.lf_sha256(lf) == restricted.lf_sha256(crlf)


@pytest.mark.parametrize("name", restricted.RESTRICTED_FILES)
def test_the_restricted_files_are_ignored_by_git_and_not_tracked(name):
    relative = f"eqasim-data/data/braunschweig/parking/{name}"
    assert _git("check-ignore", "-q", "--no-index", relative).returncode == 0, f"{relative} is not ignored"
    assert _git("ls-files", "--error-unmatch", relative).returncode != 0, f"{relative} is tracked again"


def _record(name: str):
    record_path = REPO_ROOT / "docs" / "registry" / "data" / f"{RECORD_NAMES[name]}.yml"
    return record_path, yaml.safe_load(record_path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", restricted.RESTRICTED_FILES)
def test_the_data_record_states_that_the_file_is_local_restricted_and_how_to_request_it(name):
    # runs without the files: the record is committed
    _, record = _record(name)
    assert record["storage"]["committed"] is False and record["storage"]["local_only"] is True
    assert record["licensing"]["restricted"] is True and record["licensing"]["redistribution"] == "restricted"
    notes = " ".join(record["storage"]["notes"].split())
    assert "available on request" in notes and "TUBS-IVS/eqasim-bs" in notes
    assert len(re.findall(r"\b[0-9a-f]{64}\b", notes)) == 1, "exactly one pinned SHA-256 per file"


@pytest.mark.parametrize("name", restricted.RESTRICTED_FILES)
def test_the_working_file_matches_the_hash_pinned_in_its_data_record(name):
    # The data record owns the pin (storage.notes, once per file); a recipient of the files on request verifies against it.
    path = restricted.parking_data_path(name)
    record_path, record = _record(name)
    digest = restricted.lf_sha256(path)
    assert digest in " ".join(record["storage"]["notes"].split()), (
        f"{name}: SHA-256 {digest} of the working file is not the pin in {record_path.name}; if the file was "
        "regenerated on purpose, update the pin in storage.notes of the record")
