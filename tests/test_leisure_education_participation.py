"""Task 5 (feature #224): ``leisure_participation`` + ``education_participation`` Kreis
controls -- identical to ``work_participation`` (Tasks 1-4) except for the W_ZWECK code
set, built DRY by parametrizing the existing seed machinery by purpose rather than
copy-pasting it (see ``mid.PARTICIPATION_W_ZWECK`` / ``mid.compute_has_purpose_trip`` /
``mid.derive_participation_seed`` / ``attributes.map_participation``).

Mirrors ``tests/test_work_participation_control.py`` and
``tests/test_map_work_participation.py``'s structure and depth for the two new purposes;
does NOT re-test work_participation itself (see those two files, which must keep passing
unchanged -- the regression proof for the refactor into thin wrappers).
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import pytest  # noqa: E402

from braunschweig.popsim import attributes  # noqa: E402
from braunschweig.popsim import control_spec as cs  # noqa: E402
from braunschweig.popsim import mid  # noqa: E402
from braunschweig.popsim import sources  # noqa: E402
from braunschweig.popsim.kreis_attribute_control import (  # noqa: E402
    REGISTRY,
    control_columns,
)


def _entry(name):
    return next(c for c in REGISTRY if c.name == name)


# --- PARTICIPATION_W_ZWECK: the shared purpose -> code-set constant -----------------


def test_participation_w_zweck_codes():
    """The leisure set widened from {7} to {7, 14, 15, 16} with issue #241, which brings it
    CLOSER to the SrV target it is compared against, not further away.

    The target is built from SrV ``E_ZWECK_9``, whose leisure is ONE coarse bucket (code 7)
    fed by the fine purposes 13-18 (Kultur, Gaststaette, Privater Besuch, Erholung/Sport,
    Sportstaette, Andere Freizeit). MiD splits the same concept across W_ZWECK 7 + 14 Sport +
    15 Freunde + 16 Unterricht. While 14/15/16 sat in "other" via the silent fallback, the
    synthetic side of this control counted a NARROWER leisure than its target -- a latent
    apples-to-apples gap (the #96 / #169 class), now closed for 14 and 15.

    One construct difference remains, verified on the SrV microdata and deliberately accepted:
    MiD 16 "Unterricht (nicht Schule)" corresponds to SrV V_ZWECK 7 "Andere
    Bildungseinrichtung", which SrV rolls into E_ZWECK_9 = 4 (education), while we map it to
    leisure. It is 0.11 % of MiD legs, and mapping it to eqasim ``education`` would anchor an
    evening class at the person's assigned school -- see the ADR.
    """
    assert mid.PARTICIPATION_W_ZWECK == {
        "work": {1, 2}, "leisure": {7, 14, 15, 16}, "education": {3, 11, 12},
    }


# --- Registry entry shape (mirrors work_participation's shape 1:1) -----------------


@pytest.mark.parametrize("purpose,yes_col,no_col", [
    ("leisure", "leisure_yes", "leisure_no"),
    ("education", "education_yes", "education_no"),
])
def test_registered_hard_person_control(purpose, yes_col, no_col):
    name = f"{purpose}_participation"
    e = _entry(name)
    assert e.seed_column == name
    assert e.level == "person"
    assert e.tier == "hard"
    assert e.categories == (("yes", "== 1"), ("no", "== 0"))
    assert e.target_columns == (yes_col, no_col)
    assert e.target_csv_relpath == f"braunschweig/targets/target2026_{name}_by_kreis.csv"


@pytest.mark.parametrize("purpose", ["leisure", "education"])
def test_control_columns_follow_name_category(purpose):
    name = f"{purpose}_participation"
    e = _entry(name)
    assert control_columns(e) == (f"{name}_yes", f"{name}_no")


# --- Catalog factory: person-level predicate rendering -----------------------------


@pytest.mark.parametrize("purpose", ["leisure", "education"])
def test_attribute_kreis_controls_renders_person_predicate(purpose):
    name = f"{purpose}_participation"
    controls = cs.attribute_kreis_controls([_entry(name)])
    exprs = {c.name: c.expression_for("mid") for c in controls}
    assert exprs[f"{name}_yes"] == f"(persons.{name} == 1)"
    assert exprs[f"{name}_no"] == f"(persons.{name} == 0)"
    assert all(c.seed_table == cs.SEED_TABLE_PERSONS for c in controls)
    assert all(c.geography == cs.GEO_KREIS for c in controls)
    # ENTD cannot express the MiD donor Wege-derived column -> dropped.
    assert all(c.expression_for("entd") is None for c in controls)


# --- Importance profile: tier="hard" auto-classifies into the "kreis_hard" group ---


@pytest.mark.parametrize("purpose", ["leisure", "education"])
def test_importance_group_for_field_classifies_as_kreis_hard(purpose):
    name = f"{purpose}_participation"
    assert cs.importance_group_for_field(f"{name}_yes_KREIS") == "kreis_hard"
    assert cs.importance_group_for_field(f"{name}_no_KREIS") == "kreis_hard"


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


@pytest.mark.parametrize("purpose,key_name", [
    ("leisure", "KEY_LEISURE_PARTICIPATION_CONTROL"),
    ("education", "KEY_EDUCATION_PARTICIPATION_CONTROL"),
])
def test_toggle_key_registered_and_defaults_on(purpose, key_name):
    from braunschweig.popsim import stage
    name = f"{purpose}_participation"
    assert stage._KREIS_CONTROL_TOGGLE_KEY[name] == getattr(stage, key_name)
    assert stage._KREIS_CONTROL_DEFAULT[name] == "on"


def test_active_kreis_entries_includes_both_new_controls_by_default():
    from braunschweig.popsim import stage
    active = stage.active_kreis_entries(_FakeContext({}), "mid")
    names = {c.name for c in active}
    assert names == {
        "economic_status", "number_of_cars", "number_of_bicycles", "has_ebike",
        "trip_class", "employment_status", "pt_ticket_group",
        "work_participation", "leisure_participation", "education_participation",
    }


def test_off_path_excludes_both_new_controls_independently():
    from braunschweig.popsim import stage
    active = stage.active_kreis_entries(
        _FakeContext({
            stage.KEY_LEISURE_PARTICIPATION_CONTROL: "off",
            stage.KEY_EDUCATION_PARTICIPATION_CONTROL: "off",
        }), "mid",
    )
    names = {c.name for c in active}
    assert "leisure_participation" not in names
    assert "education_participation" not in names
    # work_participation and the other six default-on entries are unaffected.
    assert "work_participation" in names
    assert {"economic_status", "number_of_cars", "number_of_bicycles", "has_ebike",
            "trip_class", "employment_status"} <= names


def test_off_controls_csv_byte_identical_to_pre_task_default():
    """The OFF path (neither new control requested) leaves build_controls_df's output
    byte-identical to a call that never mentions them at all."""
    from braunschweig.popsim.stage import build_controls_df

    a = build_controls_df(controls_source="catalog", seed="mid", tiers=("tier0", "tier1"))
    b = build_controls_df(
        controls_source="catalog", seed="mid", tiers=("tier0", "tier1"),
        kreis_control_names=(),
    )
    assert a.to_csv(index=False) == b.to_csv(index=False)
    fields = set(a["control_field"])
    assert not any(f.startswith("leisure_participation_") or f.startswith("education_participation_")
                   for f in fields)


# --- compute_has_purpose_trip: generic core, tested for leisure + education ---------


def test_compute_has_purpose_trip_leisure_fires_only_on_w_zweck_7():
    persons = pd.DataFrame({
        "H_ID": ["h1", "h2", "h3"],
        "P_ID": ["p1", "p2", "p3"],
        "anzwege1": [1, 1, 2],
    })
    wege = pd.DataFrame({
        "H_ID": ["h1", "h2", "h3", "h3"],
        "P_ID": ["p1", "p2", "p3", "p3"],
        "W_ZWECK": [7, 1, 3, 11],  # p1: leisure; p2: work; p3: education (two codes)
    })
    out = mid.compute_has_purpose_trip(persons, wege, "leisure")
    by_pid = dict(zip(persons["P_ID"], out))
    assert by_pid["p1"] == 1  # W_ZWECK=7 -> leisure
    assert by_pid["p2"] == 0  # W_ZWECK=1 (work) is NOT leisure
    assert by_pid["p3"] == 0  # W_ZWECK in {3, 11} (education) is NOT leisure


@pytest.mark.parametrize("education_code", [3, 11, 12])
def test_compute_has_purpose_trip_education_fires_on_all_three_codes(education_code):
    persons = pd.DataFrame({"H_ID": ["h1"], "P_ID": ["p1"], "anzwege1": [1]})
    wege = pd.DataFrame({"H_ID": ["h1"], "P_ID": ["p1"], "W_ZWECK": [education_code]})
    out = mid.compute_has_purpose_trip(persons, wege, "education")
    assert out.iloc[0] == 1


def test_compute_has_purpose_trip_education_does_not_fire_on_work_or_leisure_codes():
    persons = pd.DataFrame({
        "H_ID": ["h1", "h2"], "P_ID": ["p1", "p2"], "anzwege1": [1, 1],
    })
    wege = pd.DataFrame({
        "H_ID": ["h1", "h2"], "P_ID": ["p1", "p2"], "W_ZWECK": [1, 7],  # work, leisure
    })
    out = mid.compute_has_purpose_trip(persons, wege, "education")
    assert list(out) == [0, 0]


@pytest.mark.parametrize("purpose", ["leisure", "education"])
def test_compute_has_purpose_trip_803_804_pass_through(purpose):
    # A person whose OWN diary trip count is a non-response code must carry that code
    # through unchanged, regardless of purpose -- never forced to 0 (see
    # attributes.map_participation's impute_codes=(803, 804)).
    persons = pd.DataFrame({"H_ID": ["h1", "h2"], "P_ID": ["p1", "p2"], "anzwege1": [803, 804]})
    wege = pd.DataFrame({"H_ID": [], "P_ID": [], "W_ZWECK": []})
    out = mid.compute_has_purpose_trip(persons, wege, purpose)
    assert list(out) == [803, 804]


def test_compute_has_purpose_trip_rejects_unknown_purpose():
    persons = pd.DataFrame({"H_ID": ["h1"], "P_ID": ["p1"], "anzwege1": [1]})
    wege = pd.DataFrame({"H_ID": ["h1"], "P_ID": ["p1"], "W_ZWECK": [1]})
    with pytest.raises(ValueError, match="purpose must be one of"):
        mid.compute_has_purpose_trip(persons, wege, "commute")


# --- attributes.map_participation + purpose-specific thin wrappers -----------------


@pytest.mark.parametrize("map_fn_name,default_src", [
    ("map_leisure_participation", "leisure_participation_src"),
    ("map_education_participation", "education_participation_src"),
])
def test_map_purpose_participation_binary_and_impute(map_fn_name, default_src):
    map_fn = getattr(attributes, map_fn_name)
    persons = pd.DataFrame({
        default_src: [0, 1, 1, 0, 803],
        "alter_gr1": [1, 1, 1, 1, 1],
    })
    out = map_fn(persons, rng=np.random.RandomState(0))
    out_col = default_src.replace("_src", "")
    assert out[out_col].dtype.kind == "i"
    assert set(out[out_col].unique()) <= {0, 1}
    assert out[out_col].iloc[4] in (0, 1)  # 803 imputed, never left raw


@pytest.mark.parametrize("map_fn_name", ["map_leisure_participation", "map_education_participation"])
def test_map_purpose_participation_fails_on_absent_column(map_fn_name):
    map_fn = getattr(attributes, map_fn_name)
    persons = pd.DataFrame({"alter_gr1": [1, 2]})
    with pytest.raises(KeyError):
        map_fn(persons)


def test_map_participation_generic_core_used_by_all_three_wrappers():
    # The three purpose wrappers all delegate to the SAME generic core (no duplicated
    # missing.AttributeSpec logic); prove it directly with an arbitrary name.
    persons = pd.DataFrame({"my_src": [0, 1, 803], "alter_gr1": [1, 1, 1]})
    out = attributes.map_participation(persons, "my_control", source_col="my_src", rng=np.random.RandomState(0))
    assert "my_control" in out.columns
    assert out["my_control"].dtype.kind == "i"


# --- derive_participation_seed: generic realised-plan-source remapping -------------
# Mirrors test_map_work_participation.py's plan-source trio for leisure + education.


def _plan_source_persons_and_wege_for(purpose_code: int, other_code: int):
    """A real weekday donor (p1, has a Weg of ``purpose_code``) and a weekend reporter
    (p2, own Wege have ``other_code`` only) whose plan source was remapped to the
    weekday donor (source -> h1/p1)."""
    persons = pd.DataFrame({
        "H_ID": ["h1", "h2"],
        "P_ID": ["p1", "p2"],
        "anzwege1": [5, 1],
        "alter_gr1": [5, 5],
        "member_imputed": [False, False],
        "source_H_ID": ["h1", "h1"],
        "source_P_ID": ["p1", "p1"],
    })
    wege = pd.DataFrame({
        "H_ID": ["h1", "h2"],
        "P_ID": ["p1", "p2"],
        "W_ZWECK": [purpose_code, other_code],
    })
    return persons, wege


@pytest.mark.parametrize("purpose,purpose_code,other_code", [
    ("leisure", 7, 1),        # leisure Weg vs. a work Weg
    ("education", 3, 7),      # education Weg vs. a leisure Weg
])
def test_derive_participation_seed_uses_realised_plan_source_not_own(purpose, purpose_code, other_code):
    persons, wege = _plan_source_persons_and_wege_for(purpose_code, other_code)
    out = mid.derive_participation_seed(persons, wege, purpose, rng=np.random.RandomState(0))
    col = f"{purpose}_participation"
    by_pid = dict(zip(out["P_ID"], out[col]))
    assert by_pid["p1"] == 1
    # p2's realised plan is p1's -> 1, NOT 0 from its own Weg of a different purpose.
    assert by_pid["p2"] == 1
    assert f"_plan_source_has_{purpose}_trip" not in out.columns


@pytest.mark.parametrize("purpose,purpose_code,other_code", [
    ("leisure", 7, 1),
    ("education", 3, 7),
])
def test_derive_participation_seed_falls_back_to_own_without_source_columns(purpose, purpose_code, other_code):
    persons, wege = _plan_source_persons_and_wege_for(purpose_code, other_code)
    persons = persons.drop(columns=["source_H_ID", "source_P_ID", "member_imputed"])
    out = mid.derive_participation_seed(persons, wege, purpose, rng=np.random.RandomState(0))
    col = f"{purpose}_participation"
    by_pid = dict(zip(out["P_ID"], out[col]))
    assert by_pid["p1"] == 1   # own purpose Weg -> 1
    assert by_pid["p2"] == 0   # own Weg of a different purpose -> 0


def test_derive_participation_seed_raises_on_unresolved_plan_source():
    persons, wege = _plan_source_persons_and_wege_for(7, 1)
    persons.loc[persons["P_ID"] == "p2", "source_P_ID"] = "does_not_exist"
    with pytest.raises(ValueError, match="absent from the donor frame"):
        mid.derive_participation_seed(persons, wege, "leisure", rng=np.random.RandomState(0))


# --- Seed wiring: load_mid_seed / project_completed_seed (mid_dir + wege gating) ---


def _write_mini_mid_with_wege(tmp: Path):
    """A minimal MiD household/person/Wege CSV triple exercising work + leisure +
    education Wege on distinct persons, so all three participation controls can be
    derived from the SAME fixture."""
    hh = tmp / "MiD2023_Haushalte.csv"
    pers = tmp / "MiD2023_Personen.csv"
    wege = tmp / "MiD2023_Wege.csv"
    hh.write_text(
        "H_ID;H_GEW;RegioStaR7;H_GR;H_MIETE;haustyp\n"
        "1;1.0;71;1;1;1\n2;1.0;71;1;1;1\n3;1.0;71;1;1;1\n", encoding="utf-8")
    pers.write_text(
        "P_ID;H_ID;P_GEW;HP_ALTER;HP_SEX;kernwo;anzwege1;alter_gr1\n"
        "11;1;1.0;40;1;1;2;5\n12;2;1.0;20;2;1;1;2\n13;3;1.0;25;1;1;1;1\n", encoding="utf-8")
    wege.write_text(
        "H_ID;P_ID;W_ID;W_ZWECK;hvm_imp;W_SZS;W_SZM;W_AZS;W_AZM;wegkm_imp;wegmin_imp1\n"
        "1;11;101;1;4;8;0;8;30;5.0;30\n"      # p11: work
        "1;11;102;7;4;18;0;18;30;5.0;30\n"    # p11: leisure
        "2;12;103;11;1;9;0;9;20;2.0;20\n"     # p12: education
        "3;13;104;7;1;9;0;9;20;2.0;20\n",     # p13: leisure only
        encoding="utf-8")


def _all_three_entries():
    return [_entry("work_participation"), _entry("leisure_participation"), _entry("education_participation")]


def test_load_mid_seed_derives_all_three_participation_controls_when_active(tmp_path):
    from braunschweig.popsim.mid import load_mid_seed
    _write_mini_mid_with_wege(tmp_path)
    _hh, pers, _rep = load_mid_seed(
        tmp_path, day_filter_values=(), kreis_control_entries=_all_three_entries(),
        kreis_seed_rng=np.random.RandomState(0))
    for col in ("work_participation", "leisure_participation", "education_participation"):
        assert col in pers.columns
        assert set(pers[col]).issubset({0, 1})
    by_pid = {c: dict(zip(pers["P_ID"], pers[c])) for c in
              ("work_participation", "leisure_participation", "education_participation")}
    assert by_pid["work_participation"][11] == 1
    assert by_pid["leisure_participation"][11] == 1
    assert by_pid["education_participation"][11] == 0
    assert by_pid["education_participation"][12] == 1
    assert by_pid["leisure_participation"][13] == 1
    assert by_pid["work_participation"][13] == 0


def test_load_mid_seed_off_path_no_participation_control_touches_wege(tmp_path):
    """OFF byte-identity: with work_participation, leisure_participation, AND
    education_participation all inactive, load_mid_seed must not touch
    MiD2023_Wege.csv at all -- proven by NOT writing that file."""
    from braunschweig.popsim.mid import load_mid_seed
    hh = tmp_path / "MiD2023_Haushalte.csv"
    pers = tmp_path / "MiD2023_Personen.csv"
    hh.write_text(
        "H_ID;H_GEW;RegioStaR7;H_GR;H_MIETE;haustyp\n1;1.0;71;1;1;1\n", encoding="utf-8")
    pers.write_text(
        "P_ID;H_ID;P_GEW;HP_ALTER;HP_SEX;kernwo;anzwege1;alter_gr1\n11;1;1.0;40;1;1;2;5\n",
        encoding="utf-8")
    assert not (tmp_path / "MiD2023_Wege.csv").exists()
    _hh, pers_off, _rep = load_mid_seed(
        tmp_path, day_filter_values=(),
        kreis_control_entries=[_entry("trip_class")],  # an unrelated active entry
        kreis_seed_rng=np.random.RandomState(0),
    )
    assert "leisure_participation" not in pers_off.columns
    assert "education_participation" not in pers_off.columns
    assert "work_participation" not in pers_off.columns
    assert not (tmp_path / "MiD2023_Wege.csv").exists()


def test_load_mid_seed_leisure_only_active_does_not_derive_others(tmp_path):
    """Activating ONLY leisure_participation must not derive work_participation or
    education_participation (each control is independently gated)."""
    from braunschweig.popsim.mid import load_mid_seed
    _write_mini_mid_with_wege(tmp_path)
    _hh, pers, _rep = load_mid_seed(
        tmp_path, day_filter_values=(), kreis_control_entries=[_entry("leisure_participation")],
        kreis_seed_rng=np.random.RandomState(0))
    assert "leisure_participation" in pers.columns
    assert "work_participation" not in pers.columns
    assert "education_participation" not in pers.columns


def test_load_mid_seed_participation_requires_seeded_rng(tmp_path):
    from braunschweig.popsim.mid import load_mid_seed
    _write_mini_mid_with_wege(tmp_path)
    with pytest.raises(ValueError, match="kreis_seed_rng is not set"):
        load_mid_seed(
            tmp_path, day_filter_values=(), kreis_control_entries=[_entry("education_participation")])


def _completed_donor_frames():
    cols = sources.get_source("mid").seed_columns()
    households = pd.DataFrame({
        cols.household_id: ["h1", "h2", "h3"],
        cols.household_weight: [1.0, 1.0, 1.0],
        "H_GR": [1, 1, 1], "H_MIETE": [1, 1, 1], "haustyp": [1, 1, 1],
        "RegioStaR7": [73, 73, 73],
    })
    persons = pd.DataFrame({
        cols.person_household_id: ["h1", "h2", "h3"],
        cols.person_id: ["p1", "p2", "p3"],
        cols.person_weight: [1.0, 1.0, 1.0],
        cols.age: [40, 20, 25],
        cols.sex: [1, 2, 1],
        "anzwege1": [2, 1, 1],
        "alter_gr1": [5, 2, 1],
    })
    return cols, households, persons


def _write_wege_only(tmp: Path):
    wege = tmp / "MiD2023_Wege.csv"
    wege.write_text(
        "H_ID;P_ID;W_ID;W_ZWECK;hvm_imp;W_SZS;W_SZM;W_AZS;W_AZM;wegkm_imp;wegmin_imp1\n"
        "h1;p1;101;1;4;8;0;8;30;5.0;30\n"
        "h1;p1;102;7;4;18;0;18;30;5.0;30\n"
        "h2;p2;103;11;1;9;0;9;20;2.0;20\n"
        "h3;p3;104;7;1;9;0;9;20;2.0;20\n", encoding="utf-8")


def test_project_completed_seed_derives_leisure_and_education(tmp_path):
    _write_wege_only(tmp_path)
    cols, households, persons = _completed_donor_frames()
    _seed_hh, seed_p = mid.project_completed_seed(
        households, persons, cols,
        kreis_control_entries=[_entry("leisure_participation"), _entry("education_participation")],
        kreis_seed_rng=np.random.RandomState(0),
        mid_dir=tmp_path,
    )
    assert "leisure_participation" in seed_p.columns
    assert "education_participation" in seed_p.columns
    assert "work_participation" not in seed_p.columns
    by_leisure = dict(zip(seed_p[cols.person_id], seed_p["leisure_participation"]))
    by_education = dict(zip(seed_p[cols.person_id], seed_p["education_participation"]))
    assert by_leisure["p1"] == 1
    assert by_education["p2"] == 1
    assert by_leisure["p3"] == 1
    assert by_education["p1"] == 0


def test_project_completed_seed_participation_requires_mid_dir():
    """No silent fallback: leisure_participation active without mid_dir must fail fast."""
    cols, households, persons = _completed_donor_frames()
    with pytest.raises(ValueError, match="mid_dir"):
        mid.project_completed_seed(
            households, persons, cols,
            kreis_control_entries=[_entry("leisure_participation")], kreis_seed_rng=np.random.RandomState(0),
        )


def test_project_completed_seed_participation_inactive_is_noop_without_mid_dir():
    """OFF byte-identity: with neither new control active, mid_dir is not required."""
    cols, households, persons = _completed_donor_frames()
    _seed_hh, seed_p = mid.project_completed_seed(
        households, persons, cols, kreis_control_entries=(),
    )
    assert "leisure_participation" not in seed_p.columns
    assert "education_participation" not in seed_p.columns
