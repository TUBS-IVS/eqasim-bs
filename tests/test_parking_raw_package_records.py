"""The three owner research packages behind the parking v3 data (spec v3 B6, issue #436) are registered raw sources.

A package is registered once, as a data record of its own (acquisition, SHA-256, expected local path, licence), and is
listed in the input preflight (``scripts/verify_braunschweig_inputs.py``) as an optional, restricted curation input. The
curation code pins the same SHA-256 in a constant; these tests tie the record, the code pin, the preflight entry and
(where the local file exists) the file itself together, so a package that was replaced cannot go unnoticed.
"""
from __future__ import annotations

import hashlib
import importlib
import importlib.util
import re
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
CURATION_DIR = REPO_ROOT / "scripts" / "curation" / "parking_zones_2026"
DATA_DIRECTORY = REPO_ROOT / "docs" / "registry" / "data"
RAW_SOURCES = "braunschweig/parking/raw_sources"

#: record id -> (preflight entry prefix, path of the zip below eqasim-data/data, code module and constant of the pin)
PACKAGES = {
    "parking_package_nachrecherche_2026_10_14": (
        "D12", f"{RAW_SOURCES}/municipal_2026-10-14/Parkdaten_Nachrecherche_2026-10-14.zip",
        "resident_districts_from_streets", "PACKAGE_SHA256"),
    "parking_package_restluecken_2026_10_14": (
        "D13", f"{RAW_SOURCES}/municipal_2026-10-14/Parkdaten_Restluecken_2026-10-14.zip",
        "large_lots", "WOB_EVIDENCE_SHA256"),
    "parking_package_wolfsburg_parkdaten_2026_10_01": (
        "D14", f"{RAW_SOURCES}/municipal_2026-10-01/Wolfsburg_Parkdaten.zip",
        "municipal_zones", "PACKAGE_SHA256"),
}


def _record(dataset: str, directory: Path = DATA_DIRECTORY) -> dict:
    with open(directory / f"{dataset}.yml", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def _code_pin(module_name: str, constant: str) -> str:
    sys.path.insert(0, str(CURATION_DIR))
    try:
        value = getattr(importlib.import_module(module_name), constant)
    finally:
        sys.path.remove(str(CURATION_DIR))
    if isinstance(value, dict):
        value = value["Wolfsburg_Parkdaten"]
    return value


def _preflight():
    spec = importlib.util.spec_from_file_location(
        "verify_braunschweig_inputs", REPO_ROOT / "scripts" / "verify_braunschweig_inputs.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return {entry.name.split()[0]: entry for entry in module.INPUTS}


def _recorded_size_bytes(record: dict) -> int | None:
    """The byte size a record states in storage.notes as ``(N bytes``, with thousands separators; None when absent."""
    match = re.search(r"\(([\d,]+) bytes", record["storage"]["notes"])
    return int(match.group(1).replace(",", "")) if match else None


def _assert_record_matches_pin(dataset: str, directory: Path = DATA_DIRECTORY) -> None:
    """The record states the SHA-256 that the curation code verifies, and the expected path of the package."""
    _, relative_path, module_name, constant = PACKAGES[dataset]
    pin = _code_pin(module_name, constant)
    assert len(pin) == 64
    record = _record(dataset, directory)
    assert pin in record["storage"]["notes"], (
        f"{dataset}: storage.notes must state the SHA-256 {pin} that {module_name}.{constant} verifies")
    assert record["storage"]["expected_path"] == "eqasim-data/data/" + relative_path


def _assert_file_matches_record(path: Path, record: dict, pinned_sha256: str) -> None:
    """The file's bytes have the pinned SHA-256 and the byte size that the record states."""
    content = path.read_bytes()
    assert hashlib.sha256(content).hexdigest() == pinned_sha256, f"{path}: SHA-256 differs from the pin"
    recorded_size = _recorded_size_bytes(record)
    assert recorded_size is not None, "the record states no byte size in storage.notes"
    assert len(content) == recorded_size, f"{path}: size {len(content)} bytes, the record states {recorded_size}"


@pytest.mark.parametrize("dataset", sorted(PACKAGES))
def test_the_record_pins_the_sha256_that_the_curation_code_checks(dataset):
    _assert_record_matches_pin(dataset)


@pytest.mark.parametrize("dataset", sorted(PACKAGES))
def test_the_record_states_the_byte_size_of_the_package(dataset):
    assert _recorded_size_bytes(_record(dataset)) is not None


@pytest.mark.parametrize("dataset", sorted(PACKAGES))
def test_the_record_is_a_restricted_local_only_raw_source(dataset):
    record = _record(dataset)
    assert record["acquisition"]["method"] == "restricted_delivery"
    assert record["storage"]["committed"] is False and record["storage"]["local_only"] is True
    assert record["licensing"]["restricted"] is True and record["licensing"]["redistribution"] == "restricted"
    # curation input only: no stage reads the package, so nothing of it is needed to run the pipeline
    assert record["requirements"] == {"synthesis": "not_needed", "matsim": "not_needed", "production": "not_needed"}
    assert record["verification"]["verifier_entry"] == PACKAGES[dataset][0]
    assert not record.get("used_by")


@pytest.mark.parametrize("dataset", sorted(PACKAGES))
def test_the_preflight_lists_the_package_as_an_optional_restricted_curation_input(dataset):
    prefix, relative_path, _, _ = PACKAGES[dataset]
    entry = _preflight()[prefix]
    assert entry.rel_path == relative_path
    assert entry.optional and entry.restricted and entry.matsim_only
    assert dataset in entry.source and "available on request" in entry.source
    assert "curation" in entry.notes.lower()


def test_the_preflight_prefixes_of_the_packages_follow_the_garage_dataset_entry():
    names = list(_preflight())
    assert names.index("D11") < names.index("D12") < names.index("D13") < names.index("D14") < names.index("D15")


# ---- the owner's transcriptions of 2026-10-15 (spec v3 B7): a folder of three text files, one of them read by the curation code
TRANSCRIPTIONS = "parking_owner_transcriptions_2026_10_15"
TRANSCRIPTIONS_FOLDER = f"{RAW_SOURCES}/municipal_2026-10-15"
#: file -> (SHA-256 that the record states, how the code uses it: the module and constant that pin it, or None)
TRANSCRIPTION_FILES = {
    "seesen_parken_in_seesen_city_page.txt": (
        "e0bee423a5a7ec73b26a2da44af3009b0fe1dee9332a578a175acf3b49b0a123", ("documented_lots", "SEESEN_PAGE_SHA256")),
    "goslar_galeria_meinestadt_tariff.txt": (
        "e84d9312589341d29e9b3da3a329483f2aabb62534635a4726289178b605b76e", ("regional_garage_specs", "GALERIA_DIRECTORY_SHA256")),
    "northeim_qpark_city_center.txt": ("82dc47451fd6f9e7ba05490f5e840ce02d72acc40e9a2d9542f3f8ec264aa480", None),
}


def test_the_transcription_record_states_every_file_with_its_hash_and_its_use():
    record = _record(TRANSCRIPTIONS)
    assert record["storage"]["expected_path"] == "eqasim-data/data/" + TRANSCRIPTIONS_FOLDER + "/"
    notes = record["storage"]["notes"]
    for file, (digest, _) in TRANSCRIPTION_FILES.items():
        assert file in notes and digest in notes, file
    used = " ".join(record["acquisition"]["notes"].split())
    assert "seesen_parken_in_seesen_city_page.txt is USED" in used
    assert "northeim_qpark_city_center.txt is NOT USED" in used
    assert "goslar_galeria_meinestadt_tariff.txt is CITED" in used
    assert record["acquisition"]["method"] == "restricted_delivery"
    assert record["storage"]["committed"] is False and record["storage"]["local_only"] is True
    assert record["licensing"]["restricted"] is True and record["licensing"]["redistribution"] == "restricted"
    assert record["requirements"] == {"synthesis": "not_needed", "matsim": "not_needed", "production": "not_needed"}
    assert record["verification"]["verifier_entry"] == "D15"


@pytest.mark.parametrize("file", sorted(f for f, (_, pin) in TRANSCRIPTION_FILES.items() if pin is not None))
def test_the_code_pins_of_the_transcriptions_are_the_hashes_of_the_record(file):
    digest, (module_name, constant) = TRANSCRIPTION_FILES[file]
    assert _code_pin(module_name, constant) == digest
    assert digest in _record(TRANSCRIPTIONS)["storage"]["notes"]


def test_the_preflight_lists_the_transcription_that_the_code_reads_as_an_optional_restricted_curation_input():
    entry = _preflight()["D15"]
    assert entry.rel_path == f"{TRANSCRIPTIONS_FOLDER}/seesen_parken_in_seesen_city_page.txt"
    assert entry.optional and entry.restricted and entry.matsim_only
    assert TRANSCRIPTIONS in entry.source and "available on request" in entry.source
    assert "curation" in entry.notes.lower()


@pytest.mark.parametrize("file", sorted(TRANSCRIPTION_FILES))
def test_a_local_transcription_is_the_recorded_file(file):
    """Where the owner's transcription lies in the main checkout (or this worktree's data), its hash is the recorded one."""
    digest = TRANSCRIPTION_FILES[file][0]
    candidates = [
        REPO_ROOT / "eqasim-data" / "data" / TRANSCRIPTIONS_FOLDER / file,
        *([REPO_ROOT.parents[2] / "eqasim-data" / "data" / TRANSCRIPTIONS_FOLDER / file] if len(REPO_ROOT.parents) > 2 else []),
    ]
    path = next((candidate for candidate in candidates if candidate.is_file()), None)
    if path is None:
        pytest.skip(f"transcription {file} is not available locally (restricted, not in the repository)")
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest


def test_the_galeria_notes_of_the_dataset_name_the_pinned_transcription_and_keep_the_15_eur_decision():
    sys.path.insert(0, str(CURATION_DIR))
    try:
        specs = importlib.import_module("regional_garage_specs")
    finally:
        sys.path.remove(str(CURATION_DIR))
    galeria = next(spec for spec in specs.GARAGE_SPECS if spec["garage_id"] == "gs_galeria")
    for text in (galeria["comment"], galeria["qa_comment"]):
        assert specs.GALERIA_DIRECTORY_FILE in text and specs.GALERIA_DIRECTORY_SHA256 in text
    assert "keeps 15 EUR" in galeria["comment"] and "8 EUR" in galeria["comment"]
    assert galeria["cap"] == "GS_GALERIA_WVV"          # the owner decision of spec E12 (R-4b2-2): the cap stays the 15 EUR rule


@pytest.mark.parametrize("dataset", sorted(PACKAGES))
def test_a_local_package_is_the_pinned_file(dataset):
    """Where the owner's package lies in the main checkout (or this worktree's data), its hash and size are the recorded ones."""
    _, relative_path, module_name, constant = PACKAGES[dataset]
    candidates = [
        REPO_ROOT / "eqasim-data" / "data" / relative_path,
        # a worktree has no gitignored data; the main checkout holds the packages
        *([REPO_ROOT.parents[2] / "eqasim-data" / "data" / relative_path] if len(REPO_ROOT.parents) > 2 else []),
    ]
    path = next((candidate for candidate in candidates if candidate.is_file()), None)
    if path is None:
        pytest.skip(f"package {relative_path} is not available locally (restricted, not in the repository)")
    _assert_file_matches_record(path, _record(dataset), _code_pin(module_name, constant))


def test_a_wrong_hash_in_a_copy_of_a_record_fails_the_pin_check(tmp_path):
    """Mutation check: the pin check is able to fail, so a green run of it says something."""
    dataset = "parking_package_wolfsburg_parkdaten_2026_10_01"
    _assert_record_matches_pin(dataset)   # the committed record passes
    record = _record(dataset)
    pin = _code_pin(PACKAGES[dataset][2], PACKAGES[dataset][3])
    record["storage"]["notes"] = record["storage"]["notes"].replace(pin, "0" * 64)
    with open(tmp_path / f"{dataset}.yml", "w", encoding="utf-8") as handle:
        yaml.safe_dump(record, handle)
    with pytest.raises(AssertionError, match="storage.notes must state the SHA-256"):
        _assert_record_matches_pin(dataset, directory=tmp_path)


def test_a_wrong_hash_or_size_in_a_record_fails_the_file_check(tmp_path):
    """Mutation check of the file check on a synthetic package: the recorded hash and the recorded size both bite."""
    package = tmp_path / "package.zip"
    package.write_bytes(b"synthetic package content")
    digest = hashlib.sha256(package.read_bytes()).hexdigest()
    size = package.stat().st_size
    good = {"storage": {"notes": f"SHA-256 of the zip: {digest} ({size:,} bytes, verified)."}}
    _assert_file_matches_record(package, good, digest)
    wrong_size = {"storage": {"notes": f"SHA-256 of the zip: {digest} ({size + 1:,} bytes, verified)."}}
    with pytest.raises(AssertionError, match="size"):
        _assert_file_matches_record(package, wrong_size, digest)
    with pytest.raises(AssertionError, match="SHA-256"):
        _assert_file_matches_record(package, good, "0" * 64)
    no_size = {"storage": {"notes": f"SHA-256 of the zip: {digest}."}}
    with pytest.raises(AssertionError, match="byte size"):
        _assert_file_matches_record(package, no_size, digest)

