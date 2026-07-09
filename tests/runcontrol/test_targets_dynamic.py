"""Task 14/15: dynamic ssh targets -- store roundtrip, validation, probe(), API + UI wiring."""
import json
import sys

import pytest
from fastapi.testclient import TestClient

from braunschweig.runcontrol.app import create_app
from braunschweig.runcontrol.daemon import QueueWorker
from braunschweig.runcontrol.db import Database
from braunschweig.runcontrol.settings import Settings, TargetConfig
from braunschweig.runcontrol.targets.local import LocalTarget
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


# ---- Task 15: dynamically added targets appear in the topbar vitals row ---

def test_added_target_shows_in_home_topbar_vitals(tmp_path, monkeypatch):
    """A target added via POST /api/targets must appear on the very next home render
    (same live `targets` dict as `_home_ctx`), without a server restart. Its vitals
    collection is forced to fail fast (FileNotFoundError instead of real ssh), so the
    row must still render -- with honest 'unknown' fields -- rather than be absent."""
    (tmp_path / "logs").mkdir()
    local_cfg = TargetConfig(name="local", kind="local", repo=str(tmp_path), runner="fake_runner.py")
    settings = Settings(db_path=tmp_path / "runs.db", targets={"local": local_cfg},
                        targets_store_path=tmp_path / "runcontrol_data" / "targets.json")
    db = Database(settings.db_path)
    targets = {"local": LocalTarget(local_cfg, python=sys.executable)}
    worker = QueueWorker(db, targets)
    c = TestClient(create_app(settings, db, worker, targets))

    monkeypatch.setattr(SshTarget, "probe",
                        lambda self: {"ok": True, "message": "connected", "git_commit": "abc123"})

    def _raise(self, *args, **kwargs):
        raise FileNotFoundError("no real ssh in tests")

    monkeypatch.setattr(SshTarget, "read_text_command", _raise)
    monkeypatch.setattr(SshTarget, "read_text", _raise)

    r = c.post("/api/targets", data={"name": "newbox", "host": "1.2.3.4", "repo": "~/eqasim-bs"})
    assert r.status_code == 200, r.text
    html = c.get("/").text
    assert "newbox:" in html                     # rendered in the topbar vitals row
    assert "unknown" in html                     # failed vitals shown honestly, not hidden


# ---- Security review fixes: C-1 repo quoting, I-1 robust store, M-1 messages

def test_ssh_command_quotes_repo_preserving_tilde_home():
    ssh = FakeSsh()
    t = SshTarget(_cfg(repo="~/eqasim-bs"), run_command=ssh)
    t.git_commit()
    assert ssh.calls[-1][-1].startswith('cd "$HOME"/eqasim-bs && ')


def test_ssh_command_quotes_repo_with_space():
    ssh = FakeSsh()
    t = SshTarget(_cfg(repo="/opt/x y"), run_command=ssh)
    t.git_commit()
    assert ssh.calls[-1][-1].startswith("cd '/opt/x y' && ")


def test_malicious_repo_is_neutralized_in_ssh_and_probe():
    # A repo like "~/x; rm -rf /tmp/z" must never yield an executable ";" on the
    # remote: the tail after "~" is shell-quoted, so the ";" stays inside quotes.
    import shlex
    ssh = FakeSsh()
    t = SshTarget(_cfg(repo="~/x; rm -rf /tmp/z"), run_command=ssh)
    expected = '"$HOME"' + shlex.quote("/x; rm -rf /tmp/z")
    t.git_commit()
    assert f"cd {expected} && " in ssh.calls[-1][-1]
    t.probe()
    assert f"cd {expected} && " in ssh.calls[-1][-1]


@pytest.mark.parametrize("repo", ["~/x; y", "a && b", "x`y", "/opt/x y"])
def test_validate_rejects_repo_shell_metacharacters(repo):
    with pytest.raises(ValueError, match="repo"):
        validate_new_target("ok", "1.2.3.4", repo, existing=set())


def test_validate_rejects_host_with_semicolon_regression():
    with pytest.raises(ValueError, match="host"):
        validate_new_target("ok", "1.2.3.4;id", "~/eqasim-bs", existing=set())


def test_validate_collision_message_distinguishes_config_and_user():
    existing = {"server", "mine"}
    with pytest.raises(ValueError, match="runcontrol.toml"):
        validate_new_target("server", "1.2.3.4", "~/x", existing=existing, config_names={"server"})
    with pytest.raises(ValueError, match="targets page"):
        validate_new_target("mine", "1.2.3.4", "~/x", existing=existing, config_names={"server"})


def test_load_corrupt_store_raises_actionable_valueerror(tmp_path):
    p = tmp_path / "targets.json"
    p.write_text('{"targets": "garbage"', encoding="utf-8")          # invalid JSON
    with pytest.raises(ValueError, match="targets.json"):
        load_dynamic_targets(p)
    p.write_text('{"nope": 1}', encoding="utf-8")                    # missing "targets" key
    with pytest.raises(ValueError, match="targets.json"):
        load_dynamic_targets(p)
    p.write_text('{"targets": "garbage"}', encoding="utf-8")         # wrong type for "targets"
    with pytest.raises(ValueError, match="targets.json"):
        load_dynamic_targets(p)
