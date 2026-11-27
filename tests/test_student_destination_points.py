"""Pins on the committed destination points of the student origin-destination table T (#140, Phase 4, Task 1).

``eqasim-data/data/braunschweig/schools/student_destination_points.csv`` is written by
``scripts/build_student_balance.py points`` from the same ``prepare`` and ``student_geometry.site_points``
as ``balance``, so its points are those T's distances were measured to. The file is committed: the checks
below are not data-gated, except the distance reproduction, which needs the local-only VG250 archive.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from braunschweig.data.education import student_geometry as G

ROOT = Path(__file__).resolve().parents[1]
SCHOOLS = ROOT / "eqasim-data" / "data" / "braunschweig" / "schools"
POINTS = SCHOOLS / "student_destination_points.csv"
OD = SCHOOLS / "student_od_table.csv"
COLUMNS = ["destination_id", "institution", "scope", "site_ags8", "x", "y", "point_source"]
# The VG250 archive is local-only (never committed); the main checkout holds it.
VG250_CANDIDATES = [
    ROOT / "eqasim-data" / "data" / "germany" / "vg250-ew_12-31.utm32s.gpkg.ebenen.zip",
    ROOT.parents[2] / "eqasim-data" / "data" / "germany" / "vg250-ew_12-31.utm32s.gpkg.ebenen.zip",
]


@pytest.fixture(scope="module")
def points():
    return pd.read_csv(POINTS, comment="#", dtype={"site_ags8": str})


@pytest.fixture(scope="module")
def od():
    return pd.read_csv(OD, comment="#", dtype={"home_ars5": str, "site_kreis_ars5": str})


def test_columns_and_point_sources(points):
    assert list(points.columns) == COLUMNS
    assert set(points["point_source"]) <= {"curated", "commune_centre"}
    # a commune-centre point has its commune, a curated point has none
    assert points.loc[points["point_source"] == "commune_centre", "site_ags8"].notna().all()


def test_every_destination_of_t_exactly_once(points, od):
    assert not points["destination_id"].duplicated().any()
    assert set(points["destination_id"]) == set(od["destination_id"])
    # the surrounding institutions have points too
    assert set(od.loc[od["scope"] == "out_commuter", "destination_id"]) <= set(points["destination_id"])


def test_coordinates_finite_and_inside_lower_saxony_and_neighbours(points):
    assert points[["x", "y"]].notna().all().all()
    assert points["x"].between(3.0e5, 7.5e5).all() and points["y"].between(5.6e6, 6.1e6).all()


def test_distance_of_every_commuter_row_is_reproduced(points, od):
    vg250 = next((path for path in VG250_CANDIDATES if path.exists()), None)
    if vg250 is None:
        pytest.skip("local-only VG250 archive vg250-ew_12-31.utm32s.gpkg.ebenen.zip not present")
    centroids = G.population_weighted_kreis_centroids(G.load_gemeinden(vg250)).set_index("ars5")
    commuters = od[od["cell_kind"] == "commuter"]
    point = points.set_index("destination_id")
    home = centroids.loc[commuters["home_ars5"]]
    target = point.loc[commuters["destination_id"]]
    distance_km = np.hypot(home["x"].to_numpy() - target["x"].to_numpy(),
                           home["y"].to_numpy() - target["y"].to_numpy()) / 1000.0
    assert len(commuters) > 0
    np.testing.assert_allclose(distance_km, commuters["distance_km"].to_numpy(), atol=0.01)
