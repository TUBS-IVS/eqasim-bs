"""Background resource recording for a pipeline run (issue #350).

Wired into ``scripts/run_synpp.py``, so every run records its own resource series
without anyone remembering to start a monitor. Two properties outrank every
feature here:

**It must not be able to kill the run it measures.** A sample is a best-effort
diagnostic: a ``/proc`` read racing with an exiting worker, a restricted kernel
log, a full disk -- none of these may propagate into the pipeline. Failures are
counted and logged (never swallowed silently) and the summary states how many
samples failed, so a thin series cannot be mistaken for a quiet machine.

**A run that dies must still leave its measurement behind.** The series is
appended and flushed sample by sample, and the summary is written from a
``finally``, so a killed or failed run keeps everything recorded up to its last
breath -- which is precisely the run whose resource record is wanted.

The series lands in ``<working_directory>/monitoring/`` next to the outputs a run
manifest already references. Default ON, switched by ``monitoring_enabled`` in the
run config (project feature-flag policy).
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import threading
import time

import yaml

from braunschweig.monitoring import sampler as sampler_module
from braunschweig.monitoring import summary as summary_module

logger = logging.getLogger(__name__)

# Sampling interval. 30 s keeps a multi-hour run's series small (a 10 h run yields
# ~1200 rows) while still resolving the memory ramp of a PopulationSim batch.
DEFAULT_INTERVAL_SECONDS = 30.0

# Seconds allowed for the sampling thread to finish its current sample when the run
# ends. Generous compared to a sample (milliseconds), so a shutdown never hangs.
THREAD_JOIN_TIMEOUT_SECONDS = 30.0

# Config keys read from the run config's ``config:`` block. Named after the feature
# so a resolved config shows at a glance whether a run was recorded.
KEY_ENABLED = "monitoring_enabled"
KEY_INTERVAL_SECONDS = "monitoring_interval_seconds"
KEY_OUTPUT_DIRECTORY = "monitoring_output_directory"
KEY_INCLUDE_PROCESS_ROWS = "monitoring_include_process_rows"
KEY_KERNEL_EVENTS = "monitoring_kernel_events"

# Not a monitoring key: the PopulationSim working directory, read only to include its
# filesystem in the free-space samples (its per-batch pipeline.h5 files are ~9 GB).
KEY_POPSIM_WORK_DIRECTORY = "braunschweig.population.popsim.work_dir"

# Subdirectory of the run's working_directory that holds the series and summaries.
DEFAULT_OUTPUT_SUBDIRECTORY = "monitoring"


class ResourceRecorder:
    """Appends samples to a JSONL series and can summarise it afterwards.

    The recorder owns the file, the counters and the stop flag; the sampling itself
    is delegated to an injected sampler (anything with a ``sample()`` returning a
    dict), which keeps the loop testable without a real process tree.
    """

    def __init__(self, series_path, resource_sampler,
                 interval_seconds=DEFAULT_INTERVAL_SECONDS, log=None):
        self.series_path = str(series_path)
        self.resource_sampler = resource_sampler
        self.interval_seconds = float(interval_seconds)
        self.log = log or logger
        self.written_sample_count = 0
        self.failed_sample_count = 0
        self.thread = None
        self._first_failure_logged = False
        self._stop = threading.Event()

        directory = os.path.dirname(self.series_path)
        if directory:
            os.makedirs(directory, exist_ok=True)

    def sample_once(self):
        """Take one sample and append it. Returns the row, or ``None`` on failure.

        Never raises: measuring the run must not be able to end it.
        """
        try:
            row = self.resource_sampler.sample()
            with open(self.series_path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(row) + "\n")
                handle.flush()
        except Exception as error:
            self.failed_sample_count += 1
            if not self._first_failure_logged:
                # Logged once at WARNING with the cause, then only counted: a
                # broken sampler must be visible without flooding the run log.
                self.log.warning("[monitoring] sampling failed (%s: %s); recording "
                                 "continues, failures are counted in the summary.",
                                 type(error).__name__, error)
                self._first_failure_logged = True
            return None

        self.written_sample_count += 1
        return row

    def run_until_stopped(self):
        """Sample every ``interval_seconds`` until :meth:`request_stop` is called."""
        self.log.info("[monitoring] recording every %.0f s to %s",
                      self.interval_seconds, self.series_path)
        while True:
            self.sample_once()
            if self.wait_for_stop(self.interval_seconds):
                break
        self.log.info("[monitoring] recording stopped: %d sample(s) written, %d failed.",
                      self.written_sample_count, self.failed_sample_count)

    def request_stop(self):
        """Ask the sampling loop to finish after the current sample."""
        self._stop.set()

    def wait_for_stop(self, timeout):
        """Sleep up to ``timeout`` seconds; ``True`` when a stop was requested.

        Interruptible by design: a run that ends between two samples must not have
        to wait out the remaining interval before the summary is written.
        """
        return self._stop.wait(timeout)

    def counters(self) -> dict:
        return {
            "written_sample_count": self.written_sample_count,
            "failed_sample_count": self.failed_sample_count,
            "interval_seconds": self.interval_seconds,
            "series_path": self.series_path,
        }

    def write_summary(self):
        """Write the JSON + markdown summary beside the series. Never raises."""
        try:
            return summary_module.write_summary(self.series_path, extra=self.counters())
        except Exception as error:
            self.log.warning("[monitoring] could not summarise %s (%s: %s); the raw "
                             "series is still on disk.", self.series_path,
                             type(error).__name__, error)
            return None


@contextlib.contextmanager
def record_in_background(series_path, resource_sampler,
                         interval_seconds=DEFAULT_INTERVAL_SECONDS,
                         write_summary=True, log=None):
    """Record in a daemon thread for the duration of the ``with`` block.

    The summary is written from a ``finally``, so it exists even when the block
    raises -- the failed run keeps its measurement.
    """
    resource_recorder = ResourceRecorder(series_path, resource_sampler,
                                         interval_seconds=interval_seconds, log=log)
    thread = threading.Thread(target=resource_recorder.run_until_stopped,
                              name="resource-recorder", daemon=True)
    resource_recorder.thread = thread
    thread.start()
    try:
        yield resource_recorder
    finally:
        resource_recorder.request_stop()
        thread.join(timeout=THREAD_JOIN_TIMEOUT_SECONDS)
        if write_summary:
            resource_recorder.write_summary()


def _read_config(config_path):
    with open(config_path, encoding="utf-8") as handle:
        document = yaml.safe_load(handle) or {}
    return document, document.get("config", {}) or {}


def _filesystem_paths_for_run(working_directory, config):
    """The directories whose free space this run's failure modes depend on.

    Three of them, deduplicated while keeping their order: the synpp
    ``working_directory`` (stage caches), the ``output_path`` (population, MATSim
    scenario, analyses) and the PopulationSim ``work_dir``, whose per-batch
    ``pipeline.h5`` files are ~9 GB each and may well sit on another disk. ENOSPC is
    a documented failure class of this pipeline, so the question is asked for each.
    """
    candidates = [working_directory, config.get("output_path"),
                  config.get(KEY_POPSIM_WORK_DIRECTORY)]
    ordered = []
    for path in candidates:
        if path and str(path) not in ordered:
            ordered.append(str(path))
    return ordered


def series_path_for_run(output_directory, now=None):
    """Timestamped series file name, so consecutive runs never overwrite each other."""
    stamp = time.strftime("%Y%m%dT%H%M%S", time.localtime(now or time.time()))
    return os.path.join(str(output_directory), "resource_series_%s.jsonl" % stamp)


def record_from_config(config_path, log_path=None, root_pid=None,
                       interval_seconds=None, log=None):
    """Context manager recording THIS process' tree for the run described by a config.

    Reads the ``monitoring_*`` keys from the run config (see the module constants).
    Returns a no-op context yielding ``None`` when ``monitoring_enabled`` is false,
    so a disabled run is byte-identical to one without this feature.

    The recorded filesystems are the run's ``working_directory`` and its
    ``output_path``: those are the two that fill up, and ENOSPC is a documented
    failure class of this pipeline.
    """
    log = log or logger
    document, config = _read_config(config_path)

    if not config.get(KEY_ENABLED, True):
        log.info("[monitoring] disabled (%s false) -> no-op.", KEY_ENABLED)
        return contextlib.nullcontext(None)

    working_directory = document.get("working_directory")
    output_directory = config.get(KEY_OUTPUT_DIRECTORY)
    if not output_directory:
        if not working_directory:
            log.warning("[monitoring] neither working_directory nor %s is set -> "
                        "nothing recorded for this run.", KEY_OUTPUT_DIRECTORY)
            return contextlib.nullcontext(None)
        output_directory = os.path.join(working_directory, DEFAULT_OUTPUT_SUBDIRECTORY)

    filesystem_paths = _filesystem_paths_for_run(working_directory, config)
    resource_sampler = sampler_module.ResourceSampler(
        root_pid=root_pid or os.getpid(),
        log_path=log_path,
        filesystem_paths=filesystem_paths,
        include_process_rows=bool(config.get(KEY_INCLUDE_PROCESS_ROWS, True)),
        collect_kernel_events=bool(config.get(KEY_KERNEL_EVENTS, True)),
    )
    resolved_interval = (interval_seconds if interval_seconds is not None
                         else float(config.get(KEY_INTERVAL_SECONDS,
                                               DEFAULT_INTERVAL_SECONDS)))
    return record_in_background(series_path_for_run(output_directory), resource_sampler,
                                interval_seconds=resolved_interval, log=log)
