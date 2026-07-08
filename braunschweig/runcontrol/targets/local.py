"""LocalTarget: run on this machine as a detached subprocess.

The run itself is `python <path-to>/local_runner.py <runner> <config>
<log> <exit_marker>` -- a tiny wrapper that executes the configured runner
script, tees output to the log, and writes the exit code to the marker file.
Detachment: CREATE_NEW_PROCESS_GROUP|DETACHED_PROCESS on Windows,
start_new_session on POSIX, so the run survives a GUI restart. Liveness is a
PID check; identity is the manifest written before launch.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from ..models import LaunchHandle, RunManifest, RunSpec
from ..settings import TargetConfig
from .base import ExecutionTarget

_STOP_ESCALATE_SECONDS = 30.0

# local_runner.py has no intra-package imports, so it is invoked by absolute file path
# (not `python -m`): the launch stays independent of the subprocess cwd/sys.path and
# cannot be shadowed by another checkout of this repo on the same machine.
_LOCAL_RUNNER_PATH = Path(__file__).resolve().parent.parent / "local_runner.py"


def _git_commit(repo: Path) -> str:
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=repo,
                             capture_output=True, text=True, errors="replace", timeout=10)
        return out.stdout.strip() or "unknown"
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"


class LocalTarget(ExecutionTarget):
    kind = "local"

    def __init__(self, cfg: TargetConfig, python: str = sys.executable,
                 stop_escalate_seconds: float = _STOP_ESCALATE_SECONDS):
        self.name = cfg.name
        self.cfg = cfg
        self.repo = Path(cfg.repo).resolve()
        self.python = python
        self._stop_escalate = stop_escalate_seconds

    def git_commit(self) -> str:
        return _git_commit(self.repo)

    # -- paths -------------------------------------------------------------
    def _abs(self, relpath: str) -> Path:
        return (self.repo / relpath).resolve()

    # -- lifecycle ---------------------------------------------------------
    def launch(self, spec: RunSpec) -> LaunchHandle:
        logs = self._abs(self.cfg.logs_dir)
        logs.mkdir(parents=True, exist_ok=True)
        log_rel = f"{self.cfg.logs_dir}/rc_{spec.run_id}.log"
        exit_rel = f"{self.cfg.logs_dir}/rc_{spec.run_id}.exit"
        argv = [self.python, str(_LOCAL_RUNNER_PATH),
                self.cfg.runner, spec.config_path, log_rel, exit_rel]
        kwargs: dict = dict(cwd=self.repo, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        else:
            kwargs["start_new_session"] = True
        proc = subprocess.Popen(argv, **kwargs)
        handle = LaunchHandle(run_id=spec.run_id, tmux_session=None, pid=proc.pid,
                              log_path=log_rel, exit_marker_path=exit_rel)
        manifest = RunManifest(run_id=spec.run_id, target=self.name, label=spec.label,
                               config_path=spec.config_path, git_commit=_git_commit(self.repo),
                               started_at_iso=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                               tmux_session=None, pid=proc.pid,
                               log_path=log_rel, exit_marker_path=exit_rel)
        self.write_text(f"{self.cfg.logs_dir}/rc_{spec.run_id}.manifest.json", manifest.to_json())
        return handle

    def is_alive(self, handle: LaunchHandle) -> bool:
        if handle.pid is None:
            return False
        if os.name == "nt":
            # errors="replace": tasklist emits OEM-codepage bytes that crash strict locale decoding
            out = subprocess.run(["tasklist", "/FI", f"PID eq {handle.pid}"],
                                 capture_output=True, text=True, errors="replace")
            return str(handle.pid) in out.stdout
        try:
            os.kill(handle.pid, 0)
            return True
        except OSError:
            return False

    def exit_code(self, handle: LaunchHandle) -> int | None:
        if not self.exists(handle.exit_marker_path):
            return None
        text = self.read_text(handle.exit_marker_path).strip()
        return int(text) if text else None

    def stop(self, handle: LaunchHandle) -> None:
        if handle.pid is None or not self.is_alive(handle):
            return
        if os.name == "nt":
            # CTRL events are unreliable for detached groups; taskkill the run's OWN tree by PID.
            subprocess.run(["taskkill", "/PID", str(handle.pid), "/T"], capture_output=True)
        else:
            os.killpg(handle.pid, signal.SIGINT)
        deadline = time.time() + self._stop_escalate
        while self.is_alive(handle) and time.time() < deadline:
            time.sleep(0.2)
        if self.is_alive(handle):
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(handle.pid), "/T", "/F"], capture_output=True)
            else:
                os.killpg(handle.pid, signal.SIGKILL)

    # -- filesystem ---------------------------------------------------------
    def read_text(self, relpath: str, tail_bytes: int | None = None) -> str:
        p = self._abs(relpath)
        with open(p, "rb") as f:
            if tail_bytes is not None:
                f.seek(0, os.SEEK_END)
                f.seek(max(0, f.tell() - tail_bytes))
            return f.read().decode("utf-8", errors="replace")

    def exists(self, relpath: str) -> bool:
        return self._abs(relpath).exists()

    def listdir(self, relpath: str) -> list[dict]:
        p = self._abs(relpath)
        if not p.is_dir():
            return []
        out = []
        for c in p.iterdir():
            st = c.stat()
            out.append({"name": c.name, "size": st.st_size, "mtime": st.st_mtime})
        return out

    def write_text(self, relpath: str, content: str) -> None:
        p = self._abs(relpath)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")

    def manifest_glob(self) -> list[str]:
        logs = self._abs(self.cfg.logs_dir)
        if not logs.is_dir():
            return []
        return [f"{self.cfg.logs_dir}/{p.name}" for p in sorted(logs.glob("rc_*.manifest.json"))]
