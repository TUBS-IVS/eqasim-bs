"""The owner packages behind the parking v3 data (spec v3 B6, issue #436) are registered raw sources: the three research
packages and, per delivery folder, the earlier deliveries of 2026-10-01, -07, -08 and -13 (``FOLDER_RECORDS``).

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


def _code_pin(module_name: str, constant: str, key: str | None = None) -> str:
    sys.path.insert(0, str(CURATION_DIR))
    try:
        value = getattr(importlib.import_module(module_name), constant)
    finally:
        sys.path.remove(str(CURATION_DIR))
    if isinstance(value, dict):
        value = value[key or "Wolfsburg_Parkdaten"]
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


# ---- the earlier owner deliveries of 2026-10-01, -07, -08 and -13 (one record per delivery folder, Part C final fix)
#: record id -> (preflight entry, folder below raw_sources, the file the preflight entry names, {file below the folder:
#: (SHA-256 that the record states, size in bytes, code pin (module, constant, key of a dict constant or None) or None)})
FOLDER_RECORDS = {
    "parking_packages_municipal_2026_10_01": (
        "D16", "municipal_2026-10-01", "Braunschweig_Parkzonen.zip", {
            "Braunschweig_Parkzonen.zip": (
                "b47c463296995b035afc9894fe58dc57abdaffa14fa9709b2de1bfd144cd5abe", 17434642,
                ('municipal_zones', 'PACKAGE_SHA256', 'Braunschweig_Parkzonen')),
            "Braunschweig_Parkhaeuser.zip": (
                "6e2c19d102eb9984f4ff8d857a3701476816820ecffebd775c37374eccb8513b", 17965,
                None),
            "Goslar_Parkdaten.zip": (
                "aa1bc980ae605cbe5fc2f533a2a991b4ba32c0913dc9b27090cf9d67ddb5b9d9", 438525,
                ('municipal_zones', 'PACKAGE_SHA256', 'Goslar_Parkdaten')),
        }),
    "parking_packages_municipal_2026_10_07": (
        "D17", "municipal_2026-10-07", "Regional_Parkdaten_Belege_2026-10-07.zip", {
            "Regional_Parkdaten_Belege_2026-10-07.zip": (
                "e789623752bf508b3e31f37ed2e30fe019e171cb43274f495cfacc12924008e7", 62237402,
                ('regional_zones', 'PACKAGE_SHA256', None)),
            "Parkhaus_Ergaenzungen_2026-10-07.zip": (
                "76d2651433e05a4d0d0a75ba352fd17f99b329555eb3bb4525c34990383978fa", 7775796,
                ('garage_supplement', 'SUPPLEMENT_SHA256', None)),
            "Parkhaus_Nachrecherche_2026-10-07.zip": (
                "3bbaff93fb8b26d6cdfb187c7e0bf746f099997c1c7fd04a961fcae7d37329d8", 2712028,
                ('garage_supplement', 'FOLLOWUP_SHA256', None)),
            "Wolfsburg_Parkplaetze_Pruefung_2026-10-07.zip": (
                "650508f87c80ee2b84063def301ff67b5ecb8f2653cef2ab4202fb398c818df0", 9634890,
                ('regional_garages', 'SUPERSEDED_WOLFSBURG_LOTS_SHA256', None)),
            "Braunschweig_Parkgebuehrenzonen_2023.zip": (
                "7ce77084a61fa79b42b3321fb6b8f0660c8cb85260827be2ee665c211192535e", 26473044,
                None),
            "Goslar_Zone2_Gesamtpaket.zip": (
                "61f2368ecf5d41a67adc49ff3d5344c6ad4b963063ce44694a1ad099b7a87bda", 390047,
                None),
            "TU_Braunschweig_Parkbereiche.zip": (
                "12afddef2b6c1bc583b5542537aa8e99eccdc465958fcfdf10e11f61c2e83f65", 4810416,
                None),
            "Parken_BadHarzburg_Seesen_Koenigslutter_Schoeningen.zip": (
                "32d4fd9b71d07ee6b6bf874d2d71386b98d40db6f7c7bb40246eb5ec049776e9", 1859183,
                None),
            "MANIFEST.md": (
                "02aabfd3e3cbd3fd9ccc01d47a19f6a1e0e7cd181b67c37347680920cb1ce5cd", 14385,
                None),
        }),
    "parking_packages_municipal_2026_10_08": (
        "D18", "municipal_2026-10-08", "Braunschweig_Monatstarife_2026-10-08.zip", {
            "Braunschweig_Monatstarife_2026-10-08.zip": (
                "72fec30d4515a3f7468af66222802697b09ae042b283adc335b5ae3607650c81", 3183430,
                ('bs_monthly_products', 'PACKAGE_SHA256', None)),
            "contipark_configurator_2026-10-08/SHA256SUMS": (
                "2617d497bddfaadced2ff433cd4b675122570a58ca7b6b2b49c27b0514a76ceb", 193,
                ('bs_monthly_products', 'EVIDENCE_SUMS_SHA256', None)),
            "contipark_configurator_2026-10-08/wallstrasse_configurator.txt": (
                "af0d4e3f77795e035e9c250c4ef820d0cc0ae204da4d5316713da4463655b9c0", 1139,
                None),
            "contipark_configurator_2026-10-08/eiermarkt_no_rental_product.txt": (
                "0a7d31ae502822897971675af54cdb7511cc2a2d20f4427f19b2daa4c411fa87", 1061,
                None),
            "osm_braunschweig_hbf_station_lots_geometry_2026-10-08.json": (
                "fe4332938fcc7e99595aff167071a040fcea01b5be5df38e259647e64a7ecc26", 4915,
                ('station_lots', 'OSM_SHA256', None)),
            "osm_braunschweig_hbf_parking_2026-10-08.json": (
                "d4ec9d2ad3224fae6bdb84119c7803815b6cc07c1b0c01fd2d54539057a4f876", 10437,
                None),
            "MANIFEST.md": (
                "4f136249acaa26c68649a23c94b3697c1b95628f52549df2d060999b77f04ad2", 2869,
                None),
        }),
    "parking_packages_municipal_2026_10_13": (
        "D19", "municipal_2026-10-13", "Grossparkplaetze_ZGB8_2026-10-12.zip", {
            "Grossparkplaetze_ZGB8_2026-10-12.zip": (
                "d467275ced5b68717ff2f426ad94be4debd085a9bebfaa239cfa6f4518fc9db4", 38210859,
                ('large_lots', 'LOTS_SHA256', None)),
            "wob_free_lots_evidence/MANIFEST.md": (
                "0c702d30dcc289f5649af855df11892c49e24db37d2d0752d720c00c669bf9c2", 3412,
                None),
        }),
}


def _normalised_storage_notes(dataset: str, directory: Path = DATA_DIRECTORY) -> str:
    return " ".join(str(_record(dataset, directory)["storage"]["notes"]).split())


def _assert_folder_record_states_members(dataset: str, directory: Path = DATA_DIRECTORY) -> None:
    """The record names the expected folder and states every member with its SHA-256 and its byte size."""
    _, folder, _, members = FOLDER_RECORDS[dataset]
    assert _record(dataset, directory)["storage"]["expected_path"] == f"eqasim-data/data/{RAW_SOURCES}/{folder}/"
    notes = _normalised_storage_notes(dataset, directory)
    for name, (digest, size, _) in members.items():
        assert name in notes, f"{dataset}: storage.notes must name {name}"
        assert digest in notes, f"{dataset}: storage.notes must state the SHA-256 {digest} of {name}"
        assert f"{size:,} bytes" in notes, f"{dataset}: storage.notes must state the size {size:,} bytes of {name}"


@pytest.mark.parametrize("dataset", sorted(FOLDER_RECORDS))
def test_a_folder_record_states_every_member_with_its_hash_and_size(dataset):
    _assert_folder_record_states_members(dataset)


@pytest.mark.parametrize("dataset, name", sorted((dataset, name) for dataset, spec in FOLDER_RECORDS.items()
                                                 for name, (_, _, pin) in spec[3].items() if pin is not None))
def test_the_code_pin_of_a_folder_member_is_the_hash_of_the_record(dataset, name):
    digest, _, (module_name, constant, key) = FOLDER_RECORDS[dataset][3][name]
    assert len(digest) == 64
    assert _code_pin(module_name, constant, key) == digest


@pytest.mark.parametrize("dataset", sorted(FOLDER_RECORDS))
def test_a_folder_record_is_a_restricted_local_only_raw_source(dataset):
    record = _record(dataset)
    assert record["acquisition"]["method"] == "restricted_delivery"
    assert record["storage"]["committed"] is False and record["storage"]["local_only"] is True
    assert record["licensing"]["restricted"] is True and record["licensing"]["redistribution"] == "restricted"
    assert record["requirements"] == {"synthesis": "not_needed", "matsim": "not_needed", "production": "not_needed"}
    assert record["verification"]["verifier_entry"] == FOLDER_RECORDS[dataset][0]
    assert not record.get("used_by")


@pytest.mark.parametrize("dataset", sorted(FOLDER_RECORDS))
def test_the_preflight_lists_a_folder_record_as_an_optional_restricted_curation_input(dataset):
    prefix, folder, preflight_file, _ = FOLDER_RECORDS[dataset]
    entry = _preflight()[prefix]
    assert entry.rel_path == f"{RAW_SOURCES}/{folder}/{preflight_file}"
    assert entry.optional and entry.restricted and entry.matsim_only
    assert dataset in entry.source and "available on request" in entry.source
    assert "curation" in entry.notes.lower()


def test_the_preflight_entries_of_the_folder_records_follow_the_transcription_entry():
    names = list(_preflight())
    assert names.index("D15") < names.index("D16") < names.index("D17") < names.index("D18") < names.index("D19")


@pytest.mark.parametrize("dataset, name", sorted((dataset, name) for dataset, spec in FOLDER_RECORDS.items() for name in spec[3]))
def test_a_local_folder_member_is_the_recorded_file(dataset, name):
    """Where the member lies in the main checkout (or this worktree's data), its hash and size are the recorded ones."""
    _, folder, _, members = FOLDER_RECORDS[dataset]
    digest, size, _ = members[name]
    relative = f"{RAW_SOURCES}/{folder}/{name}"
    candidates = [
        REPO_ROOT / "eqasim-data" / "data" / relative,
        *([REPO_ROOT.parents[2] / "eqasim-data" / "data" / relative] if len(REPO_ROOT.parents) > 2 else []),
    ]
    path = next((candidate for candidate in candidates if candidate.is_file()), None)
    if path is None:
        pytest.skip(f"{relative} is not available locally (restricted, not in the repository)")
    content = path.read_bytes()
    assert hashlib.sha256(content).hexdigest() == digest, f"{path}: SHA-256 differs from the record"
    assert len(content) == size, f"{path}: size {len(content)} bytes, the record states {size}"


def test_a_wrong_member_hash_in_a_copy_of_a_folder_record_fails_the_check(tmp_path):
    """Mutation check: the member check is able to fail, so a green run of it says something."""
    dataset = "parking_packages_municipal_2026_10_13"
    _assert_folder_record_states_members(dataset)   # the committed record passes
    record = _record(dataset)
    digest = FOLDER_RECORDS[dataset][3]["Grossparkplaetze_ZGB8_2026-10-12.zip"][0]
    record["storage"]["notes"] = record["storage"]["notes"].replace(digest, "0" * 64)
    with open(tmp_path / f"{dataset}.yml", "w", encoding="utf-8") as handle:
        yaml.safe_dump(record, handle)
    with pytest.raises(AssertionError, match="must state the SHA-256"):
        _assert_folder_record_states_members(dataset, directory=tmp_path)
