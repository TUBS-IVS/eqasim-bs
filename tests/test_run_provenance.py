"""Issue #125: crash-proof launch-time run provenance."""
from __future__ import annotations

import json
import logging
from pathlib import Path

import yaml

from braunschweig import provenance


def _write_config(tmp_path: Path, working_directory: str | None) -> Path:
    doc = {
        "config": {
            "sampling_rate": 0.25,
            "hts": "entd",
            "random_seed": 1234,
            "braunschweig.population.method": "popsim_mid",
        },
    }
    if working_directory is not None:
        doc["working_directory"] = working_directory
    path = tmp_path / "config_test.yml"
    path.write_text(yaml.safe_dump(doc), encoding="utf-8")
    return path


def test_collect_records_config_keys_and_pipeline_commit(tmp_path) -> None:
    config = _write_config(tmp_path, str(tmp_path / "cache"))
    record = provenance.collect_run_provenance(str(config))
    assert record["sampling_rate"] == 0.25
    assert record["hts"] == "entd"
    assert record["random_seed"] == 1234
    assert record["braunschweig.population.method"] == "popsim_mid"
    # This repo IS a git repository, so the pipeline commit must resolve.
    assert record["pipeline_commit"] != "unknown"
    assert record["config_path"].endswith("config_test.yml")


def test_git_commit_unknown_for_non_repo_is_warned(tmp_path, caplog) -> None:
    with caplog.at_level(logging.WARNING):
        got = provenance.git_commit(str(tmp_path))
    assert got == "unknown"
    assert any("provenance" in r.message for r in caplog.records)


def test_log_and_write_persists_record_into_working_directory(tmp_path) -> None:
    workdir = tmp_path / "cache"
    config = _write_config(tmp_path, str(workdir))
    record = provenance.log_and_write_run_provenance(str(config))
    files = list(workdir.glob("run_provenance_*.json"))
    assert len(files) == 1
    on_disk = json.loads(files[0].read_text(encoding="utf-8"))
    assert on_disk["sampling_rate"] == record["sampling_rate"] == 0.25
    assert on_disk["pipeline_commit"] == record["pipeline_commit"]


def test_missing_working_directory_downgrades_to_log_only(tmp_path, caplog) -> None:
    config = _write_config(tmp_path, working_directory=None)
    with caplog.at_level(logging.WARNING):
        record = provenance.log_and_write_run_provenance(str(config))
    assert record["working_directory"] is None
    assert any("logged only" in r.message for r in caplog.records)


def test_unreadable_config_never_raises(tmp_path) -> None:
    record = provenance.collect_run_provenance(str(tmp_path / "nope.yml"))
    assert "error" in record
    assert record["pipeline_commit"] != ""
