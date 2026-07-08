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
