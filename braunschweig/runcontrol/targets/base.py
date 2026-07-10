"""ExecutionTarget: the single seam between runcontrol and an execution host.

Everything that touches a host (launching, stopping, reading logs, listing
run directories, preflight commands) goes through this interface, so the
daemon, the collectors and the web app are identical for local and ssh runs.
Paths are ALWAYS relative to the target's repo root (posix separators).
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import LaunchHandle, RunSpec


class ExecutionTarget(ABC):
    name: str
    kind: str

    @abstractmethod
    def launch(self, spec: RunSpec) -> LaunchHandle: ...

    @abstractmethod
    def is_alive(self, handle: LaunchHandle) -> bool: ...

    @abstractmethod
    def exit_code(self, handle: LaunchHandle) -> int | None:
        """Exit code from the run's exit-marker file; None while missing (= still running or lost)."""

    @abstractmethod
    def stop(self, handle: LaunchHandle) -> None:
        """Interrupt the run (SIGINT / C-c to ITS OWN group/session only), escalate to kill after a timeout."""

    @abstractmethod
    def read_text(self, relpath: str, tail_bytes: int | None = None) -> str: ...

    @abstractmethod
    def exists(self, relpath: str) -> bool: ...

    @abstractmethod
    def listdir(self, relpath: str) -> list[dict]:
        """Entries as {'name': str, 'size': int, 'mtime': float}; [] for missing dirs."""

    @abstractmethod
    def write_text(self, relpath: str, content: str) -> None: ...

    @abstractmethod
    def manifest_glob(self) -> list[str]:
        """Relative paths of all rc_*.manifest.json under the target's logs dir."""

    @abstractmethod
    def git_commit(self) -> str:
        """Short git commit of the target's repo checkout; 'unknown' when undeterminable."""

    @abstractmethod
    def newest_files(self, reldir: str, maxdepth: int = 4, limit: int = 200) -> list[tuple[float, str]]:
        """Newest files under reldir as (mtime_epoch, relpath) sorted desc, <= limit.

        relpath is relative to reldir with forward slashes. Read-only; a missing
        or empty dir yields []. Used by auto-detection (newest activity across a
        target) and depth-aware liveness (newest descendant of a watch dir)."""
