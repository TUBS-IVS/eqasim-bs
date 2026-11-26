"""End-to-end test of ``scripts/build_student_balance.py`` (#140, Phase 2).

The committed inputs (CHE origins, SrV and MiD reference tables, the Phase 1 model level) are read
from the repository, exactly as the CLI reads them by default. The local-only inputs are
synthetic: the LSN site table is rebuilt from the seed script's pinned rows, the two Zensus tables
are small hand-made files, and the VG250 geometry is stubbed (``student_geometry.load_gemeinden``
and the script's Kreis lookup for the three curated institutions), so no restricted file is read.
"""
from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
CHE_TABLE = ROOT / "eqasim-data" / "data" / "braunschweig" / "schools" / "che_student_origins_ws2022_23.csv"
KREIS_AGE_CLASSES = ("15-19", "20-29", "30-39", "40-49", "50-59", "60-67", "68+")
ZGB = ("03101", "03102", "03103", "03151", "03153", "03154", "03157", "03158")
CURATED_KREIS = "15003"


def _load_script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)  # never registered in sys.modules
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def script():
    return _load_script("build_student_balance")


def _header(path: Path) -> list:
    with open(path, encoding="utf-8") as handle:
        lines = handle.read().split("\n")
    assert "\r" not in "".join(lines), f"{path.name}: CR in file, expected LF"
    return [line for line in lines if line.startswith("#")]


def _table(path: Path, **kwargs) -> pd.DataFrame:
    return pd.read_csv(path, comment="#", **kwargs)


@pytest.fixture(scope="module")
def world(tmp_path_factory, script):
    """The synthetic local-only inputs and the stubbed geometry; returns the argument fragments."""
    base = tmp_path_factory.mktemp("student_balance_inputs")
    seed = _load_script("seed_lsn_university_sites")
    sites = seed.build_sites_frame(seed.SITE_ROWS, semester="WS 2024/25")
    sites_path = base / "lsn_university_sites.csv"
    with open(sites_path, "w", encoding="utf-8", newline="") as handle:
        handle.write("# synthetic copy of the pinned LSN rows (test fixture)\n")
        sites.to_csv(handle, index=False, lineterminator="\n")

    zensus_students = pd.DataFrame(
        [(code, "Kreis " + code, "kreis", "20-29", 1500, 10000) for code in ZGB]
        + [("03", "Niedersachsen", "land", "20-29", 80000, 1000000),
           ("DG", "Deutschland", "germany", "20-29", 1000000, 10000000)],
        columns=["region", "region_name", "region_level", "age_group",
                 "students_nonemployed_persons", "all_persons"])
    students_path = base / "zensus_students.csv"
    zensus_students.to_csv(students_path, index=False, lineterminator="\n")
    employed = pd.DataFrame([(code, "Kreis " + code, age_class, sex, 50000)
                             for code in ZGB for age_class in KREIS_AGE_CLASSES for sex in ("M", "F")],
                            columns=["region", "region_name", "age_class", "sex", "employed_persons"])
    employed_path = base / "zensus_employed.csv"
    with open(employed_path, "w", encoding="utf-8", newline="") as handle:
        handle.write("# synthetic employed persons (test fixture)\n")
        employed.to_csv(handle, index=False, lineterminator="\n")

    vg250_path = base / "vg250.zip"
    vg250_path.write_bytes(b"stub")  # read only by the stubbed geometry

    # One commune per Kreis code (region, sites, CHE origins) and per site commune on a line, all within
    # 100 km of every site: every CHE origin Kreis is within reach.
    che = pd.read_csv(CHE_TABLE, comment="#", dtype=str)
    codes = sorted({*ZGB, *sites["site_kreis_ars5"], *che.loc[che["row_kind"] == "listed", "origin_ars5"]})
    ags8 = [code + "999" for code in codes] + sorted(set(sites["site_ags8"]))
    gemeinden = pd.DataFrame({"ags8": ags8, "ewz": 10000.0,
                              "x": [600000.0 + 700.0 * i for i in range(len(ags8))], "y": 5800000.0})
    import braunschweig.data.education.student_geometry as geometry
    patcher = pytest.MonkeyPatch()
    patcher.setattr(geometry, "load_gemeinden", lambda path: gemeinden.copy())
    patcher.setattr(script, "locate_site_kreise",
                    lambda path, points: {destination: CURATED_KREIS for destination in points["destination_id"]})
    yield {"sites": str(sites_path), "vg250": str(vg250_path), "zensus_students": str(students_path),
           "zensus_employed": str(employed_path), "sites_frame": sites}
    patcher.undo()


def _common(world, out_dir=None):
    return ["--sites", world["sites"], "--vg250", world["vg250"], "--source-commit", "abc1234",
            "--max-commute-km", "100"]


def test_read_header_value(script, tmp_path):
    path = tmp_path / "table.csv"
    path.write_text("# Source: a\n# Exclusions: n=1, education_leg_share_control_definition=0.37 other=2, z=3\n"
                    "# prose: education_leg_share_control_definition = share of the students\n"
                    "code,value\nx,1\n", encoding="utf-8")
    assert script.read_header_value(path, "education_leg_share_control_definition") == "0.37"
    assert script.read_header_value(path, "n") == "1"
    assert script.read_header_value(path, "z") == "3"
    with pytest.raises(ValueError, match="absent_key"):
        script.read_header_value(path, "absent_key")
    path.write_text("# a: k=1\n# b: k=2\ncode\nx\n", encoding="utf-8")
    with pytest.raises(ValueError, match="more than once"):
        script.read_header_value(path, "k")


def test_site_table_with_a_missing_column_fails_before_any_computation(script, world, tmp_path):
    broken = tmp_path / "sites.csv"
    world["sites_frame"].drop(columns=["site_ags8"]).to_csv(broken, index=False)
    arguments = ["calibrate", "--sites", str(broken), "--vg250", world["vg250"], "--source-commit", "abc",
                 "--out-dir", str(tmp_path / "out")]
    with pytest.raises(ValueError, match="site_ags8"):
        script.main(arguments)
    assert not (tmp_path / "out").exists()


def test_cli_writes_all_tables_with_headers(script, world, tmp_path):
    """Both sub-commands, ``--label`` and ``--final``: every file has its header, the weight triple
    and a ``# Diagnostics:`` line; the snippet exists exactly when the final stage is decided."""
    calibration_dir = tmp_path / "calibration"
    assert script.main(["calibrate", *_common(world), "--out-dir", str(calibration_dir),
                        "--grid-step", "0.5", "--mover-step", "0.5"]) == 0
    calibration_files = ["student_commuter_weight_calibration.csv", "student_commuter_weight_fit.csv",
                         "student_commuter_weight_context.csv"]
    for name in calibration_files:
        header = _header(calibration_dir / name)
        text = "\n".join(header)
        assert any(line.startswith("# Diagnostics:") for line in header), name
        assert "short=" in text and "middle=" in text and "long=" in text, name
        assert "abc1234" in text and "sha256" in text, name
        assert "weight_rule=commune_curve" in text, name
        assert "lsn_university_sites.csv" in text, name           # local-only: file name and hash only
        assert str(tmp_path) not in text, name                     # never an absolute path
    calibration = _table(calibration_dir / calibration_files[0], dtype={"stage": str})
    fit = _table(calibration_dir / calibration_files[1])
    context = _table(calibration_dir / calibration_files[2], dtype={"code": str})
    assert list(context.columns) == ["source", "code", "band", "share", "n_unweighted"]
    assert {"srv_education_distance", "mid_education_distance"} <= set(context["source"])
    assert any(source.startswith("model_stage_") for source in context["source"])
    assert set(fit["stage"]) <= {"weights", "weights_and_movers"} and len(fit)
    stage_one = calibration[calibration["stage"] == "weights"]
    best = stage_one.loc[stage_one["objective_tvd"].idxmin()]
    stage_two_ran = "weights_and_movers" in set(calibration["stage"])
    assert stage_two_ran == (best["short"] >= 1.0 - 1e-9)       # stage two exactly when stage one ends at the bound
    final_stage = "weights_and_movers" if stage_two_ran else "weights"
    final_decided = any(re.search(r"Stage %s: .*decided=True" % final_stage, line)
                        for line in _header(calibration_dir / calibration_files[0]))
    assert (calibration_dir / "student_commuter_weight_snippet.yml").exists() == final_decided

    artifact_dir, eqasim_data = tmp_path / "artifacts", tmp_path / "eqasim-data"
    balance = ["balance", *_common(world), "--weights", "0.5,0.25,0.1", "--mover-share", "0.2",
               "--weights-source", "owner", "--zensus-students", world["zensus_students"],
               "--zensus-employed", world["zensus_employed"], "--artifact-dir", str(artifact_dir)]
    assert script.main([*balance, "--label", "trial"]) == 0       # exit code 0 whatever the gate says
    option_dir = artifact_dir / "options" / "trial"
    names = ["student_od_table.csv", "student_balance_by_site.csv", "student_balance_out_commuters.csv",
             "student_balance_in_commuters_by_origin.csv", "student_level_table.csv", "student_level_gate.csv",
             "student_employed_target_precheck.csv"]
    assert sorted(path.name for path in option_dir.iterdir()) == sorted(names)
    assert script.main([*balance, "--final", "--out-dir", str(eqasim_data)]) == 0
    schools, popsim = eqasim_data / "data" / "braunschweig" / "schools", eqasim_data / "data" / "braunschweig" / "popsim"
    committed = [schools / name for name in names[:4]] + [popsim / "student_level_table.csv"]
    assert all(path.is_file() for path in committed)
    assert (artifact_dir / "student_level_gate.csv").is_file()
    assert (artifact_dir / "student_employed_target_precheck.csv").is_file()
    assert not (schools / "student_level_gate.csv").exists()

    for path in [*committed, option_dir / "student_level_gate.csv"]:
        header = _header(path)
        text = "\n".join(header)
        assert any(line.startswith("# Diagnostics:") for line in header), path.name
        assert "short=0.5" in text and "middle=0.25" in text and "long=0.1" in text, path.name
        assert "mover_share=0.2" in text and "weights_source=owner" in text, path.name
        assert "sha256" in text and "abc1234" in text, path.name
    diagnostics = [line for line in _header(committed[0]) if line.startswith("# Diagnostics:")][0]
    parsed = json.loads(diagnostics[len("# Diagnostics:"):])
    assert {"origin_cells", "build_od_table", "balance"} <= set(parsed)

    level_header = "\n".join(_header(popsim / "student_level_table.csv"))
    for key in ("omega=", "Z=", "S=", "mid_students_to_pupils_and_students_ratio=",
                "srv_age_share_20_29_among_adult_students=", "education_leg_share_control_definition=",
                "away_all_day_share=", "no day factor applied"):
        assert key in level_header, key
    gate_header = "\n".join(_header(artifact_dir / "student_level_gate.csv"))
    assert re.search(r"gate_triggered=(True|False)", gate_header)
    assert "ONE value" in gate_header                            # the away-share note of student_level_table

    # The final tables hold the same students as the labelled run: heads of the enrollment.
    od = _table(committed[0], dtype={"home_ars5": str})
    by_site = _table(committed[1])
    # the files hold ten significant digits, so the sums agree to that precision, not to float noise
    assert by_site["total_persons"].sum() == pytest.approx(by_site["enrollment_persons"].sum(), rel=1e-8)
    local = od[od["scope"] == "local"]
    assert local["students_persons"].sum() == pytest.approx(by_site["enrollment_persons"].sum(), rel=1e-8)
    assert set(od["cell_kind"]) >= {"commuter", "local", "mover_to_braunschweig", "out_commuter"}
    pd.testing.assert_frame_equal(_table(option_dir / "student_od_table.csv", dtype={"home_ars5": str}), od)


def test_curated_site_gets_the_kreis_polygon_that_contains_it_and_a_point_in_none_raises(script):
    """The Kreis of the three institutions outside the LSN table comes from the VG250 polygons."""
    gpd = pytest.importorskip("geopandas")
    from shapely.geometry import box
    kreise = gpd.GeoDataFrame({"ars5": ["15003", "15085"], "geometry": [box(0, 0, 10, 10), box(10, 0, 20, 10)]},
                              crs="EPSG:25832")
    points = pd.DataFrame({"destination_id": ["a", "b"], "x": [5.0, 15.0], "y": [5.0, 5.0]})
    assert script.kreis_of_points(kreise, points) == {"a": "15003", "b": "15085"}
    outside = pd.DataFrame({"destination_id": ["c"], "x": [50.0], "y": [5.0]})
    with pytest.raises(ValueError, match="'c'"):
        script.kreis_of_points(kreise, outside)
