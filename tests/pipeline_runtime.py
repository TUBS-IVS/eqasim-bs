"""Portable external-tool settings for opt-in pipeline integration tests."""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any


_TOOL_CONFIG_KEYS = {
    "java_binary": "java",
    "maven_binary": "mvn",
    "osmosis_binary": "osmosis",
    "osmconvert_binary": "osmconvert",
}


def configure_pipeline_runtime(config: dict[str, Any]) -> None:
    """Replace fixture-local binary paths with required tools found on ``PATH``."""
    resolved_tools: dict[str, Path] = {}
    missing_tools: list[str] = []
    for config_key, executable in _TOOL_CONFIG_KEYS.items():
        resolved = shutil.which(executable)
        if resolved is None:
            missing_tools.append(executable)
        else:
            resolved_tools[config_key] = Path(resolved).resolve()

    if missing_tools:
        raise RuntimeError(
            "Pipeline integration tests require these executables on PATH: "
            + ", ".join(missing_tools)
        )

    for config_key, executable in resolved_tools.items():
        config[config_key] = str(executable)
    config["java_home"] = str(resolved_tools["java_binary"].parent.parent)
