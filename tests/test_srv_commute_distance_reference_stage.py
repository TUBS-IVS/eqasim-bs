"""Reference stage: loads the committed SrV distance tables (eqasim analysis/reference pattern)."""
import pandas as pd
import pytest

from braunschweig.analysis.reference.srv import commute_distance as stage
from braunschweig.calibration import srv_distance_targets as T


class FakeConfigurationContext:
    def __init__(self):
        self.required_config = {}
        self.declared_stages = []

    def config(self, key, *default):
        self.required_config[key] = default[0] if default else None

    def stage(self, name, *a, **k):
        self.declared_stages.append(name)


class FakeContext:
    def __init__(self, config):
        self._config = dict(config)

    def config(self, key):
        if key not in self._config:
            raise KeyError(key)
        return self._config[key]


def test_configure_declares_data_path_only():
    ctx = FakeConfigurationContext()
    stage.configure(ctx)
    assert "data_path" in ctx.required_config
    assert ctx.declared_stages == []


def test_execute_loads_tables(tmp_path):
    srv_dir = tmp_path / "braunschweig" / "srv"
    srv_dir.mkdir(parents=True)
    pd.DataFrame({"level_geo": ["zgb"], "code": ["zgb"], "n_persons": [10]}).to_csv(srv_dir / T.COMMUTE_TABLE, index=False)
    pd.DataFrame({"level_geo": ["zgb"], "code": ["zgb"], "education_level": ["kindergarten"]}).to_csv(srv_dir / T.EDUCATION_TABLE, index=False)
    pd.DataFrame({"level_geo": ["zgb"], "code": ["zgb"], "percentile": [50]}).to_csv(srv_dir / T.QUANTILE_TABLE, index=False)
    out = stage.execute(FakeContext({"data_path": str(tmp_path)}))
    assert set(out) == {"commute", "education", "quantiles", "srv_dir"}
    assert out["commute"]["code"].iloc[0] == "zgb"


def test_execute_fails_loudly_when_table_missing(tmp_path):
    (tmp_path / "braunschweig" / "srv").mkdir(parents=True)
    with pytest.raises(FileNotFoundError, match="extract_srv_primary_distance_targets"):
        stage.execute(FakeContext({"data_path": str(tmp_path)}))
