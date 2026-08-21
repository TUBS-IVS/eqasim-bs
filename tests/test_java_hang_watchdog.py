"""Pipeline-side hang watchdog for the Java subprocess calls (issue #330).

The 100 % run of 2026-08-20 finished MATSim's controler at 16:18 and then kept
the JVM alive at ~0 % CPU (a leaked non-daemon univocity reader thread from the
SimWrapper dashboards blocked ``DestroyJavaVM``). ``matsim.simulation.run``
never returned, so synpp stalled for hours without any failure being reported.
The Java side now terminates explicitly, but the pipeline had no guard at all:
``java.run`` waits on ``subprocess.check_call`` without any limit.

The guard tested here judges a live process by CPU-time ACCUMULATION rather
than by wall clock, because a legitimate 100 % MATSim run takes hours while a
hung JVM burns no CPU at all. A pure wall-clock timeout cannot tell the two
apart without risking the kill of a healthy multi-hour run.
"""
import subprocess as sp

import matsim.runtime.java as java
import matsim.runtime.process_watchdog as watchdog


class _Clock:
    """Deterministic monotonic clock, advanced explicitly by the test."""

    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class _FakeProcess:
    """Minimal Popen stand-in.

    Models the real contract the driver relies on: ``wait(timeout=...)`` raises
    ``TimeoutExpired`` while the process is still alive and consumes that timeout
    of wall-clock time (the injected clock is advanced accordingly), so a test
    needs no separate sleep hook. ``exits_after_waits=None`` never exits on its
    own -- the hung case.
    """

    def __init__(self, exits_after_waits=None, return_code=0, ignore_terminate=False,
                 clock=None):
        self.pid = 4242
        self.waits = 0
        self.exits_after_waits = exits_after_waits
        self.return_code = return_code
        self.ignore_terminate = ignore_terminate
        self.clock = clock
        self.terminated = False
        self.killed = False

    def wait(self, timeout=None):
        self.waits += 1

        if self.clock is not None and timeout is not None:
            self.clock.advance(timeout)

        if self.killed:
            return -9
        if self.terminated:
            if self.ignore_terminate:
                raise sp.TimeoutExpired("java", timeout)
            return -15
        if self.exits_after_waits is not None and self.waits >= self.exits_after_waits:
            return self.return_code
        if timeout is None:
            # Unguarded wait: a real Popen blocks until the process exits.
            return self.return_code

        raise sp.TimeoutExpired("java", timeout)

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True


# --- the state machine -----------------------------------------------------

def test_accumulating_cpu_time_is_never_a_hang():
    """A working process accumulates CPU time, so the window must keep resetting."""
    clock = _Clock()
    cpu = {"seconds": 0.0}
    dog = watchdog.HangWatchdog(
        pid=1, hang_timeout_s=100.0, min_cpu_seconds=1.0,
        cpu_seconds_reader=lambda pid: cpu["seconds"], monotonic=clock)

    for _ in range(10):
        clock.advance(60.0)
        cpu["seconds"] += 55.0  # a busy JVM burns ~1 CPU-second per wall second
        assert dog.sample() == watchdog.STATE_WORKING


def test_zero_cpu_growth_is_a_hang_only_after_the_window_elapsed():
    """The hang verdict must not fire before the configured window is full."""
    clock = _Clock()
    dog = watchdog.HangWatchdog(
        pid=1, hang_timeout_s=100.0, min_cpu_seconds=1.0,
        cpu_seconds_reader=lambda pid: 8000.0, monotonic=clock)

    clock.advance(60.0)
    assert dog.sample() == watchdog.STATE_IDLE
    clock.advance(39.0)
    assert dog.sample() == watchdog.STATE_IDLE
    clock.advance(1.0)
    assert dog.sample() == watchdog.STATE_HUNG


def test_slow_progress_above_the_threshold_resets_the_window():
    """A slow-but-real writer must survive: it still accumulates CPU seconds."""
    clock = _Clock()
    cpu = {"seconds": 0.0}
    dog = watchdog.HangWatchdog(
        pid=1, hang_timeout_s=100.0, min_cpu_seconds=1.0,
        cpu_seconds_reader=lambda pid: cpu["seconds"], monotonic=clock)

    for _ in range(5):
        clock.advance(90.0)
        cpu["seconds"] += 1.5
        assert dog.sample() == watchdog.STATE_WORKING


def test_growth_below_the_threshold_does_not_reset_the_window():
    """Noise-level CPU growth (a parked thread waking up) is not progress."""
    clock = _Clock()
    cpu = {"seconds": 0.0}
    dog = watchdog.HangWatchdog(
        pid=1, hang_timeout_s=100.0, min_cpu_seconds=1.0,
        cpu_seconds_reader=lambda pid: cpu["seconds"], monotonic=clock)

    clock.advance(60.0)
    cpu["seconds"] += 0.2
    assert dog.sample() == watchdog.STATE_IDLE
    clock.advance(60.0)
    cpu["seconds"] += 0.2
    assert dog.sample() == watchdog.STATE_HUNG


def test_zero_timeout_disables_the_watchdog():
    clock = _Clock()
    dog = watchdog.HangWatchdog(
        pid=1, hang_timeout_s=0, min_cpu_seconds=1.0,
        cpu_seconds_reader=lambda pid: 0.0, monotonic=clock)

    clock.advance(100000.0)
    assert dog.sample() == watchdog.STATE_DISABLED


def test_unmeasurable_cpu_never_declares_a_hang():
    """No CPU signal (no psutil, no permission) must degrade to inert, not to a kill."""
    clock = _Clock()
    dog = watchdog.HangWatchdog(
        pid=1, hang_timeout_s=100.0, min_cpu_seconds=1.0,
        cpu_seconds_reader=lambda pid: None, monotonic=clock)

    clock.advance(100000.0)
    assert dog.sample() == watchdog.STATE_UNMEASURABLE


# --- the wait driver -------------------------------------------------------

def test_driver_returns_the_return_code_of_a_healthy_process():
    clock = _Clock()
    process = _FakeProcess(exits_after_waits=3, return_code=0, clock=clock)
    code = watchdog.wait_with_hang_watchdog(
        process, hang_timeout_s=100.0, sample_interval_s=1.0,
        cpu_seconds_reader=lambda pid: clock.now, monotonic=clock,
        log=lambda m: None)
    assert code == 0


def test_driver_returns_as_soon_as_the_process_exits():
    """The wait must not cost a whole sample interval after the process is gone:
    a run makes 14 Java calls, most of them seconds long."""
    clock = _Clock()
    process = _FakeProcess(exits_after_waits=1, return_code=0, clock=clock)
    watchdog.wait_with_hang_watchdog(
        process, hang_timeout_s=900.0, sample_interval_s=30.0,
        cpu_seconds_reader=lambda pid: 5.0, monotonic=clock, log=lambda m: None)
    assert process.waits == 1


def test_driver_terminates_a_hung_process_and_reports_a_nonzero_code():
    clock = _Clock()
    messages = []
    process = _FakeProcess(exits_after_waits=None, clock=clock)
    code = watchdog.wait_with_hang_watchdog(
        process, hang_timeout_s=10.0, sample_interval_s=5.0, min_cpu_seconds=1.0,
        cpu_seconds_reader=lambda pid: 42.0, monotonic=clock, log=messages.append)

    assert process.terminated
    assert code != 0
    assert any("hang" in m.lower() for m in messages), messages


def test_driver_escalates_to_kill_when_terminate_is_ignored():
    clock = _Clock()
    messages = []
    process = _FakeProcess(exits_after_waits=None, ignore_terminate=True, clock=clock)
    watchdog.wait_with_hang_watchdog(
        process, hang_timeout_s=10.0, sample_interval_s=5.0, min_cpu_seconds=1.0,
        terminate_grace_s=1.0, cpu_seconds_reader=lambda pid: 42.0, monotonic=clock,
        log=messages.append)

    assert process.terminated and process.killed


def test_driver_reports_an_unmeasurable_cpu_signal_once():
    """No silent fallbacks: an inert watchdog must say so, and only once."""
    clock = _Clock()
    messages = []
    process = _FakeProcess(exits_after_waits=5, clock=clock)
    watchdog.wait_with_hang_watchdog(
        process, hang_timeout_s=10.0, sample_interval_s=5.0,
        cpu_seconds_reader=lambda pid: None, monotonic=clock, log=messages.append)

    unmeasurable = [m for m in messages if "inactive" in m.lower()]
    assert len(unmeasurable) == 1, messages


def test_disabled_driver_just_waits():
    process = _FakeProcess(exits_after_waits=1, return_code=7)
    code = watchdog.wait_with_hang_watchdog(
        process, hang_timeout_s=0, log=lambda m: None)
    assert code == 7
    assert not process.terminated


# --- config wiring ---------------------------------------------------------

class _RecordingContext:
    """configure()-time context: records declared options and volatile flags."""

    def __init__(self):
        self.declared = {}
        self.volatile = set()

    def stage(self, name, *args, **kwargs):
        return None

    def config(self, name, *args, **kwargs):
        self.declared[name] = args[0] if args else None
        if kwargs.get("volatile"):
            self.volatile.add(name)
        return self.declared[name]


def test_java_configure_declares_the_watchdog_options_with_defaults():
    ctx = _RecordingContext()
    java.configure(ctx)
    assert ctx.declared[java.KEY_HANG_TIMEOUT] == watchdog.DEFAULT_HANG_TIMEOUT_S
    assert ctx.declared[java.KEY_HANG_MIN_CPU] == watchdog.DEFAULT_HANG_MIN_CPU_SECONDS


def test_watchdog_options_are_volatile_so_they_never_invalidate_a_cache():
    """An operational guard has no influence on results, so it must stay out of
    the stage hash -- otherwise raising the timeout would force a 2.5 h re-run."""
    ctx = _RecordingContext()
    java.configure(ctx)
    assert {java.KEY_HANG_TIMEOUT, java.KEY_HANG_MIN_CPU} <= ctx.volatile


def test_the_watchdog_is_on_by_default():
    """Project rule: new features default ON. 0 disables the guard explicitly."""
    assert watchdog.DEFAULT_HANG_TIMEOUT_S > 0
