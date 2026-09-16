"""Tests for portable pipeline integration-test runtime settings."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.pipeline_runtime import configure_pipeline_runtime


def _make_tool(directory: Path, name: str) -> Path:
    suffix = ".exe" if os.name == "nt" else ""
    path = directory / f"{name}{suffix}"
    path.write_text("placeholder", encoding="utf-8")
    if os.name != "nt":
        path.chmod(0o755)
    return path


def test_configure_pipeline_runtime_replaces_fixture_specific_tool_paths(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    tools = {name: _make_tool(tmp_path, name) for name in ("java", "mvn", "osmosis", "osmconvert")}
    monkeypatch.setenv("PATH", str(tmp_path))
    config = {
        "java_binary": "C:/Users/example/jdk/bin/java.exe",
        "java_home": "C:/Users/example/jdk",
        "maven_binary": "C:/Users/example/mvn.cmd",
        "osmosis_binary": "C:/Users/example/osmosis.bat",
        "osmconvert_binary": "C:/Users/example/osmconvert.exe",
        "sampling_rate": 0.001,
    }

    configure_pipeline_runtime(config)

    assert config["java_binary"] == str(tools["java"].resolve())
    assert config["java_home"] == str(tools["java"].resolve().parent.parent)
    assert config["maven_binary"] == str(tools["mvn"].resolve())
    assert config["osmosis_binary"] == str(tools["osmosis"].resolve())
    assert config["osmconvert_binary"] == str(tools["osmconvert"].resolve())
    assert config["sampling_rate"] == 0.001


def test_configure_pipeline_runtime_lists_missing_path_tools(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    for name in ("java", "mvn", "osmosis"):
        _make_tool(tmp_path, name)
    monkeypatch.setenv("PATH", str(tmp_path))

    with pytest.raises(RuntimeError, match="osmconvert"):
        configure_pipeline_runtime({})
