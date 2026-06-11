import gzip
import pytest

from braunschweig.data.freight import german_wide


class FakeContext:
    def __init__(self, configs):
        self._configs = configs
    def config(self, key, default=None):
        return self._configs.get(key, default)


def _make_files(tmp_path):
    freight_dir = tmp_path / "braunschweig" / "freight" / "german-wide-freight-v3"
    freight_dir.mkdir(parents=True)
    for name in ("german_freight.100pct.plans.xml.gz", "germany-europe-network.xml.gz"):
        with gzip.open(freight_dir / name, "wb") as f:
            f.write(b"<dummy/>")
    return tmp_path


def test_execute_returns_paths_when_files_present(tmp_path):
    data_path = _make_files(tmp_path)
    context = FakeContext({
        "data_path": str(data_path),
        "freight_plans_path": german_wide.DEFAULT_PLANS_PATH,
        "freight_network_path": german_wide.DEFAULT_NETWORK_PATH,
    })
    result = german_wide.execute(context)
    assert result["plans_path"].endswith("german_freight.100pct.plans.xml.gz")
    assert result["network_path"].endswith("germany-europe-network.xml.gz")


def test_execute_fails_early_with_download_instructions(tmp_path):
    context = FakeContext({
        "data_path": str(tmp_path),
        "freight_plans_path": german_wide.DEFAULT_PLANS_PATH,
        "freight_network_path": german_wide.DEFAULT_NETWORK_PATH,
    })
    with pytest.raises(RuntimeError, match="download_german_wide_freight"):
        german_wide.execute(context)


def test_execute_rejects_non_gzip_file(tmp_path):
    data_path = _make_files(tmp_path)
    bad = (data_path / "braunschweig" / "freight" / "german-wide-freight-v3"
           / "german_freight.100pct.plans.xml.gz")
    bad.write_bytes(b"not gzip")
    context = FakeContext({
        "data_path": str(data_path),
        "freight_plans_path": german_wide.DEFAULT_PLANS_PATH,
        "freight_network_path": german_wide.DEFAULT_NETWORK_PATH,
    })
    with pytest.raises(RuntimeError, match="not a valid gzip"):
        german_wide.execute(context)
