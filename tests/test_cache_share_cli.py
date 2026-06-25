"""Tests for the scripts/cache_share.py CLI (export/prime wrapper)."""
import importlib.util
import os

_CLI_PATH = os.path.join(os.path.dirname(__file__), "..", "scripts", "cache_share.py")


def _load_cli():
    spec = importlib.util.spec_from_file_location("cache_share_cli", _CLI_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cli_export_then_prime(tmp_path):
    cli = _load_cli()
    wd = tmp_path / "wd"
    os.makedirs(wd)
    with open(wd / "m.x__h1.p", "wb") as f:
        f.write(b"x")
    store = tmp_path / "store"
    target = tmp_path / "t"

    rc = cli.main(["export", "--working-directory", str(wd), "--store", str(store),
                   "--modules", "m.x"])
    assert rc == 0
    assert (store / "m.x__h1.p").exists()

    rc2 = cli.main(["prime", "--working-directory", str(target), "--store", str(store),
                    "--modules", "m.x"])
    assert rc2 == 0
    assert (target / "m.x__h1.p").exists()


def test_cli_prime_recompute_skips(tmp_path):
    cli = _load_cli()
    store = tmp_path / "store"
    os.makedirs(store)
    with open(store / "m.x__h1.p", "wb") as f:
        f.write(b"x")
    target = tmp_path / "t"
    rc = cli.main(["prime", "--working-directory", str(target), "--store", str(store),
                   "--modules", "m.x", "--recompute", "m.x"])
    assert rc == 0
    assert not (target / "m.x__h1.p").exists()
