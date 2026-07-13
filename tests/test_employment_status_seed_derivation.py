"""Tests for deriving ``employment_status`` onto the popsim SEED (task 4b, feature #172).

Task 4 registered ``employment_status`` as a per-Kreis PopulationSim control
(seed_column ``employment_status``, person-level, min_age=14; see
tests/test_employment_status_control_registered.py) but did not make it RUNNABLE: the
popsim seed persons frame (what PopulationSim actually balances) never carried an
``employment_status`` column, so with the control active a run would fail at
``seed.select_seed_columns`` with a missing-column error. This task derives
``employment_status`` onto BOTH seed paths (``mid.load_mid_seed`` and
``mid.project_completed_seed``), gated on
``"employment_status" in active_kreis_entry_names``, mirroring EXACTLY how the
pre-existing person-level ``trip_class`` control is seeded (see the trip_class tests in
tests/test_popsim_seed_kreis_columns.py, which this file's fixtures deliberately parallel).

Uses ``attributes.map_employment_status`` -- the SAME function
``assembly.build_persons`` calls for the post-expansion attribute -- so seed and expanded
values agree deterministically for the 99.87% of persons with a valid (non-9) P_BKAT; no
P_BKAT -> employment_status logic is reimplemented here.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from braunschweig.popsim import attributes
from braunschweig.popsim import mid
from braunschweig.popsim import sources
from braunschweig.popsim.kreis_attribute_control import REGISTRY


def _entry(name):
    return next(c for c in REGISTRY if c.name == name)


# --- load_mid_seed: CSV-backed fixture with a P_BKAT mix (codes 1, 2, 6, 7 + a 9) ---


def _write_mini_mid_with_bkat(tmp: Path):
    """A minimal MiD household/person CSV pair carrying ``P_BKAT`` (Umfang der
    Erwerbstaetigkeit): four persons with the four "clean" substantive codes (1
    vollzeit, 2 teilzeit, 6 in_ausbildung, 7 nicht_erwerbstaetig) plus one person with
    the 9 (keine Angabe) item-nonresponse code that must be imputed. ``anzwege1`` is
    also carried so a combined test can activate ``trip_class`` alongside
    ``employment_status`` (both are person-level KREIS controls).

    ``alter_gr1`` = 5 is shared by P_ID 11 (P_BKAT=1, valid) and 15 (P_BKAT=9, imputed)
    so the code-9 imputation has a non-trivial within-age-group valid pool to draw from
    (rather than trivially falling back to the global pool).
    """
    hh = tmp / "MiD2023_Haushalte.csv"
    pers = tmp / "MiD2023_Personen.csv"
    hh.write_text(
        "H_ID;H_GEW;RegioStaR7;H_GR;H_MIETE;haustyp\n"
        "1;1.0;71;1;1;1\n"
        "2;1.0;71;1;1;1\n"
        "3;1.0;71;1;1;1\n"
        "4;1.0;71;1;1;1\n"
        "5;1.0;71;1;1;1\n", encoding="utf-8")
    pers.write_text(
        "P_ID;H_ID;P_GEW;HP_ALTER;HP_SEX;kernwo;P_BKAT;alter_gr1;anzwege1\n"
        "11;1;1.0;40;1;1;1;5;2\n"
        "12;2;1.0;35;2;1;2;4;0\n"
        "13;3;1.0;25;1;1;6;1;5\n"
        "14;4;1.0;70;2;1;7;5;3\n"
        "15;5;1.0;45;1;1;9;5;1\n", encoding="utf-8")


def test_load_mid_seed_derives_employment_status_only_when_active(tmp_path):
    from braunschweig.popsim.mid import load_mid_seed
    _write_mini_mid_with_bkat(tmp_path)

    # Active -> the PERSONS frame carries a clean string-coded employment_status (P9 taxonomy).
    _hh, pers_on, _rep = load_mid_seed(
        tmp_path, day_filter_values=(), kreis_control_entries=[_entry("employment_status")],
        kreis_seed_rng=np.random.RandomState(0))
    assert "employment_status" in pers_on.columns
    by_pid = dict(zip(pers_on["P_ID"], pers_on["employment_status"]))
    assert by_pid[11] == "vollzeit"
    assert by_pid[12] == "teilzeit"
    assert by_pid[13] == "in_ausbildung"
    assert by_pid[14] == "nicht_erwerbstaetig"
    # P_BKAT=9 (keine Angabe) must be imputed to one of the 7 valid classes, never left raw.
    assert by_pid[15] in attributes.EMPLOYMENT_STATUS_CATEGORIES
    # The raw donor column is not retained on the seed (mirrors trip_class dropping anzwege1).
    assert "P_BKAT" not in pers_on.columns

    # Inactive -> no employment_status column on the persons seed (byte-identical no-op).
    _hh2, pers_off, _rep2 = load_mid_seed(
        tmp_path, day_filter_values=(), kreis_control_entries=())
    assert "employment_status" not in pers_off.columns


def test_load_mid_seed_employment_status_requires_seeded_rng(tmp_path):
    # The seeded-RNG guard must cover the person-level employment_status entry (the
    # ~0.13% code-9 keine-Angabe imputation), mirroring trip_class's 803/804 guard. The
    # match= pins this to the RNG guard specifically (not e.g. a missing-column error
    # from an incomplete wiring, which is also a ValueError but a different failure).
    from braunschweig.popsim.mid import load_mid_seed
    _write_mini_mid_with_bkat(tmp_path)
    with pytest.raises(ValueError, match="kreis_seed_rng is not set"):
        load_mid_seed(
            tmp_path, day_filter_values=(), kreis_control_entries=[_entry("employment_status")])


def test_load_mid_seed_employment_status_imputation_is_deterministic(tmp_path):
    from braunschweig.popsim.mid import load_mid_seed
    _write_mini_mid_with_bkat(tmp_path)
    _hh_a, pers_a, _rep_a = load_mid_seed(
        tmp_path, day_filter_values=(), kreis_control_entries=[_entry("employment_status")],
        kreis_seed_rng=np.random.RandomState(42))
    _hh_b, pers_b, _rep_b = load_mid_seed(
        tmp_path, day_filter_values=(), kreis_control_entries=[_entry("employment_status")],
        kreis_seed_rng=np.random.RandomState(42))
    assert pers_a["employment_status"].tolist() == pers_b["employment_status"].tolist()


def test_load_mid_seed_trip_class_and_employment_status_coexist(tmp_path):
    """Both person-level KREIS controls active together must each retain their own seed
    column -- a regression check that the generic per-entry retention loop in
    load_mid_seed (``_person_extra``) correctly accumulates a SECOND person-level entry
    instead of only supporting the first (trip_class)."""
    from braunschweig.popsim.mid import load_mid_seed
    _write_mini_mid_with_bkat(tmp_path)
    _hh, pers, _rep = load_mid_seed(
        tmp_path, day_filter_values=(),
        kreis_control_entries=[_entry("trip_class"), _entry("employment_status")],
        kreis_seed_rng=np.random.RandomState(0))
    assert "trip_class" in pers.columns
    assert "employment_status" in pers.columns
    by_pid_tc = dict(zip(pers["P_ID"], pers["trip_class"]))
    by_pid_es = dict(zip(pers["P_ID"], pers["employment_status"]))
    # anzwege1 [2, 0, 5, 3, 1] -> trip_class [1, 0, 3, 2, 1].
    assert by_pid_tc[11] == 1
    assert by_pid_tc[12] == 0
    assert by_pid_tc[13] == 3
    assert by_pid_es[11] == "vollzeit"
    assert by_pid_es[13] == "in_ausbildung"


# --- project_completed_seed: the completed-donor path (member completion; the path the
# kreis5 100% run uses) ---


def _completed_donor_frames_with_bkat():
    """Minimal completed-donor households/persons frames carrying ``P_BKAT`` (Umfang der
    Erwerbstaetigkeit), mirroring tests/test_popsim_seed_kreis_columns.py's
    ``_completed_donor_frames`` (same household/person shape) with ``P_BKAT`` added: four
    "clean" substantive codes (1, 2, 6, 7) plus one 9 (keine Angabe, imputed)."""
    cols = sources.get_source("mid").seed_columns()
    households = pd.DataFrame({
        cols.household_id: ["h1", "h2", "h3", "h4"],
        cols.household_weight: [1.0, 1.0, 1.0, 1.0],
        "H_GR": [1, 2, 2, 1], "H_MIETE": [1, 2, 1, 1], "haustyp": [1, 5, 1, 1],
        "RegioStaR7": [73, 74, 73, 73],
    })
    persons = pd.DataFrame({
        cols.person_household_id: ["h1", "h2", "h2", "h3", "h4"],
        cols.person_id: ["p1", "p2", "p3", "p4", "p5"],
        cols.person_weight: [1.0, 1.0, 1.0, 1.0, 1.0],
        cols.age: [40, 38, 10, 45, 30],
        cols.sex: [1, 2, 1, 2, 1],
        # anzwege1 + alter_gr1 feed trip_class (carried via MID_PERSON_ATTR_COLS on the
        # real completed-donor path); P_BKAT feeds employment_status (also carried via
        # MID_PERSON_ATTR_COLS). alter_gr1=5 is shared by p1 (P_BKAT=1, valid) and p5
        # (P_BKAT=9, imputed) so the code-9 imputation has a within-group valid pool.
        "anzwege1": [2, 0, 4, 5, 3],
        "alter_gr1": [5, 4, 1, 5, 5],
        "P_BKAT": [1, 2, 6, 7, 9],
    })
    return cols, households, persons


def test_project_completed_seed_derives_employment_status():
    cols, households, persons = _completed_donor_frames_with_bkat()
    _seed_hh, seed_p = mid.project_completed_seed(
        households, persons, cols,
        kreis_control_entries=[_entry("employment_status")], kreis_seed_rng=np.random.RandomState(0),
    )
    assert "employment_status" in seed_p.columns
    by_pid = dict(zip(seed_p[cols.person_id], seed_p["employment_status"]))
    assert by_pid["p1"] == "vollzeit"
    assert by_pid["p2"] == "teilzeit"
    assert by_pid["p3"] == "in_ausbildung"
    assert by_pid["p4"] == "nicht_erwerbstaetig"
    assert by_pid["p5"] in attributes.EMPLOYMENT_STATUS_CATEGORIES
    assert "P_BKAT" not in seed_p.columns


def test_project_completed_seed_employment_status_requires_seeded_rng():
    # match= pins this to the RNG guard specifically (see the analogous load_mid_seed test).
    cols, households, persons = _completed_donor_frames_with_bkat()
    with pytest.raises(ValueError, match="kreis_seed_rng is not set"):
        mid.project_completed_seed(
            households, persons, cols, kreis_control_entries=[_entry("employment_status")])


def test_project_completed_seed_employment_status_inactive_is_noop():
    # P_BKAT is present on the completed-donor input (it is unconditionally loaded via
    # MID_PERSON_ATTR_COLS in the real pipeline), but employment_status must NOT be
    # derived/retained unless the entry is active -- byte-identical no-op otherwise.
    cols, households, persons = _completed_donor_frames_with_bkat()
    _seed_hh, seed_p = mid.project_completed_seed(
        households, persons, cols, kreis_control_entries=(),
    )
    assert "employment_status" not in seed_p.columns


def test_project_completed_seed_trip_class_and_employment_status_coexist():
    """Mirrors the load_mid_seed coexistence check on the completed-donor path."""
    cols, households, persons = _completed_donor_frames_with_bkat()
    _seed_hh, seed_p = mid.project_completed_seed(
        households, persons, cols,
        kreis_control_entries=[_entry("trip_class"), _entry("employment_status")],
        kreis_seed_rng=np.random.RandomState(0),
    )
    assert "trip_class" in seed_p.columns
    assert "employment_status" in seed_p.columns
    by_pid_tc = dict(zip(seed_p[cols.person_id], seed_p["trip_class"]))
    by_pid_es = dict(zip(seed_p[cols.person_id], seed_p["employment_status"]))
    # anzwege1 [2, 0, 4, 5, 3] -> trip_class [1, 0, 2, 3, 2].
    assert by_pid_tc["p1"] == 1
    assert by_pid_tc["p4"] == 3
    assert by_pid_es["p1"] == "vollzeit"
    assert by_pid_es["p4"] == "nicht_erwerbstaetig"
