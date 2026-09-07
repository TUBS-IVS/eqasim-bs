"""Diary plan match + plan-source facts wiring into completed_donor (Task 3, issue #365).

Companion to test_completed_donor_stage.py's byte-identity gate: these tests exercise the
diary_plan_match.reassign_diaryless_plan_sources / diary_facts.attach_plan_source_facts
wiring itself -- a person whose plan source has no realisable MiD diary (anzwege1 == 803
"Person ohne Wegeerfassung" WITH mobil == 1) gets remapped to a matched weekday donor, and
every person carries the eight src_* plan-source fact columns regardless.

The last test in this file closes the loop to the PopulationSim seed (controller ruling
R18): the two 803 populations the match treats differently -- remapped (mobil == 1) and
KEPT (mobil != 1) -- must both survive mid.project_completed_seed's
forbid_no_diary_sources guard, which previously raised on every kept-immobile source and
would have aborted a production run.
"""
import logging

import numpy as np
import pandas as pd
import pytest

from braunschweig.popsim import completed_donor as cd
from braunschweig.popsim import mid, sources
from braunschweig.popsim.kreis_attribute_control import REGISTRY
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


def _make_immobile_and_mobile_diaryless(mid_dir):
    """Turn two fixture donors into the two 803 populations of the design spec.

    The FIRST person becomes "not mobile, no diary" (anzwege1 803, mobil 0) and loses
    their Wege rows, which is what an immobile reporting day looks like: diary_plan_match
    KEEPS such a source (REASON_KEEP_IMMOBILE, "zero trips is the observed day"). The
    SECOND becomes "mobile, no diary" (anzwege1 803, mobil 1), which the match REMAPS.
    Returns both (H_ID, P_ID) keys.
    """
    persons_path = mid_dir / "MiD2023_Personen.csv"
    p = pd.read_csv(persons_path)
    p.loc[p.index[0], ["anzwege1", "mobil"]] = [803, 0]
    p.loc[p.index[1], ["anzwege1", "mobil"]] = [803, 1]
    p.to_csv(persons_path, index=False)
    immobile = (int(p.loc[p.index[0], "H_ID"]), int(p.loc[p.index[0], "P_ID"]))
    mobile = (int(p.loc[p.index[1], "H_ID"]), int(p.loc[p.index[1], "P_ID"]))

    wege_path = mid_dir / "MiD2023_Wege.csv"
    w = pd.read_csv(wege_path)
    w = w[~((w["H_ID"] == immobile[0]) & (w["P_ID"] == immobile[1]))]
    w.to_csv(wege_path, index=False)
    return immobile, mobile


def test_kept_immobile_803_source_survives_the_seed_guard_and_seeds_class_zero(tmp_path):
    """End-to-end (ruling R18): completed_donor -> project_completed_seed with the guard on.

    The kept-immobile source must NOT raise (the pre-fix guard raised on ANY surviving
    803, aborting popsim.stage on real data although diary_plan_match was working as
    designed) and must be seeded as class 0 -- its plan is empty, so the control must ask
    for zero trips rather than an age-band imputation. The remapped mobile-803 source must
    not raise either, and no longer carries a diary code at all.
    """
    _write_mid_attribute_fixture(tmp_path)
    immobile, mobile = _make_immobile_and_mobile_diaryless(tmp_path)
    result = cd.build_completed_donor(
        tmp_path, random_seed=1, seed_day_filter=None, weekend_plan_match_on=True,
    )
    persons = result.persons
    immobile_row = persons[(persons["H_ID"] == immobile[0]) & (persons["P_ID"] == immobile[1])].iloc[0]
    mobile_row = persons[(persons["H_ID"] == mobile[0]) & (persons["P_ID"] == mobile[1])].iloc[0]
    # The match keeps the immobile source and remaps the mobile one.
    assert (immobile_row["source_H_ID"], immobile_row["source_P_ID"]) == immobile
    assert (mobile_row["source_H_ID"], mobile_row["source_P_ID"]) != mobile

    columns = sources.get_source("mid").seed_columns()
    trip_class_entry = next(c for c in REGISTRY if c.name == "trip_class")
    _seed_hh, seed_persons = mid.project_completed_seed(
        result.households, persons, columns,
        kreis_control_entries=[trip_class_entry],
        kreis_seed_rng=np.random.RandomState(0),
        trip_class_counts_closure=True,
        forbid_no_diary_sources=True,
        drop_leading_arrive_home_leg=True,
    )
    seeded = seed_persons.set_index([columns.person_household_id, columns.person_id])["trip_class"]
    assert seeded.loc[immobile] == 0
    # The remapped person is seeded from the BORROWED donor's realised count, which is a
    # valid 0..50 diary count and no longer a 803/804 code -- the point of the remap.
    borrowed = persons.set_index(["H_ID", "P_ID"]).loc[
        (mobile_row["source_H_ID"], mobile_row["source_P_ID"]), "anzwege1"]
    assert int(borrowed) not in (803, 804)
    assert seeded.loc[mobile] in (0, 1, 2, 3)


def test_mobile_803_source_still_raises_when_the_match_did_not_run(tmp_path):
    """The guard must still fire when diary_plan_match is OFF but the caller arms it.

    That combination is exactly the flag-wiring defect the guard exists to catch.
    """
    _write_mid_attribute_fixture(tmp_path)
    _immobile, _mobile = _make_immobile_and_mobile_diaryless(tmp_path)
    result = cd.build_completed_donor(
        tmp_path, random_seed=1, seed_day_filter=None, weekend_plan_match_on=True,
        diary_plan_match_on=False,
    )
    columns = sources.get_source("mid").seed_columns()
    trip_class_entry = next(c for c in REGISTRY if c.name == "trip_class")
    with pytest.raises(ValueError, match="803"):
        mid.project_completed_seed(
            result.households, result.persons, columns,
            kreis_control_entries=[trip_class_entry],
            kreis_seed_rng=np.random.RandomState(0),
            trip_class_counts_closure=True,
            forbid_no_diary_sources=True,
        )


# ---------------------------------------------------------------------------
# Un-relaxable employment boundary -- Plan B Task 6, issue #368
# ---------------------------------------------------------------------------
def _make_employment_crossing_fixture(mid_dir):
    """Set up the one plan source whose remap can cross the employment boundary.

    Person (1,1) -- employed, male, 40, with a licence -- loses their diary (803 with
    mobil == 1) and must be remapped. Person (2,1), otherwise their perfect soft-key
    twin (male, 41, licence, PT sub), is turned into a Rentner (P_TAET 11): today's
    ladder drops ``employed`` before ``sex``/``age_band`` and therefore prefers that
    not-employed twin, while the remaining employed weekday donors (1,2) / (2,2) are
    female. Returns the (H_ID, P_ID) of the remapped person.
    """
    persons_path = mid_dir / "MiD2023_Personen.csv"
    p = pd.read_csv(persons_path)
    diaryless = (p["H_ID"] == 1) & (p["P_ID"] == 1)
    p.loc[diaryless, ["anzwege1", "mobil"]] = [803, 1]
    p.loc[(p["H_ID"] == 2) & (p["P_ID"] == 1), "P_TAET"] = 11
    p.to_csv(persons_path, index=False)
    return 1, 1


def _employed_lookup(persons):
    from braunschweig.popsim.attributes import EMPLOYED_TAET
    return persons.set_index(["H_ID", "P_ID"])["P_TAET"].isin(EMPLOYED_TAET)


def test_diary_match_keeps_the_employment_boundary_and_reports_the_crossing_rate(tmp_path, caplog):
    _write_mid_attribute_fixture(tmp_path)
    hid, pid = _make_employment_crossing_fixture(tmp_path)

    with caplog.at_level(logging.INFO, logger="braunschweig.popsim.diary_plan_match"):
        hard = cd.build_completed_donor(tmp_path, random_seed=1, seed_day_filter=None,
                                        weekend_plan_match_on=True)
    soft = cd.build_completed_donor(tmp_path, random_seed=1, seed_day_filter=None,
                                    weekend_plan_match_on=True,
                                    diary_match_hard_employment=False)

    # ON (default): no remapped person may take a donor of the other employment class.
    assert hard.diary_report.n_crossed_employment_boundary == 0
    hard_row = hard.persons[(hard.persons["H_ID"] == hid) & (hard.persons["P_ID"] == pid)].iloc[0]
    employed = _employed_lookup(hard.persons)
    assert bool(employed.loc[(hard_row["source_H_ID"], hard_row["source_P_ID"])]) is True
    # OFF: the same build crosses the boundary at least once (here: the Rentner twin),
    # which is what makes the ON assertion above discriminating.
    assert soft.diary_report.n_crossed_employment_boundary >= 1
    soft_row = soft.persons[(soft.persons["H_ID"] == hid) & (soft.persons["P_ID"] == pid)].iloc[0]
    assert (soft_row["source_H_ID"], soft_row["source_P_ID"]) == (2, 1)
    # The counter is logged as a rate, not silently carried in the report only.
    assert any("employment boundary crossed by 0/" in record.getMessage()
               for record in caplog.records)
