import numpy as np
import pandas as pd
import pytest

from braunschweig.popsim import member_completion, mid


def _fixture():
    households = pd.DataFrame({
        "H_ID": ["A", "B"], "H_GR": [4, 4], "hhgr_gr": [4, 4],
        "oek_status": [3, 3], "RegioStaR7": [73, 73],
    })
    persons = pd.DataFrame({
        "H_ID":     ["A", "A", "B", "B", "B", "B"],
        "P_ID":     [1, 2, 1, 2, 3, 4],
        "HP_ALTER": [40, 38, 41, 39, 10, 8],
        "HP_SEX":   [1, 2, 1, 2, 1, 2],
    })
    return households, persons


def test_fill_infers_missing_member_from_mirror_household():
    households, persons = _fixture()
    filled_h, filled_p, report = member_completion.complete_members(
        households, persons, rng=np.random.RandomState(0),
    )
    a = filled_p[filled_p["H_ID"] == "A"]
    assert len(a) == 4
    assert a["member_imputed"].sum() == 2
    assert (a.loc[a["member_imputed"], "HP_ALTER"] < 18).all()   # the two children
    assert report.n_households_filled == 1
    assert report.n_persons_added == 2


def test_fillers_keep_source_donor_traceability():
    households, persons = _fixture()
    _, filled_p, _ = member_completion.complete_members(
        households, persons, rng=np.random.RandomState(0),
    )
    fillers = filled_p[filled_p["member_imputed"]]
    # fillers live in household A but reference their source donor (B) for the trips join
    assert (fillers["H_ID"] == "A").all()
    assert (fillers["source_H_ID"] == "B").all()
    assert set(fillers["source_P_ID"]) == {3, 4}
    regular = filled_p[~filled_p["member_imputed"]]
    assert (regular["source_H_ID"] == regular["H_ID"]).all()
    assert (regular["source_P_ID"] == regular["P_ID"]).all()
    # filler P_IDs do not collide with the host's existing P_IDs
    assert set(fillers["P_ID"]).isdisjoint({1, 2})


def test_no_mirror_leaves_household_unfilled_but_reported():
    households = pd.DataFrame({"H_ID": ["A"], "H_GR": [4], "hhgr_gr": [4],
                               "oek_status": [3], "RegioStaR7": [73]})
    persons = pd.DataFrame({"H_ID": ["A", "A"], "P_ID": [1, 2],
                            "HP_ALTER": [40, 38], "HP_SEX": [1, 2]})
    _, filled_p, report = member_completion.complete_members(
        households, persons, rng=np.random.RandomState(0),
    )
    assert len(filled_p) == 2
    assert report.n_households_incomplete == 1
    assert report.n_households_filled == 0


# ---------------------------------------------------------------------------
# load_mid_seed wiring (opt-in completion)
# ---------------------------------------------------------------------------

def _write_mid_fixture(tmp_path):
    # Household A declares 4 members but only 2 person rows exist (incomplete);
    # household B is a complete 4-person mirror. All persons report on a
    # weekday (kernwo=1) so the day filter keeps both households.
    (tmp_path / "MiD2023_Haushalte.csv").write_text(
        "H_ID,H_GEW,RegioStaR7,H_GR,hhgr_gr,oek_status,H_MIETE,haustyp\n"
        "A,1.0,73,4,4,3,1,1\n"
        "B,1.0,73,4,4,3,2,2\n",
        encoding="utf-8",
    )
    (tmp_path / "MiD2023_Personen.csv").write_text(
        "H_ID,P_ID,P_GEW,HP_ALTER,HP_SEX,kernwo\n"
        "A,1,1.0,40,1,1\n"
        "A,2,1.0,38,2,1\n"
        "B,1,1.0,41,1,1\n"
        "B,2,1.0,39,2,1\n"
        "B,3,1.0,10,1,1\n"
        "B,4,1.0,8,2,1\n",
        encoding="utf-8",
    )


def test_load_mid_seed_with_completion_fills_and_keeps_traceability(tmp_path):
    _write_mid_fixture(tmp_path)
    households, persons, report = mid.load_mid_seed(
        tmp_path, complete_members=True,
        completion_rng=np.random.RandomState(0),
    )
    assert {"member_imputed", "source_H_ID", "source_P_ID"} <= set(persons.columns)
    a = persons[persons["H_ID"] == "A"]
    assert len(a) == 4
    fillers = a[a["member_imputed"]]
    assert len(fillers) == 2
    assert (fillers["source_H_ID"] == "B").all()
    assert set(fillers["source_P_ID"]) == {3, 4}
    regular = persons[~persons["member_imputed"]]
    assert (regular["source_H_ID"] == regular["H_ID"]).all()
    assert (regular["source_P_ID"] == regular["P_ID"]).all()
    # Household frame keeps the output schema (no completion columns).
    # H_GR, hh_type5, H_MIETE, haustyp are now unconditionally included in the seed.
    assert list(households.columns) == [
        "H_ID", "H_GEW", "RegioStaR7", "H_GR", "hh_type5", "H_MIETE", "haustyp", "STAAT",
    ]


def test_load_mid_seed_completion_requires_rng(tmp_path):
    _write_mid_fixture(tmp_path)
    with pytest.raises(ValueError, match="completion_rng"):
        mid.load_mid_seed(tmp_path, complete_members=True)


def test_load_mid_seed_default_is_unchanged(tmp_path):
    _write_mid_fixture(tmp_path)
    households, persons, report = mid.load_mid_seed(tmp_path)
    # Default OFF path (complete_members=False): no completion columns on persons.
    # H_GR, hh_type5, H_MIETE, haustyp are now unconditionally included in households.
    assert list(households.columns) == [
        "H_ID", "H_GEW", "RegioStaR7", "H_GR", "hh_type5", "H_MIETE", "haustyp", "STAAT",
    ]
    assert list(persons.columns) == [
        "H_ID", "P_ID", "P_GEW", "HP_ALTER", "HP_SEX", "STAAT",
    ]
    assert len(persons) == 6  # nothing filled, nothing dropped
