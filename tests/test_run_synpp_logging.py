import os


def _run_synpp_source() -> str:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(root, "scripts", "run_synpp.py")
    return open(path, encoding="utf-8").read()


def test_run_synpp_uses_setup_logging():
    src = _run_synpp_source()
    assert "setup_logging" in src and "from braunschweig.logging_setup import" in src
    assert "basicConfig" not in src  # replaced
