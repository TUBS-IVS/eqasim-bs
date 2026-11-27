"""End-to-end test of ``scripts/build_student_balance.py`` (#140, Phase 2).

The committed inputs (CHE origins, SrV and MiD reference tables, the Phase 1 model level) are read
from the repository, exactly as the CLI reads them by default. The local-only inputs are
synthetic: the LSN site table is rebuilt from the seed script's pinned rows, the two Zensus tables
are small hand-made files, and the VG250 geometry is stubbed (``student_geometry.load_gemeinden``
and the script's Kreis lookup for the three curated institutions), so no restricted file is read.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
CHE_TABLE = ROOT / "eqasim-data" / "data" / "braunschweig" / "schools" / "che_student_origins_ws2022_23.csv"
KREIS_AGE_CLASSES = ("15-19", "20-29", "30-39", "40-49", "50-59", "60-67", "68+")
ZGB = ("03101", "03102", "03103", "03151", "03153", "03154", "03157", "03158")
CURATED_KREIS = "15003"
AGE_MARGIN = {"18_19": 0.1, "20_24": 0.55, "25_29": 0.25, "30plus": 0.1}


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
    sites = seed.build_sites_frame(seed.SITE_ROWS, semester=seed.SEMESTER)
    sites_path = base / "lsn_university_sites.csv"
    census_sites = seed.build_sites_frame(seed.CENSUS_YEAR_SITE_ROWS, semester=seed.CENSUS_YEAR_SEMESTER)
    census_sites_path = base / "lsn_university_sites_census_year.csv"
    for frame, path in ((sites, sites_path), (census_sites, census_sites_path)):
        with open(path, "w", encoding="utf-8", newline="") as handle:
            handle.write("# synthetic copy of the pinned LSN rows (test fixture)\n")
            frame.to_csv(handle, index=False, lineterminator="\n")

    # 1000 persons aged 20 to 29 per Kreis at the base date; 900 in Braunschweig and 1100 elsewhere at the
    # model date; the Land row (outside the eight Kreise) is ignored by the CLI.
    population = pd.DataFrame(
        [(code, "Kreis " + code, date, 1000 if date == "2022-12-31" else (900 if code == "03101" else 1100))
         for code in ZGB for date in ("2022-12-31", "2025-12-31")]
        + [("03", "Niedersachsen", date, 5000) for date in ("2022-12-31", "2025-12-31")],
        columns=["district_ars5", "district_name", "date", "persons_20_29"])
    population_path = base / "population_20_29.csv"
    population.to_csv(population_path, index=False, lineterminator="\n")
    # Joint MiD shares of (sex, age group): the age margin is .1 / .55 / .25 / .1, so 0.8 of the adult
    # students are aged 20 to 29; women hold 0.6 of every age group.
    age_sex = pd.DataFrame(
        [("DE", sex, age, share * (0.6 if sex == "female" else 0.4))
         for sex in ("female", "male") for age, share in AGE_MARGIN.items()]
        + [("DE", "all", age, share) for age, share in AGE_MARGIN.items()],
        columns=["area", "sex", "age_group", "share_of_adult_students"])
    age_sex_path = base / "age_sex.csv"
    age_sex.to_csv(age_sex_path, index=False, lineterminator="\n")

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
    yield {"sites": str(sites_path), "sites_census_year": str(census_sites_path),
           "population": str(population_path), "mid_age_sex": str(age_sex_path), "vg250": str(vg250_path),
           "zensus_students": str(students_path), "zensus_employed": str(employed_path), "sites_frame": sites}
    patcher.undo()


def _common(world, out_dir=None):
    return ["--sites", world["sites"], "--vg250", world["vg250"], "--source-commit", "abc1234",
            "--max-commute-km", "100"]


def _balance_arguments(world, artifact_dir, *extra):
    """The ``balance`` command line on the synthetic inputs (the census-year table and the two new
    committed-style tables passed explicitly)."""
    return ["balance", *_common(world), "--sites-census-year", world["sites_census_year"],
            "--mid-age-sex", world["mid_age_sex"], "--population", world["population"],
            "--weights", "0.5,0.25,0.1", "--mover-share", "0.2", "--weights-source", "owner",
            "--zensus-students", world["zensus_students"], "--zensus-employed", world["zensus_employed"],
            "--artifact-dir", str(artifact_dir), *extra]


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


def test_cli_writes_all_tables_with_headers(script, world, tmp_path, caplog):
    """Both sub-commands, ``--label`` and ``--final``: every file has its header, the weight triple
    and a ``# Diagnostics:`` line; the snippet exists exactly when the final stage is decided."""
    calibration_dir = tmp_path / "calibration"
    caplog.set_level("WARNING", logger="braunschweig.data.education.student_geometry")
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
    balance = _balance_arguments(world, artifact_dir)
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
        assert "mover_site_kreise=[%s]" % script.MOVER_SITE_KREISE_DEFAULT in text, path.name
        assert "sha256" in text and "abc1234" in text, path.name
    diagnostics = [line for line in _header(committed[0]) if line.startswith("# Diagnostics:")][0]
    parsed = json.loads(diagnostics[len("# Diagnostics:"):])
    assert {"origin_cells", "build_od_table", "balance"} <= set(parsed)
    # the row counts after the movers step hold the mover rows the builder's own count cannot see
    after = parsed["balance"]["rows_per_cell_kind_after_movers"]
    assert after["mover_to_braunschweig"] > 0 == parsed["build_od_table"]["rows_per_cell_kind"]["mover_to_braunschweig"]
    assert parsed["balance"]["movers_to_braunschweig_persons"] > 0

    level_header = "\n".join(_header(popsim / "student_level_table.csv"))
    for key in ("omega=", "Z=", "S=", "mid_students_to_pupils_and_students_ratio=",
                "education_leg_share_control_definition=", "away_all_day_share=", "no day factor applied"):
        assert key in level_header, key
    level = _table(popsim / "student_level_table.csv", dtype={"code": str})
    assert len(level) == 64 and set(level["age_group"]) == {"18_19", "20_24", "25_29", "30plus"}
    living = _table(committed[0], dtype={"home_ars5": str}).query("home_in_region")["students_persons"].sum()
    assert level["students_persons"].sum() == pytest.approx(living, rel=1e-8)
    per_kreis = level.groupby("code")["students_persons"].sum()
    # every Kreis has the same census count in the fixture, so the level share is the population pattern
    assert per_kreis["03101"] / per_kreis.sum() == pytest.approx(900 / (900 + 7 * 1100), rel=1e-8)
    for key in ("omega=", "Z=", "S=", "age_share_20_29_among_adult_students=", "students_living_census_year_persons=",
                "level_total_persons=", "population_base_date=2022-12-31", "population_model_date=2025-12-31",
                "enrollment_semester=WS_2025/26", "census_year_semester=WS_2022/23", "owner decision of 2026-11-27"):
        assert key in level_header, key
    assert float(script.read_header_value(popsim / "student_level_table.csv",
                                          "age_share_20_29_among_adult_students")) == pytest.approx(0.8)
    shares_line = [line for line in level_header.split("\n") if "shares_json=" in line]
    assert len(shares_line) == 1 and shares_line[0].startswith("# Level rule (A18, A19)")
    shares = json.loads(shares_line[0].split("shares_json=")[1])
    assert set(shares) == {"balance", "level"} and set(shares["level"]) == set(ZGB)
    assert shares["level"]["03101"] == pytest.approx(900 / (900 + 7 * 1100), rel=1e-8)
    for name in ("student_od_table.csv", "student_balance_by_site.csv"):
        assert "enrollment_semester=WS_2025/26" in "\n".join(_header(schools / name)), name
    # Every key is read back from the level table, the gate and the pre-check: it occurs exactly once per file.
    for path in (popsim / "student_level_table.csv", artifact_dir / "student_level_gate.csv",
                 artifact_dir / "student_employed_target_precheck.csv"):
        assert script.read_header_value(path, "enrollment_semester") == "WS_2025/26", path.name
        assert script.read_header_value(path, "census_year_semester") == "WS_2022/23", path.name
        for key in ("omega", "Z", "S", "mid_students_to_pupils_and_students_ratio",
                    "age_share_20_29_among_adult_students", "zensus_students_nonemployed_persons",
                    "students_living_census_year_persons", "level_total_persons", "population_base_date",
                    "population_model_date"):
            script.read_header_value(path, key)  # raises when the key is absent or occurs twice
    # The model semester is read back once from each of the seven files that carry it.
    for path in [*committed, artifact_dir / "student_level_gate.csv",
                 artifact_dir / "student_employed_target_precheck.csv"]:
        assert script.read_header_value(path, "enrollment_semester") == "WS_2025/26", path.name
    # The two builds of T differ: the census-year build (A12) is not the build of the balance, and it equals the
    # balance of a run whose --sites is the census-year table (a second --sites overrides the first).
    level_path = popsim / "student_level_table.csv"
    census_year_total = float(script.read_header_value(level_path, "students_living_census_year_persons"))
    level_total = float(script.read_header_value(level_path, "level_total_persons"))
    assert census_year_total != pytest.approx(level_total, rel=1e-3)
    census_run_dir = tmp_path / "census_run"
    assert script.main([*balance, "--sites", world["sites_census_year"], "--label", "census_sites",
                        "--artifact-dir", str(census_run_dir)]) == 0
    census_run_level = census_run_dir / "options" / "census_sites" / "student_level_table.csv"
    assert float(script.read_header_value(census_run_level, "level_total_persons")) == pytest.approx(
        census_year_total, rel=1e-8)
    assert script.read_header_value(census_run_level, "enrollment_semester") == "WS_2022/23"
    gate_header = "\n".join(_header(artifact_dir / "student_level_gate.csv"))
    assert "level_census_year_students_20_29_persons is the same share" in gate_header
    assert re.search(r"gate_triggered=(True|False)", gate_header)
    assert re.search(r"gate_triggered_area_consistent=(True|False)", gate_header)
    assert "ONE value" in gate_header                            # the away-share note of student_level_table
    gate = _table(artifact_dir / "student_level_gate.csv", dtype={"code": str})
    assert {"level_students_persons", "level_students_20_29_persons",
            "level_census_year_students_20_29_persons"} <= set(gate.columns)
    assert parsed["sites"]["outside_lsn_enrollment"]["fallback"].startswith("fallback 2/3")
    assert parsed["sites"]["outside_lsn_enrollment_census_year"]["fallback"].startswith("fallback 0/3")

    # The final tables hold the same students as the labelled run: heads of the enrollment.
    od = _table(committed[0], dtype={"home_ars5": str})
    by_site = _table(committed[1])
    # the files hold ten significant digits, so the sums agree to that precision, not to float noise
    assert by_site["total_persons"].sum() == pytest.approx(by_site["enrollment_persons"].sum(), rel=1e-8)
    local = od[od["scope"] == "local"]
    assert local["students_persons"].sum() == pytest.approx(by_site["enrollment_persons"].sum(), rel=1e-8)
    assert set(od["cell_kind"]) >= {"commuter", "local", "mover_to_braunschweig", "out_commuter"}
    pd.testing.assert_frame_equal(_table(option_dir / "student_od_table.csv", dtype={"home_ars5": str}), od)

    # No written file (calibration, labelled and final tables, gate, pre-check) holds an absolute path:
    # neither the output folders nor the folder of the local-only inputs.
    written = [path for path in tmp_path.rglob("*") if path.is_file()]
    assert len(written) >= 3 + 7 + 5 + 2
    forbidden = {str(tmp_path), tmp_path.as_posix(), str(Path(world["sites"]).parent),
                 Path(world["sites"]).parent.as_posix(), str(ROOT), ROOT.as_posix()}
    for path in written:
        text = path.read_text(encoding="utf-8")
        assert not any(value in text for value in forbidden), path.name
    # LSN sites and curated institutions are disjoint lists: the normal path logs no override warning
    geometry_warnings = [r.getMessage() for r in caplog.records
                         if r.name.endswith("student_geometry") and r.levelname == "WARNING"]
    assert geometry_warnings == []


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


@pytest.mark.parametrize("decided", [True, False])
def test_snippet_exists_exactly_for_a_decided_final_stage(script, world, tmp_path, monkeypatch, decided):
    """A decided final stage writes the snippet (with its header and the triple); an undecided one
    writes none and removes a snippet left by an earlier run."""
    for name in ("calibrate", "calibrate_movers"):
        original = getattr(script.W, name)
        monkeypatch.setattr(script.W, name, (lambda original: lambda *args, **kwargs: original(*args, **kwargs)._replace(
            decided=decided, reasons=[] if decided else ["forced by the test"]))(original))
    out = tmp_path / "calibration"
    out.mkdir()
    snippet = out / "student_commuter_weight_snippet.yml"
    snippet.write_text("stale: from an earlier run" + chr(10), encoding="utf-8")
    assert script.main(["calibrate", *_common(world), "--out-dir", str(out), "--grid-step", "1", "--mover-step", "1"]) == 0
    if not decided:
        assert not snippet.exists()
        return
    text = snippet.read_text(encoding="utf-8")
    lines = text.splitlines()
    header = [line for line in lines if line.startswith("#")]
    assert any(line.startswith("# Diagnostics:") for line in header) and "stale" not in text
    assert [line.strip().split(":")[0] for line in lines if line.startswith("  ")][:3] == ["short", "middle", "long"]
    assert "student_commuter_weights:" in lines
    stage_line = [line for line in header if re.match(r"# Stage \w+: optimum", line)][-1]
    for name in ("short", "middle", "long"):
        value = re.search(r" %s=(\S+)" % name, stage_line).group(1)
        assert float(value) == pytest.approx(float(re.search(r"^  %s: (\S+)$" % name, text, re.M).group(1)))


def test_model_level_pooled_row_must_match_the_surveyed_kreise(script, world, tmp_path):
    """The pooled model row is checked against the sum of the surveyed Kreis rows, not trusted."""
    level = pd.read_csv(script.MODEL_LEVEL_DEFAULT, comment="#", dtype={"code": str})
    level.loc[level["code"] == "03ZGB", "model_students_expanded_persons"] += 10
    broken = tmp_path / "model_level.csv"
    level.to_csv(broken, index=False)
    arguments = _balance_arguments(world, tmp_path / "art", "--label", "x", "--model-level", str(broken))
    with pytest.raises(ValueError, match="03ZGB"):
        script.main(arguments)
    assert not (tmp_path / "art").exists()


def test_kreis_lookup_that_misses_a_curated_site_fails_before_any_output(script, world, tmp_path, monkeypatch):
    """The three institutions outside the LSN table each need a Kreis: a lookup that locates fewer raises."""
    monkeypatch.setattr(script, "locate_site_kreise", lambda path, points: {points["destination_id"].iloc[0]: CURATED_KREIS})
    with pytest.raises(ValueError, match="located 1 of the 3 curated sites"):
        script.main(["calibrate", *_common(world), "--out-dir", str(tmp_path / "out")])
    assert not (tmp_path / "out").exists()


def test_read_header_value_stops_at_space_before_a_bracketed_list(script, tmp_path):
    path = tmp_path / "header.csv"
    path.write_text("# Weights: mover_share=0.45 mover_site_kreise=[03158] weights_source=owner\ncode\n", encoding="utf-8")
    assert script.read_header_value(path, "mover_share") == "0.45"


def test_committed_text_inputs_hash_as_git_stores_them_and_local_only_inputs_as_raw_bytes(script, tmp_path):
    """A committed text input hashes the same on a CRLF and an LF checkout; a local-only file hashes its bytes."""
    lf, crlf = tmp_path / "lf.csv", tmp_path / "crlf.csv"
    lf.write_bytes(b"a,b\n1,2\n")
    crlf.write_bytes(b"a,b\r\n1,2\r\n")

    def digest(line):
        return re.search(r"sha256 ([0-9a-f]{64})", line).group(1)

    committed = script._input_lines([("lf", lf, False), ("crlf", crlf, False)])
    local = script._input_lines([("lf", lf, True), ("crlf", crlf, True)])
    assert digest(committed[0]) == digest(committed[1]) == hashlib.sha256(b"a,b\n1,2\n").hexdigest()
    assert digest(local[1]) == hashlib.sha256(b"a,b\r\n1,2\r\n").hexdigest()
    assert "LF" in committed[0] and "raw bytes" in local[0]   # the header says which normalisation applies


def _header_args(rule):
    return SimpleNamespace(command="balance", source_commit="abc1234", weight_rule=rule, max_commute_km=100.0)


def _header_lines(script, rule):
    prepared = {"region_kreise": list(ZGB), "site_kreise": ["03101"], "origin_kreise": list(ZGB)}
    return script._common_header("t.csv", _header_args(rule), prepared, [], {}, [])


def test_header_cites_only_assumption_ids_described_in_the_module_it_points_to(script):
    """Every id A<n> in the common header is described as an assumption in the module the header names."""
    text = "\n".join(_header_lines(script, "commune_curve"))
    owners = {"student_od_table.py": ROOT / "braunschweig" / "data" / "education" / "student_od_table.py",
              "student_level_table.py": ROOT / "braunschweig" / "calibration" / "student_level_table.py",
              "student_geometry.py": ROOT / "braunschweig" / "data" / "education" / "student_geometry.py"}
    described = {}
    for name, path in owners.items():
        source = path.read_text(encoding="utf-8")
        for number in re.findall(r"(?:ASSUMPTION A|\* A)(\d+)\b", source):
            described.setdefault(int(number), set()).add(name)
    cited = sorted({int(number) for number in re.findall(r"\bA(\d+)\b", text)})
    assert cited, text
    for number in cited:
        assert number in described, "A%d cited in the header but described in no module" % number
        assert any(name in text for name in described[number]), "A%d: header names none of %s" % (
            number, described[number])
    # the commune-population sentence is an owner decision (ADR-0210), not an assumption id
    population_line = [line for line in text.split("\n") if "proportion to population" in line]
    assert population_line and not re.search(r"\bA\d+\b", population_line[0]), population_line


def test_centroid_band_header_prints_the_band_rule_not_the_curve(script):
    curve = "\n".join(_header_lines(script, "commune_curve"))
    bands = "\n".join(_header_lines(script, "centroid_bands"))
    assert "curve_anchors_km=" in curve and "proportion to population" in curve
    assert "curve_anchors_km" not in bands and "proportion to population" not in bands
    assert "weight_rule=centroid_bands" in bands and "band_edges_km=" in bands


def _site_table(world, tmp_path, name, mutate):
    """A copy of the model-semester site table after ``mutate(frame)``."""
    frame = pd.read_csv(world["sites"], comment="#", dtype=str)
    path = tmp_path / name
    path.write_text(mutate(frame).to_csv(index=False, lineterminator="\n"), encoding="utf-8")
    return str(path)


def test_semester_without_a_pinned_enrollment_outside_lsn_fails_naming_it(script, world, tmp_path):
    broken = _site_table(world, tmp_path, "sites.csv", lambda frame: frame.assign(semester="WS 1999/00"))
    with pytest.raises(ValueError, match="WS 1999/00"):
        script.main(["calibrate", "--sites", broken, "--vg250", world["vg250"], "--source-commit", "abc",
                     "--out-dir", str(tmp_path / "out")])
    assert not (tmp_path / "out").exists()


def test_site_table_with_two_semesters_fails_before_any_output(script, world, tmp_path):
    def two(frame):
        frame.loc[frame.index[0], "semester"] = "WS 2022/23"
        return frame
    broken = _site_table(world, tmp_path, "sites.csv", two)
    with pytest.raises(ValueError, match="semester"):
        script.main(["calibrate", "--sites", broken, "--vg250", world["vg250"], "--source-commit", "abc",
                     "--out-dir", str(tmp_path / "out")])
    assert not (tmp_path / "out").exists()


def test_census_year_table_with_a_different_site_set_fails_before_any_output(script, world, tmp_path):
    census = pd.read_csv(world["sites_census_year"], comment="#", dtype=str)
    short = tmp_path / "census_year_short.csv"
    short.write_text(census.iloc[1:].to_csv(index=False, lineterminator="\n"), encoding="utf-8")
    world_short = {**world, "sites_census_year": str(short)}
    with pytest.raises(ValueError, match="census-year"):
        script.main(_balance_arguments(world_short, tmp_path / "art", "--label", "x"))
    assert not (tmp_path / "art").exists()
