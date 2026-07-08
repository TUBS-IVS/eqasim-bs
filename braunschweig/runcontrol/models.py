"""Shared value objects for runcontrol (no behavior, no I/O)."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from enum import Enum


class RunStatus(str, Enum):
    QUEUED = "queued"
    LAUNCHING = "launching"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    STOPPED = "stopped"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class RunSpec:
    run_id: str
    target: str
    label: str
    config_path: str      # relative to the target's repo root


@dataclass(frozen=True)
class LaunchHandle:
    run_id: str
    tmux_session: str | None      # ssh targets
    pid: int | None               # local targets
    log_path: str                 # relative to target repo root
    exit_marker_path: str         # file containing the runner's exit code


@dataclass(frozen=True)
class RunManifest:
    """Durable per-run truth, written next to the log ON THE EXECUTION HOST.

    Reconciliation after daemon/app restarts reads these files; the SQLite DB
    is a cache/index, never the only copy.
    """
    run_id: str
    target: str
    label: str
    config_path: str
    git_commit: str               # "unknown" when not determinable
    started_at_iso: str
    tmux_session: str | None
    pid: int | None
    log_path: str
    exit_marker_path: str

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True)

    @classmethod
    def from_json(cls, text: str) -> "RunManifest":
        return cls(**json.loads(text))
