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
from .models import LaunchHandle, RunManifest, RunSpec, RunStatus
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
        handle = _handle_from_row(row)
        target.stop(handle)
        self.db.set_status(run_id, RunStatus.STOPPED, exit_code=target.exit_code(handle))
        self.db.add_event(run_id, "status", "stopped by user")

    def reconcile(self) -> None:
        """Startup pass: match every DB row against reality and repair launch crash windows."""
        queued_ids = set(self.db.queue_ids())
        for row in self.db.list_runs():
            run_id = row["run_id"]
            if row["status"] in _ACTIVE:
                self._settle(row)
            elif row["status"] == RunStatus.QUEUED.value and run_id not in queued_ids:
                # Crash window: dequeued but never marked LAUNCHING, so no process was
                # ever started -- safe to retry by putting the run back into the queue.
                self.db.enqueue(run_id)
                self.db.add_event(run_id, "warning",
                                  "re-enqueued stranded queued run (daemon restart during launch window)")
                logger.warning("run %s: re-enqueued stranded queued run", run_id)
        # Purge queue entries whose run is no longer QUEUED (e.g. crashed after a successful
        # launch); leaving them would re-launch an already-running/finished run.
        for run_id in self.db.queue_ids():
            row = self.db.get_run(run_id)
            if row is None or row["status"] != RunStatus.QUEUED.value:
                self.db.remove_from_queue(run_id)
                self.db.add_event(run_id, "warning", "removed non-queued run from queue (stale entry)")
                logger.warning("run %s: removed stale queue entry (status %s)",
                               run_id, row["status"] if row else "missing")

    def tick(self) -> None:
        active = False
        for row in self.db.list_runs():
            if row["status"] in _ACTIVE:
                try:
                    active = self._settle(row) or active
                except Exception as exc:
                    # One misbehaving run/target must not kill the poll cycle. Treat the run
                    # as still active so we never launch on top of an unsettled run.
                    logger.exception("run %s: settle failed", row["run_id"])
                    self.db.add_event(row["run_id"], "error", f"settle failed: {exc}")
                    active = True
        if not active:
            try:
                self._launch_next()
            except Exception:
                logger.exception("launching next queued run failed")

    # -- internals ------------------------------------------------------------
    def _settle(self, row: dict) -> bool:
        """Update one active run from target reality; True while still running."""
        target = self.targets[row["target"]]
        if not row["log_path"]:
            # Crash window: status went active but the handle was never persisted.
            # Recover it from the on-host manifest; never query the target with a
            # fabricated/empty handle.
            row = self._recover_handle(row, target)
            if row is None:
                return False
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

    def _recover_handle(self, row: dict, target: ExecutionTarget) -> dict | None:
        """Rebuild a lost handle from the per-run manifest on the execution host.

        The manifest is written by the target BEFORE the process starts, so it is
        the durable truth for whether a launch got far enough to be observable.
        Returns the refreshed DB row, or None (status set to UNKNOWN) when no
        manifest exists -- in that case a process may or may not have started and
        the run must be verified manually.
        """
        run_id = row["run_id"]
        wanted = f"rc_{run_id}.manifest.json"
        match = next((p for p in target.manifest_glob() if p.rsplit("/", 1)[-1] == wanted), None)
        if match is None:
            self.db.set_status(run_id, RunStatus.UNKNOWN)
            self.db.add_event(run_id, "warning",
                              "active run has no handle and no manifest -- a process may or may not have started; "
                              "verify manually")
            logger.warning("run %s: active without handle or manifest -> status unknown", run_id)
            return None
        manifest = RunManifest.from_json(target.read_text(match))
        handle = LaunchHandle(run_id=run_id, tmux_session=manifest.tmux_session, pid=manifest.pid,
                              log_path=manifest.log_path, exit_marker_path=manifest.exit_marker_path)
        self.db.attach_handle(run_id, handle)
        self.db.add_event(run_id, "status", "handle recovered from run manifest after restart")
        logger.info("run %s: handle recovered from run manifest after restart", run_id)
        return self.db.get_run(run_id)

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
            try:
                self.tick()
            except Exception:
                # Belt-and-braces: tick() isolates per-row errors itself, but the daemon
                # loop must survive anything unexpected (e.g. a transient DB error).
                logger.exception("tick failed")
            time.sleep(poll_seconds)
