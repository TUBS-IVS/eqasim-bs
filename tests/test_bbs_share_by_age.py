"""Loader + wiring for the age-resolved BBS share (education gravity, 16-19).

The CSV (regionalstatistik 21211 BBS-by-age + 21111 Oberstufe-by-age extract,
schema ``age,bbs_pupils,oberstufe_pupils``) is optional: absent -> ``None`` with
an explicit log (the scalar ``education_bbs_share`` applies); present -> the
derived per-age shares activate ``bbs_share_vector``. A malformed table raises,
it must never silently degrade to the scalar.
"""
from __future__ import annotations

import pandas as pd
import pytest

from braunschweig.data.schools.bbs_share import load_bbs_share_by_age
from braunschweig.synthesis.locations import education_gravity


def _write(tmp_path, text):
    path = tmp_path / "nds_bbs_share_by_age.csv"
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_loads_shares_from_counts(tmp_path):
    path = _write(tmp_path, (
        "# Source: regionalstatistik 21211 + 21111, NDS, school year 2024/25\n"
        "age,bbs_pupils,oberstufe_pupils\n"
        "16,2000,8000\n"
        "17,6000,6000\n"
        "18,9000,3000\n"
        "19,9500,500\n"
    ))
    shares = load_bbs_share_by_age(path)
    assert shares == {16: 0.2, 17: 0.5, 18: 0.75, 19: 0.95}


def test_absent_file_returns_none_with_log(tmp_path, capsys):
    out = load_bbs_share_by_age(str(tmp_path / "missing.csv"))
    assert out is None
    log = capsys.readouterr().out
    assert "scalar education_bbs_share applies" in log


def test_missing_column_raises(tmp_path):
    path = _write(tmp_path, "age,bbs_pupils\n16,100\n")
    with pytest.raises(ValueError, match="missing columns"):
        load_bbs_share_by_age(path)


def test_duplicate_age_raises(tmp_path):
    path = _write(tmp_path, (
        "age,bbs_pupils,oberstufe_pupils\n16,1,1\n16,2,2\n"))
    with pytest.raises(ValueError, match="duplicated age"):
        load_bbs_share_by_age(path)


def test_zero_total_raises(tmp_path):
    path = _write(tmp_path, (
        "age,bbs_pupils,oberstufe_pupils\n16,0,0\n"))
    with pytest.raises(ValueError, match="zero pupils"):
        load_bbs_share_by_age(path)


def test_negative_count_raises(tmp_path):
    path = _write(tmp_path, (
        "age,bbs_pupils,oberstufe_pupils\n16,-1,10\n"))
    with pytest.raises(ValueError, match="negative pupil count"):
        load_bbs_share_by_age(path)


class _Ctx:
    def __init__(self, cfg):
        self._cfg = cfg

    def config(self, key, default=None):
        return self._cfg.get(key, default)


def test_resolve_prefers_inline_dict_over_csv(tmp_path):
    # An inline dict config wins; the CSV path is not even touched.
    ctx = _Ctx({
        "education_bbs_share_by_age": {17: 0.6},
        "data_path": str(tmp_path),
        "education_bbs_share_by_age_path": "does/not/exist.csv",
    })
    assert education_gravity._resolve_bbs_share_by_age(ctx) == {17: 0.6}


def test_resolve_loads_csv_when_inline_is_none(tmp_path):
    sub = tmp_path / "braunschweig" / "schools"
    sub.mkdir(parents=True)
    (sub / "nds_bbs_share_by_age.csv").write_text(
        "age,bbs_pupils,oberstufe_pupils\n16,1,3\n", encoding="utf-8")
    ctx = _Ctx({
        "education_bbs_share_by_age": None,
        "data_path": str(tmp_path),
        "education_bbs_share_by_age_path":
            "braunschweig/schools/nds_bbs_share_by_age.csv",
    })
    assert education_gravity._resolve_bbs_share_by_age(ctx) == {16: 0.25}


def test_resolve_none_when_csv_absent(tmp_path):
    ctx = _Ctx({
        "education_bbs_share_by_age": None,
        "data_path": str(tmp_path),
        "education_bbs_share_by_age_path":
            "braunschweig/schools/nds_bbs_share_by_age.csv",
    })
    assert education_gravity._resolve_bbs_share_by_age(ctx) is None


def test_bbs_share_vector_uses_age_overrides():
    ages = pd.Series([16, 17, 18, 19, 16])
    shares = education_gravity.bbs_share_vector(
        ages, {16: 0.2, 17: 0.5, 18: 0.75, 19: 0.95}, 0.681)
    assert list(shares) == [0.2, 0.5, 0.75, 0.95, 0.2]
