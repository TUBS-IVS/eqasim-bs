"""Pure-helper tests for braunschweig.analysis.synthesis.commute_distance_by_kreis."""
import json
import logging

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import Point, box

from braunschweig.analysis.synthesis import commute_distance_by_kreis as S
from braunschweig.calibration import srv_distance_targets as T


def _gemeinden():
    # two Gemeinden in Kreis 03101 (A, B) and one in 03151 (C), 10 km squares
    return gpd.GeoDataFrame(
        {"commune_id": ["03101000", "03101001", "03151005"]},
        geometry=[box(0, 0, 10_000, 10_000), box(10_000, 0, 20_000, 10_000), box(40_000, 0, 50_000, 10_000)],
        crs="EPSG:25832")


def _homes():
    # household 1 in A, household 2 in C; already carry ars5 + commune_id as assign_geographies would
    return gpd.GeoDataFrame(
        {"household_id": [1, 2], "ars5": ["03101", "03151"], "commune_id": ["03101000", "03151005"]},
        geometry=[Point(1_000, 1_000), Point(41_000, 1_000)], crs="EPSG:25832")


def _persons():
    return pd.DataFrame({"person_id": [10, 11, 20], "household_id": [1, 1, 2], "age": [40, 8, 17]})


def _work():
    # person 10 works in B (inter, 12 km east), person 20 works in C (intra, 3 km)
    return gpd.GeoDataFrame({"person_id": [10, 20], "commune_id": ["", ""], "location_id": [1, 2]},
                            geometry=[Point(13_000, 1_000), Point(44_000, 1_000)], crs="EPSG:25832")


def _education():
    return gpd.GeoDataFrame({"person_id": [11, 20], "commune_id": ["", ""], "location_id": [3, 4]},
                            geometry=[Point(2_000, 1_000), Point(41_500, 1_000)], crs="EPSG:25832")


def test_realised_work_frame_distances_and_intra_flag():
    out = S.realised_work_frame(_homes(), _work(), _persons(), _gemeinden())
    out = out.set_index("person_id")
    assert out.loc[10, "distance_km_euclid"] == pytest.approx(12.0)
    assert out.loc[10, "ars5"] == "03101" and bool(out.loc[10, "intra_gemeinde"]) is False
    assert out.loc[20, "distance_km_euclid"] == pytest.approx(3.0)
    assert bool(out.loc[20, "intra_gemeinde"]) is True


def test_realised_education_frame_levels():
    out = S.realised_education_frame(_homes(), _education(), _persons()).set_index("person_id")
    assert out.loc[11, "level"] == "grundschule" and out.loc[11, "distance_km_euclid"] == pytest.approx(1.0)
    assert out.loc[20, "level"] == "upper_secondary"


def _targets_commute():
    rows = []
    for code in list(T.ZGB_KREISE) + ["zgb"]:
        row = {"level_geo": "zgb" if code == "zgb" else "kreis", "code": code,
               "source": "srv", "n_persons": 500, "n_persons_inter": 250, "n_persons_intra": 250,
               "share_intra": 0.5}
        for scope in ("all", "inter", "intra"):
            shares = [0.5, 0.3, 0.2, 0, 0, 0, 0]
            for lbl, s in zip(T.WORK_BAND_LABELS, shares):
                row[f"share_{scope}_{lbl}"] = s
                row[f"share_{scope}_shrunk_{lbl}"] = s
            row[f"emd_noise_95_{scope}"] = 0.02
        rows.append(row)
    return pd.DataFrame(rows)


def _education_targets(comparable_only=True, n_persons=300):
    """One row per (code, level); comparable levels always included, descriptive-only
    levels (oberstufe, bbs) included with comparable=False unless ``comparable_only``."""
    shares = [0.4, 0.3, 0.3, 0, 0, 0]
    levels = list(T.COMPARABLE_LEVELS) if comparable_only else list(T.COMPARABLE_LEVELS) + list(T.DESCRIPTIVE_ONLY_LEVELS)
    rows = []
    for code in list(T.ZGB_KREISE) + ["zgb"]:
        for lvl in levels:
            row = {"level_geo": "zgb" if code == "zgb" else "kreis", "code": code, "source": "srv",
                   "education_level": lvl, "comparable": lvl in T.COMPARABLE_LEVELS,
                   "n_persons": n_persons, "emd_noise_95": 0.02}
            row.update({f"share_{l}": s for l, s in zip(T.EDUCATION_BAND_LABELS, shares)})
            row.update({f"share_shrunk_{l}": s for l, s in zip(T.EDUCATION_BAND_LABELS, shares)})
            rows.append(row)
    return pd.DataFrame(rows)


def test_compare_work_cells_and_decision():
    realised = S.realised_work_frame(_homes(), _work(), _persons(), _gemeinden())
    cells, decision = S.compare_work(realised, _targets_commute(), detour_factor=1.3,
                                     emd_threshold=0.08, min_persons=200)
    assert set(cells["scope"]) == {"all", "inter", "intra"}
    row = cells[(cells["code"] == "03101") & (cells["scope"] == "all")].iloc[0]
    # 12 km * 1.3 = 15.6 km -> band 10_20 (index 2); target puts 0.5 in 0_5 -> EMD > 0
    assert row["n_model"] == 1 and row["model_share_10_20"] == pytest.approx(1.0)
    assert row["emd"] > 0.08 and row["classification"] == "gap"
    assert set(decision) == {"all", "inter", "intra"}
    assert decision["all"]["build"] is True


def test_compare_work_reports_realised_intra_share_on_all_scope():
    # Minor: model_share_intra / target_share_intra live only on the "all" scope row.
    realised = S.realised_work_frame(_homes(), _work(), _persons(), _gemeinden())
    cells, _ = S.compare_work(realised, _targets_commute(), 1.3, 0.08, 200)
    row = cells[(cells["code"] == "03151") & (cells["scope"] == "all")].iloc[0]
    # person 20 is the only 03151 worker and is intra -> realised intra share is 1.0
    assert row["model_share_intra"] == pytest.approx(1.0)
    assert row["target_share_intra"] == pytest.approx(0.5)


def test_compare_work_zero_model_persons_with_target_is_no_reference():
    # code 03151 scope "inter": person 20 (the only 03151 worker) is intra, not inter, so
    # this cell has zero MODEL persons even though a target row exists.
    realised = S.realised_work_frame(_homes(), _work(), _persons(), _gemeinden())
    cells, _ = S.compare_work(realised, _targets_commute(), 1.3, 0.08, 200)
    row = cells[(cells["code"] == "03151") & (cells["scope"] == "inter")].iloc[0]
    assert row["n_model"] == 0
    assert pd.isna(row["emd"])
    assert row["classification"] == "no_reference"


def test_compare_work_missing_target_row_is_no_reference_source_none():
    targets = _targets_commute()
    targets = targets[targets["code"] != "03158"].reset_index(drop=True)
    realised = S.realised_work_frame(_homes(), _work(), _persons(), _gemeinden())
    cells, _ = S.compare_work(realised, targets, 1.3, 0.08, 200)
    row = cells[(cells["code"] == "03158") & (cells["scope"] == "all")].iloc[0]
    assert row["classification"] == "no_reference"
    assert row["source"] == "none"


def test_compare_work_wolfsburg_cell_reports_proxy_source():
    targets = _targets_commute()
    targets.loc[targets["code"] == T.WOLFSBURG_KREIS, "source"] = T.PROXY_SOURCE
    realised = S.realised_work_frame(_homes(), _work(), _persons(), _gemeinden())
    cells, _ = S.compare_work(realised, targets, 1.3, 0.08, 200)
    row = cells[(cells["code"] == T.WOLFSBURG_KREIS) & (cells["scope"] == "all")].iloc[0]
    assert row["source"] == T.PROXY_SOURCE


def test_compare_work_target_zero_persons_keeps_source_not_none():
    # Minor: a target row that EXISTS but has zero reference persons in this scope must
    # keep its own source (not be overwritten with "none", which is reserved for a
    # genuinely absent target row).
    targets = _targets_commute()
    targets.loc[targets["code"] == "03101", ["n_persons", "n_persons_inter", "n_persons_intra"]] = 0
    realised = S.realised_work_frame(_homes(), _work(), _persons(), _gemeinden())
    cells, _ = S.compare_work(realised, targets, 1.3, 0.08, 200)
    row = cells[(cells["code"] == "03101") & (cells["scope"] == "all")].iloc[0]
    assert row["n_reference_persons"] == 0
    assert row["classification"] == "no_reference"
    assert row["source"] == "srv"


def test_model_quantiles_eqasim_curve():
    realised = S.realised_work_frame(_homes(), _work(), _persons(), _gemeinden())
    q = S.model_quantiles(realised)
    assert set(q["code"]) == {"03101", "03151", "zgb"}
    assert len(q[q["code"] == "zgb"]) == 20
    assert np.all(np.diff(q[q["code"] == "zgb"].sort_values("cdf")["distance_km_euclid"]) >= 0)


def test_realised_work_frame_destination_outside_every_polygon_is_inter_and_counted():
    homes = _homes()
    persons = _persons()
    work = gpd.GeoDataFrame({"person_id": [10, 20], "commune_id": ["", ""], "location_id": [1, 2]},
                            geometry=[Point(25_000, 1_000), Point(44_000, 1_000)], crs="EPSG:25832")
    stats = {}
    out = S.realised_work_frame(homes, work, persons, _gemeinden(), stats=stats)
    row10 = out.set_index("person_id").loc[10]
    assert bool(row10["intra_gemeinde"]) is False
    assert pd.isna(row10["dest_commune_id"])
    assert stats["n_dest_outside"] == 1


def test_realised_work_frame_nan_destination_geometry_is_dropped_and_counted():
    homes = _homes()
    persons = _persons()
    work = gpd.GeoDataFrame({"person_id": [10, 20], "commune_id": ["", ""], "location_id": [1, 2]},
                            geometry=[None, Point(44_000, 1_000)], crs="EPSG:25832")
    stats = {}
    out = S.realised_work_frame(homes, work, persons, _gemeinden(), stats=stats)
    assert 10 not in set(out["person_id"])
    assert stats["n_nan_distance"] == 1


def test_realised_work_frame_raises_on_crs_mismatch():
    homes = _homes()
    mismatched_work = _work().to_crs("EPSG:4326")
    with pytest.raises(ValueError, match="CRS mismatch"):
        S.realised_work_frame(homes, mismatched_work, _persons(), _gemeinden())


def test_realised_work_frame_raises_when_home_commune_missing_rate_too_high():
    homes = _homes().copy()
    homes["commune_id"] = pd.NA
    with pytest.raises(ValueError, match="no home Kreis/Gemeinde match"):
        S.realised_work_frame(homes, _work(), _persons(), _gemeinden())


def _many_homes_with_one_missing_commune(n):
    ids = list(range(1, n + 1))
    xs = np.linspace(500, 9_500, n)
    commune_ids = ["03101000"] * (n - 1) + [pd.NA]
    return gpd.GeoDataFrame(
        {"household_id": ids, "ars5": ["03101"] * n, "commune_id": commune_ids},
        geometry=[Point(x, 1_000) for x in xs], crs="EPSG:25832")


def _many_persons(n):
    return pd.DataFrame({"person_id": list(range(1000, 1000 + n)),
                         "household_id": list(range(1, n + 1)), "age": [40] * n})


def _many_work(n):
    xs = np.linspace(500, 9_500, n)
    return gpd.GeoDataFrame({"person_id": list(range(1000, 1000 + n)), "commune_id": [""] * n,
                            "location_id": list(range(n))},
                            geometry=[Point(x, 2_000) for x in xs], crs="EPSG:25832")


def test_realised_work_frame_single_missing_home_commune_below_threshold_is_intra_false_and_counted(caplog):
    n = 25  # 1/25 = 4% < the 5% default threshold -> no raise
    homes = _many_homes_with_one_missing_commune(n)
    persons = _many_persons(n)
    work = _many_work(n)
    with caplog.at_level(logging.INFO, logger="braunschweig.analysis.synthesis.commute_distance_by_kreis"):
        out = S.realised_work_frame(homes, work, persons, _gemeinden())
    missing_person_id = 1000 + n - 1
    row = out.set_index("person_id").loc[missing_person_id]
    assert bool(row["intra_gemeinde"]) is False
    assert any("1 with a missing home Gemeinde" in r.message for r in caplog.records)


def test_realised_education_frame_raises_when_home_commune_missing_rate_too_high():
    homes = _homes().copy()
    homes["commune_id"] = pd.NA
    with pytest.raises(ValueError, match="no home Kreis/Gemeinde match"):
        S.realised_education_frame(homes, _education(), _persons())


def _homes_no_geometry():
    # both households in _homes() carry no home geometry at all (e.g. a broken
    # home.locations / household_id join upstream) -- ars5/commune_id stay populated so
    # this exercises ONLY the no-home-geometry guard, not the Kreis/Gemeinde-match guard.
    homes = _homes().copy()
    homes["geometry"] = [None, None]
    return homes


def test_realised_work_frame_raises_when_no_home_geometry_rate_too_high():
    with pytest.raises(ValueError, match="no home geometry"):
        S.realised_work_frame(_homes_no_geometry(), _work(), _persons(), _gemeinden())


def test_realised_education_frame_raises_when_no_home_geometry_rate_too_high():
    with pytest.raises(ValueError, match="no home geometry"):
        S.realised_education_frame(_homes_no_geometry(), _education(), _persons())


def _many_homes_with_one_missing_geometry(n):
    ids = list(range(1, n + 1))
    xs = np.linspace(500, 9_500, n)
    geoms = [Point(x, 1_000) for x in xs[:-1]] + [None]
    return gpd.GeoDataFrame(
        {"household_id": ids, "ars5": ["03101"] * n, "commune_id": ["03101000"] * n},
        geometry=geoms, crs="EPSG:25832")


def test_realised_work_frame_single_missing_home_geometry_below_threshold_is_counted_and_logged(caplog):
    n = 25  # 1/25 = 4% < the 5% default threshold -> no raise
    homes = _many_homes_with_one_missing_geometry(n)
    persons = _many_persons(n)
    work = _many_work(n)
    stats = {}
    with caplog.at_level(logging.INFO, logger="braunschweig.analysis.synthesis.commute_distance_by_kreis"):
        out = S.realised_work_frame(homes, work, persons, _gemeinden(), stats=stats)
    missing_person_id = 1000 + n - 1
    assert missing_person_id not in set(out["person_id"])
    assert stats["n_no_home"] == 1
    assert any("1/25" in r.message and "no home geometry" in r.message for r in caplog.records)


def test_realised_education_frame_single_missing_home_geometry_below_threshold_is_counted_and_logged(caplog):
    n = 25  # 1/25 = 4% < the 5% default threshold -> no raise
    homes = _many_homes_with_one_missing_geometry(n)
    persons = _many_persons(n)
    education = gpd.GeoDataFrame(
        {"person_id": list(range(1000, 1000 + n)), "commune_id": [""] * n,
         "location_id": list(range(n))},
        geometry=[Point(x, 2_000) for x in np.linspace(500, 9_500, n)], crs="EPSG:25832")
    stats = {}
    with caplog.at_level(logging.INFO, logger="braunschweig.analysis.synthesis.commute_distance_by_kreis"):
        out = S.realised_education_frame(homes, education, persons, stats=stats)
    missing_person_id = 1000 + n - 1
    assert missing_person_id not in set(out["person_id"])
    assert stats["n_no_home"] == 1
    assert any("1/25" in r.message and "no home geometry" in r.message for r in caplog.records)


def test_realised_education_frame_raises_on_crs_mismatch():
    mismatched_education = _education().to_crs("EPSG:4326")
    with pytest.raises(ValueError, match="CRS mismatch"):
        S.realised_education_frame(_homes(), mismatched_education, _persons())


def test_compare_education_ignores_non_comparable_rows():
    edu = S.realised_education_frame(_homes(), _education(), _persons())
    targets = _education_targets(comparable_only=False)
    cells, decisions = S.compare_education(edu, targets, 1.3, 0.08, 200)
    assert set(cells["education_level"]) == set(T.COMPARABLE_LEVELS)
    assert set(decisions) == set(T.COMPARABLE_LEVELS)
    assert set(cells["scope"]) == {"education"}


def test_compare_education_decisions_one_per_level_and_no_gap_when_matching():
    edu = S.realised_education_frame(_homes(), _education(), _persons())
    # grundschule: only person 11 (home 03101), 1.0 km euclid * 1.3 detour = 1.3 km -> band "1_2".
    # upper_secondary: only person 20 (home 03151), 0.5 km euclid * 1.3 detour = 0.65 km -> band "0_1".
    matching = {"grundschule": ("1_2", "03101"), "upper_secondary": ("0_1", "03151")}
    rows = []
    for level in T.COMPARABLE_LEVELS:
        for code in list(T.ZGB_KREISE) + ["zgb"]:
            row = {"level_geo": "zgb" if code == "zgb" else "kreis", "code": code, "source": "srv",
                   "education_level": level, "comparable": True, "n_persons": 0, "emd_noise_95": 0.02}
            row.update({f"share_{lbl}": 0.0 for lbl in T.EDUCATION_BAND_LABELS})
            row.update({f"share_shrunk_{lbl}": 0.0 for lbl in T.EDUCATION_BAND_LABELS})
            rows.append(row)
    targets = pd.DataFrame(rows)
    for level, (lbl, matching_code) in matching.items():
        for code in (matching_code, "zgb"):
            mask = (targets["education_level"] == level) & (targets["code"] == code)
            targets.loc[mask, "n_persons"] = 300
            targets.loc[mask, f"share_shrunk_{lbl}"] = 1.0
    cells, decisions = S.compare_education(edu, targets, 1.3, 0.08, 200)
    assert set(decisions) == set(T.COMPARABLE_LEVELS)
    assert all(d["build"] is False for d in decisions.values())
    # the matching cells classify "ok" (not merely "no_reference"), proving the "ok" path
    # threads correctly through decide_layer, not just the trivial all-no_reference case.
    grund_zgb = cells[(cells["education_level"] == "grundschule") & (cells["code"] == "zgb")].iloc[0]
    assert grund_zgb["classification"] == "ok"


def test_write_outputs_creates_files(tmp_path):
    realised = S.realised_work_frame(_homes(), _work(), _persons(), _gemeinden())
    cells, decision = S.compare_work(realised, _targets_commute(), 1.3, 0.08, 200)
    edu = S.realised_education_frame(_homes(), _education(), _persons())
    edu_targets = _education_targets()
    ecells, edecision = S.compare_education(edu, edu_targets, 1.3, 0.08, 200)
    S.write_outputs(tmp_path, cells, decision, ecells, edecision, S.model_quantiles(realised))
    assert (tmp_path / "commute_by_kreis.csv").exists()
    assert (tmp_path / "education_by_kreis_level.csv").exists()
    assert (tmp_path / "commute_quantiles_model.csv").exists()
    assert (tmp_path / "provenance.json").exists()
    d = json.loads((tmp_path / "decisions.json").read_text(encoding="utf-8"))
    assert "work" in d and "education" in d
    assert (tmp_path / "summary.md").exists()


def test_write_outputs_content_columns_decisions_and_nan_rendering(tmp_path):
    realised = S.realised_work_frame(_homes(), _work(), _persons(), _gemeinden())
    cells, decision = S.compare_work(realised, _targets_commute(), 1.3, 0.08, 200)
    edu = S.realised_education_frame(_homes(), _education(), _persons())
    edu_targets = _education_targets()
    ecells, edecision = S.compare_education(edu, edu_targets, 1.3, 0.08, 200)
    provenance = {"parameters": {"detour_factor": 1.3, "emd_threshold": 0.08},
                 "generated_at": "2026-09-03T00:00:00+00:00"}
    S.write_outputs(tmp_path, cells, decision, ecells, edecision, S.model_quantiles(realised), provenance)

    written = pd.read_csv(tmp_path / "commute_by_kreis.csv")
    # Task 9's plot needs at least this column set; the stage additionally emits
    # target_share_raw_<label>, model_share_intra and target_share_intra (Minors), so this
    # is a subset check, not an exact-equality one.
    required = {"code", "scope", "n_model", "n_reference_persons", "emd", "noise_floor",
                "classification", "is_aggregate", "source"}
    required |= {f"model_share_{lbl}" for lbl in T.WORK_BAND_LABELS}
    required |= {f"target_share_{lbl}" for lbl in T.WORK_BAND_LABELS}
    assert required.issubset(set(written.columns))

    d = json.loads((tmp_path / "decisions.json").read_text(encoding="utf-8"))
    for scope_dict in d["work"].values():
        assert {"build", "reason", "gap_codes", "classification"} <= set(scope_dict)
    for level_dict in d["education"].values():
        assert {"build", "reason", "gap_codes", "classification"} <= set(level_dict)

    assert (tmp_path / "provenance.json").exists()
    prov = json.loads((tmp_path / "provenance.json").read_text(encoding="utf-8"))
    assert prov["parameters"]["detour_factor"] == pytest.approx(1.3)

    summary = (tmp_path / "summary.md").read_text(encoding="utf-8")
    # at least one no_reference cell has a NaN emd; it must render as "n/a", never raise.
    assert "n/a" in summary
    assert "detour_factor=1.3" in summary
