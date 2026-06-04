"""Unit tests for ``braunschweig.analysis.run_mid_validation``.

These tests exercise the pure helpers (``band_share``, ``_df_to_markdown``,
``_bool_share``) plus the CLI argument parser.  The full ``run`` pipeline
needs the eqasim CSV/GPKG outputs of an actual run and is therefore not
covered by unit tests; it is exercised manually after every simulation
via ``python -m braunschweig.analysis.run_mid_validation``.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from braunschweig.analysis import run_mid_validation as rmv


class TestBandShare:
    def test_returns_zero_dict_for_empty_input(self) -> None:
        result = rmv.band_share(np.array([]))
        assert set(result.keys()) == {name for _, _, name in rmv.BANDS}
        assert all(v == 0.0 for v in result.values())

    def test_shares_sum_to_100_for_finite_distances(self) -> None:
        # One value in each band.
        distances = np.array([0.1, 1.0, 7.0, 15.0, 25.0, 40.0, 70.0, 150.0])
        result = rmv.band_share(distances)
        # Each band gets exactly one of eight observations -> 12.5 %.
        for value in result.values():
            assert value == pytest.approx(12.5)
        assert sum(result.values()) == pytest.approx(100.0)

    def test_ignores_nan_values(self) -> None:
        result = rmv.band_share(np.array([np.nan, 1.0, np.nan]))
        # The single 1.0 km lands in d_0_5 -> 100 % of valid obs.
        assert result["d_0_5"] == pytest.approx(100.0)


class TestBoolShare:
    def test_recognises_string_truthy_values(self) -> None:
        s = pd.Series(["True", "false", "1", "0", "yes"])
        assert rmv._bool_share(s) == pytest.approx(60.0)

    def test_returns_nan_for_empty_series(self) -> None:
        assert np.isnan(rmv._bool_share(pd.Series([], dtype=str)))


class TestDfToMarkdown:
    def test_renders_header_and_rows(self) -> None:
        df = pd.DataFrame({"a": [1.5, 2.0], "b": ["x", "y"]})
        md = rmv._df_to_markdown(df)
        lines = md.splitlines()
        assert lines[0] == "| a | b |"
        assert lines[1] == "| --- | --- |"
        assert lines[2] == "| 1.50 | x |"
        assert lines[3] == "| 2.00 | y |"

    def test_handles_empty_dataframe(self) -> None:
        assert rmv._df_to_markdown(pd.DataFrame()) == "(no rows)"


class TestEducationLevelForAge:
    def test_maps_t43_age_bands_to_levels(self) -> None:
        assert rmv.education_level_for_age(4) == "kindergarten"
        assert rmv.education_level_for_age(8) == "grundschule"
        assert rmv.education_level_for_age(12) == "sekundar_1"
        assert rmv.education_level_for_age(16) == "oberstufe"

    def test_returns_none_outside_t43_scope(self) -> None:
        # BBS / university (18+) and infants below school age have no T43 target.
        assert rmv.education_level_for_age(19) is None
        assert rmv.education_level_for_age(25) is None
        assert rmv.education_level_for_age(np.nan) is None


class TestEducationDistanceTable:
    def test_means_and_delta_vs_t43_target(self) -> None:
        # T43 routed km for RS7 72; with detour 1.3 the grundschule straight-line
        # target is 3.9 / 1.3 = 3.0 km.
        t43_raw = pd.DataFrame({
            "regiostar7": [72],
            "km_0_6": [2.0], "km_7_10": [3.9],
            "km_11_13": [6.5], "km_14_17": [10.0],
        })
        edu = pd.DataFrame({
            "regiostar7": [72, 72],
            "level": ["grundschule", "grundschule"],
            "distance_km": [2.0, 4.0],  # mean 3.0
        })
        table = rmv._education_distance_table(edu, t43_raw, detour_factor=1.3)
        row = table[table["level"] == "grundschule"].iloc[0]
        assert int(row["n_pupils"]) == 2
        assert row["mean_synthetic_km"] == pytest.approx(3.0)
        assert row["target_km"] == pytest.approx(3.0)
        assert row["delta_km"] == pytest.approx(0.0)

    def test_skips_levels_without_a_target(self) -> None:
        t43_raw = pd.DataFrame({
            "regiostar7": [72], "km_0_6": [2.0], "km_7_10": [3.9],
            "km_11_13": [6.5], "km_14_17": [10.0],
        })
        # bbs has no T43 target -> excluded from the comparison table.
        edu = pd.DataFrame({
            "regiostar7": [72], "level": ["bbs"], "distance_km": [15.0],
        })
        table = rmv._education_distance_table(edu, t43_raw, detour_factor=1.3)
        assert table.empty


class TestModeShare:
    def test_returns_empty_dict_for_empty_series(self) -> None:
        assert rmv.mode_share(pd.Series([], dtype=str)) == {}

    def test_shares_sum_to_100_and_are_percentages(self) -> None:
        # 2x car, 1x pt, 1x walk -> 50 / 25 / 25.
        modes = pd.Series(["car", "car", "pt", "walk"])
        result = rmv.mode_share(modes)
        assert result["car"] == pytest.approx(50.0)
        assert result["pt"] == pytest.approx(25.0)
        assert result["walk"] == pytest.approx(25.0)
        assert sum(result.values()) == pytest.approx(100.0)

    def test_ignores_nan_values(self) -> None:
        # NaN must not inflate the denominator: 1x car, 1x pt over 2 valid obs.
        modes = pd.Series(["car", np.nan, "pt"])
        result = rmv.mode_share(modes)
        assert result["car"] == pytest.approx(50.0)
        assert result["pt"] == pytest.approx(50.0)
        assert sum(result.values()) == pytest.approx(100.0)
        # NaN is dropped, not reported as a key.
        assert all(isinstance(k, str) for k in result)


class TestModeShareTable:
    def _trips(self) -> pd.DataFrame:
        # Five trips: 3 car, 1 pt, 1 walk; two of them are work commutes.
        return pd.DataFrame(
            {
                "mode": ["car", "car", "pt", "walk", "car"],
                "following_purpose": ["work", "work", "home", "leisure", "shop"],
            }
        )

    def test_all_trips_table_sums_to_100(self) -> None:
        all_tbl, _, _ = rmv._mode_share_table(self._trips(), mid_p12_1=None)
        assert set(all_tbl["mode"]) == {"car", "pt", "walk"}
        assert all_tbl["share_pct"].sum() == pytest.approx(100.0)
        car = all_tbl.loc[all_tbl["mode"] == "car", "share_pct"].iloc[0]
        assert car == pytest.approx(60.0)

    def test_by_purpose_table_each_purpose_sums_to_100(self) -> None:
        _, by_purpose, _ = rmv._mode_share_table(self._trips(), mid_p12_1=None)
        for _, sub in by_purpose.groupby("following_purpose"):
            assert sub["share_pct"].sum() == pytest.approx(100.0)
        work = by_purpose[by_purpose["following_purpose"] == "work"]
        # Both work trips are by car -> 100 %.
        assert work.loc[work["mode"] == "car", "share_pct"].iloc[0] == pytest.approx(100.0)

    def test_commute_vs_p12_1_compares_only_work_trips(self) -> None:
        # Minimal P12_1 ZGB-total row (auto/oeffentlich/fahrrad/zu_fuss in percent).
        mid_p12_1 = pd.DataFrame(
            {
                "ars5": ["03ZGB"],
                "auto": [73.0],
                "oeffentlich": [11.0],
                "fahrrad": [26.0],
                "zu_fuss": [14.0],
            }
        )
        _, _, commute_cmp = rmv._mode_share_table(self._trips(), mid_p12_1=mid_p12_1)
        # Both work trips are car -> synthetic Car = 100, MiD Car = 73.
        car_row = commute_cmp[commute_cmp["mode"] == "Car"].iloc[0]
        assert car_row["synthetic_pct"] == pytest.approx(100.0)
        assert car_row["mid_pct"] == pytest.approx(73.0)
        # The four canonical MiD modes are always reported.
        assert set(commute_cmp["mode"]) == {"Car", "PT", "Bicycle", "Walk"}

    def test_handles_missing_mode_column(self) -> None:
        # Pipeline trips.csv has no mode column -> empty tables, no crash.
        trips = pd.DataFrame({"following_purpose": ["work", "home"]})
        all_tbl, by_purpose, commute_cmp = rmv._mode_share_table(trips, mid_p12_1=None)
        assert all_tbl.empty
        assert by_purpose.empty
        assert commute_cmp.empty


class TestArgParser:
    def test_requires_existing_output_dir(self, tmp_path: Path) -> None:
        with pytest.raises(SystemExit):
            rmv._parse_args(["--output-dir", str(tmp_path / "nope")])

    def test_auto_detects_prefix_and_default_output(self, tmp_path: Path) -> None:
        out = tmp_path / "run"
        out.mkdir()
        (out / "demo_persons.csv").write_text("person_id\n1\n", encoding="utf-8")
        args = rmv._parse_args(["--output-dir", str(out)])
        assert args.prefix == "demo_"
        assert args.label == "demo"
        assert args.analysis_out == out / "analysis" / "mid_validation"
        assert args.analysis_out.is_dir()

    def test_explicit_prefix_and_label(self, tmp_path: Path) -> None:
        out = tmp_path / "run"
        out.mkdir()
        analysis = tmp_path / "anywhere"
        args = rmv._parse_args(
            [
                "--output-dir",
                str(out),
                "--prefix",
                "x_",
                "--analysis-out",
                str(analysis),
                "--label",
                "custom",
            ]
        )
        assert args.prefix == "x_"
        assert args.label == "custom"
        assert args.analysis_out == analysis.resolve()
        assert args.analysis_out.is_dir()

    def test_errors_when_no_persons_csv_and_no_prefix(self, tmp_path: Path) -> None:
        out = tmp_path / "empty"
        out.mkdir()
        with pytest.raises(SystemExit):
            rmv._parse_args(["--output-dir", str(out)])
