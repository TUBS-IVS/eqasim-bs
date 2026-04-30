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
