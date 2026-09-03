"""Smoke tests for the SrV distance plots (matplotlib Agg, synthetic CSV fixtures)."""
import os

import matplotlib
matplotlib.use("Agg")

import pandas as pd
import pytest

from braunschweig.calibration import srv_distance_targets as T
from documentation.plots import srv_commute_distance as P


def _work_row(code, scope, source="srv", n_model=100, n_reference_persons=400,
              emd=0.05, noise_floor=0.02, classification="ok", has_target=True):
    row = {"code": code, "scope": scope, "n_model": n_model, "n_reference_persons": n_reference_persons,
           "emd": emd, "noise_floor": noise_floor, "classification": classification,
           "is_aggregate": code == "zgb", "source": source}
    for i, lbl in enumerate(T.WORK_BAND_LABELS):
        row[f"model_share_{lbl}"] = 1.0 if i == 1 else 0.0
        row[f"target_share_{lbl}"] = (1.0 if i == 2 else 0.0) if has_target else float("nan")
        row[f"target_share_raw_{lbl}"] = (1.0 if i == 2 else 0.0) if has_target else float("nan")
    return row


def _work_cells(tmp_path):
    rows = []
    for code in ["03101", "03103", "03151", "zgb"]:
        for scope in ["all", "inter", "intra"]:
            # Wolfsburg (03103) production-mode always compares against the RS7-72
            # proxy, never a direct SrV sample of its own; exercise the honest-label fix.
            source = "proxy_rs7_72" if code == "03103" else "srv"
            rows.append(_work_row(code, scope, source=source))
    # A cell with no usable reference (NaN EMD, classification "no_reference") --
    # exercises the "n/a" EMD rendering and the no-silent-fallback classification path.
    rows.append(_work_row("03153", "all", n_reference_persons=0, emd=float("nan"),
                          noise_floor=float("nan"), classification="no_reference", has_target=False))
    path = tmp_path / "commute_by_kreis.csv"
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def _education_cells(tmp_path):
    rows = []
    for level in T.COMPARABLE_LEVELS:
        for code in ["03101", "zgb"]:
            row = {"code": code, "education_level": level, "scope": "education", "n_model": 50,
                   "n_reference_persons": 200, "emd": 0.04, "noise_floor": 0.03, "classification": "ok",
                   "is_aggregate": code == "zgb", "source": "srv"}
            for i, lbl in enumerate(T.EDUCATION_BAND_LABELS):
                row[f"model_share_{lbl}"] = 1.0 if i == 0 else 0.0
                row[f"target_share_{lbl}"] = 1.0 if i == 1 else 0.0
                row[f"target_share_raw_{lbl}"] = 1.0 if i == 1 else 0.0
            rows.append(row)
    path = tmp_path / "education_by_kreis_level.csv"
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def test_plot_work_bands_writes_png(tmp_path):
    out = P.plot_work_bands(_work_cells(tmp_path), tmp_path / "work.png", scope="all")
    png = tmp_path / "work.png"
    assert png.exists() and str(out).endswith("work.png")
    assert os.path.getsize(png) > 0


def test_plot_work_bands_bad_scope_raises(tmp_path):
    csv_path = _work_cells(tmp_path)
    with pytest.raises(ValueError, match="unknown_scope"):
        P.plot_work_bands(csv_path, tmp_path / "work.png", scope="unknown_scope")


def test_plot_education_bands_writes_png(tmp_path):
    out = P.plot_education_bands(_education_cells(tmp_path), tmp_path / "edu.png")
    png = tmp_path / "edu.png"
    assert png.exists() and str(out).endswith("edu.png")
    assert os.path.getsize(png) > 0


def test_plot_education_bands_no_zgb_rows_raises(tmp_path):
    cells = pd.read_csv(_education_cells(tmp_path))
    cells = cells[cells["code"] != "zgb"]
    path = tmp_path / "education_no_zgb.csv"
    cells.to_csv(path, index=False)
    with pytest.raises(ValueError, match="zgb"):
        P.plot_education_bands(path, tmp_path / "edu.png")
