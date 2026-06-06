"""Tests for the outside-free modestats post-processor (pure transform + IO)."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from braunschweig.analysis.modestats_inside import (  # noqa: E402
    renormalise_without_outside,
    write_modestats_inside,
)


def test_renormalise_drops_outside_and_sums_to_one():
    df = pd.DataFrame({
        "iteration": [0, 1],
        "car": [0.4, 0.5],
        "pt": [0.1, 0.1],
        "outside": [0.5, 0.4],
    })
    out = renormalise_without_outside(df)
    assert "outside" not in out.columns
    assert list(out.columns) == ["iteration", "car", "pt"]
    # Row 0: car 0.4, pt 0.1 over real-sum 0.5 -> 0.8, 0.2.
    assert abs(out.loc[0, "car"] - 0.8) < 1e-9
    assert abs(out.loc[0, "pt"] - 0.2) < 1e-9
    # Every iteration's real modes sum to 1.
    assert (abs(out[["car", "pt"]].sum(axis=1) - 1.0) < 1e-9).all()


def test_renormalise_is_noop_without_outside_column():
    df = pd.DataFrame({"iteration": [0], "car": [0.7], "pt": [0.3]})
    out = renormalise_without_outside(df)
    assert list(out.columns) == ["iteration", "car", "pt"]
    assert abs(out.loc[0, "car"] - 0.7) < 1e-9
    assert abs(out.loc[0, "pt"] - 0.3) < 1e-9


def test_renormalise_requires_iteration_and_real_modes():
    import pytest
    with pytest.raises(ValueError):
        renormalise_without_outside(pd.DataFrame({"car": [0.5], "pt": [0.5]}))
    with pytest.raises(ValueError):
        renormalise_without_outside(pd.DataFrame({"iteration": [0], "outside": [1.0]}))


def test_write_modestats_inside_creates_csv_and_png(tmp_path):
    src = tmp_path / "modestats.csv"
    pd.DataFrame({
        "iteration": [0, 1, 2],
        "car": [0.45, 0.46, 0.47],
        "pt": [0.10, 0.11, 0.12],
        "walk": [0.15, 0.14, 0.13],
        "outside": [0.30, 0.29, 0.28],
    }).to_csv(src, sep=";", index=False)

    csv_path, png_path = write_modestats_inside(tmp_path)

    assert csv_path.exists() and png_path.exists()
    inside = pd.read_csv(csv_path, sep=";")
    assert "outside" not in inside.columns
    mode_cols = [c for c in inside.columns if c != "iteration"]
    assert (abs(inside[mode_cols].sum(axis=1) - 1.0) < 1e-9).all()
