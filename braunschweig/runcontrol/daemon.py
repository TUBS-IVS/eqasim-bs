"""Queue worker: reconcile run states against reality, advance the queue.

State machine per run: queued -> launching -> running -> done|failed|stopped,
plus 'unknown' when a process disappeared without an exit marker (surfaced as
a warning event -- never guessed into done/failed). One run executes at a
time per QueueWorker (matches the single-box 64c/128G reality); the queue
advances only while this process lives -- documented V1 limitation."""
from __future__ import annotations

import logging
import time

from .db import Database
from .models import LaunchHandle, RunSpec, RunStatus
from .targets.base import ExecutionTarget

logger = logging.getLogger(__name__)

_ACTIVE = (RunStatus.LAUNCHING.value, RunStatus.RUNNING.value)


def _handle_from_row(row: dict) -> LaunchHandle:
    return LaunchHandle(run_id=row["run_id"], tmux_session=row["tmux_session"], pid=row["pid"],
                        log_path=row["log_path"] or "", exit_marker_path=row["exit_marker_path"] or "")


class QueueWorker:
    def __init__(self, db: Database, targets: dict[str, ExecutionTarget]):
        self.db = db
        self.targets = targets

    # -- public API ---------------------------------------------------------
    def submit(self, spec: RunSpec) -> None:
        if spec.target not in self.targets:
            raise ValueError(f"unknown target '{spec.target}' (configured: {sorted(self.targets)})")
        self.db.insert_run(spec, RunStatus.QUEUED)
        self.db.enqueue(spec.run_id)
        self.db.add_event(spec.run_id, "status", "submitted to queue")

    def stop_run(self, run_id: str) -> None:
        row = self.db.get_run(run_id)
        if row is None:
            raise ValueError(f"unknown run '{run_id}'")
        if row["status"] == RunStatus.QUEUED.value:
            self.db.remove_from_queue(run_id)
            self.db.set_status(run_id, RunStatus.STOPPED)
            return
        target = self.targets[row["target"]]
        target.stop(_handle_from_row(row))
        self.db.set_status(run_id, RunStatus.STOPPED, exit_code=target.exit_code(_handle_from_row(row)))
        self.db.add_event(run_id, "status", "stopped by user")

    def reconcile(self) -> None:
        """Startup pass: every DB row in an active state is checked against reality."""
        for row in self.db.list_runs():
            if row["status"] in _ACTIVE:
                self._settle(row)

    def tick(self) -> None:
        active = False
        for row in self.db.list_runs():
            if row["status"] in _ACTIVE:
                active = self._settle(row) or active
        if not active:
            self._launch_next()

    # -- internals ------------------------------------------------------------
    def _settle(self, row: dict) -> bool:
        """Update one active run from target reality; True while still running."""
        target = self.targets[row["target"]]
        handle = _handle_from_row(row)
        if target.is_alive(handle):
            if row["status"] != RunStatus.RUNNING.value:
                self.db.set_status(row["run_id"], RunStatus.RUNNING)
            return True
        code = target.exit_code(handle)
        if code is None:
            self.db.set_status(row["run_id"], RunStatus.UNKNOWN)
            self.db.add_event(row["run_id"], "warning",
                              "process gone but no exit marker found -- state unknown, check the log")
            logger.warning("run %s: dead without exit marker -> status unknown", row["run_id"])
        else:
            status = RunStatus.DONE if code == 0 else RunStatus.FAILED
            self.db.set_status(row["run_id"], status, exit_code=code)
            self.db.add_event(row["run_id"], "status", f"finished with exit code {code}")
        return False

    def _launch_next(self) -> None:
        run_id = self.db.dequeue_next()
        if run_id is None:
            return
        row = self.db.get_run(run_id)
        spec = RunSpec(run_id=row["run_id"], target=row["target"],
                       label=row["label"], config_path=row["config_path"])
        self.db.set_status(run_id, RunStatus.LAUNCHING)
        try:
            handle = self.targets[spec.target].launch(spec)
        except Exception as exc:                     # launch failure is a terminal, visible state
            self.db.set_status(run_id, RunStatus.FAILED)
            self.db.add_event(run_id, "error", f"launch failed: {exc}")
            logger.error("run %s: launch failed: %s", run_id, exc)
            return
        self.db.attach_handle(run_id, handle)
        self.db.set_status(run_id, RunStatus.RUNNING)
        self.db.add_event(run_id, "status", f"launched on {spec.target}")

    def run_forever(self, poll_seconds: float) -> None:
        self.reconcile()
        while True:
            self.tick()
            time.sleep(poll_seconds)
