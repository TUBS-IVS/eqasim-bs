"""Load runcontrol.toml into typed settings.

The TOML file declares the web bind address, the SQLite path, and one
[target.<name>] table per execution host. Fails early (FileNotFoundError /
ValueError) on missing files or inconsistent target declarations -- a GUI
that silently mis-targets a 100% run is not acceptable.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import tomli

_DEFAULT_RUNNERS = {"local": "scripts/run_synpp.py", "ssh": "scripts/run_pipeline.sh"}
_VALID_KINDS = ("local", "ssh")


@dataclass(frozen=True)
class TargetConfig:
    name: str
    kind: str                    # "local" | "ssh"
    repo: str                    # repo root on that host
    host: str | None = None      # ssh alias, required for kind == "ssh"
    runner: str = ""             # launch script, relative to repo
    data_dir: str = "eqasim-data"
    logs_dir: str = "logs"


@dataclass(frozen=True)
class Settings:
    db_path: Path
    host: str = "127.0.0.1"
    port: int = 8099
    poll_seconds: float = 3.0
    targets: dict[str, TargetConfig] = field(default_factory=dict)
    # Dynamic ssh targets added at runtime through the web UI (Task 14) are persisted here,
    # separately from the config-file targets above, which stay immutable seeds.
    targets_store_path: Path = field(default_factory=lambda: Path("runcontrol_data/targets.json"))


def load_settings(path: Path) -> Settings:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"runcontrol.toml not found at {path} -- copy the repo-root example and adjust paths")
    with open(path, "rb") as f:
        raw = tomli.load(f)

    targets: dict[str, TargetConfig] = {}
    for name, t in raw.get("target", {}).items():
        kind = t.get("kind", "")
        if kind not in _VALID_KINDS:
            raise ValueError(f"target '{name}': kind must be one of {_VALID_KINDS}, got '{kind}'")
        if kind == "ssh" and not t.get("host"):
            raise ValueError(f"target '{name}': kind 'ssh' requires a 'host' (ssh alias, e.g. 'felix')")
        if not t.get("repo"):
            raise ValueError(f"target '{name}': 'repo' (repository root on that host) is required")
        targets[name] = TargetConfig(
            name=name,
            kind=kind,
            repo=t["repo"],
            host=t.get("host"),
            runner=t.get("runner", _DEFAULT_RUNNERS[kind]),
            data_dir=t.get("data_dir", "eqasim-data"),
            logs_dir=t.get("logs_dir", "logs"),
        )
    if not targets:
        raise ValueError(f"{path}: at least one [target.<name>] table is required")

    return Settings(
        db_path=Path(raw.get("db_path", "runcontrol_data/runs.db")),
        host=raw.get("host", "127.0.0.1"),
        port=int(raw.get("port", 8099)),
        poll_seconds=float(raw.get("poll_seconds", 3.0)),
        targets=targets,
        targets_store_path=Path(raw.get("targets_store_path", "runcontrol_data/targets.json")),
    )
