import pytest

from braunschweig.runcontrol.models import LaunchHandle, RunSpec
from braunschweig.runcontrol.settings import TargetConfig
from braunschweig.runcontrol.targets.ssh import SshTarget


class FakeSsh:
    def __init__(self, responses=None):
        self.calls = []
        self.responses = responses or {}

    def __call__(self, argv):
        self.calls.append(argv)
        remote = argv[-1]
        for key, resp in self.responses.items():
            if key in remote:
                return resp
        return (0, "")


def _cfg():
    return TargetConfig(name="server", kind="ssh", repo="~/eqasim-bs", host="felix",
                        runner="scripts/run_pipeline.sh")


def test_launch_builds_tmux_command_with_exit_marker_and_manifest():
    ssh = FakeSsh()
    t = SshTarget(_cfg(), run_command=ssh)
    h = t.launch(RunSpec(run_id="r1", target="server", label="l", config_path="config_x.yml"))
    assert h.tmux_session == "rc_r1" and h.pid is None
    joined = " ||| ".join(" ".join(c) for c in ssh.calls)
    assert "tmux new-session -d -s rc_r1" in joined
    assert "scripts/run_pipeline.sh config_x.yml logs/rc_r1.log" in joined
    assert "echo $? > logs/rc_r1.exit" in joined
    assert "rc_r1.manifest.json" in joined            # manifest written on the server
    assert all(c[0] == "ssh" and c[1] == "felix" for c in ssh.calls)


def test_is_alive_uses_tmux_has_session():
    ssh = FakeSsh(responses={"tmux has-session": (0, "")})
    t = SshTarget(_cfg(), run_command=ssh)
    h = LaunchHandle(run_id="r1", tmux_session="rc_r1", pid=None,
                     log_path="logs/rc_r1.log", exit_marker_path="logs/rc_r1.exit")
    assert t.is_alive(h) is True
    ssh.responses = {"tmux has-session": (1, "")}
    assert t.is_alive(h) is False


def test_exit_code_reads_marker_file():
    ssh = FakeSsh(responses={"rc_r1.exit": (0, "137\n")})
    t = SshTarget(_cfg(), run_command=ssh)
    h = LaunchHandle(run_id="r1", tmux_session="rc_r1", pid=None,
                     log_path="logs/rc_r1.log", exit_marker_path="logs/rc_r1.exit")
    assert t.exit_code(h) == 137
    ssh.responses = {"rc_r1.exit": (1, "")}           # cat fails -> marker missing
    assert t.exit_code(h) is None


def test_stop_sends_ctrl_c_to_own_session_only():
    ssh = FakeSsh(responses={"tmux has-session": (1, "")})   # dead right after C-c
    t = SshTarget(_cfg(), run_command=ssh)
    h = LaunchHandle(run_id="r1", tmux_session="rc_r1", pid=None,
                     log_path="logs/rc_r1.log", exit_marker_path="logs/rc_r1.exit")
    t.stop(h)
    joined = " ||| ".join(" ".join(c) for c in ssh.calls)
    assert "tmux send-keys -t rc_r1 C-c" in joined
    assert "pkill" not in joined and "killall" not in joined


def test_read_text_tail_and_listdir_parse():
    listing = "1024 1751970000 run_a.log\n2048 1751970100 rc_r1.manifest.json\n"
    ssh = FakeSsh(responses={"stat -c": (0, listing), "tail -c": (0, "TAIL"), "test -e": (0, "")})
    t = SshTarget(_cfg(), run_command=ssh)
    assert t.read_text("logs/x.log", tail_bytes=4) == "TAIL"
    entries = t.listdir("logs")
    assert entries[0] == {"name": "run_a.log", "size": 1024, "mtime": 1751970000.0}
    assert t.exists("logs/x.log") is True


def test_launch_failure_raises_with_stderr():
    ssh = FakeSsh(responses={"tmux new-session": (255, "ssh: connect refused")})
    t = SshTarget(_cfg(), run_command=ssh)
    with pytest.raises(RuntimeError, match="connect refused"):
        t.launch(RunSpec(run_id="r9", target="server", label="l", config_path="c.yml"))
