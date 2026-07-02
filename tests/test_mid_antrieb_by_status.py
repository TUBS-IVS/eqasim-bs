"""Tests for the MiD 2023 powertrain-by-economic-status extractor and loader.

Schema: columns status, powertrain, share, base_weighted
        share = P(powertrain | status), sums to 1.0 per status (incl. the
        pooled "all" row = overall MiD powertrain mix, the EV-income tilt's
        denominator, see Task B2).

Covers:
  * ``scripts.build_mid_antrieb_by_status.build()`` on a tiny synthetic
    MiD-Autos fixture CSV (A_ANTRIEB, A_GEW, oek_status): correct A_GEW-
    weighted P(powertrain | status), A_ANTRIEB 94/99 exclusion, invalid
    oek_status exclusion, the pooled "all" row, shares summing to 1.0, and the
    0-row abort.
  * ``braunschweig.data.kba.fleet_tables.load_mid_antrieb_by_status()`` on a
    synthetic derived CSV written into a tmp_path fixture (the real derived
    CSV is server-generated and not present locally): schema/label validation
    and the "all"-row presence check.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

import build_mid_antrieb_by_status as builder  # noqa: E402
from braunschweig.data.kba import fleet_tables as ft  # noqa: E402


# --------------------------------------------------------------------------- #
# Synthetic MiD-Autos fixture
# --------------------------------------------------------------------------- #
# Weighted composition per status (A_GEW as the weight):
#   very_low : 2x petrol (w=10 each) + 1x bev (w=5)            -> base 25
#   very_high: 1x petrol (w=5) + 3x bev (w=10 each)            -> base 35
#   plus rows excluded by A_ANTRIEB in {94, 99} and by an invalid oek_status.
_FIXTURE_ROWS: list[dict] = [
    # very_low: P(bev|very_low) = 5 / 25 = 0.2, P(petrol|very_low) = 20/25 = 0.8
    {"A_ANTRIEB": 1, "A_GEW": 10.0, "oek_status": 1},
    {"A_ANTRIEB": 1, "A_GEW": 10.0, "oek_status": 1},
    {"A_ANTRIEB": 5, "A_GEW": 5.0, "oek_status": 1},
    # very_high: P(bev|very_high) = 30 / 35 = 0.857142..., P(petrol|very_high) = 5/35
    {"A_ANTRIEB": 5, "A_GEW": 10.0, "oek_status": 5},
    {"A_ANTRIEB": 5, "A_GEW": 10.0, "oek_status": 5},
    {"A_ANTRIEB": 5, "A_GEW": 10.0, "oek_status": 5},
    {"A_ANTRIEB": 1, "A_GEW": 5.0, "oek_status": 5},
    # excluded: A_ANTRIEB unplausibel / keine Angabe
    {"A_ANTRIEB": 94, "A_GEW": 999.0, "oek_status": 1},
    {"A_ANTRIEB": 99, "A_GEW": 999.0, "oek_status": 5},
    # excluded: oek_status outside 1..5
    {"A_ANTRIEB": 2, "A_GEW": 999.0, "oek_status": 9},
]


def _write_fixture_csv(tmp_path: Path) -> Path:
    path = tmp_path / "MiD2023_Autos_fixture.csv"
    pd.DataFrame(_FIXTURE_ROWS).to_csv(path, index=False)
    return path


# --------------------------------------------------------------------------- #
# Extractor: build()
# --------------------------------------------------------------------------- #
class TestBuild:
    def test_weighted_bev_share_very_low(self, tmp_path):
        tidy = builder.build(_write_fixture_csv(tmp_path))
        row = tidy[(tidy["status"] == "very_low") & (tidy["powertrain"] == "bev")]
        assert len(row) == 1
        assert row["share"].iloc[0] == pytest.approx(5.0 / 25.0, abs=1e-9)
        assert row["base_weighted"].iloc[0] == pytest.approx(25.0, abs=1e-9)

    def test_weighted_bev_share_very_high(self, tmp_path):
        tidy = builder.build(_write_fixture_csv(tmp_path))
        row = tidy[(tidy["status"] == "very_high") & (tidy["powertrain"] == "bev")]
        assert len(row) == 1
        # share is rounded to 8 decimals in build(), so use a tolerance that
        # comfortably absorbs that rounding.
        assert row["share"].iloc[0] == pytest.approx(30.0 / 35.0, abs=1e-6)
        assert row["base_weighted"].iloc[0] == pytest.approx(35.0, abs=1e-6)

    def test_weighted_petrol_share_very_low(self, tmp_path):
        tidy = builder.build(_write_fixture_csv(tmp_path))
        row = tidy[(tidy["status"] == "very_low") & (tidy["powertrain"] == "petrol")]
        assert row["share"].iloc[0] == pytest.approx(20.0 / 25.0, abs=1e-9)

    def test_excluded_antrieb_codes_dropped(self, tmp_path):
        """A_ANTRIEB 94/99 rows (weight 999) must not leak into any share."""
        tidy = builder.build(_write_fixture_csv(tmp_path))
        # Total weighted base across all real statuses (excluding "all") must be
        # 25 + 35 = 60, NOT inflated by the two w=999 excluded-antrieb rows.
        real_status_base = (
            tidy[tidy["status"] != "all"]
            .drop_duplicates("status")["base_weighted"]
            .sum()
        )
        assert real_status_base == pytest.approx(60.0, abs=1e-9)

    def test_excluded_invalid_oek_status_dropped(self, tmp_path):
        """oek_status=9 (weight 999) must not create a 6th status group."""
        tidy = builder.build(_write_fixture_csv(tmp_path))
        assert set(tidy["status"]) == {"very_low", "very_high", "all"}

    def test_all_row_present_and_correct(self, tmp_path):
        """The 'all' row pools very_low + very_high (base 25 + 35 = 60)."""
        tidy = builder.build(_write_fixture_csv(tmp_path))
        all_rows = tidy[tidy["status"] == "all"]
        assert len(all_rows) > 0
        for value in all_rows["base_weighted"].tolist():
            assert value == pytest.approx(60.0, abs=1e-6)
        all_bev = all_rows[all_rows["powertrain"] == "bev"]
        # bev weighted count pooled = 5 + 30 = 35; base = 60 -> 35/60
        assert all_bev["share"].iloc[0] == pytest.approx(35.0 / 60.0, abs=1e-6)

    def test_shares_sum_to_one_per_status(self, tmp_path):
        tidy = builder.build(_write_fixture_csv(tmp_path))
        totals = tidy.groupby("status")["share"].sum()
        assert (abs(totals - 1.0) < 1e-6).all(), totals.to_dict()

    def test_powertrain_labels_are_canonical(self, tmp_path):
        tidy = builder.build(_write_fixture_csv(tmp_path))
        assert set(tidy["powertrain"]) <= set(builder.ANTRIEB_LABELS)

    def test_status_labels_are_canonical_or_all(self, tmp_path):
        tidy = builder.build(_write_fixture_csv(tmp_path))
        assert set(tidy["status"]) <= set(builder.STATUS_LABELS) | {"all"}

    def test_zero_rows_raises(self, tmp_path):
        """A CSV with only excluded rows must abort with a clear error."""
        rows = [
            {"A_ANTRIEB": 94, "A_GEW": 1.0, "oek_status": 1},
            {"A_ANTRIEB": 99, "A_GEW": 1.0, "oek_status": 2},
        ]
        path = tmp_path / "MiD2023_Autos_empty.csv"
        pd.DataFrame(rows).to_csv(path, index=False)
        with pytest.raises(RuntimeError, match="0 rows"):
            builder.build(path)


# --------------------------------------------------------------------------- #
# Loader: load_mid_antrieb_by_status()
# --------------------------------------------------------------------------- #
def _write_derived(tmp_path: Path, name: str, df: pd.DataFrame) -> str:
    """Write df as CSV into tmp_path/braunschweig/kba/derived/; return data_path str."""
    d = tmp_path / "braunschweig" / "kba" / "derived"
    d.mkdir(parents=True, exist_ok=True)
    df.to_csv(d / name, index=False)
    return str(tmp_path)


def _minimal_antrieb_by_status_rows() -> list[dict]:
    rows = []
    for status in (*ft.STATUS_LABELS, "all"):
        for i, powertrain in enumerate(builder.ANTRIEB_LABELS):
            rows.append({
                "status": status,
                "powertrain": powertrain,
                "share": 1.0 / len(builder.ANTRIEB_LABELS),
                "base_weighted": 100.0,
            })
    return rows


class TestLoader:
    def test_returns_dataframe_with_expected_columns(self, tmp_path):
        rows = _minimal_antrieb_by_status_rows()
        dp = _write_derived(tmp_path, "mid2023_antrieb_by_status.csv", pd.DataFrame(rows))
        df = ft.load_mid_antrieb_by_status(dp)
        assert isinstance(df, pd.DataFrame)
        required = {"status", "powertrain", "share", "base_weighted"}
        assert required.issubset(set(df.columns))

    def test_all_row_present(self, tmp_path):
        rows = _minimal_antrieb_by_status_rows()
        dp = _write_derived(tmp_path, "mid2023_antrieb_by_status.csv", pd.DataFrame(rows))
        df = ft.load_mid_antrieb_by_status(dp)
        assert "all" in set(df["status"])

    def test_missing_column_raises(self, tmp_path):
        rows = _minimal_antrieb_by_status_rows()
        df_raw = pd.DataFrame(rows).drop(columns=["base_weighted"])
        dp = _write_derived(tmp_path, "mid2023_antrieb_by_status.csv", df_raw)
        with pytest.raises(RuntimeError, match="base_weighted"):
            ft.load_mid_antrieb_by_status(dp)

    def test_invalid_powertrain_label_raises(self, tmp_path):
        rows = _minimal_antrieb_by_status_rows()
        rows[0]["powertrain"] = "unbekannt_antrieb"
        dp = _write_derived(tmp_path, "mid2023_antrieb_by_status.csv", pd.DataFrame(rows))
        with pytest.raises(RuntimeError, match="powertrain"):
            ft.load_mid_antrieb_by_status(dp)

    def test_missing_all_row_raises(self, tmp_path):
        """A derived CSV without the pooled 'all' row must raise (missing tilt denominator)."""
        rows = [r for r in _minimal_antrieb_by_status_rows() if r["status"] != "all"]
        dp = _write_derived(tmp_path, "mid2023_antrieb_by_status.csv", pd.DataFrame(rows))
        with pytest.raises(RuntimeError, match="all"):
            ft.load_mid_antrieb_by_status(dp)

    def test_missing_status_raises(self, tmp_path):
        """A derived CSV missing one of the 5 canonical statuses must raise."""
        rows = [r for r in _minimal_antrieb_by_status_rows() if r["status"] != "very_high"]
        dp = _write_derived(tmp_path, "mid2023_antrieb_by_status.csv", pd.DataFrame(rows))
        with pytest.raises(RuntimeError, match="very_high"):
            ft.load_mid_antrieb_by_status(dp)

    def test_file_not_found(self, tmp_path):
        dp = str(tmp_path)
        with pytest.raises(FileNotFoundError):
            ft.load_mid_antrieb_by_status(dp)
