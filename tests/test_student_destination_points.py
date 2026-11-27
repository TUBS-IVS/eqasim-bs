"""Pins on the committed destination points of the student origin-destination table T (#140, Phase 4, Task 1).

``eqasim-data/data/braunschweig/schools/student_destination_points.csv`` is written by
``scripts/build_student_balance.py points`` from the same ``prepare`` and ``student_geometry.site_points``
as ``balance``, so its points are those T's distances were measured to. The file is committed: the checks
below are not data-gated, except the distance reproduction, which needs the local-only VG250 archive.
"""
from __future__ import annotations

import importlib.util
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


# The institutions outside the LSN site table: the only destinations that may carry a curated coordinate.
CURATED_DESTINATIONS = {"OVGU Magdeburg | curated", "Hochschule Magdeburg-Stendal | curated", "Hochschule Harz | curated"}


@pytest.fixture(scope="module")
def points():
    return pd.read_csv(POINTS, comment="#", dtype={"site_ags8": str, "destination_id": str}, encoding="utf-8")


@pytest.fixture(scope="module")
def od():
    return pd.read_csv(OD, comment="#", dtype={"home_ars5": str, "site_kreis_ars5": str, "destination_id": str},
                       encoding="utf-8")


@pytest.fixture(scope="module")
def script():
    spec = importlib.util.spec_from_file_location("build_student_balance", ROOT / "scripts" / "build_student_balance.py")
    module = importlib.util.module_from_spec(spec)  # never registered in sys.modules
    spec.loader.exec_module(module)
    return module


def test_columns_and_point_sources(points):
    assert list(points.columns) == COLUMNS
    assert set(points["point_source"]) <= {"curated", "commune_centre"}
    # a commune-centre point has its commune, a curated point has none
    assert points.loc[points["point_source"] == "commune_centre", "site_ags8"].notna().all()


def test_primary_rate_exact_rows_are_commune_centres(points):
    # Primary: every LSN site sits at its commune centre; fallback: exactly the three institutions outside LSN.
    curated = points[points["point_source"] == "curated"]
    assert set(curated["destination_id"]) == CURATED_DESTINATIONS
    assert curated["site_ags8"].isna().all()
    centre = points[points["point_source"] == "commune_centre"]
    assert len(centre) == len(points) - len(CURATED_DESTINATIONS)
    # a commune-centre row is the site named by its own id (institution | site_ags8)
    assert (centre["destination_id"] == centre["institution"] + " | " + centre["site_ags8"]).all()


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


def _prepared(curated_ids):
    sites = pd.DataFrame({"destination_id": ["Uni A | 01234567", "Uni B | curated"],
                          "institution": ["Uni A", "Uni B"], "scope": ["local", "surrounding"]})
    lsn = pd.DataFrame({"institution": ["Uni A"], "site_ags8": ["01234567"]})
    pts = pd.DataFrame({"destination_id": sites["destination_id"], "x": [4.0e5, 5.0e5], "y": [5.8e6, 5.9e6]})
    return {"sites": sites, "lsn_sites": lsn, "points": pts, "curated_destination_ids": set(curated_ids)}


def test_destination_points_maps_commune_and_curated_rows(script):
    frame = script.destination_points(_prepared({"Uni B | curated"}))
    assert list(frame.columns) == COLUMNS
    assert frame["point_source"].tolist() == ["commune_centre", "curated"]
    assert frame["site_ags8"].tolist()[0] == "01234567" and pd.isna(frame["site_ags8"].tolist()[1])


@pytest.mark.parametrize("curated_ids", [set(), {"Uni A | 01234567", "Uni B | curated"}])
def test_destination_points_raises_when_lookup_and_curated_list_disagree(script, curated_ids):
    # an LSN site that is not on the curated list but has no commune, or a curated one that has a commune
    prepared = _prepared(curated_ids)
    if not curated_ids:
        prepared["lsn_sites"] = prepared["lsn_sites"].assign(site_ags8="99999999")  # key lookup misses
    with pytest.raises(ValueError, match="destination points"):
        script.destination_points(prepared)
