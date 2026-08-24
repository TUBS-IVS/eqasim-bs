"""Hang watchdog for long-running child processes (Braunschweig addition, issue #330).

Why this exists
---------------
The 100 % run of 2026-08-20 finished MATSim's controler at 16:18:55 -- every
output including all five SimWrapper dashboards was on disk -- and then kept the
JVM alive at ~0 % CPU until it was killed manually at 17:25. ``jstack`` showed a
non-daemon ``"unVocity-parsers input reading thread"`` parked in
``FixedInstancePool.allocate`` (SimWrapper dashboards read CSV through tablesaw,
whose univocity backend starts that thread and never marks it as a daemon), with
``DestroyJavaVM`` waiting for it. Consequence for the pipeline:
``matsim.simulation.run`` never returned, synpp stalled, and because the stage
was never cached a naive re-run would repeat 2.5 h of simulation and hang again.

The Java entry point now terminates explicitly (``System.exit`` in
``org.eqasim.braunschweig.RunSimulation``, TUBS-IVS/eqasim-java-bs#23), but that
guard only covers OUR entry point and only this one leak. The pipeline itself had
no limit at all: ``matsim.runtime.java.run`` waits on ``subprocess.check_call``
forever. This module closes that gap for every Java call the pipeline makes.

Why CPU time and not a wall clock
---------------------------------
A legitimate 100 % MATSim run occupies the JVM for hours, so a wall-clock timeout
either has to be so generous that it detects a hang only after most of a working
day, or it risks killing a healthy multi-hour run -- which would destroy real
work. A hung JVM, in contrast, is trivially distinguishable: it accumulates no
CPU time. That is exactly how the 2026-08-20 hang was diagnosed (the machine sat
at load 0.02 while the process stayed alive), and it is the signal used here: a
process that is alive but has gained less than ``min_cpu_seconds`` of CPU time
over the whole ``hang_timeout_s`` window is doing nothing and is declared hung.
Even a single-threaded, I/O-bound writer accumulates far more than a second of
CPU time over a 15-minute window, so the discrimination is wide.

Why the whole process TREE
--------------------------
The CPU signal is read for the process AND all its descendants (issue #350). A
process that forks helpers and then waits on them accumulates no CPU time itself,
so a per-process reading would call a busy tree idle -- the same class of error
that had a healthy ``secondary_chainsolvers`` stage killed as "deadlocked" on
2026-08-24. The tree reader lives in
``braunschweig.monitoring.process_tree.read_tree_cpu_seconds`` and is shared with
the run resource recorder, so there is exactly ONE definition of "is this tree
doing work". For a childless process (the ordinary JVM case) the tree total equals
the process' own, so this widening cannot change an existing verdict.

Fallback transparency (project rule): when the CPU signal cannot be read at all
(no ``/proc`` and no ``psutil``, no permission, platform without support), the
watchdog does NOT fall back to a wall-clock kill. It degrades to inert and says so
once, so an unprotected run is visible in the log instead of silently pretending
to be guarded.
"""
import logging
import subprocess as sp
import time

# Seconds a process may stay alive without accumulating CPU time before it counts
# as hung. Default ON per the project feature-flag policy; 0 disables the guard.
# 15 minutes is far beyond any pause a working JVM takes (garbage collection,
# output writing and network I/O all accumulate CPU time continuously) while it
# still turns a silent multi-hour stall into a failure within the same hour.
DEFAULT_HANG_TIMEOUT_S = 900

# CPU seconds that must accumulate inside the window to count as progress. A
# parked non-daemon thread waking up on a timed wait produces noise-level growth;
# any real work produces orders of magnitude more.
DEFAULT_HANG_MIN_CPU_SECONDS = 1.0

# How often the CPU counter is sampled. Cheap (one /proc read per sample), so the
# interval only bounds how precisely the window edge is detected.
DEFAULT_SAMPLE_INTERVAL_S = 30.0

# Grace period between SIGTERM and SIGKILL for a process declared hung.
DEFAULT_TERMINATE_GRACE_S = 60.0

# Watchdog verdicts. Explicit strings rather than booleans so the log line and the
# tests can distinguish "no CPU signal available" from "measured, still working".
STATE_DISABLED = "disabled"
STATE_UNMEASURABLE = "unmeasurable"
STATE_WORKING = "working"
STATE_IDLE = "idle"
STATE_HUNG = "hung"


def read_tree_cpu_seconds(pid):
    """Total CPU time (user + system, in seconds) of ``pid`` AND its descendants.

    Delegates to ``braunschweig.monitoring.process_tree``, which is the single
    definition of this measurement in the repository (issue #350) and which itself
    reads ``/proc`` where available and ``psutil`` otherwise. The import is done
    here rather than at module level to keep the dependency direction of the
    packages intact: ``braunschweig`` imports ``matsim``, not the other way round.

    Returns ``None`` when the value cannot be determined -- an unreadable counter,
    a process that has already exited, or the ``braunschweig`` package not being
    importable next to this one. Callers must treat ``None`` as "no signal" and
    never as "no progress"; see the fallback-transparency note in the module
    docstring.
    """
    try:
        from braunschweig.monitoring.process_tree import read_tree_cpu_seconds as reader
    except ImportError:
        logging.getLogger(__name__).warning(
            "[watchdog] braunschweig.monitoring.process_tree is not importable; the "
            "hang watchdog has no CPU signal and stays inert for this call.")
        return None

    return reader(pid)


class HangWatchdog:
    """Classifies a live process as working or hung by CPU-time accumulation.

    The watchdog keeps a reference point (wall-clock instant + CPU seconds at
    that instant). Every ``sample()`` compares the current CPU counter against
    that reference: growth of at least ``min_cpu_seconds`` counts as progress and
    moves the reference forward, otherwise the reference stays and the process is
    reported as idle until the full ``hang_timeout_s`` window has passed without
    progress -- at which point it is reported as hung.

    Units: ``hang_timeout_s`` and ``min_cpu_seconds`` are seconds. The watchdog
    is side-effect free; terminating a hung process is the caller's decision
    (see :func:`wait_with_hang_watchdog`).
    """

    def __init__(self, pid, hang_timeout_s = DEFAULT_HANG_TIMEOUT_S,
                 min_cpu_seconds = DEFAULT_HANG_MIN_CPU_SECONDS,
                 cpu_seconds_reader = read_tree_cpu_seconds,
                 monotonic = time.monotonic):
        self.pid = pid
        self.hang_timeout_s = float(hang_timeout_s)
        self.min_cpu_seconds = float(min_cpu_seconds)
        self.cpu_seconds_reader = cpu_seconds_reader
        self.monotonic = monotonic

        # Anchor the window at construction time, with the CPU counter as it
        # stands right now: the watchdog is "armed" from here, so a process that
        # never moves again is detected exactly hang_timeout_s later. A None
        # baseline (no CPU signal) is anchored on the first measurable sample
        # instead, so the window can never start from a fabricated zero.
        self.reference_time = monotonic()
        self.reference_cpu_seconds = cpu_seconds_reader(pid)
        self.cpu_seconds = self.reference_cpu_seconds

    @property
    def idle_seconds(self):
        """Wall-clock seconds since the last observed CPU progress."""
        return self.monotonic() - self.reference_time

    def sample(self):
        """Read the CPU counter once and return the current verdict.

        One of ``STATE_DISABLED``, ``STATE_UNMEASURABLE``, ``STATE_WORKING``,
        ``STATE_IDLE`` or ``STATE_HUNG``.
        """
        if self.hang_timeout_s <= 0:
            return STATE_DISABLED

        cpu_seconds = self.cpu_seconds_reader(self.pid)

        if cpu_seconds is None:
            return STATE_UNMEASURABLE

        self.cpu_seconds = cpu_seconds
        now = self.monotonic()

        # No baseline yet (the counter was unreadable when the watchdog was
        # armed): anchor the window here instead of judging against nothing.
        if self.reference_cpu_seconds is None:
            self.reference_cpu_seconds = cpu_seconds
            self.reference_time = now
            return STATE_WORKING

        if cpu_seconds - self.reference_cpu_seconds >= self.min_cpu_seconds:
            self.reference_cpu_seconds = cpu_seconds
            self.reference_time = now
            return STATE_WORKING

        if now - self.reference_time >= self.hang_timeout_s:
            return STATE_HUNG

        return STATE_IDLE


def wait_with_hang_watchdog(process, hang_timeout_s = DEFAULT_HANG_TIMEOUT_S,
                            min_cpu_seconds = DEFAULT_HANG_MIN_CPU_SECONDS,
                            sample_interval_s = DEFAULT_SAMPLE_INTERVAL_S,
                            terminate_grace_s = DEFAULT_TERMINATE_GRACE_S,
                            cpu_seconds_reader = read_tree_cpu_seconds,
                            monotonic = time.monotonic, log = print):
    """Wait for ``process``, terminating it if it stops accumulating CPU time.

    Returns the process return code. A process declared hung is sent SIGTERM and,
    after ``terminate_grace_s``, SIGKILL; the resulting non-zero return code
    turns a silent stall into a loud pipeline failure at the call site.

    ``hang_timeout_s <= 0`` disables the guard entirely: the call then degenerates
    to a plain ``process.wait()``, which is byte-identical in behaviour to the
    unguarded ``subprocess.check_call`` this replaces.
    """
    if hang_timeout_s is None or float(hang_timeout_s) <= 0:
        return process.wait()

    watchdog = HangWatchdog(process.pid, hang_timeout_s = hang_timeout_s,
                            min_cpu_seconds = min_cpu_seconds,
                            cpu_seconds_reader = cpu_seconds_reader,
                            monotonic = monotonic)

    log("Hang watchdog armed for pid %s: terminate after %s s without at least "
        "%s CPU seconds of progress." % (process.pid, hang_timeout_s, min_cpu_seconds))

    reported_unmeasurable = False

    while True:
        # Wait WITH a timeout rather than sleeping and polling: the call returns the
        # moment the process exits, so the watchdog costs no wall-clock time on the
        # many short-lived Java calls of a run (pt2matsim, config generation, ...).
        try:
            return process.wait(timeout = sample_interval_s)
        except sp.TimeoutExpired:
            pass

        state = watchdog.sample()

        if state == STATE_UNMEASURABLE:
            if not reported_unmeasurable:
                # No silent fallbacks: an inert watchdog must be visible in the log.
                log("WARNING! Hang watchdog inactive for pid %s: process CPU time "
                    "cannot be read (psutil missing or access denied). The Java "
                    "call is NOT protected against a silent hang." % process.pid)
                reported_unmeasurable = True
            continue

        if state == STATE_HUNG:
            log("ERROR! Hang watchdog: pid %s is alive but gained less than %s CPU "
                "seconds in %.0f s (total %.1f CPU s). Treating this as a hang and "
                "terminating it. Outputs written before the hang may be complete -- "
                "inspect the stage output directory, and capture 'jstack %s' before "
                "a retry if this recurs." % (
                    process.pid, min_cpu_seconds, watchdog.idle_seconds,
                    watchdog.cpu_seconds or 0.0, process.pid))

            process.terminate()

            try:
                return process.wait(timeout = terminate_grace_s)
            except sp.TimeoutExpired:
                log("ERROR! Hang watchdog: pid %s ignored SIGTERM for %s s, "
                    "sending SIGKILL." % (process.pid, terminate_grace_s))
                process.kill()
                return process.wait()
