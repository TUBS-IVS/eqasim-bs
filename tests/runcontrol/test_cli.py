from braunschweig.runcontrol.__main__ import build_parser, cmd_status
from braunschweig.runcontrol.db import Database
from braunschweig.runcontrol.models import RunSpec, RunStatus


def test_parser_has_serve_and_status():
    p = build_parser()
    args = p.parse_args(["serve", "--config", "x.toml"])
    assert args.command == "serve" and args.config == "x.toml"
    args = p.parse_args(["status", "--config", "x.toml"])
    assert args.command == "status"


def test_cmd_status_prints_runs(tmp_path, capsys):
    db = Database(tmp_path / "runs.db")
    db.insert_run(RunSpec("r1", "local", "demo", "c.yml"), RunStatus.DONE)
    cmd_status(db)
    out = capsys.readouterr().out
    assert "demo" in out and "done" in out
