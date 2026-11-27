"""Pins on the committed final student balance of 2026-11-27 (owner weights 1 / 1 / 0.3, mover share 0.4;
enrollment WS 2025/26, level split by the census pattern).

The files are committed, so these tests are not data-gated. They check the internal identities of the
tables and pin a few values copied unrounded from the files written by
``scripts/build_student_balance.py balance --final``. A deliberate re-run changes the pins on purpose.
"""
from __future__ import annotations

import importlib.util
import io
import json
import re
import subprocess
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
GATE = ROOT / "docs" / "runs" / "artifacts" / "student-balance-2026-11-27" / "student_level_gate.csv"
ALL_FILES = [OD, BY_SITE, OUT, IN, LEVEL, GATE]
# The commit that holds the five tables of run student-balance-2026-11-27 (before the origin-pattern proxy).
OFF_REFERENCE_COMMIT = "a5e99557"


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


def test_headers_name_owner_weights_and_mover_site_2026_11_27(script):
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


def _level_shares():
    match = re.search(r"shares_json=(\{.*\})", _header_text(LEVEL))
    assert match, "shares_json missing in the Level rule line"
    return json.loads(match.group(1))


def test_level_table_total_equals_od_region_rows():
    od = _od()
    living_total = od[od.home_in_region].students_persons.sum()
    level = pd.read_csv(LEVEL, comment="#", dtype={"code": str})
    assert abs(living_total - level.students_persons.sum()) < 1e-4
    od_codes = sorted(od[od.home_in_region].home_ars5.unique())
    assert sorted(level.code.unique()) == od_codes
    assert (level.groupby("code").size() == 8).all()
    assert set(level.age_group) == {"18_19", "20_24", "25_29", "30plus"}


def test_level_split_follows_the_header_shares():
    shares = _level_shares()["level"]
    level = pd.read_csv(LEVEL, comment="#", dtype={"code": str})
    per_kreis = level.groupby("code").students_persons.sum()
    observed = per_kreis / per_kreis.sum()
    assert sorted(shares) == sorted(observed.index)
    for code, share in shares.items():
        assert abs(observed[code] - share) < 1e-8, code


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


def test_gate_triggered_2026_11_27(script):
    assert script.read_header_value(GATE, "gate_triggered") == "True"


def test_gate_area_consistent_trigger_2026_11_27(script):
    assert script.read_header_value(GATE, "gate_triggered_area_consistent") == "True"


def test_art_academy_censored_bound_is_capped_at_enrollment_minus_own_kreis_2026_11_27():
    site = pd.read_csv(BY_SITE, comment="#", dtype={"site_kreis_ars5": str})
    art = site[site.destination_id == "Hochsch. f. Bildende Künste Braunschweig | 03101000"].iloc[0]
    assert art.censored_upper_bound_commuters_persons == 657
    od = _od()
    own = od[(od.destination_id == art.destination_id) & (od.home_ars5 == art.site_kreis_ars5)]
    assert len(own) == 1
    assert art.enrollment_persons == 873 and own.origin_persons_scaled.iloc[0] == 216
    assert art.censored_upper_bound_commuters_persons == art.enrollment_persons - own.origin_persons_scaled.iloc[0]


def test_od_table_values_2026_11_27():
    od = _od()
    assert od.loc[0, "origin_persons_scaled"] == pytest.approx(2164.213636, rel=1e-9)
    assert od.loc[1, "distance_km"] == pytest.approx(19.79034171, rel=1e-9)
    movers = od[od.cell_kind == "mover_to_braunschweig"]
    assert movers.movers_persons.iloc[0] == pytest.approx(751.6861903, rel=1e-9)


def test_balance_by_site_values_2026_11_27():
    site = pd.read_csv(BY_SITE, comment="#", dtype={"site_kreis_ars5": str})
    tu = site[site.destination_id == "TU Braunschweig | 03101000"].iloc[0]
    assert tu.residents_region_persons == pytest.approx(13615.11017, rel=1e-9)
    assert tu.in_commuters_persons == pytest.approx(1435.889828, rel=1e-9)
    clausthal = site[site.destination_id == "TU Clausthal | 03153018"].iloc[0]
    assert clausthal.in_commuters_persons == pytest.approx(207.4377334, rel=1e-9)


def test_out_commuter_values_2026_11_27():
    out = pd.read_csv(OUT, comment="#").set_index("destination_id").out_commuters_persons
    assert out["Uni Hannover | 03241001"] == pytest.approx(954.6214891, rel=1e-9)
    assert out["HS Hannover | 03241001"] == pytest.approx(205.6658075, rel=1e-9)
    assert out["HS Braunschweig/Wolfenbüttel | 03360023"] == pytest.approx(128.527981, rel=1e-9)


def test_in_commuter_by_origin_values_2026_11_27():
    inc = pd.read_csv(IN, comment="#", dtype={"home_ars5": str}).set_index("home_ars5").in_commuters_persons
    assert inc["03155"] == pytest.approx(91.84579471, rel=1e-9)
    assert inc["03159"] == pytest.approx(131.0246037, rel=1e-9)
    assert inc["03241"] == pytest.approx(962.4689369, rel=1e-9)


def test_level_table_values_2026_11_27(script):
    assert float(script.read_header_value(LEVEL, "omega")) == pytest.approx(0.5348793385, rel=1e-12)
    level = pd.read_csv(LEVEL, comment="#", dtype={"code": str})
    cell = level[level.code == "03101"].set_index(["sex", "age_group"]).students_persons
    assert cell[("female", "18_19")] == pytest.approx(610.4646126, rel=1e-9)
    assert cell[("female", "20_24")] == pytest.approx(4209.73584, rel=1e-9)
    assert cell[("female", "25_29")] == pytest.approx(1244.619786, rel=1e-9)
    assert cell[("female", "30plus")] == pytest.approx(412.4348083, rel=1e-9)
    assert cell[("male", "18_19")] == pytest.approx(505.3404386, rel=1e-9)
    assert cell[("male", "20_24")] == pytest.approx(3510.626081, rel=1e-9)
    assert cell[("male", "25_29")] == pytest.approx(1434.297268, rel=1e-9)
    assert cell[("male", "30plus")] == pytest.approx(439.5621738, rel=1e-9)


def _first_existing(relative):
    # Local-only inputs (never committed): this worktree or the main checkout.
    return next((base / relative for base in (ROOT, ROOT.parents[2]) if (base / relative).exists()), None)


def _blob_frame(commit, path):
    shown = subprocess.run(["git", "-C", str(ROOT), "show", "%s:%s" % (commit, path.relative_to(ROOT).as_posix())],
                           capture_output=True)
    if shown.returncode != 0:
        pytest.skip("commit %s of the 2026-11-27 tables is not in this clone" % commit)
    return pd.read_csv(io.BytesIO(shown.stdout), comment="#", encoding="utf-8")


def test_without_origin_pattern_proxy_the_balance_equals_the_tables_of_2026_11_27(script, tmp_path):
    """OFF pin of the origin-pattern proxy (ADR-0210, amendment of 2026-11-27): the full ``balance`` run of
    docs/runs/student-balance-2026-11-27.yml with ``--origin-pattern-proxy none`` reproduces the five tables of
    that run. Data-gated (VG250 and the Zensus tables are local-only); takes about ten seconds because it loads
    the VG250 communes, which no smaller input can replace for a pin on the real tables."""
    vg250 = _first_existing(Path("eqasim-data/data/germany/vg250-ew_12-31.utm32s.gpkg.ebenen.zip"))
    students = _first_existing(Path("eqasim-data/data/braunschweig/popsim/zensus2022_students_20_29_kreis.csv"))
    employed = _first_existing(Path("eqasim-data/data/braunschweig/popsim/zensus2022_employed_by_age_kreis.csv"))
    if None in (vg250, students, employed):
        pytest.skip("local-only VG250 archive or Zensus tables not present")
    seed = script._load_script("seed_lsn_university_sites")
    site_paths = []
    for rows, semester in ((seed.SITE_ROWS, seed.SEMESTER), (seed.CENSUS_YEAR_SITE_ROWS, seed.CENSUS_YEAR_SEMESTER)):
        path = tmp_path / ("sites_%d.csv" % len(site_paths))
        seed.build_sites_frame(rows, semester=semester).to_csv(path, index=False, lineterminator="\n")
        site_paths.append(path)
    assert script.main(["balance", "--sites", str(site_paths[0]), "--sites-census-year", str(site_paths[1]),
                        "--vg250", str(vg250), "--zensus-students", str(students), "--zensus-employed", str(employed),
                        "--weights", "1,1,0.3", "--mover-share", "0.4", "--mover-site-kreise", "03158",
                        "--weights-source", "owner", "--source-commit", "test", "--origin-pattern-proxy", "none",
                        "--label", "off", "--artifact-dir", str(tmp_path)]) == 0
    for path in [OD, BY_SITE, OUT, IN, LEVEL]:
        written = pd.read_csv(tmp_path / "options" / "off" / path.name, comment="#", encoding="utf-8")
        pd.testing.assert_frame_equal(written, _blob_frame(OFF_REFERENCE_COMMIT, path), check_exact=False, rtol=1e-9)


def test_level_gate_values_2026_11_27():
    gate = pd.read_csv(GATE, comment="#", dtype={"code": str}).set_index("code")
    assert gate.loc["03101", "balance_students_persons"] == pytest.approx(15132.48495, rel=1e-9)
    assert gate.loc["03102", "difference_in_srv_se"] == pytest.approx(-2.086131443, rel=1e-9)
    assert gate.loc["03103", "balance_students_persons"] == pytest.approx(2624.440449, rel=1e-9)
