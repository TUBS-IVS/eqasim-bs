"""Pins on the committed final student balance of 2026-11-26 (owner weights 1 / 1 / 0.3, mover share 0.4).

The files are committed, so these tests are not data-gated. They check the internal identities of the
tables and pin a few values copied unrounded from the files written by
``scripts/build_student_balance.py balance --final``. A deliberate re-run changes the pins on purpose.
"""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
SCHOOLS = ROOT / "eqasim-data" / "data" / "braunschweig" / "schools"
OD = SCHOOLS / "student_od_table.csv"
BY_SITE = SCHOOLS / "student_balance_by_site.csv"
OUT = SCHOOLS / "student_balance_out_commuters.csv"
IN = SCHOOLS / "student_balance_in_commuters_by_origin.csv"
LEVEL = ROOT / "eqasim-data" / "data" / "braunschweig" / "popsim" / "student_level_table.csv"
GATE = ROOT / "docs" / "runs" / "artifacts" / "student-balance-2026-11-26" / "student_level_gate.csv"
ALL_FILES = [OD, BY_SITE, OUT, IN, LEVEL, GATE]


@pytest.fixture(scope="module")
def script():
    spec = importlib.util.spec_from_file_location("build_student_balance_pins", ROOT / "scripts" / "build_student_balance.py")
    module = importlib.util.module_from_spec(spec)  # never registered in sys.modules
    spec.loader.exec_module(module)
    return module


def _header_text(path):
    lines = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            if not line.startswith("#"):
                break
            lines.append(line)
    return "".join(lines)


def _od():
    return pd.read_csv(OD, comment="#", dtype={"home_ars5": str, "site_kreis_ars5": str})


def _weight_triple(path):
    match = re.search(r"short=(\S+) middle=(\S+) long=(\S+) mover_share", _header_text(path))
    assert match, path.name
    return tuple(float(value) for value in match.groups())


def test_headers_name_owner_weights_and_mover_site_2026_11_26(script):
    for path in ALL_FILES:
        text = _header_text(path)
        assert "weights_source=owner" in text, path.name
        assert "mover_site_kreise=[03158]" in text, path.name
        assert script.read_header_value(path, "mover_share") == "0.4", path.name
        assert _weight_triple(path) == (1.0, 1.0, 0.3), path.name


def test_site_residents_plus_in_commuters_equal_enrollment():
    site = pd.read_csv(BY_SITE, comment="#", dtype={"site_kreis_ars5": str})
    assert (site.residents_region_persons + site.in_commuters_persons - site.enrollment_persons).abs().max() < 1e-4
    assert (site.total_persons == site.enrollment_persons).all()


def test_no_negative_persons_in_any_table():
    for path in [OD, BY_SITE, OUT, IN, LEVEL]:
        frame = pd.read_csv(path, comment="#")
        columns = [c for c in frame.columns if c.endswith("_persons") or c.endswith("_persons_scaled")]
        assert columns, path.name
        assert (frame[columns].dropna() >= 0).all().all(), path.name


def test_level_table_per_kreis_equals_od_region_rows():
    od = _od()
    living = od[od.home_in_region].groupby("home_ars5").students_persons.sum()
    level = pd.read_csv(LEVEL, comment="#", dtype={"code": str}).groupby("code").students_persons.sum()
    assert sorted(living.index) == sorted(level.index)
    assert (living - level.reindex(living.index)).abs().max() < 1e-4


def test_omega_strictly_inside_unit_interval(script):
    omega = float(script.read_header_value(LEVEL, "omega"))
    assert 0.0 < omega < 1.0


def test_commuter_weights_follow_header_triple(script):
    short, middle, long_ = _weight_triple(OD)
    od = _od()
    rows = od[od.cell_kind.isin(["commuter", "out_commuter"])]
    assert len(rows) > 0
    expected = short * rows.coeff_short + middle * rows.coeff_middle + long_ * rows.coeff_long
    assert (rows.weight - expected).abs().max() < 1e-8


def test_mover_rows_only_at_site_kreis_03158():
    od = _od()
    movers = od[od.cell_kind == "mover_to_braunschweig"]
    assert len(movers) == 1
    assert set(movers.site_kreis_ars5) == {"03158"}


def test_gate_not_triggered(script):
    assert script.read_header_value(GATE, "gate_triggered") == "False"


def test_gate_area_consistent_trigger_2026_11_26(script):
    assert script.read_header_value(GATE, "gate_triggered_area_consistent") == "True"


def test_art_academy_censored_bound_is_capped_at_enrollment_minus_own_kreis_2026_11_26():
    site = pd.read_csv(BY_SITE, comment="#", dtype={"site_kreis_ars5": str})
    art = site[site.destination_id == "Hochsch. f. Bildende Künste Braunschweig | 03101000"].iloc[0]
    assert art.censored_upper_bound_commuters_persons == 686
    od = _od()
    own = od[(od.destination_id == art.destination_id) & (od.home_ars5 == art.site_kreis_ars5)]
    assert len(own) == 1
    assert art.enrollment_persons == 902 and own.origin_persons_scaled.iloc[0] == 216
    assert art.censored_upper_bound_commuters_persons == art.enrollment_persons - own.origin_persons_scaled.iloc[0]


def test_od_table_values_2026_11_26():
    od = _od()
    assert od.loc[0, "origin_persons_scaled"] == pytest.approx(2247.7567969540128, rel=1e-9)  # %.10g after the re-run
    assert od.loc[1, "distance_km"] == pytest.approx(19.79034171, rel=1e-9)
    movers = od[od.cell_kind == "mover_to_braunschweig"]
    assert movers.movers_persons.iloc[0] == pytest.approx(749.995305, rel=1e-9)


def test_balance_by_site_values_2026_11_26():
    site = pd.read_csv(BY_SITE, comment="#", dtype={"site_kreis_ars5": str})
    tu = site[site.destination_id == "TU Braunschweig | 03101000"].iloc[0]
    assert tu.residents_region_persons == pytest.approx(14140.68183, rel=1e-9)
    assert tu.in_commuters_persons == pytest.approx(1491.318171, rel=1e-9)
    clausthal = site[site.destination_id == "TU Clausthal | 03153018"].iloc[0]
    assert clausthal.in_commuters_persons == pytest.approx(204.2543564, rel=1e-9)


def test_out_commuter_values_2026_11_26():
    out = pd.read_csv(OUT, comment="#").set_index("destination_id").out_commuters_persons
    assert out["Uni Hannover | 03241001"] == pytest.approx(980.8409467, rel=1e-9)
    assert out["HS Hannover | 03241001"] == pytest.approx(205.1796574, rel=1e-9)
    assert out["HS Braunschweig/Wolfenbüttel | 03360023"] == pytest.approx(120.8448891, rel=1e-9)


def test_in_commuter_by_origin_values_2026_11_26():
    inc = pd.read_csv(IN, comment="#", dtype={"home_ars5": str}).set_index("home_ars5").in_commuters_persons
    assert inc["03155"] == pytest.approx(92.39408523, rel=1e-9)
    assert inc["03159"] == pytest.approx(130.8373518, rel=1e-9)
    assert inc["03241"] == pytest.approx(980.4686741, rel=1e-9)


def test_level_table_values_2026_11_26(script):
    assert float(script.read_header_value(LEVEL, "omega")) == pytest.approx(0.5349547395, rel=1e-12)
    level = pd.read_csv(LEVEL, comment="#", dtype={"code": str})
    cell = level[level.code == "03101"].set_index(["sex", "age_group"]).students_persons
    assert cell[("female", "18_29")] == pytest.approx(7615.891849, rel=1e-9)
    assert cell[("female", "30plus")] == pytest.approx(492.0310454, rel=1e-9)
    assert cell[("male", "18_29")] == pytest.approx(7009.824419, rel=1e-9)


def test_level_gate_values_2026_11_26():
    gate = pd.read_csv(GATE, comment="#", dtype={"code": str}).set_index("code")
    assert gate.loc["03101", "balance_students_persons"] == pytest.approx(15570.62287, rel=1e-9)
    assert gate.loc["03102", "difference_in_srv_se"] == pytest.approx(-2.096088575, rel=1e-9)
    assert gate.loc["03103", "balance_students_persons"] == pytest.approx(2639.654021, rel=1e-9)
