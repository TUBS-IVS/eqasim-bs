"""End-to-end integration of the mirror-household member completion (D3).

The completion runs ONCE on the attribute-bearing donor tables
(``mid.load_completed_donor``); BOTH the PopulationSim seed AND the expansion
derive from the completed frames.  Fillers carry ``member_imputed`` plus the
total traceability columns ``source_H_ID`` / ``source_P_ID`` (regular persons
reference themselves), which the downstream stages must honour:

- ``assembly.assign_donor_surrogates`` builds the pseudonym surrogates from the
  source ids, so a filler re-links to its MIRROR donor person;
- ``trips.build_trip_table`` joins the MiD Wege on the source ids, so a filler
  inherits the mirror donor's trip chain (its fresh (host H_ID, P_ID) pair does
  not exist in the Wege file).

The stage flag ``braunschweig.population.popsim.complete_members`` (default
True) gates the whole path; False reproduces the legacy load_mid_seed +
load_donor behaviour byte-identically.
"""

import pathlib

import numpy as np
import pandas as pd

from braunschweig.popsim import assembly, mid, trips


# ---------------------------------------------------------------------------
# Fixture: minimal MiD raw CSVs carrying ALL attribute usecols
# (load_mid_attributes reads with usecols=MID_*_ATTR_COLS, so every column in
# those tuples must exist in the fixture files).
# ---------------------------------------------------------------------------

def _write_mid_attribute_fixture(tmp_path):
    """Household A declares H_GR=4 but has only 2 person rows (incomplete);
    household B is a complete 4-person mirror.  All persons report on a
    weekday (kernwo=1) so the day filter keeps both households."""
    (tmp_path / "MiD2023_Haushalte.csv").write_text(
        "H_ID,oek_status,hheink_gr1,H_ANZAUTO,H_ANZRAD,anzpedrad,H_ANZPED,RegioStaR7,hhgr_gr,H_GR,H_GEW,H_MIETE,haustyp\n"
        "A,3,4,1,2,2,0,73,4,4,1.0,1,1\n"
        "B,3,4,1,2,2,0,73,4,4,1.0,2,2\n",
        encoding="utf-8",
    )
    # anzwege1 (diary trip count) is part of MID_PERSON_ATTR_COLS (person-level
    # trip_class control), so the fixture includes it after alter_gr1.
    (tmp_path / "MiD2023_Personen.csv").write_text(
        "H_ID,P_ID,HP_ALTER,HP_SEX,P_TAET,P_FSCHEIN,P_FKARTE,P_BKAT,alter_gr1,anzwege1,P_GEW,kernwo\n"
        "A,1,40,1,1,1,3,1,5,3,1.0,1\n"
        "A,2,38,2,1,1,3,1,5,2,1.0,1\n"
        "B,1,41,1,1,1,3,1,5,4,1.0,1\n"
        "B,2,39,2,1,1,3,1,5,0,1.0,1\n"
        "B,3,10,1,5,2,3,4,2,1,1.0,1\n"
        "B,4,8,2,5,2,3,4,1,1,1.0,1\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# 1. load_completed_donor returns the filled attribute frames
# ---------------------------------------------------------------------------

def test_load_completed_donor_returns_filled_frames(tmp_path):
    _write_mid_attribute_fixture(tmp_path)

    households, persons, completeness_report, completion_report = mid.load_completed_donor(
        tmp_path, completion_rng=np.random.RandomState(0),
    )

    # A is filled to its declared size 4; B stays complete -> 8 person rows.
    assert len(persons) == 8
    assert len(persons[persons["H_ID"] == "A"]) == 4
    assert completion_report.n_persons_added == 2
    assert completion_report.n_households_filled == 1
    assert completeness_report.n_households_complete == 2

    # Traceability columns are TOTAL: regular persons reference themselves,
    # fillers reference the mirror donor.
    assert {"member_imputed", "source_H_ID", "source_P_ID"} <= set(persons.columns)
    fillers = persons[persons["member_imputed"]]
    assert len(fillers) == 2
    assert (fillers["H_ID"] == "A").all()
    assert (fillers["source_H_ID"] == "B").all()
    assert set(fillers["source_P_ID"]) == {3, 4}
    regular = persons[~persons["member_imputed"]]
    assert (regular["source_H_ID"] == regular["H_ID"]).all()
    assert (regular["source_P_ID"] == regular["P_ID"]).all()

    # The attribute columns the expansion needs are still present, and the
    # seed columns (weights, day flag, declared size) are carried too so the
    # SAME frames can serve the PopulationSim seed.
    assert {"oek_status", "hheink_gr1", "H_ANZAUTO", "H_ANZRAD", "RegioStaR7",
            "H_GR", "H_GEW"} <= set(households.columns)
    assert {"P_TAET", "P_FSCHEIN", "P_FKARTE", "P_BKAT", "alter_gr1",
            "P_GEW", "kernwo"} <= set(persons.columns)


# ---------------------------------------------------------------------------
# 2. trips join: a filler inherits the MIRROR donor's Wege
# ---------------------------------------------------------------------------

def test_filler_inherits_mirror_wege():
    # Persons frame as it would leave the expansion: a filler living in host
    # household A under a fresh P_ID=5, referencing mirror donor (B, 3); plus a
    # regular copy of the mirror person itself (source_* == own ids).
    persons = pd.DataFrame({
        "person_id":   ["cell_A_0_5", "cell_B_0_3"],
        "H_ID":        ["A", "B"],
        "P_ID":        [5, 3],
        "source_H_ID": ["B", "B"],
        "source_P_ID": [3, 3],
        "member_imputed": [True, False],
    })
    # Wege frame contains ONLY the mirror person's trips: (A, 5) has no rows.
    mid_wege = pd.DataFrame({
        "H_ID":      ["B", "B"],
        "P_ID":      [3, 3],
        "W_ID":      [1, 2],
        "W_ZWECK":   [1, 8],          # work, home
        "hvm_imp":   [4, 4],          # car
        "W_SZS":     [8, 17],
        "W_SZM":     [0, 0],
        "W_AZS":     [8, 17],
        "W_AZM":     [30, 30],
        "wegkm_imp": [5.0, 5.0],
    })

    table = trips.build_trip_table(persons, mid_wege)

    filler_trips = table[table["person_id"] == "cell_A_0_5"]
    assert len(filler_trips) == 2, (
        "the filler must inherit the mirror donor's trip chain via the "
        "source_H_ID/source_P_ID join keys; its fresh (H_ID, P_ID) pair does "
        "not exist in the Wege file."
    )
    assert list(filler_trips.sort_values("trip_index")["following_purpose"]) == ["work", "home"]
    assert (filler_trips["mode"] == "car").all()

    # The regular person (source_* == own ids) is unaffected.
    regular_trips = table[table["person_id"] == "cell_B_0_3"]
    assert len(regular_trips) == 2


def test_trips_join_without_source_columns_is_unchanged():
    """Legacy path (flag OFF): persons without source_* join on H_ID/P_ID."""
    persons = pd.DataFrame({
        "person_id": ["cell_B_0_3"],
        "H_ID": ["B"],
        "P_ID": [3],
    })
    mid_wege = pd.DataFrame({
        "H_ID": ["B"], "P_ID": [3], "W_ID": [1],
        "W_ZWECK": [1], "hvm_imp": [4],
        "W_SZS": [8], "W_SZM": [0], "W_AZS": [8], "W_AZM": [30],
        "wegkm_imp": [5.0],
    })
    table = trips.build_trip_table(persons, mid_wege)
    assert len(table) == 1
    assert table["following_purpose"].iloc[0] == "work"


# ---------------------------------------------------------------------------
# 3. pseudonym surrogates prefer the source ids (filler -> mirror donor)
# ---------------------------------------------------------------------------

def test_surrogates_prefer_source_ids():
    persons = pd.DataFrame({
        "person_id":   ["x1", "x2", "x3"],
        "H_ID":        ["hostA", "hostA", "B"],
        "P_ID":        [1, 99, 3],
        # x2 is a filler: lives in hostA under fresh P_ID=99, mirror donor (B, 3).
        "source_H_ID": ["hostA", "B", "B"],
        "source_P_ID": [1, 3, 3],
    })

    out, mapping = assembly.assign_donor_surrogates(persons)

    # The filler's surrogate maps to the MIRROR donor (B, 3) in the pseudonym
    # map -- NOT to its synthetic (hostA, 99) pair.
    mapped_pairs = set(zip(mapping["H_ID"], mapping["P_ID"]))
    assert ("B", 3) in mapped_pairs
    assert ("hostA", 99) not in mapped_pairs

    # Filler and the real mirror person share the same person surrogate
    # (they reference the same MiD respondent).
    by_person = out.set_index("person_id")
    assert by_person.loc["x2", "source_person_id"] == by_person.loc["x3", "source_person_id"]
    assert by_person.loc["x2", "source_household_id"] == by_person.loc["x3", "source_household_id"]
    # The regular person of hostA keeps a distinct surrogate.
    assert by_person.loc["x1", "source_person_id"] != by_person.loc["x2", "source_person_id"]

    # The map row for the shared surrogate carries the mirror's raw ids.
    surrogate = by_person.loc["x2", "source_person_id"]
    row = mapping[mapping["source_person_id"] == surrogate].iloc[0]
    assert row["H_ID"] == "B"
    assert row["P_ID"] == 3


def test_surrogates_fall_back_to_raw_ids_without_source_columns():
    """Legacy path: frames without source_* factorize H_ID/P_ID as before."""
    persons = pd.DataFrame({
        "person_id": ["x1", "x2"],
        "H_ID": ["A", "B"],
        "P_ID": [1, 1],
    })
    out, mapping = assembly.assign_donor_surrogates(persons)
    assert set(zip(mapping["H_ID"], mapping["P_ID"])) == {("A", 1), ("B", 1)}
    assert list(out["source_household_id"]) == [1, 2]


# ---------------------------------------------------------------------------
# 3b. expansion passthrough: build_persons keeps the traceability columns and
#     maps the filler's surrogate to the mirror donor (end-to-end wiring check)
# ---------------------------------------------------------------------------

def test_build_persons_keeps_completion_columns_and_mirror_surrogate():
    merged = pd.DataFrame({
        "ZENSUS100m": ["C1", "C2"],
        "ZENSUS1km": ["K1", "K2"],
        "H_ID": ["A", "B"],
        "RegionalSchlussel_ARS": ["031010000000", "031010000000"],
    })
    # Completed donor frames: household A got one filler (P_ID 2) mirrored from
    # household B's person 1; all source_* columns are total.
    mid_persons = pd.DataFrame({
        "H_ID":           ["A", "A", "B"],
        "P_ID":           [1, 2, 1],
        "HP_ALTER":       [40, 38, 38],
        "HP_SEX":         [1, 2, 2],
        "P_TAET":         [1, 1, 1],
        "P_FSCHEIN":      [1, 1, 1],
        "P_FKARTE":       [3, 8, 8],
        "member_imputed": [False, True, False],
        "source_H_ID":    ["A", "B", "B"],
        "source_P_ID":    [1, 1, 1],
    })
    mid_households = pd.DataFrame({
        "H_ID": ["A", "B"],
        "oek_status": [3, 3],
        "hheink_gr1": [4, 4],
        "H_ANZAUTO": [1, 1],
        "H_ANZRAD": [2, 2],
        "anzpedrad": [2, 2],
        "H_ANZPED": [0, 0],
    })

    persons, mapping = assembly.build_persons(merged, mid_households, mid_persons)

    # The traceability columns survive expansion + attribute mapping so the
    # downstream trips stage (via synthesis.population.sampled) can join on them.
    assert {"member_imputed", "source_H_ID", "source_P_ID"} <= set(persons.columns)

    by_pid = persons.set_index("person_id")
    filler = by_pid.loc["C1_A_0_2"]
    mirror = by_pid.loc["C2_B_0_1"]
    assert bool(filler["member_imputed"]) is True
    # The filler's pseudonym surrogate re-links to the MIRROR donor (B, 1).
    assert filler["source_person_id"] == mirror["source_person_id"]
    row = mapping[mapping["source_person_id"] == filler["source_person_id"]].iloc[0]
    assert row["H_ID"] == "B" and row["P_ID"] == 1
    # The regular person of A keeps its own donor reference.
    regular = by_pid.loc["C1_A_0_1"]
    assert regular["source_person_id"] != filler["source_person_id"]


# ---------------------------------------------------------------------------
# 3c. cell RegioStaR7 join: the stage carries the SYNTHETIC HOME's RS7 onto
#     every expanded person (stage-B spatial matching key), and the DONOR
#     household's RegioStaR7 must never shadow it.
# ---------------------------------------------------------------------------

def _rs7_fixture(cell_rs7: bool):
    """Cells + merged PopulationSim output + donor frames; donor households
    carry RegioStaR7=77 (rural) while the cells carry 71 (metropolis), so any
    donor-RS7 leak onto the persons frame is detectable."""
    cells = {
        "ZENSUS100m": ["C1", "C2"],
        "ZENSUS1km": ["K1", "K1"],
        "RegionalSchlussel_ARS": ["031010000000", "031010000000"],
        "POP": [1.0, 1.0],
    }
    if cell_rs7:
        cells["RegioStaR7"] = [71, 71]
    cells = pd.DataFrame(cells)
    merged = pd.DataFrame({
        "ZENSUS100m": ["C1", "C2"],
        "H_ID": ["A", "B"],
    })
    mid_persons = pd.DataFrame({
        "H_ID":      ["A", "B"],
        "P_ID":      [1, 1],
        "HP_ALTER":  [40, 38],
        "HP_SEX":    [1, 2],
        "P_TAET":    [1, 1],
        "P_FSCHEIN": [1, 1],
        "P_FKARTE":  [3, 8],
    })
    mid_households = pd.DataFrame({
        "H_ID": ["A", "B"],
        "oek_status": [3, 3],
        "hheink_gr1": [4, 4],
        "H_ANZAUTO": [1, 1],
        "H_ANZRAD": [2, 2],
        "anzpedrad": [2, 2],
        "H_ANZPED": [0, 0],
        # The DONOR's home-region RS7 (MID_HOUSEHOLD_ATTR_COLS): deliberately
        # DIFFERENT from the cell value so a leak onto persons is detectable.
        "RegioStaR7": [77, 77],
    })
    return cells, merged, mid_households, mid_persons


def test_cell_regiostar7_joined_onto_persons_beats_donor_rs7(caplog):
    """The persons frame's RegioStaR7 must be the SYNTHETIC HOME's cell value
    (71), not the donor household's survey value (77)."""
    import logging

    from braunschweig.popsim import stage

    cells, merged, mid_households, mid_persons = _rs7_fixture(cell_rs7=True)

    with caplog.at_level(logging.INFO, logger="braunschweig.popsim.stage"):
        combined = stage.join_cell_attributes(merged, cells)

    assert "RegioStaR7" in combined.columns
    assert combined["RegioStaR7"].tolist() == [71, 71]
    assert combined["RegionalSchlussel_ARS"].notna().all()
    assert any(
        "cell RegioStaR7 joined" in record.getMessage() for record in caplog.records
    )

    persons, _ = assembly.build_persons(combined, mid_households, mid_persons)
    # Every expanded person carries the CELL's RS7, never the donor's 77.
    assert (persons["RegioStaR7"] == 71).all()


def test_join_cell_attributes_without_regiostar7_is_graceful(caplog):
    """Cells frames without RegioStaR7 (older parquets) must not crash: the
    join is info-logged and the persons frame simply has no RegioStaR7, so
    stage-B matching falls back to the 4-key list (existing warn path)."""
    import logging

    from braunschweig.popsim import stage

    cells, merged, mid_households, mid_persons = _rs7_fixture(cell_rs7=False)

    with caplog.at_level(logging.INFO, logger="braunschweig.popsim.stage"):
        combined = stage.join_cell_attributes(merged, cells)

    assert "RegioStaR7" not in combined.columns
    assert any("RegioStaR7" in record.getMessage() for record in caplog.records)

    persons, _ = assembly.build_persons(combined, mid_households, mid_persons)
    assert "RegioStaR7" not in persons.columns


# ---------------------------------------------------------------------------
# 4. stage flag wiring (source-text check, like test_popsim_stage_seed.py)
# ---------------------------------------------------------------------------

def test_stage_flag_off_uses_legacy_path():
    src = pathlib.Path("braunschweig/popsim/stage.py").read_text(encoding="utf-8")
    # The flag exists with default True (project rule: features default ON).
    assert '"braunschweig.population.popsim.complete_members"' in src
    assert "context.config(KEY_COMPLETE_MEMBERS, True)" in src
    # The ON branch delegates to the cached completed_donor stage (Task 4 refactor).
    assert 'context.stage("completed_donor")' in src
    assert "load_completed_donor" not in src  # inline build replaced by stage consumption
    # The OFF branch still calls the legacy seed loader + donor loader.
    assert "mid.load_mid_seed(" in src
    assert "source.load_donor(mid_dir)" in src
