"""Diary plan match + plan-source facts wiring into completed_donor (Task 3, issue #365).

Companion to test_completed_donor_stage.py's byte-identity gate: these tests exercise the
diary_plan_match.reassign_diaryless_plan_sources / diary_facts.attach_plan_source_facts
wiring itself -- a person whose plan source has no realisable MiD diary (anzwege1 == 803
"Person ohne Wegeerfassung" WITH mobil == 1) gets remapped to a matched weekday donor, and
every person carries the eight src_* plan-source fact columns regardless.
"""
import pandas as pd
import pytest

from braunschweig.popsim import completed_donor as cd
from tests.test_completed_donor_stage import _write_mid_attribute_fixture


def _make_one_person_diaryless(mid_dir):
    persons_path = mid_dir / "MiD2023_Personen.csv"
    p = pd.read_csv(persons_path)
    p.loc[p.index[0], "anzwege1"] = 803
    p.loc[p.index[0], "mobil"] = 1
    p.to_csv(persons_path, index=False)
    return int(p.loc[p.index[0], "H_ID"]), int(p.loc[p.index[0], "P_ID"])


def test_diary_match_remaps_803_source_and_reports(tmp_path):
    _write_mid_attribute_fixture(tmp_path)
    hid, pid = _make_one_person_diaryless(tmp_path)
    result = cd.build_completed_donor(tmp_path, random_seed=1, seed_day_filter=None, weekend_plan_match_on=True,
                                      diary_trace_path=tmp_path / "trace.parquet")
    row = result.persons[(result.persons["H_ID"] == hid) & (result.persons["P_ID"] == pid)].iloc[0]
    assert (row["source_H_ID"], row["source_P_ID"]) != (hid, pid)
    assert result.diary_report.counts_by_reason.get("nodiary_mobile", 0) >= 1
    assert (tmp_path / "trace.parquet").exists()
    assert {"src_n_direct_legs", "src_ends_at_home", "src_n_rbw_legs"} <= set(result.persons.columns)


def test_diary_match_off_is_byte_identical_in_sources(tmp_path):
    _write_mid_attribute_fixture(tmp_path)
    _make_one_person_diaryless(tmp_path)
    off = cd.build_completed_donor(tmp_path, random_seed=1, seed_day_filter=None, weekend_plan_match_on=True,
                                   diary_plan_match_on=False)
    ref = cd.build_completed_donor(tmp_path, random_seed=1, seed_day_filter=None, weekend_plan_match_on=True,
                                   diary_plan_match_on=False, exclude_holiday_plan_sources=False)
    pd.testing.assert_frame_equal(off.persons[["H_ID", "P_ID", "source_H_ID", "source_P_ID"]],
                                  ref.persons[["H_ID", "P_ID", "source_H_ID", "source_P_ID"]])
    assert off.diary_report is None


def test_diary_match_requires_mobility_columns(tmp_path):
    """Pins the completed_donor guard itself (not some downstream library KeyError):
    the match string is the config-key hint from completed_donor's own error message,
    not just the column name "mobil" (which could also come from inside
    diary_plan_match's own, differently-worded checks)."""
    _write_mid_attribute_fixture(tmp_path)
    p = pd.read_csv(tmp_path / "MiD2023_Personen.csv").drop(columns=["mobil", "mobil_diff", "feiertag"])
    p.to_csv(tmp_path / "MiD2023_Personen.csv", index=False)
    with pytest.raises(KeyError, match="diary_plan_match: false"):
        cd.build_completed_donor(tmp_path, random_seed=1, seed_day_filter=None, weekend_plan_match_on=True)
