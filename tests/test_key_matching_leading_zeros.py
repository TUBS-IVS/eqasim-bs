"""Leading-zero safety for AGS/ARS/Kreis key loaders (project-wide key-matching audit).

pandas' int64 inference during ``read_csv`` strips leading zeros from a purely
numeric key column irreversibly; a later ``.astype(str)`` then just re-stringifies
the already-wrong value ("03101" -> 3101 -> "3101"), and every downstream
Kreis-key lookup silently misses (see the documented rationale in
``run_mid_validation._load_noise_bands``). Several loaders were only saved by the
*accident* that their CSVs carry a non-numeric region row ("03ZGB" / "Gesamt")
which forces object dtype. These tests pin dtype-at-READ-time semantics: a
numeric-looking ``ars5`` column must still come out as the zero-padded string,
independent of whether a region row is present.
"""
from __future__ import annotations

import pandas as pd
import pytest

from braunschweig.data.mid import references as mid_references
from braunschweig.data.mid import reference_tables as mid_reference_tables
from braunschweig.popsim import kreis_attribute_control as kac
from braunschweig.data.census import employment as census_employment


# ---------------------------------------------------------------------------
# braunschweig.data.mid.references._load_table
# ---------------------------------------------------------------------------

def _write_p9_like_csv(path, include_region_row: bool) -> None:
    rows = ["kreis,ars5,n_weighted,vollzeit"]
    if include_region_row:
        rows.append("Gesamt,03ZGB,4982.0,35.0")
    rows.append("Braunschweig,03101,1010.0,35.0")
    rows.append("Gifhorn,03151,300.0,30.0")
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def test_references_load_table_keeps_leading_zero_without_region_row(tmp_path):
    # Without the 03ZGB row the ars5 column is purely numeric; int inference
    # at read time must NOT strip the leading zero.
    path = tmp_path / "mid2023_P9.csv"
    _write_p9_like_csv(path, include_region_row=False)
    df = mid_references._load_table(str(path))
    assert set(df["ars5"]) == {"03101", "03151"}


def test_references_load_table_keeps_leading_zero_with_region_row(tmp_path):
    # Regression guard for the current (accidentally safe) file layout.
    path = tmp_path / "mid2023_P9.csv"
    _write_p9_like_csv(path, include_region_row=True)
    df = mid_references._load_table(str(path))
    assert set(df["ars5"]) == {"03ZGB", "03101", "03151"}


# ---------------------------------------------------------------------------
# braunschweig.analysis.run_mid_validation._load_mid
# ---------------------------------------------------------------------------

def test_run_mid_validation_load_mid_keeps_leading_zero(tmp_path, monkeypatch):
    run_mid_validation = pytest.importorskip(
        "braunschweig.analysis.run_mid_validation"
    )
    for code in ("P9", "P12_1", "P13", "P17_1"):
        _write_p9_like_csv(tmp_path / f"mid2023_{code}.csv",
                           include_region_row=False)
    monkeypatch.setattr(run_mid_validation, "MID_DIR", tmp_path)
    tables = run_mid_validation._load_mid()
    for code, df in tables.items():
        assert set(df["ars5"]) == {"03101", "03151"}, code


# ---------------------------------------------------------------------------
# braunschweig.data.mid.reference_tables.load_kreis_share_table
# ---------------------------------------------------------------------------

def _mid_dir(tmp_path):
    """Create the ``braunschweig/mid`` layout that reference_tables._path expects."""
    d = tmp_path / "braunschweig" / "mid"
    d.mkdir(parents=True, exist_ok=True)
    return d


def test_load_kreis_share_table_keeps_leading_zero(tmp_path):
    (_mid_dir(tmp_path) / "mid2023_H7_cars_by_kreis.csv").write_text(
        "# comment\nars5,0,1,2,3\nGesamt,0.2,0.4,0.3,0.1\n"
        "03101,0.25,0.45,0.2,0.1\n03151,0.1,0.4,0.35,0.15\n",
        encoding="utf-8",
    )
    by_kreis, region, values = mid_reference_tables.load_kreis_share_table(
        str(tmp_path), "mid2023_H7_cars_by_kreis.csv"
    )
    assert set(by_kreis) == {"03101", "03151"}
    assert region is not None
    assert list(values) == [0, 1, 2, 3]


def test_load_pt_subscription_breakdown_keeps_leading_zero(tmp_path):
    # PT_RAW_FIXTURE_OK: the loader reads the raw codebook-German column headers
    # (P24_RAW_COLUMN_BY_CATEGORY), the single boundary translation to the
    # PT_TICKET_CATEGORIES English names (issue #329) -- this fixture must match
    # that raw schema, not the English category names.
    cols = ",".join(
        mid_reference_tables.P24_RAW_COLUMN_BY_CATEGORY[c]
        for c in mid_reference_tables.PT_TICKET_CATEGORIES
    )
    n = len(mid_reference_tables.PT_TICKET_CATEGORIES)
    row = ",".join(["1.0"] * n)
    (_mid_dir(tmp_path) / "mid2023_P24_1.csv").write_text(
        f"kreis,ars5,{cols}\nGesamt,03ZGB,{row}\nBraunschweig,03101,{row}\n",
        encoding="utf-8",
    )
    by_kreis, region = mid_reference_tables.load_pt_subscription_breakdown(
        str(tmp_path)
    )
    assert set(by_kreis) == {"03101"}
    assert region is not None


# ---------------------------------------------------------------------------
# braunschweig.popsim.kreis_attribute_control.load_kreis_target
# ---------------------------------------------------------------------------

def test_load_kreis_target_keeps_leading_zero(tmp_path):
    ctl = kac.KreisAttributeControl(
        name="economic_status",
        seed_column="oek_status",
        level="household",
        categories=(("low", "== 1"), ("high", "== 2")),
        target_csv_relpath="target_test.csv",
        target_columns=("low", "high"),
        tier="soft",
    )
    (tmp_path / "target_test.csv").write_text(
        "# comment\nars5,source,n_effective,low,high\n"
        "Gesamt,mid,100,0.5,0.5\n03101,mid,50,0.4,0.6\n",
        encoding="utf-8",
    )
    out = kac.load_kreis_target(tmp_path, ctl, expected_ars5=["03101"])
    assert "03101" in set(out["ars5"])


# ---------------------------------------------------------------------------
# braunschweig.data.census.employment: silent-empty guard
# ---------------------------------------------------------------------------

class _StubContext:
    """Minimal synpp context stub for census employment execute()."""

    def __init__(self, codes: pd.DataFrame):
        self._codes = codes

    def config(self, key, default=None):
        return {
            "data_path": "unused",
            "braunschweig.employment_path": "unused.xlsx",
        }[key]

    def stage(self, name):
        assert name == "eqasim_common.spatial.codes"
        return self._codes


def _codes_frame() -> pd.DataFrame:
    return pd.DataFrame({"departement_id": ["03101"], "ags": ["03101000"]})


def _employment_frame(ags_values) -> pd.DataFrame:
    rows = []
    for ags in ags_values:
        for age_class in census_employment.AGE_CLASS_MAP:
            rows.append({
                "departement_id": ags,
                "department_name": "X",
                "age_class": age_class,
                "all_total": 10, "all_male": 5, "all_female": 5,
                "foreign_total": 0, "foreign_male": 0, "foreign_female": 0,
            })
    return pd.DataFrame(rows)


def test_employment_numeric_ags_raises_instead_of_silent_empty(monkeypatch):
    # A numeric AGS cell (3101 instead of "03101") fails the 5-char length
    # filter, so EVERY row is dropped. That must raise, not return an empty
    # marginal that downstream stages consume as "zero employment".
    monkeypatch.setattr(
        census_employment.pd, "read_excel",
        lambda *a, **k: _employment_frame([3101]),
    )
    with pytest.raises(RuntimeError, match="0 rows|empty|no Kreis"):
        census_employment.execute(_StubContext(_codes_frame()))


def test_employment_padded_ags_primary_path(monkeypatch):
    monkeypatch.setattr(
        census_employment.pd, "read_excel",
        lambda *a, **k: _employment_frame(["03101"]),
    )
    df = census_employment.execute(_StubContext(_codes_frame()))
    assert not df.empty
    assert set(df["departement_id"].astype(str)) == {"03101"}
