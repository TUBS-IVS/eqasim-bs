"""Task 4 (feature #224): register ``work_participation`` as a per-Kreis PopulationSim
STEERING control and wire its seed derivation (mirrors ``trip_class`` /
``employment_status`` end-to-end; see tests/test_employment_status_control_registered.py
and tests/test_popsim_seed_kreis_columns.py for the patterns this file parallels).

Unlike trip_class/employment_status, work_participation is registered ``tier="hard"``
(control_spec.IMPORTANCE_PROFILES classifies it into the "kreis_hard" group automatically
via ``importance_group_for_field``, alongside economic_status/number_of_cars) and its seed
(``mid.derive_work_participation_seed``) needs the FULL MiD Wege table -- unlike
trip_class/employment_status, which derive their seed purely from already-loaded raw
columns (anzwege1 / P_BKAT). Both seed call sites (``load_mid_seed`` /
``project_completed_seed``) must load the Wege table via ``mid.load_mid_wege`` when the
control is active, and must NOT touch it at all when inactive (OFF byte-identity).
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import pytest  # noqa: E402

from braunschweig.popsim import control_spec as cs  # noqa: E402
from braunschweig.popsim import mid  # noqa: E402
from braunschweig.popsim import sources  # noqa: E402
from braunschweig.popsim.kreis_attribute_control import (  # noqa: E402
    REGISTRY,
    control_columns,
)


def _entry(name):
    return next(c for c in REGISTRY if c.name == name)


# --- Registry entry shape ---


def test_registered_hard_person_control():
    e = _entry("work_participation")
    assert e.seed_column == "work_participation"
    assert e.level == "person"
    assert e.tier == "hard"
    assert e.categories == (("yes", "== 1"), ("no", "== 0"))
    assert e.target_columns == ("work_yes", "work_no")
    assert e.target_csv_relpath == (
        "braunschweig/targets/target2026_work_participation_by_kreis.csv"
    )


def test_control_columns_follow_name_category():
    e = _entry("work_participation")
    assert control_columns(e) == ("work_participation_yes", "work_participation_no")


# --- Catalog factory: person-level predicate rendering ---


def test_attribute_kreis_controls_renders_person_predicate():
    controls = cs.attribute_kreis_controls([_entry("work_participation")])
    exprs = {c.name: c.expression_for("mid") for c in controls}
    assert exprs["work_participation_yes"] == "(persons.work_participation == 1)"
    assert exprs["work_participation_no"] == "(persons.work_participation == 0)"
    assert all(c.seed_table == cs.SEED_TABLE_PERSONS for c in controls)
    assert all(c.geography == cs.GEO_KREIS for c in controls)
    # ENTD cannot express the MiD donor Wege-derived column -> dropped.
    assert all(c.expression_for("entd") is None for c in controls)


# --- Importance profile: tier="hard" auto-classifies into the "kreis_hard" group ---


def test_importance_group_for_field_classifies_work_participation_as_kreis_hard():
    assert cs.importance_group_for_field("work_participation_yes_KREIS") == "kreis_hard"
    assert cs.importance_group_for_field("work_participation_no_KREIS") == "kreis_hard"


def test_apply_importance_profile_sets_hard_weight_for_work_participation():
    from braunschweig.popsim.stage import build_controls_df

    on = build_controls_df(
        controls_source="catalog", seed="mid", tiers=("tier0",),
        kreis_control_names=("work_participation",),
        importance_profile="optimized_2026_06_30",
    )
    rows = on[on["control_field"].isin(
        {"work_participation_yes_KREIS", "work_participation_no_KREIS"})]
    assert len(rows) == 2
    assert (rows["importance"] == cs.IMPORTANCE_PROFILES["optimized_2026_06_30"]["kreis_hard"]).all()


# --- Stage flag wiring (mirrors test_kreis_control_stage_wiring.py's _FakeContext) ---


class _FakeContext:
    def __init__(self, values=None):
        self._values = values or {}

    def config(self, key):
        if key in self._values:
            return self._values[key]
        from braunschweig.popsim import stage
        for name, toggle_key in stage._KREIS_CONTROL_TOGGLE_KEY.items():
            if key == toggle_key:
                return stage._KREIS_CONTROL_DEFAULT[name]
        raise KeyError(f"_FakeContext: no value or declared default for config key {key!r}")


def test_toggle_key_registered_and_defaults_on():
    from braunschweig.popsim import stage
    assert stage._KREIS_CONTROL_TOGGLE_KEY["work_participation"] == stage.KEY_WORK_PARTICIPATION_CONTROL
    assert stage._KREIS_CONTROL_DEFAULT["work_participation"] == "on"


def test_active_kreis_entries_includes_work_participation_by_default():
    from braunschweig.popsim import stage
    active = stage.active_kreis_entries(_FakeContext({}), "mid")
    names = {c.name for c in active}
    assert "work_participation" in names
    # feature #224 task 5 adds two more default-on person-level entries
    # (leisure_participation / education_participation); the full active set now
    # includes them too (see tests/test_leisure_education_participation.py).
    assert names == {
        "economic_status", "number_of_cars", "number_of_bicycles", "has_ebike",
        "trip_class", "employment_status", "pt_ticket_group", "work_participation",
        "leisure_participation", "education_participation",
    }


def test_off_path_excludes_control():
    from braunschweig.popsim import stage
    active = stage.active_kreis_entries(
        _FakeContext({stage.KEY_WORK_PARTICIPATION_CONTROL: "off"}), "mid"
    )
    names = {c.name for c in active}
    assert "work_participation" not in names
    # The other six default-on entries are unaffected by this toggle.
    assert {"economic_status", "number_of_cars", "number_of_bicycles", "has_ebike",
            "trip_class", "employment_status"} <= names


def test_active_kreis_entries_empty_for_non_mid_source():
    from braunschweig.popsim import stage
    assert stage.active_kreis_entries(_FakeContext({}), "entd") == []


def test_off_controls_csv_byte_identical_to_pre_task_default():
    """The OFF path (work_participation not requested) leaves build_controls_df's output
    byte-identical to a call that never mentions work_participation at all."""
    from braunschweig.popsim.stage import build_controls_df

    a = build_controls_df(controls_source="catalog", seed="mid", tiers=("tier0", "tier1"))
    b = build_controls_df(
        controls_source="catalog", seed="mid", tiers=("tier0", "tier1"),
        kreis_control_names=(),
    )
    assert a.to_csv(index=False) == b.to_csv(index=False)
    fields = set(a["control_field"])
    assert not any(f.startswith("work_participation_") for f in fields)


# --- Seed wiring: load_mid_seed (direct MiD CSV path) ---


def _write_mini_mid_with_wege(tmp: Path):
    """A minimal MiD household/person/Wege CSV triple.

    Persons (H_ID;P_ID;anzwege1;alter_gr1):
      11;1  anzwege1=2  alter_gr1=5  -- one work (W_ZWECK=1) + one leisure (7) Weg -> has_work_trip=1
      12;2  anzwege1=0  alter_gr1=4  -- no Wege at all -> has_work_trip=0
      13;3  anzwege1=3  alter_gr1=1  -- shop/leisure/home Wege, no work -> has_work_trip=0
      14;4  anzwege1=1  alter_gr1=5  -- one dienstlich (W_ZWECK=2) Weg -> has_work_trip=1
      15;5  anzwege1=803 alter_gr1=5 -- diary non-response (no Wege); must be imputed within
                                        alter_gr1=5 (shared with persons 11/14, both work=1)
    """
    hh = tmp / "MiD2023_Haushalte.csv"
    pers = tmp / "MiD2023_Personen.csv"
    wege = tmp / "MiD2023_Wege.csv"
    hh.write_text(
        "H_ID;H_GEW;RegioStaR7;H_GR;H_MIETE;haustyp\n"
        "1;1.0;71;1;1;1\n"
        "2;1.0;71;1;1;1\n"
        "3;1.0;71;1;1;1\n"
        "4;1.0;71;1;1;1\n"
        "5;1.0;71;1;1;1\n", encoding="utf-8")
    pers.write_text(
        "P_ID;H_ID;P_GEW;HP_ALTER;HP_SEX;kernwo;anzwege1;alter_gr1\n"
        "11;1;1.0;40;1;1;2;5\n"
        "12;2;1.0;35;2;1;0;4\n"
        "13;3;1.0;25;1;1;3;1\n"
        "14;4;1.0;70;2;1;1;5\n"
        "15;5;1.0;45;1;1;803;5\n", encoding="utf-8")
    wege.write_text(
        "H_ID;P_ID;W_ID;W_ZWECK;hvm_imp;W_SZS;W_SZM;W_AZS;W_AZM;wegkm_imp;wegmin_imp1\n"
        "1;11;101;1;4;8;0;8;30;5.0;30\n"
        "1;11;102;7;4;18;0;18;30;5.0;30\n"
        "3;13;103;7;1;9;0;9;20;2.0;20\n"
        "3;13;104;4;1;12;0;12;15;1.0;15\n"
        "3;13;105;8;1;17;0;17;10;1.0;10\n"
        "4;14;106;2;4;8;0;8;25;6.0;25\n", encoding="utf-8")


def test_load_mid_seed_derives_work_participation_only_when_active(tmp_path):
    from braunschweig.popsim.mid import load_mid_seed
    _write_mini_mid_with_wege(tmp_path)

    # Active -> the PERSONS frame carries a clean int-coded work_participation (0/1).
    _hh, pers_on, _rep = load_mid_seed(
        tmp_path, day_filter_values=(), kreis_control_entries=[_entry("work_participation")],
        kreis_seed_rng=np.random.RandomState(0))
    assert "work_participation" in pers_on.columns
    assert set(pers_on["work_participation"]).issubset({0, 1})
    by_pid = dict(zip(pers_on["P_ID"], pers_on["work_participation"]))
    assert by_pid[11] == 1  # has a work (W_ZWECK=1) Weg
    assert by_pid[12] == 0  # no Wege at all
    assert by_pid[13] == 0  # leisure/shop/home Wege only
    assert by_pid[14] == 1  # has a dienstlich (W_ZWECK=2) Weg
    assert by_pid[15] in (0, 1)  # 803 diary non-response -> imputed, never left raw

    # Inactive -> no work_participation column on the persons seed (byte-identical no-op).
    _hh2, pers_off, _rep2 = load_mid_seed(
        tmp_path, day_filter_values=(), kreis_control_entries=())
    assert "work_participation" not in pers_off.columns


def test_load_mid_seed_off_path_does_not_require_wege_file(tmp_path):
    """OFF byte-identity: load_mid_seed must not touch MiD2023_Wege.csv at all when
    work_participation is inactive -- proven by NOT writing that file and confirming
    the (unrelated) run still succeeds."""
    from braunschweig.popsim.mid import load_mid_seed
    hh = tmp_path / "MiD2023_Haushalte.csv"
    pers = tmp_path / "MiD2023_Personen.csv"
    hh.write_text(
        "H_ID;H_GEW;RegioStaR7;H_GR;H_MIETE;haustyp\n1;1.0;71;1;1;1\n", encoding="utf-8")
    pers.write_text(
        "P_ID;H_ID;P_GEW;HP_ALTER;HP_SEX;kernwo\n11;1;1.0;40;1;1\n", encoding="utf-8")
    assert not (tmp_path / "MiD2023_Wege.csv").exists()
    _hh, pers_off, _rep = load_mid_seed(tmp_path, day_filter_values=())
    assert "work_participation" not in pers_off.columns
    assert not (tmp_path / "MiD2023_Wege.csv").exists()


def test_load_mid_seed_work_participation_requires_seeded_rng(tmp_path):
    from braunschweig.popsim.mid import load_mid_seed
    _write_mini_mid_with_wege(tmp_path)
    with pytest.raises(ValueError, match="kreis_seed_rng is not set"):
        load_mid_seed(
            tmp_path, day_filter_values=(), kreis_control_entries=[_entry("work_participation")])


def test_load_mid_seed_work_participation_imputation_is_deterministic(tmp_path):
    from braunschweig.popsim.mid import load_mid_seed
    _write_mini_mid_with_wege(tmp_path)
    _hh_a, pers_a, _rep_a = load_mid_seed(
        tmp_path, day_filter_values=(), kreis_control_entries=[_entry("work_participation")],
        kreis_seed_rng=np.random.RandomState(42))
    _hh_b, pers_b, _rep_b = load_mid_seed(
        tmp_path, day_filter_values=(), kreis_control_entries=[_entry("work_participation")],
        kreis_seed_rng=np.random.RandomState(42))
    assert pers_a["work_participation"].tolist() == pers_b["work_participation"].tolist()


def test_load_mid_seed_trip_class_and_work_participation_coexist(tmp_path):
    from braunschweig.popsim.mid import load_mid_seed
    _write_mini_mid_with_wege(tmp_path)
    _hh, pers, _rep = load_mid_seed(
        tmp_path, day_filter_values=(),
        kreis_control_entries=[_entry("trip_class"), _entry("work_participation")],
        kreis_seed_rng=np.random.RandomState(0))
    assert "trip_class" in pers.columns
    assert "work_participation" in pers.columns
    assert "anzwege1" not in pers.columns


# --- Seed wiring: project_completed_seed (member-completion path) ---


def _completed_donor_frames():
    cols = sources.get_source("mid").seed_columns()
    households = pd.DataFrame({
        cols.household_id: ["h1", "h2", "h3", "h4"],
        cols.household_weight: [1.0, 1.0, 1.0, 1.0],
        "H_GR": [1, 1, 1, 1], "H_MIETE": [1, 2, 1, 1], "haustyp": [1, 5, 1, 1],
        "RegioStaR7": [73, 74, 73, 73],
    })
    persons = pd.DataFrame({
        cols.person_household_id: ["h1", "h2", "h3", "h4"],
        cols.person_id: ["p1", "p2", "p3", "p4"],
        cols.person_weight: [1.0, 1.0, 1.0, 1.0],
        cols.age: [40, 38, 25, 70],
        cols.sex: [1, 2, 1, 2],
        # anzwege1 + alter_gr1 are carried on the completed-donor frames unconditionally
        # (MID_PERSON_ATTR_COLS); see mid.MID_PERSON_ATTR_COLS.
        "anzwege1": [2, 0, 3, 1],
        "alter_gr1": [5, 4, 1, 5],
    })
    return cols, households, persons


def _write_wege_only(tmp: Path):
    """A MiD2023_Wege.csv matching the h1..h4/p1..p4 completed-donor frame."""
    wege = tmp / "MiD2023_Wege.csv"
    wege.write_text(
        "H_ID;P_ID;W_ID;W_ZWECK;hvm_imp;W_SZS;W_SZM;W_AZS;W_AZM;wegkm_imp;wegmin_imp1\n"
        "h1;p1;101;1;4;8;0;8;30;5.0;30\n"
        "h1;p1;102;7;4;18;0;18;30;5.0;30\n"
        "h3;p3;103;7;1;9;0;9;20;2.0;20\n"
        "h3;p3;104;4;1;12;0;12;15;1.0;15\n"
        "h3;p3;105;8;1;17;0;17;10;1.0;10\n"
        "h4;p4;106;2;4;8;0;8;25;6.0;25\n", encoding="utf-8")


def test_project_completed_seed_derives_work_participation(tmp_path):
    _write_wege_only(tmp_path)
    cols, households, persons = _completed_donor_frames()
    _seed_hh, seed_p = mid.project_completed_seed(
        households, persons, cols,
        kreis_control_entries=[_entry("work_participation")], kreis_seed_rng=np.random.RandomState(0),
        mid_dir=tmp_path,
    )
    assert "work_participation" in seed_p.columns
    by_pid = dict(zip(seed_p[cols.person_id], seed_p["work_participation"]))
    assert by_pid["p1"] == 1
    assert by_pid["p2"] == 0
    assert by_pid["p3"] == 0
    assert by_pid["p4"] == 1


def test_project_completed_seed_work_participation_requires_mid_dir():
    """No silent fallback: work_participation active without mid_dir must fail fast
    (project_completed_seed has no other way to reach the MiD Wege table)."""
    cols, households, persons = _completed_donor_frames()
    with pytest.raises(ValueError, match="mid_dir"):
        mid.project_completed_seed(
            households, persons, cols,
            kreis_control_entries=[_entry("work_participation")], kreis_seed_rng=np.random.RandomState(0),
        )


def test_project_completed_seed_work_participation_requires_seeded_rng(tmp_path):
    _write_wege_only(tmp_path)
    cols, households, persons = _completed_donor_frames()
    with pytest.raises(ValueError, match="kreis_seed_rng is not set"):
        mid.project_completed_seed(
            households, persons, cols,
            kreis_control_entries=[_entry("work_participation")], mid_dir=tmp_path,
        )


def test_project_completed_seed_work_participation_inactive_is_noop_without_mid_dir():
    """OFF byte-identity: work_participation inactive must not require mid_dir at all
    (proves the Wege load is fully gated on the control being active)."""
    cols, households, persons = _completed_donor_frames()
    _seed_hh, seed_p = mid.project_completed_seed(
        households, persons, cols, kreis_control_entries=(),
    )
    assert "work_participation" not in seed_p.columns
