"""Tests for the Zensus 2022 Gemeinde-share path of the population margin.

Item #5: replace the scraped, non-redistributable urbistat Gemeinde shares with
open Zensus 2022 1000A-3082. Verify the pure share helper preserves per-Kreis
totals (shares sum to 1) and that the DESTATIS->Zensus age mapping fixes the
school-age band smear (DESTATIS 10 -> Zensus 10-15, not urbistat 12-17).

This module also covers the DESTATIS loader's fallback transparency: an
unparseable (sex, age_class) cell is dropped (a silent population shrink unless
counted/logged), so ``_load_destatis`` must count PRIMARY (parsed) vs FALLBACK
(skipped) cells, warn above a small threshold, and raise on an implausibly high
drop rate (CLAUDE.md "Fallback transparency").
"""
from __future__ import annotations

import os

import pandas as pd
import pytest

from braunschweig.data.census import population as pop


def _zensus_frame() -> pd.DataFrame:
    # One Kreis 03101 with two communes; the households_size_age schema
    # (persons per commune x sex x ALTKL2 band x hh_size).
    return pd.DataFrame([
        # commune,        sex,    lower_age, upper_age, hh_size, weight
        ["031010000001", "male",   10, 15, "5", 60.0],
        ["031010000001", "male",   10, 15, "2", 20.0],   # commune1 children: 80
        ["031010000002", "male",   10, 15, "4", 20.0],   # commune2 children: 20
        ["031010000001", "female", 75, 150, "1", 30.0],
        ["031010000002", "female", 75, 150, "1", 10.0],
    ], columns=["commune_id", "sex", "lower_age", "upper_age", "hh_size", "weight"])


class TestDestatisToZensusAge:
    def test_school_age_band_maps_to_native_zensus_band(self) -> None:
        # The urbistat path smeared DESTATIS 10 (ages 10-14) into 12-17; the
        # Zensus path maps it to the native 10-15 band.
        assert pop.DESTATIS_TO_ZENSUS_AGE[10] == 10
        assert pop.DESTATIS_TO_URBISTAT_AGE[10] == 12  # the legacy smear

    def test_every_destatis_class_maps_to_a_zensus_band(self) -> None:
        for d in pop.DESTATIS_AGES:
            assert pop.DESTATIS_TO_ZENSUS_AGE[d] in pop.ZENSUS_AGES


class TestLoadZensusShares:
    def test_shares_sum_to_one_per_kreis_sex_band(self) -> None:
        shares = pop._load_zensus_shares(_zensus_frame(), {"03101"})
        grp = shares.groupby(["kreis", "sex", "z_age"])["share"].sum()
        assert grp.loc[("03101", "male", 10)] == pytest.approx(1.0)
        assert grp.loc[("03101", "female", 75)] == pytest.approx(1.0)

    def test_aggregates_over_hh_size(self) -> None:
        shares = pop._load_zensus_shares(_zensus_frame(), {"03101"})
        # commune1 has 60+20=80 of 100 male children -> share 0.8.
        c1 = shares[(shares["commune_id"] == "031010000001")
                    & (shares["sex"] == "male") & (shares["z_age"] == 10)]
        assert c1["share"].iloc[0] == pytest.approx(0.8)

    def test_carries_twelve_digit_commune_id_no_name_match(self) -> None:
        shares = pop._load_zensus_shares(_zensus_frame(), {"03101"})
        assert set(shares["commune_id"]) == {"031010000001", "031010000002"}

    def test_out_of_scope_kreis_dropped(self) -> None:
        shares = pop._load_zensus_shares(_zensus_frame(), {"03999"})
        assert shares.empty

    def test_per_kreis_total_preserved_when_combined_with_destatis(self) -> None:
        # Multiply DESTATIS Kreis totals by the shares: the per-Kreis sum must
        # equal the DESTATIS total (shares sum to 1 per kreis x sex x band).
        shares = pop._load_zensus_shares(_zensus_frame(), {"03101"})
        # DESTATIS: 100 male children (d_age 10) + 40 female elderly (d_age 75).
        destatis = pd.DataFrame([
            ["03101", "male", 10, 100.0],
            ["03101", "female", 75, 40.0],
        ], columns=["kreis", "sex", "z_age", "destatis_weight"])
        m = shares.merge(destatis, on=["kreis", "sex", "z_age"])
        m["weight"] = m["share"] * m["destatis_weight"]
        assert m["weight"].sum() == pytest.approx(140.0)


# Number of leading rows the loader skips, and the column layout it reads:
# col 0 is the Kreis AGS, the 17 male age-class counts sit at columns
# 2, 4, ..., 34 and the 17 female counts at 36, 38, ..., 68 (a 69-column row).
_DESTATIS_SKIPROWS = 6
_DESTATIS_NUM_COLUMNS = 36 + 2 * len(pop.DESTATIS_AGES)  # 70 -> indices 0..69


def _destatis_row(ags: str, male: int, female: int,
                  bad_male_index: int | None = None) -> list[str]:
    """Build one DESTATIS-export data row with constant per-cell counts.

    Every (sex, age_class) cell carries ``male`` / ``female``. If
    ``bad_male_index`` is given, that male age-class column is replaced by the
    DESTATIS suppression placeholder ``"-"`` so the loader fails to parse it and
    drops the (male+female) cell for that age class.
    """
    row = [""] * _DESTATIS_NUM_COLUMNS
    row[0] = ags
    for i in range(len(pop.DESTATIS_AGES)):
        row[2 + 2 * i] = str(male)
        row[36 + 2 * i] = str(female)
    if bad_male_index is not None:
        row[2 + 2 * bad_male_index] = "-"  # DESTATIS suppressed-value placeholder
    return row


def _write_destatis_csv(tmp_path, data_rows: list[list[str]]) -> str:
    """Write a DESTATIS-shaped semicolon CSV with 6 ignored header rows."""
    path = os.path.join(str(tmp_path), "destatis_test.csv")
    lines = [f"header_line_{n}" for n in range(_DESTATIS_SKIPROWS)]
    lines += [";".join(cells) for cells in data_rows]
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    return path


class TestLoadDestatisFallbackTransparency:
    """A malformed DESTATIS cell must be counted/warned, not silently dropped."""

    def test_all_cells_parse_when_frame_is_clean(self, tmp_path, capsys) -> None:
        path = _write_destatis_csv(tmp_path, [_destatis_row("03101", 100, 90)])
        df = pop._load_destatis(path, {"03101"})

        # 17 age classes x 2 sexes = 34 rows, every cell parsed.
        assert len(df) == 2 * len(pop.DESTATIS_AGES)
        assert df["weight"].sum() == len(pop.DESTATIS_AGES) * (100 + 90)
        out = capsys.readouterr().out
        assert "primary 17/17 (100.0%)" in out
        assert "skipped 0 (0.0%)" in out
        assert "WARNING" not in out

    def test_one_malformed_cell_is_counted_not_silently_dropped(
            self, tmp_path, capsys) -> None:
        # Two Kreise so one bad cell is a small (1/34) share. The dropped cell
        # must be COUNTED and reported -- the core fallback-transparency fix --
        # while every other parsed value stays exactly as it was.
        rows = [
            _destatis_row("03101", 100, 90, bad_male_index=4),  # one bad cell
            _destatis_row("03102", 100, 90),
        ]
        path = _write_destatis_csv(tmp_path, rows)
        df = pop._load_destatis(path, {"03101", "03102"})

        n_age = len(pop.DESTATIS_AGES)
        # 2 Kreise x 17 cells = 34 cells; exactly one dropped -> 33 parsed.
        assert len(df) == 2 * (2 * n_age - 1)  # 33 cells -> 66 rows
        out = capsys.readouterr().out
        assert f"primary {2 * n_age - 1}/{2 * n_age}" in out  # 33/34
        assert "skipped 1" in out  # the drop is surfaced, not swallowed

        # The dropped cell is the d_age 15 class for Kreis 03101 only; every
        # other (kreis, age_class) cell still parsed to its real value.
        dropped_age = pop.DESTATIS_AGES[4]
        kept = df[(df["kreis"] == "03101") & (df["age_class"] == dropped_age)]
        assert kept.empty
        # All surviving counts are unchanged (no parsed value was altered).
        assert set(df["weight"].unique()) == {100, 90}
        # 03102 is fully intact.
        assert len(df[df["kreis"] == "03102"]) == 2 * n_age

    def test_skip_share_above_warn_threshold_emits_warning(
            self, tmp_path, capsys) -> None:
        # Three bad male cells out of 17 (single Kreis) = 17.6% > 5% WARN floor
        # but < 50% raise limit: a WARNING must be printed, parsing continues.
        rows = [_destatis_row("03101", 100, 90)]
        row = rows[0]
        for bad in (2, 5, 9):  # three suppressed male age-class cells
            row[2 + 2 * bad] = "-"
        path = _write_destatis_csv(tmp_path, rows)
        df = pop._load_destatis(path, {"03101"})

        n_age = len(pop.DESTATIS_AGES)
        assert len(df) == 2 * (n_age - 3)  # 14 cells kept -> 28 rows
        out = capsys.readouterr().out
        assert "WARNING" in out
        assert "skipped 3" in out
        assert set(df["weight"].unique()) == {100, 90}  # no value altered

    def test_implausibly_high_skip_rate_raises_on_partial_failure(
            self, tmp_path) -> None:
        # 9 of 17 male cells malformed in BOTH in-scope Kreise -> 18/34 = 52.9%
        # > 50% raise limit. Some cells still parse, so `rows` is non-empty and
        # the all-empty guard does NOT pre-empt: the skip-rate raise is what
        # fires, proving a partial parse failure cannot silently shrink the
        # population.
        rows = []
        for ags in ("03101", "03102"):
            row = _destatis_row(ags, 100, 90)
            for bad in range(9):  # 9 suppressed male cells per Kreis
                row[2 + 2 * bad] = "-"
            rows.append(row)
        path = _write_destatis_csv(tmp_path, rows)
        with pytest.raises(RuntimeError, match="implausibly high"):
            pop._load_destatis(path, {"03101", "03102"})

    def test_all_empty_still_raises_no_matching_kreis(self, tmp_path) -> None:
        # The pre-existing all-empty guard is preserved: no in-scope Kreis at all.
        path = _write_destatis_csv(tmp_path, [_destatis_row("09999", 100, 90)])
        with pytest.raises(RuntimeError, match="No DESTATIS rows matched"):
            pop._load_destatis(path, {"03101"})
