import json
import os
from pathlib import Path

import numpy as np
import pandas as pd
import geopandas as gpd
import pytest
from shapely.geometry import Point
from braunschweig.data.schools.university_facilities import build_university_facilities


def _hochschulen():
    return pd.DataFrame({
        "institution": ["TU BS", "HBK BS", "Ostfalia", "Uni Goettingen"],
        "scope": ["local", "local", "local", "surrounding"],
        "ars5": ["03101", "03101", "03158", ""],
        "enrollment": [14776.0, 851.0, 9373.0, 25513.0],
        "lon": [np.nan, np.nan, np.nan, 9.9358],
        "lat": [np.nan, np.nan, np.nan, 51.5413],
    })


def _osm_uni():
    # OSM university buildings: two in 03101 (areas 300/100), one in 03158 (area 50)
    return gpd.GeoDataFrame({
        "commune_id": ["031010000000", "031010000000", "031580000000"],
        "weight": [300.0, 100.0, 50.0],
        "geometry": [Point(605000, 5790000), Point(606000, 5790000),
                     Point(612000, 5780000)],
    }, crs="EPSG:25832")


def test_local_enrollment_distributed_by_area_per_commune():
    gdf = build_university_facilities(_hochschulen(), _osm_uni())
    assert list(gdf.columns) == ["location_id", "capacity", "geometry"]
    assert gdf.crs.to_string() == "EPSG:25832"
    # 03101 pooled enrollment = 14776 + 851 = 15627, split 300:100 -> 3:1
    bs = gdf[gdf.geometry.y == 5790000].sort_values("capacity", ascending=False)
    assert abs(bs.iloc[0]["capacity"] - 15627 * 0.75) < 1.0
    assert abs(bs.iloc[1]["capacity"] - 15627 * 0.25) < 1.0
    # Ostfalia 9373 -> the single 03158 building gets all of it
    wf = gdf[gdf.geometry.y == 5780000]
    assert abs(wf.iloc[0]["capacity"] - 9373.0) < 1.0


def test_surrounding_is_single_point_with_enrollment():
    gdf = build_university_facilities(_hochschulen(), _osm_uni())
    goe = gdf[abs(gdf["capacity"] - 25513.0) < 1.0]
    assert len(goe) == 1


def test_capacities_sum_to_total_enrollment():
    gdf = build_university_facilities(_hochschulen(), _osm_uni())
    assert abs(gdf["capacity"].sum() - (14776 + 851 + 9373 + 25513)) < 1.0


# --------------------------------------------------------------------------------------------
# OFF path: the pre-change output on the fixture above, captured from the code at commit e2c53326
# (before the switch existed), frame-equal including dtypes and row order.
def test_off_path_output_is_frame_equal_to_the_pre_change_output():
    gdf = build_university_facilities(_hochschulen(), _osm_uni())
    expected = gpd.GeoDataFrame(
        {"location_id": ["uni_sur_Uni_Goettingen", "uni_loc_1", "uni_loc_2", "uni_loc_3"],
         "capacity": [25513.0, 11720.25, 3906.75, 9373.0]},
        geometry=[Point(564897.0651903313, 5710437.31043063), Point(605000.0, 5790000.0),
                  Point(606000.0, 5790000.0), Point(612000.0, 5780000.0)],
        crs="EPSG:25832")
    pd.testing.assert_frame_equal(pd.DataFrame(gdf), pd.DataFrame(expected))
    assert gdf.crs == expected.crs


class _Context:
    """The synpp ExecuteContext calls the stage functions make: config(key) takes ONE argument (the
    default is declared in configure()), stage(name), path(). It records the keys that were read."""

    def __init__(self, config, stages=None, path=None):
        self._config, self._stages, self._path = config, stages or {}, path
        self.read = set()

    def config(self, key):
        self.read.add(key)
        return self._config[key]

    def stage(self, name):
        return self._stages[name]

    def path(self):
        return str(self._path)


class _ConfigureContext:
    """The synpp ConfigurationContext: config(key[, default]) declares a key, stage(name) a parent."""

    def __init__(self):
        self.declared, self.stages = {}, []

    def config(self, key, default=None):
        self.declared[key] = default
        return default

    def stage(self, name, **kwargs):
        self.stages.append(name)


def test_off_validate_token_keeps_its_inputs_and_adds_no_flows_files(tmp_path):
    from braunschweig.data.schools import university_facilities as module
    folder = tmp_path / "braunschweig" / "schools"
    folder.mkdir(parents=True)
    (folder / "nds_hochschulen.csv").write_text("institution\nx\n", encoding="utf-8")
    context = _Context({"data_path": str(tmp_path), module.KEY_FLOWS_ENABLED: False,
                        "nds_hochschulen_path": "braunschweig/schools/nds_hochschulen.csv"})
    token = module.validate(context)
    assert set(token) == {"nds_hochschulen_csv_size", "helpers"}
    # The code at e2c53326 returned exactly this: the size of the file and the source token of
    # building_potential_attach alone (no regiostar, no flows helper, no deferred module).
    from braunschweig import synpp_tokens
    from braunschweig.data import building_potential_attach
    assert token == {
        "nds_hochschulen_csv_size": os.path.getsize(folder / "nds_hochschulen.csv"),
        "helpers": synpp_tokens.source_token((building_potential_attach,), ())}
    assert list(token["helpers"]["modules"]) == ["braunschweig.data.building_potential_attach"]


def test_on_validate_token_holds_the_four_table_digests_and_the_flows_helpers():
    from braunschweig.data.schools import university_facilities as module
    context = _Context(
        {"data_path": _repo_data_path(), module.KEY_FLOWS_ENABLED: True}
        | {key: default for key, default in module._FLOWS_PATH_DEFAULTS.items()})
    token = module.validate(context)
    assert set(token) == {"flows_files", "helpers"}
    assert set(token["flows_files"]) == set(module._FLOWS_PATH_DEFAULTS)
    assert "braunschweig.data.education.student_flows_inputs" in token["helpers"]["modules"]


def test_off_execute_reads_the_institution_file_and_returns_the_builder_output(tmp_path):
    from braunschweig.data.schools import university_facilities as module
    folder = tmp_path / "braunschweig" / "schools"
    folder.mkdir(parents=True)
    _hochschulen().to_csv(folder / "nds_hochschulen.csv", index=False, encoding="utf-8")
    osm = _osm_uni()
    osm["education_type"] = "university"
    context = _Context({"data_path": str(tmp_path), module.KEY_FLOWS_ENABLED: False,
                        "nds_hochschulen_path": "braunschweig/schools/nds_hochschulen.csv",
                        "education_building_distribution": False},
                       {"eqasim_common.locations.education": osm}, path=tmp_path)
    gdf = module.execute(context)
    expected = build_university_facilities(
        pd.read_csv(folder / "nds_hochschulen.csv", dtype={"ars5": str}), _osm_uni())
    pd.testing.assert_frame_equal(pd.DataFrame(gdf), pd.DataFrame(expected))
    assert not (tmp_path / "university_facilities_report.json").exists()


def test_the_switch_defaults_on_and_every_key_the_stage_reads_is_declared(tmp_path):
    from braunschweig.data.schools import university_facilities as module
    configure = _ConfigureContext()
    module.configure(configure)
    assert configure.declared[module.KEY_FLOWS_ENABLED] is True
    # a key a stage reads at execute() without declaring it in configure() fails in real synpp
    data_path = _repo_data_path()
    context = _Context({key: value for key, value in configure.declared.items()}
                       | {"data_path": data_path, "education_building_distribution": False},
                       {"eqasim_common.locations.education": _toy_university_osm()}, path=tmp_path)
    module.validate(context)
    assert context.read <= set(configure.declared) | {"data_path"}


# --------------------------------------------------------------------------------------------
# ON path: one facility set per destination of the table T.
def _sites():
    return pd.DataFrame({
        "destination_id": ["TU Alpha | 03101000", "Hochsch. f. Bildende Künste Alpha | 03101000",
                           "HS Süd/Ost | 03158037"],
        "institution": ["TU Alpha", "Hochsch. f. Bildende Künste Alpha", "HS Süd/Ost"],
        "site_kreis_ars5": ["03101", "03101", "03158"],
        "enrollment_persons": [1000.0, 100.0, 500.0]})


def _surrounding():
    return pd.DataFrame({
        "destination_id": ["Uni Göttingen | 03159016", "TiHo Hannover | 03241001"],
        "institution": ["Uni Göttingen", "TiHo Hannover"],
        "site_kreis_ars5": ["03159", "03241"],
        "out_commuters_persons": [40.0, 0.0]})


def _points():
    return pd.DataFrame({
        "destination_id": ["TU Alpha | 03101000", "Hochsch. f. Bildende Künste Alpha | 03101000",
                           "HS Süd/Ost | 03158037", "Uni Göttingen | 03159016",
                           "TiHo Hannover | 03241001"],
        "institution": ["TU Alpha", "Hochsch. f. Bildende Künste Alpha", "HS Süd/Ost",
                        "Uni Göttingen", "TiHo Hannover"],
        "scope": ["local", "local", "local", "surrounding", "surrounding"],
        "site_ags8": ["03101000", "03101000", "03158037", "03159016", "03241001"],
        "x": [603990.0, 603990.0, 605411.0, 564598.0, 552391.0],
        "y": [5792749.0, 5792749.0, 5780317.0, 5710630.0, 5803526.0],
        "point_source": ["commune_centre"] * 5})


def _buildings():
    # two buildings in the commune of TU and the art academy (areas 300/100), one in the commune of
    # the third site, one in a commune without a site
    return gpd.GeoDataFrame({
        "commune_id": ["031010000000", "031010000000", "031580000037", "032410000001"],
        "weight": [300.0, 100.0, 50.0, 70.0],
        "geometry": [Point(605000, 5790000), Point(606000, 5790000), Point(612000, 5780000),
                     Point(550000, 5800000)]}, crs="EPSG:25832")


def _communes(buildings):
    # 12-digit ARS = Land, Regierungsbezirk, Kreis (5) + Verbandsgemeinde (4) + Gemeinde (3)
    return buildings["commune_id"].map(lambda ars: ars[:5] + ars[9:12])


def _build(**overrides):
    from braunschweig.data.schools.university_facilities import build_site_facilities
    buildings = overrides.pop("buildings", None)
    buildings = _buildings() if buildings is None else buildings
    arguments = dict(sites=_sites(), surrounding=_surrounding(), points=_points(),
                     buildings=buildings, communes=_communes(buildings),
                     weight_column="weight", area_weight_column=None)
    arguments.update(overrides)
    return build_site_facilities(**arguments)


def test_site_facilities_hold_one_group_per_destination_with_the_enrollment():
    gdf = _build()
    assert list(gdf.columns) == ["location_id", "capacity", "geometry", "destination_id", "scope",
                                 "site_ags8"]
    assert gdf.crs.to_string() == "EPSG:25832"
    by_destination = gdf.groupby("destination_id")["capacity"].sum()
    # local sites: the capacities sum to the enrollment of the site
    assert by_destination["TU Alpha | 03101000"] == pytest.approx(1000.0)
    assert by_destination["Hochsch. f. Bildende Künste Alpha | 03101000"] == pytest.approx(100.0)
    assert by_destination["HS Süd/Ost | 03158037"] == pytest.approx(500.0)
    # surrounding: one facility at the curated point, capacity = out-commuters of the region
    sur = gdf[gdf["scope"] == "surrounding"].set_index("destination_id")
    assert sur.loc["Uni Göttingen | 03159016", "capacity"] == 40.0
    assert sur.loc["Uni Göttingen | 03159016", "geometry"].equals(Point(564598.0, 5710630.0))
    # a surrounding institution without an out-commuter has no share in T: no facility (counted)
    assert "TiHo Hannover | 03241001" not in sur.index
    assert (gdf["capacity"] > 0).all()
    # exactly the destinations of the tables that have a share, each once as a group
    assert set(gdf["destination_id"]) == set(_points()["destination_id"]) - {"TiHo Hannover | 03241001"}
    # the 3:1 split by area inside the commune
    tu = gdf[gdf["destination_id"] == "TU Alpha | 03101000"].sort_values("capacity")
    assert list(tu["capacity"]) == pytest.approx([250.0, 750.0])


def test_two_sites_in_one_commune_get_separate_rows_on_the_same_geometries():
    gdf = _build()
    tu = gdf[gdf["destination_id"] == "TU Alpha | 03101000"]
    art = gdf[gdf["destination_id"] == "Hochsch. f. Bildende Künste Alpha | 03101000"]
    assert len(tu) == len(art) == 2
    assert [g.wkt for g in tu["geometry"]] == [g.wkt for g in art["geometry"]]
    assert not set(tu["location_id"]) & set(art["location_id"])
    assert gdf["location_id"].is_unique


def test_location_ids_are_ascii_slugs_with_the_prefixes_the_consumers_read():
    gdf = _build()
    ids = set(gdf["location_id"])
    assert all(i.isascii() and i.replace("_", "").isalnum() for i in ids)
    assert "uni_sur_uni_goettingen_03159016" in ids
    assert "uni_loc_hochsch_f_bildende_kuenste_alpha_03101000_0" in ids
    assert "uni_loc_hs_sued_ost_03158037_0" in ids


def test_two_destinations_with_one_slug_raise():
    sites = _sites()
    points = _points()
    sites.loc[2, "destination_id"] = "TU  Alpha | 03101000"
    points.loc[2, "destination_id"] = "TU  Alpha | 03101000"
    points.loc[2, "site_ags8"] = "03101000"
    with pytest.raises(ValueError, match="slug"):
        _build(sites=sites, points=points)


def test_a_local_site_without_a_university_building_in_its_commune_raises():
    buildings = _buildings().iloc[:2]
    with pytest.raises(ValueError, match="HS Süd/Ost"):
        _build(buildings=buildings)


def test_buildings_outside_the_site_communes_are_counted_and_get_no_capacity(capsys):
    gdf = _build()
    report = gdf.attrs["report"]
    assert report["buildings_total"] == 4
    assert report["buildings_in_site_communes"] == 3
    assert report["buildings_left_out"] == 1
    assert report["left_out_communes"] == ["03241001"]
    assert "[university_facilities] university buildings in a site commune 3/4 (75.0%)" in \
        capsys.readouterr().out
    # the left-out building carries no capacity: its point is not a local facility
    local = gdf[gdf["scope"] == "local"]
    assert not local["geometry"].apply(lambda g: g.equals(Point(550000, 5800000))).any()


def test_destination_of_t_without_a_facility_raises_unless_it_is_a_counted_zero_share_one(capsys):
    from braunschweig.data.schools.university_facilities import check_destinations_covered
    gdf = _build()
    report = gdf.attrs["report"]
    assert report["surrounding_left_out_zero_share"] == 1
    assert report["surrounding_zero_share_destinations"] == ["TiHo Hannover | 03241001"]
    assert report["surrounding_facilities"] == 1
    assert "left out (no student of the region, no share in T) 1 (50.0%)" in capsys.readouterr().out
    od = pd.DataFrame({"destination_id": ["TU Alpha | 03101000", "TiHo Hannover | 03241001"]})
    # exactly the counted zero-share destination may lack a facility ...
    check_destinations_covered(gdf, od, report["surrounding_zero_share_destinations"])
    with pytest.raises(ValueError, match="TiHo Hannover"):
        check_destinations_covered(gdf, od)
    # ... any other missing destination still raises
    od_other = pd.DataFrame({"destination_id": ["TU Alpha | 03101000", "Nowhere | 03000000"]})
    with pytest.raises(ValueError, match="Nowhere"):
        check_destinations_covered(gdf, od_other, report["surrounding_zero_share_destinations"])


def test_a_local_site_without_enrollment_raises():
    sites = _sites()
    sites.loc[2, "enrollment_persons"] = 0.0
    with pytest.raises(ValueError, match="without enrollment"):
        _build(sites=sites)


def test_a_building_of_weight_zero_gets_no_capacity_and_is_counted():
    buildings = _buildings()
    buildings.loc[1, "weight"] = 0.0
    gdf = _build(buildings=buildings)
    tu = gdf[gdf["destination_id"] == "TU Alpha | 03101000"]
    assert list(tu["capacity"]) == [1000.0]                 # the whole enrollment on the other building
    assert gdf.attrs["report"]["buildings_zero_weight_left_out"] == 2   # TU and the art academy
    assert gdf["location_id"].is_unique


def test_the_left_out_share_warns_only_above_the_configured_share(capsys):
    # 1 of 4 buildings lies in a commune without a site: 25 %
    _build(warn_share=0.05)
    assert "WARNING: 25.0% of the OSM university buildings lie in a commune without a site" \
        in capsys.readouterr().out
    _build(warn_share=0.5)
    assert "WARNING" not in capsys.readouterr().out


def test_potential_weights_follow_the_same_unit_rule_as_the_old_path():
    # commune 03101000: every building has a potential -> the potentials (1:3) are the weights,
    # not the areas (3:1); commune 03158037 has one building.
    buildings = _buildings()
    buildings["potential"] = [1.0, 3.0, 5.0, np.nan]
    gdf = _build(buildings=buildings, weight_column="potential", area_weight_column="weight")
    tu = gdf[gdf["destination_id"] == "TU Alpha | 03101000"]
    assert list(tu["capacity"]) == pytest.approx([250.0, 750.0])


# --------------------------------------------------------------------------------------------
# ON path through the stage functions, on the committed tables of Phase 2 and Task 1.
def _repo_data_path():
    return str(Path(__file__).resolve().parent.parent / "eqasim-data" / "data")


def _toy_university_osm():
    points = pd.read_csv(os.path.join(_repo_data_path(), "braunschweig", "schools",
                                      "student_destination_points.csv"),
                         comment="#", encoding="utf-8", dtype={"site_ags8": str})
    return _university_buildings_of_every_site_commune(points)


def _university_buildings_of_every_site_commune(points):
    local = points[points["scope"] == "local"].drop_duplicates("site_ags8")
    ars = [a[:5] + "0000" + a[5:] for a in local["site_ags8"]]
    return gpd.GeoDataFrame({
        "commune_id": ars + ["031600000000"],   # the last one lies in no site commune
        "weight": [10.0 + i for i in range(len(ars))] + [5.0],
        "education_type": "university",
        "geometry": [Point(600000 + 1000 * i, 5790000) for i in range(len(ars) + 1)]},
        crs="EPSG:25832")


def test_on_stage_builds_the_facilities_of_every_destination_of_the_committed_t(tmp_path):
    from braunschweig.data.education import student_flows_inputs as flows
    from braunschweig.data.schools import university_facilities as module
    data_path = _repo_data_path()
    schools = os.path.join(data_path, "braunschweig", "schools")
    points = pd.read_csv(os.path.join(schools, "student_destination_points.csv"), comment="#",
                         encoding="utf-8", dtype={"site_ags8": str})
    osm = _university_buildings_of_every_site_commune(points)
    configure = _ConfigureContext()
    module.configure(configure)
    context = _Context(
        configure.declared | {"data_path": data_path, "education_building_distribution": False},
        {"eqasim_common.locations.education": osm,
         "data.spatial.municipalities": pd.DataFrame({"commune_id": sorted(set(osm["commune_id"]))})},
        path=tmp_path)
    gdf = module.execute(context)
    assert context.read <= set(configure.declared)

    od = flows.load_od_table(os.path.join(schools, "student_od_table.csv"))
    report = json.loads((tmp_path / "university_facilities_report.json").read_text(encoding="utf-8"))
    # none without a facility, except the counted zero-share destinations (execute() enforces it)
    assert set(od["destination_id"]) - set(gdf["destination_id"]) <= set(
        report["surrounding_zero_share_destinations"])
    assert (gdf["capacity"] > 0).all()
    sites = pd.read_csv(os.path.join(schools, "student_balance_by_site.csv"), comment="#",
                        encoding="utf-8")
    by_destination = gdf.groupby("destination_id")["capacity"].sum()
    for destination, enrollment in zip(sites["destination_id"], sites["enrollment_persons"]):
        assert by_destination[destination] == pytest.approx(float(enrollment))
    assert gdf["location_id"].is_unique
    # primary path: every site was placed on the buildings of its own commune, nothing on a fallback
    assert report["buildings_left_out"] == 1
    assert report["local_sites_placed"] == report["local_sites"] == len(sites)


def test_the_stage_raises_when_the_placed_capacity_differs_from_the_table_sum(tmp_path, monkeypatch):
    from braunschweig.data.schools import university_facilities as module
    original = module.build_site_facilities

    def _losing_students(*args, **kwargs):
        gdf = original(*args, **kwargs)
        gdf.loc[gdf["scope"] == "local", "capacity"] *= 0.5
        return gdf

    monkeypatch.setattr(module, "build_site_facilities", _losing_students)
    points = pd.read_csv(os.path.join(_repo_data_path(), "braunschweig", "schools",
                                      "student_destination_points.csv"),
                         comment="#", encoding="utf-8", dtype={"site_ags8": str})
    osm = _university_buildings_of_every_site_commune(points)
    configure = _ConfigureContext()
    module.configure(configure)
    context = _Context(
        configure.declared | {"data_path": _repo_data_path(), "education_building_distribution": False},
        {"eqasim_common.locations.education": osm,
         "data.spatial.municipalities": pd.DataFrame({"commune_id": sorted(set(osm["commune_id"]))})},
        path=tmp_path)
    with pytest.raises(ValueError, match="local capacity sum"):
        module.execute(context)
