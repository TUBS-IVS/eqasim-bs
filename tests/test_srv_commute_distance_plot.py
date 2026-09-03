"""Smoke test for the SrV distance plots (matplotlib Agg, synthetic CSV)."""
import matplotlib
matplotlib.use("Agg")

import pandas as pd

from braunschweig.calibration import srv_distance_targets as T
from documentation.plots import srv_commute_distance as P


def _cells(tmp_path):
    rows = []
    for code in ["03101", "03151", "zgb"]:
        for scope in ["all", "inter", "intra"]:
            row = {"code": code, "scope": scope, "n_model": 100, "n_reference_persons": 400,
                   "emd": 0.05, "noise_floor": 0.02, "classification": "ok", "is_aggregate": code == "zgb",
                   "source": "srv"}
            for i, lbl in enumerate(T.WORK_BAND_LABELS):
                row[f"model_share_{lbl}"] = 1.0 if i == 1 else 0.0
                row[f"target_share_{lbl}"] = 1.0 if i == 2 else 0.0
            rows.append(row)
    path = tmp_path / "commute_by_kreis.csv"
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def test_plot_work_bands_writes_png(tmp_path):
    out = P.plot_work_bands(_cells(tmp_path), tmp_path / "work.png", scope="all")
    assert (tmp_path / "work.png").exists() and str(out).endswith("work.png")


def test_plot_education_bands_writes_png(tmp_path):
    rows = []
    for level in T.COMPARABLE_LEVELS:
        for code in ["03101", "zgb"]:
            row = {"code": code, "education_level": level, "scope": "education", "n_model": 50,
                   "n_reference_persons": 200, "emd": 0.04, "noise_floor": 0.03, "classification": "ok",
                   "is_aggregate": code == "zgb", "source": "srv"}
            for i, lbl in enumerate(T.EDUCATION_BAND_LABELS):
                row[f"model_share_{lbl}"] = 1.0 if i == 0 else 0.0
                row[f"target_share_{lbl}"] = 1.0 if i == 1 else 0.0
            rows.append(row)
    path = tmp_path / "education_by_kreis_level.csv"
    pd.DataFrame(rows).to_csv(path, index=False)
    P.plot_education_bands(path, tmp_path / "edu.png")
    assert (tmp_path / "edu.png").exists()
