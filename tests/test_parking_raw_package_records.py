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
import os
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


def _record(dataset: str) -> dict:
    with open(DATA_DIRECTORY / f"{dataset}.yml", encoding="utf-8") as handle:
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


@pytest.mark.parametrize("dataset", sorted(PACKAGES))
def test_the_record_pins_the_sha256_that_the_curation_code_checks(dataset):
    _, _, module_name, constant = PACKAGES[dataset]
    pin = _code_pin(module_name, constant)
    assert len(pin) == 64
    record = _record(dataset)
    assert pin in record["storage"]["notes"], (
        f"{dataset}: storage.notes must state the SHA-256 {pin} that {module_name}.{constant} verifies")
    assert record["storage"]["expected_path"] == "eqasim-data/data/" + PACKAGES[dataset][1]


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
    entries = _preflight()
    assert [entries[prefix].name.split()[0] for prefix in ("D11", "D12", "D13", "D14")] == ["D11", "D12", "D13", "D14"]
    names = list(entries)
    assert names.index("D11") < names.index("D12") < names.index("D13") < names.index("D14")


@pytest.mark.parametrize("dataset", sorted(PACKAGES))
def test_a_local_package_is_the_pinned_file(dataset):
    """Where the owner's package lies in the main checkout (or this worktree's data), its bytes are the recorded ones."""
    _, relative_path, module_name, constant = PACKAGES[dataset]
    candidates = [
        REPO_ROOT / "eqasim-data" / "data" / relative_path,
        # a worktree has no gitignored data; the main checkout holds the packages
        *([REPO_ROOT.parents[2] / "eqasim-data" / "data" / relative_path] if len(REPO_ROOT.parents) > 2 else []),
    ]
    path = next((candidate for candidate in candidates if candidate.is_file()), None)
    if path is None:
        pytest.skip(f"package {relative_path} is not available locally (restricted, not in the repository)")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    assert digest == _code_pin(module_name, constant)
