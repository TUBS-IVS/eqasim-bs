import os, sys
from braunschweig.runcontrol.settings import TargetConfig
from braunschweig.runcontrol.targets.local import LocalTarget
from braunschweig.runcontrol.targets.ssh import SshTarget


def test_local_newest_files_sorted_desc_relpaths(tmp_path):
    d = tmp_path / "eqasim-data" / "popsim_work_x" / "batch_1" / "output"
    d.mkdir(parents=True)
    old = d / "a.log"; new = d / "b.log"
    old.write_text("x"); new.write_text("y")
    os.utime(old, (1000, 1000)); os.utime(new, (2000, 2000))
    cfg = TargetConfig(name="local", kind="local", repo=str(tmp_path), runner="r")
    t = LocalTarget(cfg, python=sys.executable)
    got = t.newest_files("eqasim-data", maxdepth=4, limit=10)
    assert got[0][0] == 2000.0
    assert got[0][1].replace("\\", "/") == "popsim_work_x/batch_1/output/b.log"
    assert all(got[i][0] >= got[i + 1][0] for i in range(len(got) - 1))


def test_local_newest_files_missing_dir_returns_empty(tmp_path):
    cfg = TargetConfig(name="local", kind="local", repo=str(tmp_path), runner="r")
    t = LocalTarget(cfg, python=sys.executable)
    assert t.newest_files("eqasim-data") == []


def test_local_newest_files_respects_maxdepth(tmp_path):
    # Tight boundary tree matching find -maxdepth semantics (start dir = level 0):
    #   eqasim-data/d1.txt      -> find-depth 1
    #   eqasim-data/a/d2.txt    -> find-depth 2
    #   eqasim-data/a/b/d3.txt  -> find-depth 3
    base = tmp_path / "eqasim-data"
    (base / "a" / "b").mkdir(parents=True)
    d1 = base / "d1.txt"; d2 = base / "a" / "d2.txt"; d3 = base / "a" / "b" / "d3.txt"
    d1.write_text("1"); d2.write_text("2"); d3.write_text("3")
    os.utime(d1, (1000, 1000)); os.utime(d2, (2000, 2000)); os.utime(d3, (3000, 3000))
    cfg = TargetConfig(name="local", kind="local", repo=str(tmp_path), runner="r")
    t = LocalTarget(cfg, python=sys.executable)

    def basenames(maxdepth):
        return {p.rsplit("/", 1)[-1] for _, p in t.newest_files("eqasim-data", maxdepth=maxdepth, limit=50)}

    assert basenames(1) == {"d1.txt"}
    assert basenames(2) == {"d1.txt", "d2.txt"}
    assert basenames(3) == {"d1.txt", "d2.txt", "d3.txt"}


class FakeSsh:
    def __init__(self, out):
        self.out = out
        self.calls = []

    def __call__(self, argv):
        self.calls.append(argv)
        return (0, self.out)


def test_ssh_newest_files_builds_find_and_parses():
    out = ("1783689656.81 popsim_work_x/batch_8/output/populationsim.log\n"
           "1783689653.73 popsim_work_x/batch_10/output/populationsim.log\n"
           "garbage-line-no-space\n")
    ssh = FakeSsh(out)
    cfg = TargetConfig(name="server", kind="ssh", repo="~/eqasim-bs", host="felix", runner="r")
    t = SshTarget(cfg, run_command=ssh)
    got = t.newest_files("eqasim-data", maxdepth=4, limit=200)
    joined = " ".join(ssh.calls[-1])
    assert "find" in joined and "-maxdepth 4" in joined and "-printf" in joined and "head" in joined
    assert got[0] == (1783689656.81, "popsim_work_x/batch_8/output/populationsim.log")
    assert len(got) == 2      # malformed line skipped
