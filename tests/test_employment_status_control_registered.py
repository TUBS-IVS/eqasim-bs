"""Task 4 (feature #172): register ``employment_status`` as a per-Kreis PopulationSim
STEERING control (distinct from the Task-3 INDEPENDENT validation control in
tests/test_employment_status_control.py, which never raked the synthesis).

THE KEY CORRECTNESS REQUIREMENT (the #97 universe trap): the committed blended target
(target2026_employment_status_by_kreis.csv) reports shares over persons aged 14+ (MiD
P9 / SrV base), but the synthetic ``employment_status`` seed column is assigned to ALL
persons including <14. If the control's per-Kreis total counted every age, children
would distort ``nicht_erwerbstaetig``. So BOTH the seed expression AND the per-Kreis
person total this control's category counts partition must be restricted to age >= 14.

These tests cover:
- the REGISTRY entry shape (name/seed_column/level/min_age/categories/target_columns),
- the generic catalog factory rendering the age-restricted MiD expression for the new
  entry,
- a REGRESSION proving ``economic_status`` (min_age=None) renders its expression
  UNCHANGED (no age clause appended) -- the backward-compatibility guarantee for every
  existing REGISTRY entry,
- ``person_total_by_kreis_min_age`` restricting the per-Kreis PERSON total to the
  single-year age columns >= min_age,
- OFF byte-identical: the new toggle excludes the entry when "off", includes it by
  default ("on", project rule: new features default on).
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import pandas as pd  # noqa: E402
import pytest  # noqa: E402

from braunschweig.popsim import control_spec as cs  # noqa: E402
from braunschweig.popsim.attributes import EMPLOYMENT_STATUS_BY_P_BKAT  # noqa: E402
from braunschweig.popsim.kreis_attribute_control import (  # noqa: E402
    REGISTRY,
    control_columns,
)

_CLASSES = tuple(EMPLOYMENT_STATUS_BY_P_BKAT.values())


def _entry(name):
    return next(c for c in REGISTRY if c.name == name)


# --- Registry entry shape ---


def test_employment_status_registry_entry_shape():
    e = _entry("employment_status")
    assert e.seed_column == "employment_status"
    assert e.level == "person"
    assert e.min_age == 14
    assert tuple(label for label, _ in e.categories) == _CLASSES
    assert e.categories[0] == ("vollzeit", "== 'vollzeit'")
    assert e.target_columns == _CLASSES
    assert e.target_csv_relpath == (
        "braunschweig/targets/target2026_employment_status_by_kreis.csv"
    )


def test_employment_status_control_columns_follow_name_category():
    e = _entry("employment_status")
    assert control_columns(e) == tuple(f"employment_status_{c}" for c in _CLASSES)


# --- Catalog factory: age-restricted MiD expression ---


def test_attribute_kreis_controls_appends_age_clause_for_employment_status():
    controls = cs.attribute_kreis_controls([_entry("employment_status")])
    exprs = {c.name: c.expression_for("mid") for c in controls}
    assert exprs["employment_status_vollzeit"] == (
        "(persons.employment_status == 'vollzeit') & (persons.HP_ALTER >= 14)"
    )
    assert exprs["employment_status_nicht_erwerbstaetig"] == (
        "(persons.employment_status == 'nicht_erwerbstaetig') & (persons.HP_ALTER >= 14)"
    )
    # ENTD cannot express the MiD donor column -> dropped.
    assert all(c.expression_for("entd") is None for c in controls)
    assert all(c.seed_table == cs.SEED_TABLE_PERSONS for c in controls)
    assert all(c.geography == cs.GEO_KREIS for c in controls)


def test_attribute_kreis_controls_all_seven_classes_present_in_order():
    controls = cs.attribute_kreis_controls([_entry("employment_status")])
    assert [c.name for c in controls] == [f"employment_status_{c}" for c in _CLASSES]


# --- REGRESSION: min_age=None entries stay byte-identical ---


def test_economic_status_expression_unchanged_by_min_age_field():
    """economic_status carries min_age=None (the default); its rendered MiD expression
    must NOT gain an age clause -- proves the new dataclass field is backward-compatible
    for every pre-existing REGISTRY entry."""
    econ = _entry("economic_status")
    assert econ.min_age is None
    controls = cs.attribute_kreis_controls([econ])
    exprs = {c.name: c.expression_for("mid") for c in controls}
    assert exprs["economic_status_very_low"] == "(households.oek_status == 1)"
    assert exprs["economic_status_very_high"] == "(households.oek_status == 5)"
    # No age clause anywhere in any rendered expression.
    assert all(" & (households.HP_ALTER" not in e for e in exprs.values())
    assert all("HP_ALTER" not in e for e in exprs.values())


def test_trip_class_expression_unchanged_by_min_age_field():
    """trip_class is the pre-existing PERSON-level entry (min_age=None); its expression
    must also stay exactly as before (no incidental age clause leaking in)."""
    tc = _entry("trip_class")
    assert tc.min_age is None
    controls = cs.attribute_kreis_controls([tc])
    exprs = {c.name: c.expression_for("mid") for c in controls}
    assert exprs["trip_class_0"] == "(persons.trip_class == 0)"
    assert exprs["trip_class_5plus"] == "(persons.trip_class == 3)"


# --- person_total_by_kreis_min_age ---


def _cells_with_single_year_ages(kreis_codes, lo=10, hi=20):
    """A minimal cells frame carrying single-year {M,F}_AGE_<year> columns for
    year in [lo, hi], each with a distinct value per year (per_band_value == year)."""
    data = {}
    for prefix in ("M", "F"):
        for y in range(lo, hi + 1):
            data[f"{prefix}_AGE_{y}"] = [y] * len(kreis_codes)
    return pd.DataFrame(data)


def test_person_total_by_kreis_min_age_counts_only_ages_at_or_above_min_age():
    from braunschweig.popsim.stage import person_total_by_kreis_min_age

    kreis = pd.Series(["03101", "03101", "03102"])
    cells = _cells_with_single_year_ages(kreis, lo=10, hi=20)
    totals = person_total_by_kreis_min_age(cells, kreis, min_age=14)
    # Sum over y=14..20 for M and F: sum(14..20) = 14+15+...+20 = 119; both sexes -> 238.
    expected_per_row = sum(range(14, 21)) * 2
    assert totals["03101"] == expected_per_row * 2  # two rows in 03101
    assert totals["03102"] == expected_per_row


def test_person_total_by_kreis_min_age_excludes_under_min_age_columns():
    from braunschweig.popsim.stage import person_total_by_kreis_min_age

    kreis = pd.Series(["03101"])
    cells = _cells_with_single_year_ages(kreis, lo=10, hi=20)
    total_14 = person_total_by_kreis_min_age(cells, kreis, min_age=14)["03101"]
    total_0 = person_total_by_kreis_min_age(cells, kreis, min_age=10)["03101"]
    assert total_14 < total_0


def test_person_total_by_kreis_min_age_raises_on_no_single_year_columns():
    from braunschweig.popsim.stage import person_total_by_kreis_min_age

    kreis = pd.Series(["03101"])
    cells = pd.DataFrame({"unrelated_col": [1]})
    with pytest.raises(RuntimeError):
        person_total_by_kreis_min_age(cells, kreis, min_age=14)


# --- OFF byte-identical / default-on wiring ---


class _FakeContext:
    """Minimal synpp ExecuteContext stand-in (mirrors test_kreis_control_stage_wiring.py's
    _FakeContext): config(key) takes NO default argument; unresolved keys fall back to
    stage._KREIS_CONTROL_DEFAULT via stage._KREIS_CONTROL_TOGGLE_KEY, exactly as
    configure() declares them."""

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


def test_employment_status_toggle_key_registered():
    from braunschweig.popsim import stage
    assert stage._KREIS_CONTROL_TOGGLE_KEY["employment_status"] == stage.KEY_EMPLOYMENT_STATUS_KREIS_CONTROL
    assert stage._KREIS_CONTROL_DEFAULT["employment_status"] == "on"


def test_active_kreis_entries_includes_employment_status_by_default():
    from braunschweig.popsim import stage
    active = stage.active_kreis_entries(_FakeContext({}), "mid")
    assert "employment_status" in {c.name for c in active}


def test_active_kreis_entries_excludes_employment_status_when_off():
    from braunschweig.popsim import stage
    active = stage.active_kreis_entries(
        _FakeContext({stage.KEY_EMPLOYMENT_STATUS_KREIS_CONTROL: "off"}), "mid"
    )
    names = {c.name for c in active}
    assert "employment_status" not in names
    # The other five default-on entries are unaffected by this toggle.
    assert {"economic_status", "number_of_cars", "number_of_bicycles", "has_ebike",
            "trip_class"} <= names


def test_active_kreis_entries_empty_for_non_mid_source_still_holds():
    from braunschweig.popsim import stage
    assert stage.active_kreis_entries(_FakeContext({}), "entd") == []
