"""Unit tests for braunschweig.config_compose (base + overlay deep merge).

Merge contract: nested mappings merge recursively (overlay wins per key);
scalars and LISTS are replaced wholesale; base-only keys are kept; overlay-only
keys are added. Every override/addition is logged (no silent merges).
"""
import logging
from pathlib import Path

import pytest
import yaml

from braunschweig import config_compose


def test_deep_merge_nested_override_and_keep():
    base = {"config": {"a": 1, "b": {"x": 1, "y": 2}, "keep": "base"}}
    overlay = {"config": {"a": 2, "b": {"x": 9}}}
    merged, changes = config_compose.deep_merge(base, overlay)
    assert merged["config"]["a"] == 2
    assert merged["config"]["b"] == {"x": 9, "y": 2}
    assert merged["config"]["keep"] == "base"
    dotted = {c[0] for c in changes}
    assert dotted == {"config.a", "config.b.x"}


def test_deep_merge_lists_and_scalars_replaced_wholesale():
    base = {"run": ["a", "b"], "working_directory": "wd-base"}
    overlay = {"run": ["c"], "working_directory": "wd-overlay"}
    merged, _ = config_compose.deep_merge(base, overlay)
    assert merged["run"] == ["c"]                       # replaced, NOT concatenated
    assert merged["working_directory"] == "wd-overlay"


def test_deep_merge_overlay_only_key_added():
    merged, changes = config_compose.deep_merge({"config": {}}, {"config": {"new": 5}})
    assert merged["config"]["new"] == 5
    assert changes == [("config.new", None, 5, "added")]


def test_deep_merge_identical_value_not_reported():
    _, changes = config_compose.deep_merge({"config": {"a": 1}}, {"config": {"a": 1}})
    assert changes == []


def test_deep_merge_does_not_mutate_inputs():
    base = {"config": {"b": {"x": 1}}}
    overlay = {"config": {"b": {"x": 2}}}
    config_compose.deep_merge(base, overlay)
    assert base["config"]["b"]["x"] == 1


def _write(p: Path, doc) -> Path:
    p.write_text(yaml.safe_dump(doc), encoding="utf-8")
    return p


def test_compose_logs_every_override(tmp_path, caplog):
    base = _write(tmp_path / "base.yml", {
        "config": {"sampling_rate": 1.0, "feature": True}})
    overlay = _write(tmp_path / "overlay.yml", {
        "working_directory": "wd", "run": ["stage.a"],
        "config": {"sampling_rate": 0.01}})
    with caplog.at_level(logging.INFO, logger="braunschweig"):
        merged = config_compose.compose(str(base), str(overlay))
    assert merged["config"]["sampling_rate"] == 0.01
    assert merged["config"]["feature"] is True
    text = caplog.text
    assert "[config-merge] config.sampling_rate: 1.0 -> 0.01" in text
    assert "[config-merge] working_directory: (added) wd" in text
    assert "[config-merge]" in text and "overridden" in text  # summary line


def test_compose_fails_early_without_working_directory_or_run(tmp_path):
    base = _write(tmp_path / "base.yml", {"config": {"a": 1}})
    overlay = _write(tmp_path / "overlay.yml", {"config": {"a": 2}})
    with pytest.raises(ValueError, match="working_directory"):
        config_compose.compose(str(base), str(overlay))


def test_compose_missing_file_fails_early(tmp_path):
    base = _write(tmp_path / "base.yml", {"config": {}})
    with pytest.raises(FileNotFoundError):
        config_compose.compose(str(base), str(tmp_path / "absent.yml"))


def test_write_merged_roundtrip(tmp_path):
    merged = {"working_directory": str(tmp_path / "wd"), "run": ["s"],
              "config": {"k": 1}}
    path = config_compose.write_merged(merged, merged["working_directory"])
    assert Path(path).name == ".merged_config.yml"
    with open(path, encoding="utf-8") as f:
        assert yaml.safe_load(f) == merged
