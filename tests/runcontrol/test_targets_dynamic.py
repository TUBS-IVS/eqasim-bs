"""Task 14: dynamic ssh targets -- store roundtrip, validation, probe(), API wiring."""
import json

import pytest

from braunschweig.runcontrol.settings import TargetConfig
from braunschweig.runcontrol.targets.ssh import SshTarget
from braunschweig.runcontrol.targetstore import (
    load_dynamic_targets,
    save_dynamic_targets,
    validate_new_target,
)


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


def _cfg(name="added", host="134.169.42.227", repo="~/eqasim-bs"):
    return TargetConfig(name=name, kind="ssh", repo=repo, host=host, runner="scripts/run_pipeline.sh")


# ---- targetstore --------------------------------------------------------

def test_load_dynamic_targets_returns_empty_when_file_absent(tmp_path):
    assert load_dynamic_targets(tmp_path / "targets.json") == {}


def test_save_then_load_roundtrips(tmp_path):
    path = tmp_path / "sub" / "targets.json"
    cfg = _cfg()
    save_dynamic_targets(path, {"added": cfg})
    assert path.exists()          # mkdir parents
    loaded = load_dynamic_targets(path)
    assert set(loaded) == {"added"}
    assert loaded["added"] == cfg


def test_save_writes_expected_json_shape(tmp_path):
    path = tmp_path / "targets.json"
    save_dynamic_targets(path, {"added": _cfg()})
    raw = json.loads(path.read_text())
    assert raw["targets"]["added"]["kind"] == "ssh"
    assert raw["targets"]["added"]["host"] == "134.169.42.227"
    assert raw["targets"]["added"]["repo"] == "~/eqasim-bs"
    assert raw["targets"]["added"]["runner"] == "scripts/run_pipeline.sh"
    assert raw["targets"]["added"]["data_dir"] == "eqasim-data"
    assert raw["targets"]["added"]["logs_dir"] == "logs"


def test_validate_rejects_bad_name():
    with pytest.raises(ValueError, match="name"):
        validate_new_target("x y", "134.169.42.227", "~/eqasim-bs", existing=set())


def test_validate_rejects_host_starting_with_dash():
    with pytest.raises(ValueError, match="host"):
        validate_new_target("ok", "-oProxyCommand=evil", "~/eqasim-bs", existing=set())


def test_validate_rejects_host_with_whitespace():
    with pytest.raises(ValueError, match="host"):
        validate_new_target("ok", "134.169.42.227 extra", "~/eqasim-bs", existing=set())


def test_validate_rejects_repo_starting_with_dash():
    with pytest.raises(ValueError, match="repo"):
        validate_new_target("ok", "134.169.42.227", "-rf", existing=set())


def test_validate_rejects_empty_repo():
    with pytest.raises(ValueError, match="repo"):
        validate_new_target("ok", "134.169.42.227", "", existing=set())


def test_validate_rejects_name_collision_with_config_message():
    with pytest.raises(ValueError, match="runcontrol.toml"):
        validate_new_target("server", "134.169.42.227", "~/eqasim-bs", existing={"server"})


def test_validate_accepts_ssh_alias_and_user_at_ip_host():
    validate_new_target("felix2", "felix", "~/eqasim-bs", existing={"server"})
    validate_new_target("felix3", "user@134.169.42.227", "~/eqasim-bs", existing={"server"})


# ---- SshTarget.probe -----------------------------------------------------

def test_probe_success_parses_git_commit():
    ssh = FakeSsh(responses={"RC_PROBE_OK": (0, "RC_PROBE_OK\nabc1234\n")})
    t = SshTarget(_cfg(), run_command=ssh)
    result = t.probe()
    assert result == {"ok": True, "message": "connected", "git_commit": "abc1234"}


def test_probe_builds_batchmode_and_timeout_argv():
    ssh = FakeSsh(responses={"RC_PROBE_OK": (0, "RC_PROBE_OK\nabc1234\n")})
    t = SshTarget(_cfg(), run_command=ssh)
    t.probe()
    argv = ssh.calls[-1]
    assert "-o" in argv and "BatchMode=yes" in argv
    assert "ConnectTimeout=5" in argv


def test_probe_failure_never_raises_and_reports_message():
    ssh = FakeSsh(responses={"RC_PROBE_OK": (255, "ssh: connect to host port 22: Connection refused")})
    t = SshTarget(_cfg(), run_command=ssh)
    result = t.probe()
    assert result["ok"] is False
    assert result["git_commit"] is None
    assert "Connection refused" in result["message"] or "255" in result["message"]


def test_probe_failure_truncates_long_message():
    ssh = FakeSsh(responses={"RC_PROBE_OK": (1, "x" * 1000)})
    t = SshTarget(_cfg(), run_command=ssh)
    result = t.probe()
    assert result["ok"] is False
    assert len(result["message"]) <= 320
