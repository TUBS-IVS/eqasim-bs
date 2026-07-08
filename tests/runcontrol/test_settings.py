from pathlib import Path

import pytest

from braunschweig.runcontrol.settings import load_settings

TOML = """
db_path = "runcontrol_data/runs.db"
host = "127.0.0.1"
port = 8099
poll_seconds = 3.0

[target.local]
kind = "local"
repo = "C:/repo"

[target.server]
kind = "ssh"
host = "felix"
repo = "~/eqasim-bs"
"""


def test_load_settings_parses_targets(tmp_path):
    p = tmp_path / "runcontrol.toml"
    p.write_text(TOML)
    s = load_settings(p)
    assert s.port == 8099
    assert s.host == "127.0.0.1"
    assert set(s.targets) == {"local", "server"}
    assert s.targets["server"].kind == "ssh"
    assert s.targets["server"].host == "felix"
    # defaults are filled in and documented
    assert s.targets["local"].runner == "scripts/run_synpp.py"
    assert s.targets["server"].runner == "scripts/run_pipeline.sh"
    assert s.targets["local"].logs_dir == "logs"
    assert s.targets["local"].data_dir == "eqasim-data"


def test_ssh_target_requires_host(tmp_path):
    p = tmp_path / "bad.toml"
    p.write_text('[target.x]\nkind = "ssh"\nrepo = "~/r"\n')
    with pytest.raises(ValueError, match="target 'x'.*host"):
        load_settings(p)


def test_missing_file_fails_early(tmp_path):
    with pytest.raises(FileNotFoundError, match="runcontrol.toml"):
        load_settings(tmp_path / "nope.toml")
