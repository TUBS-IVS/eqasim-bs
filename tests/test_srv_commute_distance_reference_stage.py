"""Reference stage: loads the committed SrV distance tables (eqasim analysis/reference pattern)."""
import logging
import os

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


def _write_tables(srv_dir):
    """The four committed tables the stage loads (three targets + the sensitivity table)."""
    pd.DataFrame({"level_geo": ["zgb"], "code": ["zgb"], "n_persons": [10]}).to_csv(srv_dir / T.COMMUTE_TABLE, index=False)
    pd.DataFrame({"level_geo": ["zgb"], "code": ["zgb"], "education_level": ["kindergarten"]}).to_csv(srv_dir / T.EDUCATION_TABLE, index=False)
    pd.DataFrame({"level_geo": ["zgb"], "code": ["zgb"], "percentile": [50]}).to_csv(srv_dir / T.QUANTILE_TABLE, index=False)
    pd.DataFrame({"variant": ["inter_zgb", "all_gis_fallback"], "level_geo": ["zgb", "zgb"],
                  "code": ["zgb", "zgb"], "n_persons": [2301, 5174]}).to_csv(
        srv_dir / T.SENSITIVITY_TABLE, index=False)


def test_execute_loads_tables(tmp_path):
    srv_dir = tmp_path / "braunschweig" / "srv"
    srv_dir.mkdir(parents=True)
    _write_tables(srv_dir)
    out = stage.execute(FakeContext({"data_path": str(tmp_path)}))
    assert set(out) == {"commute", "education", "quantiles", "sensitivity", "srv_dir"}
    assert out["commute"]["code"].iloc[0] == "zgb"
    assert "n_persons" in out["commute"].columns
    assert "education_level" in out["education"].columns
    assert "percentile" in out["quantiles"].columns
    assert out["srv_dir"] == os.path.join(str(tmp_path), "braunschweig", "srv")


def test_execute_loads_the_sensitivity_table_and_logs_its_row_count(tmp_path, caplog):
    """Addendum Task 16: the SENSITIVITY table (not a target) is loaded alongside the three
    target tables, and its row count and variants are logged so a reader of the run log can
    tell which variants the downstream comparison had available."""
    srv_dir = tmp_path / "braunschweig" / "srv"
    srv_dir.mkdir(parents=True)
    _write_tables(srv_dir)
    with caplog.at_level(logging.INFO, logger="braunschweig.analysis.reference.srv.commute_distance"):
        out = stage.execute(FakeContext({"data_path": str(tmp_path)}))
    assert list(out["sensitivity"]["variant"]) == ["inter_zgb", "all_gis_fallback"]
    message = next(r.getMessage() for r in caplog.records if "sensitivity" in r.getMessage())
    assert "2 sensitivity" in message
    assert "all_gis_fallback, inter_zgb" in message


def test_execute_fails_loudly_when_table_missing(tmp_path):
    (tmp_path / "braunschweig" / "srv").mkdir(parents=True)
    with pytest.raises(FileNotFoundError, match="extract_srv_primary_distance_targets"):
        stage.execute(FakeContext({"data_path": str(tmp_path)}))


def test_execute_fails_loudly_when_only_the_sensitivity_table_is_missing(tmp_path):
    """The sensitivity table is a hard requirement of the stage, not an optional extra: a
    missing table must fail loudly (with the regeneration hint) rather than silently yield a
    run whose sensitivity section is absent."""
    srv_dir = tmp_path / "braunschweig" / "srv"
    srv_dir.mkdir(parents=True)
    _write_tables(srv_dir)
    (srv_dir / T.SENSITIVITY_TABLE).unlink()
    with pytest.raises(FileNotFoundError, match=T.SENSITIVITY_TABLE):
        stage.execute(FakeContext({"data_path": str(tmp_path)}))
