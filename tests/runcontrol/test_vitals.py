import os
import time

import pytest

from braunschweig.runcontrol.collectors import vitals


class FakeTarget:
    kind = "ssh"
    name = "server"

    def __init__(self, files):
        self.files = files

    def read_text(self, path, tail_bytes=None):
        if path not in self.files:
            raise FileNotFoundError(path)
        return self.files[path]

    def exists(self, path):
        return path in self.files


PROC = {
    "/proc/meminfo": "MemTotal: 131072000 kB\nMemAvailable: 65536000 kB\n",
    "/proc/loadavg": "12.5 10.0 8.0 3/900 1234\n",
    "/proc/cpuinfo": "processor : 0\n" * 64,
    "__df__": "/dev/sda1 500G 250G 214G 54% /home\n",
}


def test_vitals_from_proc():
    v = vitals.collect_from_proc(FakeTarget(PROC), df_output=PROC["__df__"])
    assert v.ram_avail_gb == 62.5
    assert v.cpu_percent == round(12.5 / 64 * 100, 1)
    assert v.disk_avail_gb == 214.0
    assert v.source == "proc"


def test_vitals_unavailable_is_honest_not_guessed():
    v = vitals.collect_from_proc(FakeTarget({}), df_output="")
    assert v.cpu_percent is None and v.ram_avail_gb is None
    assert "unavailable" in v.source


def test_vitals_disk_parse_failure_is_surfaced_in_source():
    v = vitals.collect_from_proc(FakeTarget(PROC), df_output="")
    assert v.disk_avail_gb is None
    assert "df" in v.source


# ---- Task 15: real Windows CPU/RAM via ctypes -----------------------------

def test_cpu_percent_from_deltas_math():
    # GetSystemTimes kernel time INCLUDES idle: busy = (kernel - idle) + user.
    # idle 50, kernel 80, user 20 -> busy (80-50)+20 = 50 of total 80+20 = 100 -> 50.0
    assert vitals._cpu_percent_from_deltas(50, 80, 20) == 50.0


def test_cpu_percent_from_deltas_clamped_and_zero_total():
    assert vitals._cpu_percent_from_deltas(0, 100, 50) == 100.0     # busy 150 of 150
    assert vitals._cpu_percent_from_deltas(100, 100, 0) == 0.0      # fully idle
    assert vitals._cpu_percent_from_deltas(0, 0, 0) == 0.0          # no elapsed time


@pytest.mark.skipif(os.name != "nt", reason="Windows-only ctypes vitals")
def test_collect_local_windows_two_sample_cpu_and_real_ram(tmp_path):
    # Reset the module-level sample cache so this test owns the "first call" semantics
    # regardless of what other tests in the same process did before.
    vitals._last_system_times = None
    v1 = vitals.collect_local_windows(str(tmp_path))
    assert v1.ram_avail_gb is not None and v1.ram_avail_gb > 0
    assert v1.disk_avail_gb is not None and v1.disk_avail_gb > 0
    assert v1.cpu_percent is None                       # no invented value on first sample
    assert "first_sample" in v1.source
    time.sleep(0.05)
    v2 = vitals.collect_local_windows(str(tmp_path))
    assert v2.source == "windows_ctypes"
    assert v2.cpu_percent is not None and 0.0 <= v2.cpu_percent <= 100.0
    assert v2.ram_avail_gb is not None and v2.ram_avail_gb > 0
