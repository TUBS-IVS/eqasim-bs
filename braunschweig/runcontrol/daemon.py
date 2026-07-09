"""Queue worker: reconcile run states against reality, advance the queue.

State machine per run: queued -> launching -> running -> done|failed|stopped,
plus 'unknown' when a process disappeared without an exit marker (surfaced as
a warning event -- never guessed into done/failed). An UNKNOWN run with
unverifiable process state (no handle and no manifest) blocks the queue until
a human resolves it (stop_run marks it stopped and unblocks). One run executes
at a time per QueueWorker (matches the single-box 64c/128G reality); the queue
advances only while this process lives -- documented V1 limitation."""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

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
        self._clock = time.time                  # injectable daemon wall clock (epoch seconds)
        self._window_override = None              # tests set an explicit window

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
        if row["external"]:
            # Monitor-only: the process was never launched by us, so it is never stopped.
            # "Stop" here means "stop monitoring" -- the run ends honestly as ENDED
            # (exit code unknowable) while the external process keeps running untouched.
            self.db.set_status(run_id, RunStatus.ENDED)
            self.db.add_event(run_id, "status",
                              "monitoring stopped; external process left running")
            return
        if not row["log_path"]:
            # No handle was ever persisted (unverifiable ghost); never call the target
            # with a fabricated handle. Marking it stopped also unblocks the queue.
            self.db.set_status(run_id, RunStatus.STOPPED)
            self.db.add_event(run_id, "status", "stopped without handle (state was unverifiable)")
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
            try:
                if row["status"] in _ACTIVE:
                    self._settle(row)
                elif row["status"] == RunStatus.QUEUED.value and run_id not in queued_ids:
                    # Crash window: dequeued but never marked LAUNCHING, so no process was
                    # ever started -- safe to retry. The run goes to the BACK of the queue
                    # (accepted simplification; original position is not preserved).
                    self.db.enqueue(run_id)
                    self.db.add_event(run_id, "warning",
                                      "re-enqueued stranded queued run (daemon restart during launch window)")
                    logger.warning("run %s: re-enqueued stranded queued run", run_id)
            except Exception as exc:
                # Startup must survive one unreachable target / corrupt manifest; the
                # remaining rows are still reconciled.
                logger.exception("run %s: reconcile failed", run_id)
                self.db.add_event(run_id, "error", f"reconcile failed: {exc}")
        # Purge queue entries whose run is no longer QUEUED (e.g. crashed after a successful
        # launch); leaving them would re-launch an already-running/finished run.
        for run_id in self.db.queue_ids():
            try:
                row = self.db.get_run(run_id)
                if row is None:
                    self.db.remove_from_queue(run_id)
                    logger.warning("run %s: removed queue entry without a run row", run_id)
                elif row["status"] != RunStatus.QUEUED.value:
                    self.db.remove_from_queue(run_id)
                    self.db.add_event(run_id, "warning", "removed non-queued run from queue (stale entry)")
                    logger.warning("run %s: removed stale queue entry (status %s)", run_id, row["status"])
            except Exception as exc:
                logger.exception("run %s: queue purge failed", run_id)
                self.db.add_event(run_id, "error", f"queue purge failed: {exc}")

    def tick(self) -> None:
        active = False
        for row in self.db.list_runs():
            if row["status"] == RunStatus.UNKNOWN.value and not row["log_path"]:
                # Unverifiable ghost (no handle, no manifest): a process may still be
                # running, so it blocks the queue until a human resolves it via stop_run.
                active = True
                continue
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
        if row["external"]:
            return self._settle_external(row, target)
        if not row["log_path"]:
            # Crash window: status went active but the handle was never persisted.
            # Recover it from the on-host manifest; never query the target with a
            # fabricated/empty handle.
            row = self._recover_handle(row, target)
            if row is None:
                # Unverifiable ghost: count as active so no new launch happens over a
                # possibly-live process (conservative bias; see module docstring).
                return True
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

    # -- clock/window seams (injectable for tests; production uses real time) -----
    def _now_epoch(self) -> float:
        return self._clock()

    def _iso(self, epoch: float) -> str:
        return datetime.fromtimestamp(epoch, timezone.utc).isoformat(timespec="seconds")

    def _now_iso(self) -> str:
        return self._iso(self._now_epoch())

    def _window_seconds(self) -> int:
        if self._window_override is not None:
            return self._window_override
        return getattr(self, "_settings_window", 300)

    def _watch_mtime(self, target: ExecutionTarget, watch_path: str) -> float | None:
        """Dir mtime of the watched artifact directory, read via one listdir call on
        its parent (the target's data_dir). Returns None when the entry is gone."""
        parent = watch_path.rsplit("/", 1)[0]
        base = watch_path.rsplit("/", 1)[-1]
        for entry in target.listdir(parent):
            if entry["name"] == base:
                return float(entry["mtime"])
        return None

    def _settle_external(self, row: dict, target: ExecutionTarget) -> bool:
        """Liveness for an adopted external run: the watched dir mtime advancing
        (server clock, compared to its own stored value) means still running; no
        advance for _window_seconds() on the daemon clock means ENDED. The external
        process is never queried via a handle and never killed."""
        run_id = row["run_id"]
        current = self._watch_mtime(target, row["watch_path"])
        stored = row["watch_mtime"]
        if current is not None and stored is not None and current > stored:
            self.db.update_watch(run_id, current, self._now_iso())
            return True
        # no advance (or dir gone): has the change-window elapsed on our clock?
        checked = row["watch_checked_at"]
        try:
            checked_epoch = datetime.fromisoformat(checked).timestamp() if checked else None
        except ValueError:
            checked_epoch = None
        if checked_epoch is None:
            # first observation with no baseline time -> record now, stay running
            self.db.update_watch(run_id, current if current is not None else (stored or 0.0), self._now_iso())
            return True
        if self._now_epoch() - checked_epoch > self._window_seconds():
            self.db.set_status(run_id, RunStatus.ENDED)
            gone = " (artifact dir no longer present)" if current is None else ""
            self.db.add_event(run_id, "status",
                              f"adopted run no longer active; exit code unavailable{gone}")
            return False
        return True

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
        # Normalize separators: a Windows local target may glob backslash paths.
        match = next((p for p in target.manifest_glob()
                      if p.replace("\\", "/").rsplit("/", 1)[-1] == wanted), None)
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
