"""Pure-helper tests for braunschweig.analysis.synthesis.commute_distance_by_kreis."""
import json

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
               "source": "srv", "n_persons": 500, "share_intra": 0.5}
        for scope in ("all", "inter", "intra"):
            shares = [0.5, 0.3, 0.2, 0, 0, 0, 0]
            for lbl, s in zip(T.WORK_BAND_LABELS, shares):
                row[f"share_{scope}_{lbl}"] = s
                row[f"share_{scope}_shrunk_{lbl}"] = s
            row[f"emd_noise_95_{scope}"] = 0.02
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


def test_model_quantiles_eqasim_curve():
    realised = S.realised_work_frame(_homes(), _work(), _persons(), _gemeinden())
    q = S.model_quantiles(realised)
    assert set(q["code"]) == {"03101", "03151", "zgb"}
    assert len(q[q["code"] == "zgb"]) == 20
    assert np.all(np.diff(q[q["code"] == "zgb"].sort_values("cdf")["distance_km_euclid"]) >= 0)


def test_write_outputs_creates_files(tmp_path):
    realised = S.realised_work_frame(_homes(), _work(), _persons(), _gemeinden())
    cells, decision = S.compare_work(realised, _targets_commute(), 1.3, 0.08, 200)
    edu = S.realised_education_frame(_homes(), _education(), _persons())
    edu_targets = pd.DataFrame([{
        "level_geo": "kreis", "code": c, "source": "srv", "education_level": lvl, "comparable": True,
        "n_persons": 300, "emd_noise_95": 0.02,
        **{f"share_{l}": s for l, s in zip(T.EDUCATION_BAND_LABELS, [0.4, 0.3, 0.3, 0, 0, 0])},
        **{f"share_shrunk_{l}": s for l, s in zip(T.EDUCATION_BAND_LABELS, [0.4, 0.3, 0.3, 0, 0, 0])},
    } for c in list(T.ZGB_KREISE) + ["zgb"] for lvl in T.COMPARABLE_LEVELS])
    edu_targets.loc[edu_targets["code"] == "zgb", "level_geo"] = "zgb"
    ecells, edecision = S.compare_education(edu, edu_targets, 1.3, 0.08, 200)
    S.write_outputs(tmp_path, cells, decision, ecells, edecision, S.model_quantiles(realised))
    assert (tmp_path / "commute_by_kreis.csv").exists()
    assert (tmp_path / "education_by_kreis_level.csv").exists()
    assert (tmp_path / "commute_quantiles_model.csv").exists()
    d = json.loads((tmp_path / "decisions.json").read_text(encoding="utf-8"))
    assert "work" in d and "education" in d
    assert (tmp_path / "summary.md").exists()
