"""``escort_participation`` x Kreis control (issue #227) -- the fourth SrV-anchored
trip-participation control, built by registering the purpose "escort" in the SAME
machinery feature #224 built for work/leisure/education (``mid.PARTICIPATION_W_ZWECK``
/ ``mid.compute_has_purpose_trip`` / ``mid.derive_participation_seed`` /
``attributes.map_participation``); no new derivation logic.

Mirrors ``tests/test_leisure_education_participation.py``'s structure for the new
purpose, plus the escort-specific universe decision it must pin: the MiD seed counts
ONLY the ACTIVE escort leg (W_ZWECK 6, Bringen/Holen -- the escorter's own trip),
NEVER the PASSIVE leg (W_ZWECK 13, the escorted person's own trip, 100% minors on the
raw MiD file per the issue #256 split). The SrV target is built from E_ZWECK_9 == 6
("Holen/Bringen", verified against SrV2023_Datenkodierung_SciUse.xlsx), which codes
only the escorter's trip -- the escorted person's SrV trip carries its own destination
purpose (e.g. Kita = 3). Including W_ZWECK 13 would count escorted minors the SrV
target universe does not, re-opening the #97-style universe trap.
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
from braunschweig.popsim.kreis_attribute_control import (  # noqa: E402
    REGISTRY,
    control_columns,
    load_kreis_target,
)

DATA = REPO / "eqasim-data" / "data" / "braunschweig"
# load_kreis_target joins target_csv_relpath ("braunschweig/targets/...") onto this.
DATA_PATH = REPO / "eqasim-data" / "data"


def _entry(name):
    return next(c for c in REGISTRY if c.name == name)


# --- PARTICIPATION_W_ZWECK: escort = the ACTIVE leg only ---------------------------


def test_participation_w_zweck_escort_is_active_leg_only():
    """escort counts W_ZWECK 6 (Bringen/Holen, the escorter's own trip) ONLY.

    W_ZWECK 13 (the escorted person's own PASSIVE leg, 100% minors per the #256
    split) must NOT be in the set: the SrV target (E_ZWECK_9 == 6, "Holen/Bringen")
    codes only the escorter's trip -- the escorted person's SrV trip carries its own
    destination purpose. Counting 13 would inflate the MiD-side flag with persons the
    target universe does not measure.
    """
    assert mid.PARTICIPATION_W_ZWECK["escort"] == {6}
    assert 13 not in mid.PARTICIPATION_W_ZWECK["escort"]


def test_participation_w_zweck_existing_purposes_unchanged():
    assert mid.PARTICIPATION_W_ZWECK["work"] == {1, 2}
    assert mid.PARTICIPATION_W_ZWECK["leisure"] == {7, 14, 15, 16}
    assert mid.PARTICIPATION_W_ZWECK["education"] == {3, 11, 12}


# --- compute_has_purpose_trip: active fires, passive does not ----------------------


def test_compute_has_purpose_trip_escort_fires_on_active_leg_only():
    persons = pd.DataFrame({
        "H_ID": ["h1", "h2", "h3", "h4"],
        "P_ID": ["p1", "p2", "p3", "p4"],
        "anzwege1": [1, 1, 1, 1],
    })
    wege = pd.DataFrame({
        "H_ID": ["h1", "h2", "h3", "h4"],
        "P_ID": ["p1", "p2", "p3", "p4"],
        "W_ZWECK": [6, 13, 1, 7],  # active escort; PASSIVE escort; work; leisure
    })
    out = mid.compute_has_purpose_trip(persons, wege, "escort")
    by_pid = dict(zip(persons["P_ID"], out))
    assert by_pid["p1"] == 1  # W_ZWECK=6 (active Bringen/Holen) -> escort
    assert by_pid["p2"] == 0  # W_ZWECK=13 (passive, escorted minor) is NOT counted
    assert by_pid["p3"] == 0
    assert by_pid["p4"] == 0


def test_compute_has_purpose_trip_escort_803_804_pass_through():
    persons = pd.DataFrame({"H_ID": ["h1", "h2"], "P_ID": ["p1", "p2"], "anzwege1": [803, 804]})
    wege = pd.DataFrame({"H_ID": [], "P_ID": [], "W_ZWECK": []})
    out = mid.compute_has_purpose_trip(persons, wege, "escort")
    assert list(out) == [803, 804]


# --- Registry entry shape (mirrors work/leisure/education 1:1) ----------------------


def test_registered_hard_person_control():
    e = _entry("escort_participation")
    assert e.seed_column == "escort_participation"
    assert e.level == "person"
    assert e.tier == "hard"
    assert e.categories == (("yes", "== 1"), ("no", "== 0"))
    assert e.target_columns == ("escort_yes", "escort_no")
    assert e.target_csv_relpath == "braunschweig/targets/target2026_escort_participation_by_kreis.csv"
    assert e.min_age is None  # SrV shares are over the full weighted-person universe


def test_control_columns_follow_name_category():
    assert control_columns(_entry("escort_participation")) == (
        "escort_participation_yes", "escort_participation_no")


# --- Catalog factory + importance group ---------------------------------------------


def test_attribute_kreis_controls_renders_person_predicate():
    controls = cs.attribute_kreis_controls([_entry("escort_participation")])
    exprs = {c.name: c.expression_for("mid") for c in controls}
    assert exprs["escort_participation_yes"] == "(persons.escort_participation == 1)"
    assert exprs["escort_participation_no"] == "(persons.escort_participation == 0)"
    assert all(c.seed_table == cs.SEED_TABLE_PERSONS for c in controls)
    assert all(c.geography == cs.GEO_KREIS for c in controls)
    assert all(c.expression_for("entd") is None for c in controls)


def test_importance_group_for_field_classifies_as_kreis_hard():
    assert cs.importance_group_for_field("escort_participation_yes_KREIS") == "kreis_hard"
    assert cs.importance_group_for_field("escort_participation_no_KREIS") == "kreis_hard"


# --- Committed target CSV: loadable, partitioned, full Kreis coverage ---------------


def test_committed_escort_target_loads_via_registry_entry():
    """The committed target2026_escort_participation_by_kreis.csv must satisfy the
    SAME load_kreis_target contract as the other three participation targets
    (region-aggregate row present, every share row sums to 1)."""
    expected = ("03101", "03102", "03103", "03151", "03153", "03154", "03157", "03158")
    df = load_kreis_target(DATA_PATH, _entry("escort_participation"), expected_ars5=expected)
    assert set(df.columns) == {"ars5", "escort_yes", "escort_no"}
    shares = df[["escort_yes", "escort_no"]].sum(axis=1)
    assert (abs(shares - 1.0) < 1e-6).all()


def test_committed_srv_aggregate_carries_escort_column():
    src = pd.read_csv(
        DATA / "srv" / "srv2023_participation_by_kreis.csv", comment="#", dtype={"code": str})
    assert "escort" in src.columns
    kreis = src[src["level"] == "kreis"]
    assert ((kreis["escort"] > 0.0) & (kreis["escort"] < 1.0)).all()


# --- Stage flag wiring ---------------------------------------------------------------


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
    assert stage._KREIS_CONTROL_TOGGLE_KEY["escort_participation"] == stage.KEY_ESCORT_PARTICIPATION_CONTROL
    assert stage._KREIS_CONTROL_DEFAULT["escort_participation"] == "on"


def test_active_kreis_entries_includes_escort_by_default():
    from braunschweig.popsim import stage
    active = stage.active_kreis_entries(_FakeContext({}), "mid")
    assert "escort_participation" in {c.name for c in active}


def test_off_path_excludes_escort_independently():
    from braunschweig.popsim import stage
    active = stage.active_kreis_entries(
        _FakeContext({stage.KEY_ESCORT_PARTICIPATION_CONTROL: "off"}), "mid")
    names = {c.name for c in active}
    assert "escort_participation" not in names
    # The other three participation controls are unaffected.
    assert {"work_participation", "leisure_participation", "education_participation"} <= names


def test_off_controls_csv_has_no_escort_fields():
    from braunschweig.popsim.stage import build_controls_df
    df = build_controls_df(controls_source="catalog", seed="mid", tiers=("tier0", "tier1"))
    assert not any(f.startswith("escort_participation_") for f in set(df["control_field"]))


# --- Seed wiring: load_mid_seed / project_completed_seed -----------------------------


def _write_mini_mid_with_escort_wege(tmp: Path):
    """Minimal MiD triple: p11 has an ACTIVE escort Weg (W_ZWECK 6), p12 has a
    PASSIVE escort Weg only (W_ZWECK 13), p13 has a work Weg only."""
    (tmp / "MiD2023_Haushalte.csv").write_text(
        "H_ID;H_GEW;RegioStaR7;H_GR;H_MIETE;haustyp\n"
        "1;1.0;71;1;1;1\n2;1.0;71;1;1;1\n3;1.0;71;1;1;1\n", encoding="utf-8")
    (tmp / "MiD2023_Personen.csv").write_text(
        "P_ID;H_ID;P_GEW;HP_ALTER;HP_SEX;kernwo;anzwege1;alter_gr1\n"
        "11;1;1.0;40;1;1;1;5\n12;2;1.0;10;2;1;1;2\n13;3;1.0;25;1;1;1;1\n", encoding="utf-8")
    (tmp / "MiD2023_Wege.csv").write_text(
        "H_ID;P_ID;W_ID;W_ZWECK;hvm_imp;W_SZS;W_SZM;W_AZS;W_AZM;wegkm_imp;wegmin_imp1\n"
        "1;11;101;6;4;8;0;8;30;5.0;30\n"      # p11: ACTIVE escort
        "2;12;103;13;1;9;0;9;20;2.0;20\n"     # p12: PASSIVE escort only
        "3;13;104;1;1;9;0;9;20;2.0;20\n",     # p13: work only
        encoding="utf-8")


def test_load_mid_seed_derives_escort_participation_when_active(tmp_path):
    from braunschweig.popsim.mid import load_mid_seed
    _write_mini_mid_with_escort_wege(tmp_path)
    _hh, pers, _rep = load_mid_seed(
        tmp_path, day_filter_values=(), kreis_control_entries=[_entry("escort_participation")],
        kreis_seed_rng=np.random.RandomState(0))
    assert "escort_participation" in pers.columns
    by_pid = dict(zip(pers["P_ID"], pers["escort_participation"]))
    assert by_pid[11] == 1  # active escorter
    assert by_pid[12] == 0  # passive (escorted) leg must NOT count
    assert by_pid[13] == 0
    # Only escort was requested; the other participation columns stay absent.
    assert "work_participation" not in pers.columns
    assert "leisure_participation" not in pers.columns


def test_project_completed_seed_derives_escort_participation(tmp_path):
    from braunschweig.popsim import sources
    (tmp_path / "MiD2023_Wege.csv").write_text(
        "H_ID;P_ID;W_ID;W_ZWECK;hvm_imp;W_SZS;W_SZM;W_AZS;W_AZM;wegkm_imp;wegmin_imp1\n"
        "h1;p1;101;6;4;8;0;8;30;5.0;30\n"
        "h2;p2;103;13;1;9;0;9;20;2.0;20\n", encoding="utf-8")
    cols = sources.get_source("mid").seed_columns()
    households = pd.DataFrame({
        cols.household_id: ["h1", "h2"],
        cols.household_weight: [1.0, 1.0],
        "H_GR": [1, 1], "H_MIETE": [1, 1], "haustyp": [1, 1],
        "RegioStaR7": [73, 73],
    })
    persons = pd.DataFrame({
        cols.person_household_id: ["h1", "h2"],
        cols.person_id: ["p1", "p2"],
        cols.person_weight: [1.0, 1.0],
        cols.age: [40, 10],
        cols.sex: [1, 2],
        "anzwege1": [1, 1],
        "alter_gr1": [5, 2],
    })
    _seed_hh, seed_p = mid.project_completed_seed(
        households, persons, cols,
        kreis_control_entries=[_entry("escort_participation")],
        kreis_seed_rng=np.random.RandomState(0),
        mid_dir=tmp_path,
    )
    assert "escort_participation" in seed_p.columns
    by_pid = dict(zip(seed_p[cols.person_id], seed_p["escort_participation"]))
    assert by_pid["p1"] == 1
    assert by_pid["p2"] == 0


# --- Aggregate + target builder scripts ----------------------------------------------


def test_compute_participation_counts_escort_e_zweck_9_code_6():
    from scripts.build_srv_participation_aggregate import compute_participation
    persons = pd.DataFrame({
        "HHNR": [1, 1, 2],
        "PNR": [1, 2, 1],
        "kreis": ["03101"] * 3,
        "GEWICHT_P_ZENSUS": [1.0, 1.0, 2.0],
    })
    wege = pd.DataFrame({
        "HHNR": [1, 2],
        "PNR": [1, 1],
        "E_ZWECK_9": [6, 7],  # (1,1) has a Holen/Bringen trip, (2,1) leisure only
    })
    out = compute_participation(persons, wege)
    row = out[out["code"] == "03101"].iloc[0]
    assert abs(row["escort"] - 1.0 / 4.0) < 1e-9
    assert abs(row["leisure"] - 2.0 / 4.0) < 1e-9


def test_build_participation_target_escort_rows_and_partition():
    from scripts.build_participation_target import build_participation_target
    df = build_participation_target(DATA, "escort")
    assert set(df.columns) == {"ars5", "source", "n_effective", "escort_yes", "escort_no"}
    assert (abs(df["escort_yes"] + df["escort_no"] - 1.0) < 1e-9).all()
    by_ars5 = df.set_index("ars5")
    # Wolfsburg = SrV region total, same convention as work/leisure/education.
    assert float(by_ars5.loc["03103", "escort_yes"]) == pytest.approx(
        float(by_ars5.loc["Gesamt", "escort_yes"]), abs=1e-9)
    assert by_ars5.loc["03103", "source"] == "srv_region_total"
