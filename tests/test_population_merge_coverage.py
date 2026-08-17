"""Fallback-transparency tests for the Gemeinde-share x Kreis-total merge in
``braunschweig.data.census.population`` (issue #163, item 1).

The Gemeinde x sex x age weight is built by multiplying a Gemeinde-within-Kreis
*share* frame by a Kreis-level DESTATIS/Zensus *total* frame on
``(kreis, sex, age)``. An inner merge silently drops the population of any
cell present on only one side, and the pre-existing post-hoc check
(``diff = (got - expected).abs().max()``) is NaN-blind: if a whole Kreis
vanishes from ``got`` the subtraction produces NaN for that Kreis and
``.max()`` skips it by default, so the loss is invisible.

These tests exercise the pure diagnostic helpers with small synthetic frames.
"""
from __future__ import annotations

import pandas as pd
import pytest

from braunschweig.data.census import population as pop


class TestMergeCellCoverage:
    def test_no_unmatched_cells_on_clean_data(self, capsys) -> None:
        df_shares = pd.DataFrame({
            "kreis": ["03101", "03101"],
            "sex": ["male", "female"],
            "u_age": [0, 0],
            "share": [0.5, 0.5],
        })
        df_destatis = pd.DataFrame({
            "kreis": ["03101", "03101"],
            "sex": ["male", "female"],
            "u_age": [0, 0],
            "weight": [100, 90],
        })
        pop._log_merge_cell_coverage(df_shares, df_destatis, ["kreis", "sex", "u_age"], "urbistat")
        out = capsys.readouterr().out
        assert "0 cells only in shares" in out
        assert "0 cells only in Kreis totals" in out

    def test_kreis_missing_from_destatis_side_is_reported(self, capsys) -> None:
        # 03102 is present in the shares (a Gemeinde exists) but the Kreis total
        # frame has no matching cell -- this cell will be silently dropped by an
        # inner merge without the diagnostic below.
        df_shares = pd.DataFrame({
            "kreis": ["03101", "03102"],
            "sex": ["male", "male"],
            "u_age": [0, 0],
            "share": [1.0, 1.0],
        })
        df_destatis = pd.DataFrame({
            "kreis": ["03101"],
            "sex": ["male"],
            "u_age": [0],
            "weight": [100],
        })
        pop._log_merge_cell_coverage(df_shares, df_destatis, ["kreis", "sex", "u_age"], "urbistat")
        out = capsys.readouterr().out
        assert "1 cells only in shares (dropped)" in out
        assert "('03102', 'male', 0)" in out


class TestPopulationPreservedByKreisNanSafe:
    def test_matching_kreis_sets_give_zero_diff(self) -> None:
        got = pd.Series({"03101": 100, "03102": 200})
        expected = pd.Series({"03101": 100, "03102": 200})
        diff = pop._check_population_preserved_by_kreis(got, expected)
        assert diff.max() == 0

    def test_vanished_kreis_is_not_hidden_as_nan(self) -> None:
        # 03102 has an expected total but is entirely absent from `got` because
        # every one of its cells was dropped by the merge. A naive
        # (got - expected).abs().max() would produce NaN for 03102 and .max()
        # (skipna=True by default) would report only the 03101 diff, hiding a
        # whole Kreis worth of lost population.
        got = pd.Series({"03101": 100})
        expected = pd.Series({"03101": 100, "03102": 5000})
        diff = pop._check_population_preserved_by_kreis(got, expected)
        assert diff.loc["03102"] == 5000
        assert diff.max() == 5000

    def test_diff_is_never_nan(self) -> None:
        got = pd.Series({"03101": 100})
        expected = pd.Series({"03102": 5000})
        diff = pop._check_population_preserved_by_kreis(got, expected)
        assert not diff.isna().any()


class TestExecuteMergeLossGuard:
    """End-to-end-ish check of the WARN/RAISE thresholds via the execute()
    logic reproduced against a minimal synthetic df_destatis/df_out pair,
    mirroring how execute() itself computes lost_fraction."""

    def test_clean_merge_has_zero_lost_fraction(self) -> None:
        got = pd.Series({"03101": 100})
        expected = pd.Series({"03101": 100})
        diff = pop._check_population_preserved_by_kreis(got, expected)
        lost_fraction = diff.sum() / expected.sum()
        assert lost_fraction == 0.0
        assert lost_fraction < pop.MERGE_LOST_POPULATION_WARN_FRACTION

    def test_large_loss_exceeds_raise_threshold(self) -> None:
        got = pd.Series({"03101": 100})
        expected = pd.Series({"03101": 100, "03102": 5000})
        diff = pop._check_population_preserved_by_kreis(got, expected)
        lost_fraction = diff.sum() / expected.sum()
        assert lost_fraction >= pop.MERGE_LOST_POPULATION_RAISE_FRACTION


class TestUrbistatNameDropRate:
    def _vg_frame(self) -> pd.DataFrame:
        return pd.DataFrame({
            "ARS": ["031010000001", "031010000002"],
            "GEN": ["Braunschweig", "Wolfsburg"],
            "EWZ": [250000, 120000],
        })

    def _urbistat_frame(self, extra_bad_name: bool) -> pd.DataFrame:
        rows = [
            {"kreis_ars": "03101", "name": "Braunschweig, Stadt", "age_class": "0 - 2 anni", "sex": "male", "count": 100},
            {"kreis_ars": "03101", "name": "Wolfsburg, Stadt", "age_class": "0 - 2 anni", "sex": "male", "count": 80},
        ]
        if extra_bad_name:
            rows.append({"kreis_ars": "03101", "name": "Nichtvorhanden, Stadt",
                         "age_class": "0 - 2 anni", "sex": "male", "count": 5})
        return pd.DataFrame(rows)

    def test_clean_names_no_warning(self, tmp_path, capsys) -> None:
        path = str(tmp_path / "urbistat_clean.csv")
        self._urbistat_frame(extra_bad_name=False).to_csv(path, index=False)
        pop._load_urbistat_shares(path, self._vg_frame())
        out = capsys.readouterr().out
        assert "Dropping" not in out
        assert "WARNING" not in out

    def test_unmatched_name_is_counted_and_reported(self, tmp_path, capsys) -> None:
        path = str(tmp_path / "urbistat_bad.csv")
        self._urbistat_frame(extra_bad_name=True).to_csv(path, index=False)
        pop._load_urbistat_shares(path, self._vg_frame())
        out = capsys.readouterr().out
        assert "Dropping 1/3" in out
        assert "WARNING" in out  # 1/3 = 33.3% far above the 5% threshold
