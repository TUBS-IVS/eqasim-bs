import importlib.util, pathlib
_P = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "diagnose_distance_fit.py"


def _load():
    spec = importlib.util.spec_from_file_location("diagnose_distance_fit", _P)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


def test_cli_module_exposes_main_and_build_parser():
    m = _load()
    assert hasattr(m, "main") and hasattr(m, "build_parser")
    args = m.build_parser().parse_args(["--working-directory", "wd", "--activity", "work"])
    assert args.activity == "work"
