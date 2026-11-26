"""Tests of the commune geometry and weight-curve coefficients for the student table (#140, Phase 2).

Every expected value is computed by hand on a few synthetic communes; the VG250 reader is
tested on a tiny GeoPackage built in ``tmp_path``, never on the real file.
"""
from __future__ import annotations

import re
import zipfile

import numpy as np
import pandas as pd
import pytest

from braunschweig.data.education import student_geometry as G


def _integers_in(records, level: str) -> list[set[int]]:
    """The integer tokens of each log record of the given level (counts are the contract, not the wording)."""
    return [set(map(int, re.findall(r"\d+", r.getMessage()))) for r in records if r.levelname == level]


def _communes_east_of_origin() -> pd.DataFrame:
    """Kreis 03158: communes 17.5 km (ewz 30) and 37.5 km (ewz 10) east of the origin."""
    return pd.DataFrame({"ags8": ["03158001", "03158002"], "ewz": [30, 10],
                         "x": [17_500.0, 37_500.0], "y": [0.0, 0.0]})


def _origin_destination() -> pd.DataFrame:
    return pd.DataFrame({"destination_id": ["site"], "x": [0.0], "y": [0.0]})


def test_population_weighted_centroid_by_hand():
    g = pd.DataFrame({"ags8": ["03101000", "03158001", "03158002"], "ewz": [10, 30, 10],
                      "x": [0.0, 0.0, 40_000.0], "y": [0.0, 0.0, 0.0]})
    c = G.population_weighted_kreis_centroids(g).set_index("ars5")
    assert c.loc["03158", "x"] == pytest.approx(10_000.0)
    assert c.loc["03158", "y"] == pytest.approx(0.0)
    assert c.loc["03158", "ewz"] == 40
    assert c.loc["03101", "x"] == pytest.approx(0.0)


def test_kreis_with_zero_population_raises():
    g = pd.DataFrame({"ags8": ["03101000", "03158001"], "ewz": [0, 5],
                      "x": [0.0, 1.0], "y": [0.0, 1.0]})
    with pytest.raises(ValueError, match="03101"):
        G.population_weighted_kreis_centroids(g)


def test_site_points_use_the_commune_or_the_curated_list_and_raise_otherwise():
    gemeinden = pd.DataFrame({"ags8": ["03101000", "03158001"], "ewz": [1, 1],
                              "x": [100.0, 200.0], "y": [300.0, 400.0]})
    # lon 9 degrees E is the central meridian of EPSG:25832: easting 500000 m exactly.
    curated = pd.DataFrame({"destination_id": ["far"], "lon": [9.0], "lat": [52.0]})
    sites = pd.DataFrame({"destination_id": ["a", "b"], "site_ags8": ["03101000", "03158001"]})

    points = G.site_points(sites, gemeinden, curated).set_index("destination_id")
    assert points.loc["a", ["x", "y"]].tolist() == [100.0, 300.0]
    assert points.loc["b", ["x", "y"]].tolist() == [200.0, 400.0]
    assert points.loc["far", "x"] == pytest.approx(500_000.0, abs=1e-3)
    assert 5_700_000 < points.loc["far", "y"] < 5_800_000  # 52 degrees N

    unknown_commune = pd.DataFrame({"destination_id": ["c"], "site_ags8": ["99999999"]})
    with pytest.raises(ValueError, match="'c'"):
        G.site_points(unknown_commune, gemeinden, curated)
    missing_commune = pd.DataFrame({"destination_id": ["d"], "site_ags8": [None]})
    with pytest.raises(ValueError, match="'d'"):
        G.site_points(missing_commune, gemeinden, curated)


def test_site_in_both_lists_takes_the_curated_point_and_warns_with_the_displacement(caplog):
    gemeinden = pd.DataFrame({"ags8": ["03101000"], "ewz": [1], "x": [500_000.0], "y": [5_000_000.0]})
    # lon 9 / lat 52 projects to easting 500000 m; the commune centroid lies on the same easting
    curated = pd.DataFrame({"destination_id": ["both", "unknown_commune"], "lon": [9.0, 9.0],
                            "lat": [52.0, 52.0]})
    sites = pd.DataFrame({"destination_id": ["both", "unknown_commune"], "site_ags8": ["03101000", "99999999"]})
    with caplog.at_level("WARNING", logger=G.LOGGER.name):
        points = G.site_points(sites, gemeinden, curated).set_index("destination_id")
    assert points.loc["both", "x"] == pytest.approx(500_000.0, abs=1e-3)
    assert points.loc["both", "y"] > 5_700_000  # the curated point, not the commune centroid
    displacement_km = (points.loc["both", "y"] - 5_000_000.0) / 1000.0
    warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1
    for token in ("both", "unknown_commune", "03101000"):
        assert token in warnings[0]
    # the first number after the id is the displacement in km (printed with two decimals)
    shown_km = float(re.search(r"both\D*(\d+(?:\.\d+)?)", warnings[0]).group(1))
    assert shown_km == pytest.approx(displacement_km, abs=0.01)


def test_curve_basis_by_hand():
    d = np.array([5.0, 17.5, 27.5, 37.5, 56.25, 75.0, 87.5, 100.0, 120.0])
    expected = [[1, 0, 0], [1, 0, 0], [.5, .5, 0], [0, 1, 0], [0, .5, .5], [0, 0, 1], [0, 0, .5],
                [0, 0, 0], [0, 0, 0]]
    np.testing.assert_allclose(G.curve_basis(d, 100.0), expected)
    assert G.curve_anchors_km(100.0) == (17.5, 37.5, 75.0)


def test_curve_is_continuous_and_non_increasing_for_a_non_increasing_triple():
    grid = np.arange(0.0, 120.0 + 1e-9, 0.1)
    curve = G.curve_basis(grid, 100.0) @ np.array([0.8, 0.4, 0.1])
    steps = np.diff(curve)
    # steepest segment: 0.4 -> 0.1 over 37.5 .. 75 km is 0.3/37.5 km, and 0.8 -> 0.4 over 20 km is 0.02/km
    assert np.abs(steps).max() <= 0.01
    assert steps.max() <= 1e-12
    assert curve[0] == pytest.approx(0.8) and curve[-1] == 0.0


def test_band_basis_by_hand():
    d = np.array([24.9, 25.0, 50.0, 100.0, 100.1])
    expected = [[1, 0, 0], [0, 1, 0], [0, 0, 1], [0, 0, 1], [0, 0, 0]]
    np.testing.assert_array_equal(G.band_basis(d, 100.0), expected)


def test_pair_coefficients_average_the_communes_by_population():
    pairs = G.pair_coefficients(_communes_east_of_origin(), _origin_destination(),
                                G.WEIGHT_RULE_COMMUNE_CURVE, 100.0)
    assert list(pairs.columns) == G.PAIR_COLUMNS
    assert len(pairs) == 1
    row = pairs.iloc[0]
    assert row["ars5"] == "03158" and row["destination_id"] == "site"
    assert (row["coeff_short"], row["coeff_middle"], row["coeff_long"]) == pytest.approx((0.75, 0.25, 0.0))
    # population-weighted centroid: (30 * 17.5 + 10 * 37.5) / 40 = 22.5 km
    assert row["distance_km"] == pytest.approx(22.5)


def test_commune_with_zero_population_carries_no_weight():
    g = pd.concat([_communes_east_of_origin(),
                   pd.DataFrame({"ags8": ["03158003"], "ewz": [0], "x": [75_000.0], "y": [0.0]})])
    row = G.pair_coefficients(g, _origin_destination(), G.WEIGHT_RULE_COMMUNE_CURVE, 100.0).iloc[0]
    assert (row["coeff_short"], row["coeff_middle"], row["coeff_long"]) == pytest.approx((0.75, 0.25, 0.0))


def test_centroid_bands_rule_uses_the_centroid_distance():
    row = G.pair_coefficients(_communes_east_of_origin(), _origin_destination(),
                              G.WEIGHT_RULE_CENTROID_BANDS, 100.0).iloc[0]
    assert (row["coeff_short"], row["coeff_middle"], row["coeff_long"]) == (1.0, 0.0, 0.0)
    assert row["distance_km"] == pytest.approx(22.5)


def test_unknown_weight_rule_raises():
    with pytest.raises(ValueError, match="nearest"):
        G.pair_coefficients(_communes_east_of_origin(), _origin_destination(), "nearest", 100.0)


def test_kreise_within_reach_uses_communes():
    # Kreis 03158: centroid 105 km away (beyond 100 km), but one populated commune at 90 km.
    g = pd.DataFrame({"ags8": ["03158001", "03158002", "03101000", "03102000"],
                      "ewz": [10, 90, 5, 5],
                      "x": [90_000.0, 106_666.6666667, 10_000.0, 90_000.0],
                      "y": [0.0, 0.0, 0.0, 0.0]})
    destinations = pd.DataFrame({"destination_id": ["s1", "s2"], "x": [0.0, 500_000.0], "y": [0.0, 0.0]})
    centroid = G.population_weighted_kreis_centroids(g).set_index("ars5").loc["03158", "x"]
    assert centroid > 100_000.0
    # 03102 has its only commune exactly at 90 km, also in reach; 03158 qualifies only through its commune
    assert G.kreise_within_reach(g, destinations, ["s1"], 100.0) == ["03101", "03102", "03158"]
    assert G.kreise_within_reach(g, destinations, ["s1"], 50.0) == ["03101"]
    assert G.kreise_within_reach(g, destinations, ["s2"], 50.0) == []
    # an unpopulated commune inside the radius does not count
    g.loc[g["ags8"] == "03101000", "ewz"] = 0
    g = pd.concat([g, pd.DataFrame({"ags8": ["03101001"], "ewz": [1], "x": [900_000.0], "y": [0.0]})])
    assert G.kreise_within_reach(g, destinations, ["s1"], 50.0) == []
    with pytest.raises(ValueError, match="nope"):
        G.kreise_within_reach(g, destinations, ["nope"], 50.0)


def test_missing_population_or_coordinate_raises_naming_column_and_id():
    destinations = _origin_destination()
    for column in ("ewz", "x", "y"):
        broken = _communes_east_of_origin()
        broken[column] = broken[column].astype(float)
        broken.loc[1, column] = np.nan
        for call in (lambda g: G.pair_coefficients(g, destinations, G.WEIGHT_RULE_COMMUNE_CURVE, 100.0),
                     lambda g: G.kreise_within_reach(g, destinations, ["site"], 100.0)):
            with pytest.raises(ValueError, match=rf"column '{column}'.*03158002"):
                call(broken)
    broken_destination = pd.DataFrame({"destination_id": ["site"], "x": [0.0], "y": [np.nan]})
    for call in (lambda d: G.pair_coefficients(_communes_east_of_origin(), d, G.WEIGHT_RULE_CENTROID_BANDS, 100.0),
                 lambda d: G.kreise_within_reach(_communes_east_of_origin(), d, ["site"], 100.0)):
        with pytest.raises(ValueError, match=r"column 'y'.*site"):
            call(broken_destination)


def test_pair_coefficients_and_reach_log_the_unpopulated_communes(caplog):
    g = pd.concat([_communes_east_of_origin(),
                   pd.DataFrame({"ags8": ["03158003"], "ewz": [0], "x": [1_000.0], "y": [0.0]})])
    with caplog.at_level("INFO", logger=G.LOGGER.name):
        G.pair_coefficients(g, _origin_destination(), G.WEIGHT_RULE_COMMUNE_CURVE, 100.0)
    # 1 unpopulated commune of 3 communes
    assert any({1, 3} <= numbers for numbers in _integers_in(caplog.records, "INFO"))
    caplog.clear()
    with caplog.at_level("INFO", logger=G.LOGGER.name):
        G.kreise_within_reach(g, _origin_destination(), ["site"], 100.0)
    # 1 Kreis, 2 populated communes in reach, 1 unpopulated commune skipped
    assert any({1, 2} <= numbers for numbers in _integers_in(caplog.records, "INFO"))


def _write_vg250_zip(tmp_path, rows):
    import geopandas as gpd
    from shapely.geometry import box

    from braunschweig.analysis.spatial import VG250_INNER

    frame = gpd.GeoDataFrame(
        {"AGS": [r[0] for r in rows], "GF": [r[1] for r in rows], "EWZ": [r[2] for r in rows]},
        geometry=[box(r[3], 0.0, r[3] + 1000.0, 2000.0) for r in rows], crs=G.CRS)
    gpkg = tmp_path / "DE_VG250.gpkg"
    frame.to_file(gpkg, layer="vg250_gem", driver="GPKG")
    archive = tmp_path / "vg250.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.write(gpkg, VG250_INNER)
    return archive


# The repository reads the real archive through a file object as well (population.py), which makes
# pyogrio warn about the in-memory file name; the warning says nothing about the data.
@pytest.mark.filterwarnings("ignore:.*non conformant file extension:RuntimeWarning")
def test_load_gemeinden_keeps_land_rows_and_one_row_per_ags(tmp_path, caplog):
    rows = [("03101000", 4, 100, 0.0), ("03101000", 2, 0, 5000.0), ("03158001", 4, 40, 10_000.0)]
    with caplog.at_level("INFO", logger=G.LOGGER.name):
        gemeinden = G.load_gemeinden(_write_vg250_zip(tmp_path, rows))
    # 3 rows read, 2 land communes kept, 1 non-land row dropped
    assert any({1, 2, 3} <= numbers for numbers in _integers_in(caplog.records, "INFO"))
    assert gemeinden["ags8"].tolist() == ["03101000", "03158001"]
    assert gemeinden["ewz"].tolist() == [100, 40]
    # centroid of box(10000, 0, 11000, 2000)
    assert gemeinden.loc[1, ["x", "y"]].tolist() == pytest.approx([10_500.0, 1000.0])

    duplicated = tmp_path / "dup"
    duplicated.mkdir()
    with pytest.raises(ValueError, match="03101000"):
        G.load_gemeinden(_write_vg250_zip(duplicated, [("03101000", 4, 1, 0.0), ("03101000", 4, 2, 5000.0)]))
