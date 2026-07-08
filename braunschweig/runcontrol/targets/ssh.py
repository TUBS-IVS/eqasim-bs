"""SshTarget: run on a remote host via the user's ssh alias + tmux.

The server needs NO new service: `ssh <alias> tmux new-session -d ...
scripts/run_pipeline.sh <config> <log>; echo $? > <marker>` is the launch,
`tmux has-session` the liveness check, `cat`/`tail -c` the file reads. The
per-run manifest is written on the server so state survives laptop offline.
`run_command` is injectable so unit tests never open a real connection.
"""
from __future__ import annotations

import shlex
import subprocess
import time
from datetime import datetime, timezone
from typing import Callable

from ..models import LaunchHandle, RunManifest, RunSpec
from ..settings import TargetConfig
from .base import ExecutionTarget

_STOP_ESCALATE_SECONDS = 30.0
RunCommand = Callable[[list[str]], tuple[int, str]]


def _default_run_command(argv: list[str]) -> tuple[int, str]:
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=60)
    return proc.returncode, proc.stdout if proc.returncode == 0 else proc.stdout + proc.stderr


class SshTarget(ExecutionTarget):
    kind = "ssh"

    def __init__(self, cfg: TargetConfig, run_command: RunCommand | None = None):
        self.name = cfg.name
        self.cfg = cfg
        self.repo = cfg.repo
        self._run = run_command or _default_run_command

    def read_text_command(self, remote_cmd: str) -> str:
        """Run a read-only remote command and return stdout (used for df etc.)."""
        rc, out = self._ssh(remote_cmd)
        if rc != 0:
            raise RuntimeError(f"remote command failed on '{self.cfg.host}' (rc={rc}): {out.strip()}")
        return out

    def _ssh(self, remote_cmd: str) -> tuple[int, str]:
        return self._run(["ssh", self.cfg.host, f"cd {self.cfg.repo} && {remote_cmd}"])

    # -- lifecycle ---------------------------------------------------------
    def launch(self, spec: RunSpec) -> LaunchHandle:
        session = f"rc_{spec.run_id}"
        log_rel = f"{self.cfg.logs_dir}/rc_{spec.run_id}.log"
        exit_rel = f"{self.cfg.logs_dir}/rc_{spec.run_id}.exit"
        inner = (f"bash {self.cfg.runner} {shlex.quote(spec.config_path)} {shlex.quote(log_rel)}; "
                 f"echo $? > {shlex.quote(exit_rel)}")
        rc, out = self._ssh(f"mkdir -p {self.cfg.logs_dir} && tmux new-session -d -s {session} {shlex.quote(inner)}")
        if rc != 0:
            raise RuntimeError(f"ssh launch of {spec.run_id} on '{self.cfg.host}' failed (rc={rc}): {out.strip()}")
        manifest = RunManifest(run_id=spec.run_id, target=self.name, label=spec.label,
                               config_path=spec.config_path, git_commit=self.git_commit(),
                               started_at_iso=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                               tmux_session=session, pid=None,
                               log_path=log_rel, exit_marker_path=exit_rel)
        self.write_text(f"{self.cfg.logs_dir}/rc_{spec.run_id}.manifest.json", manifest.to_json())
        return LaunchHandle(run_id=spec.run_id, tmux_session=session, pid=None,
                            log_path=log_rel, exit_marker_path=exit_rel)

    def git_commit(self) -> str:
        rc, out = self._ssh("git rev-parse --short HEAD")
        return out.strip() if rc == 0 and out.strip() else "unknown"

    def is_alive(self, handle: LaunchHandle) -> bool:
        rc, _ = self._ssh(f"tmux has-session -t {handle.tmux_session} 2>/dev/null")
        return rc == 0

    def exit_code(self, handle: LaunchHandle) -> int | None:
        rc, out = self._ssh(f"cat {shlex.quote(handle.exit_marker_path)}")
        if rc != 0 or not out.strip():
            return None
        return int(out.strip())

    def stop(self, handle: LaunchHandle) -> None:
        # Interrupt only the run's own tmux session; never kill by image name.
        self._ssh(f"tmux send-keys -t {handle.tmux_session} C-c")
        deadline = time.time() + _STOP_ESCALATE_SECONDS
        while self.is_alive(handle) and time.time() < deadline:
            time.sleep(1.0)
        if self.is_alive(handle):
            self._ssh(f"tmux kill-session -t {handle.tmux_session}")

    # -- filesystem ---------------------------------------------------------
    def read_text(self, relpath: str, tail_bytes: int | None = None) -> str:
        q = shlex.quote(relpath)
        cmd = f"tail -c {int(tail_bytes)} {q}" if tail_bytes is not None else f"cat {q}"
        rc, out = self._ssh(cmd)
        if rc != 0:
            raise FileNotFoundError(f"{self.name}:{relpath} (rc={rc}): {out.strip()}")
        return out

    def exists(self, relpath: str) -> bool:
        rc, _ = self._ssh(f"test -e {shlex.quote(relpath)}")
        return rc == 0

    def listdir(self, relpath: str) -> list[dict]:
        # One stat call per directory: "size mtime path" per line; basename applied below.
        rc, out = self._ssh(f"stat -c '%s %Y %n' {shlex.quote(relpath)}/* 2>/dev/null || true")
        entries = []
        for line in out.splitlines():
            parts = line.strip().split(" ", 2)
            if len(parts) != 3:
                continue
            size, mtime, name = parts
            entries.append({"name": name.rsplit("/", 1)[-1], "size": int(size), "mtime": float(mtime)})
        return entries

    def write_text(self, relpath: str, content: str) -> None:
        q = shlex.quote(relpath)
        heredoc = f"mkdir -p $(dirname {q}) && cat > {q} <<'RC_EOF'\n{content}\nRC_EOF"
        rc, out = self._ssh(heredoc)
        if rc != 0:
            raise RuntimeError(f"write_text {self.name}:{relpath} failed (rc={rc}): {out.strip()}")

    def manifest_glob(self) -> list[str]:
        rc, out = self._ssh(f"ls {self.cfg.logs_dir}/rc_*.manifest.json 2>/dev/null || true")
        return [line.strip() for line in out.splitlines() if line.strip()]
