"""Builder and loader of the SrV 2023 mode-share-by-band table (eqasim-bs#453, ADR-0145)."""
import numpy as np
import pandas as pd
import pytest

from braunschweig.calibration import srv_mode_by_distance_band as M

#: One straight-line distance (km) per default band: 0-5, 5-10, 10-15, 15-20, 20-30, 30-45, 45+.
MIDPOINTS_KM = (2.5, 7.5, 12.5, 17.5, 25.0, 37.5, 60.0)


def _trips(rows):
    columns = ["V_ZWECK", "E_START_ZWECK", "E_HVM_5", "GIS_LAENGE", "GIS_LAENGE_GUELTIG", "GEWICHT_W_ZENSUS"]
    return pd.DataFrame(rows, columns=columns)


def _grid(purpose, counts_by_mode, detour=1.3):
    """Rows for one SrV destination purpose: ``counts_by_mode`` = {E_HVM_5 code: n} in EVERY band."""
    rows = []
    for km in MIDPOINTS_KM:
        for mode, n in counts_by_mode.items():
            rows += [(purpose, 19, mode, km * detour, 1, 1.0)] * n
    return rows


def _all_groups(work=None, education=None, other=None):
    """All three purpose groups populated with 50 trips per band unless replaced."""
    rows = []
    rows += work if work is not None else _grid(1, {3: 40, 2: 10})            # work: car 40, bicycle 10
    rows += education if education is not None else _grid(4, {5: 30, 4: 20})  # education: pt 30, car_passenger 20
    rows += other if other is not None else _grid(8, {1: 25, 3: 25})          # other (shop): walk 25, car 25
    return _trips(rows)


def test_purpose_group_of_maps_work_education_other_and_home_returns_by_origin():
    v_zweck = pd.Series([1, 3, 4, 5, 6, 8, 12, 18, 70, 19, 19, 19])
    e_start = pd.Series([19, 19, 19, 19, 19, 19, 19, 19, 19, 1, 5, 13])
    groups = M.purpose_group_of(v_zweck, e_start)
    assert groups.tolist() == ["work", "education", "education", "education", "education", "other", "other",
                               "other", "other", "work", "education", "other"]


def test_purpose_group_of_follows_the_shared_srv_purpose_map():
    # 2 business -> work and 7 other education -> education (srv_plan_structure.PURPOSE_BY_V_ZWECK, the same
    # grouping the pipeline applies to MiD W_ZWECK 2); -10, unknown codes, a home-bound trip from an unknown
    # origin (-7) and home -> home are None.
    groups = M.purpose_group_of(pd.Series([2, 7, -10, 99, 19, 19]), pd.Series([19, 19, 19, 19, -7, 19]))
    assert groups.tolist() == ["work", "education", None, None, None, None]


def test_select_trips_drops_gis_invalid_negative_weight_unknown_modes_and_purposes_and_counts_them():
    trips = _trips([
        (1, 19, 3, 13.0, 1, 2.0),      # kept
        (1, 19, 3, 13.0, 0, 2.0),      # GIS invalid
        (1, 19, 3, 13.0, 1, -9.0),     # negative weight
        (1, 19, -7, 13.0, 1, 2.0),     # unknown mode
        (-10, 19, 3, 13.0, 1, 2.0),    # unknown purpose
    ])
    kept, log = M.select_trips(trips)
    assert len(kept) == 1 and kept["mode"].tolist() == ["car"] and kept["purpose_group"].tolist() == ["work"]
    assert log == {"n_trips": 5, "n_gis_invalid": 1, "n_weight_negative": 1, "n_mode_unknown": 1,
                   "n_purpose_excluded": 1, "n_selected": 1}


def test_band_label_formats_closed_and_open_bands():
    assert M.band_label(0.0, 5.0) == "0_5"
    assert M.band_label(20.0, float("inf")) == "20_plus"


def test_pool_thin_bands_merges_from_the_top_but_never_into_the_bottom_band():
    edges = (0.0, 5.0, 10.0, 20.0, float("inf"))
    # the top two bands are thin and merge downwards into the 5-10 band
    assert M.pool_thin_bands([100, 60, 30, 9], edges, 50) == [(0.0, 5.0), (5.0, float("inf"))]
    # nothing thin: nothing merges
    assert M.pool_thin_bands([100, 60, 70, 55], edges, 50) == [(0.0, 5.0), (5.0, 10.0), (10.0, 20.0),
                                                                (20.0, float("inf"))]
    # a thin bottom band or a thin band directly above it stays: the 0-5 band is never merged into, so that
    # the short-distance band keeps its own mode shares (walk is plausible there and nowhere else)
    assert M.pool_thin_bands([10, 20, 70, 55], edges, 50) == [(0.0, 5.0), (5.0, 10.0), (10.0, 20.0),
                                                               (20.0, float("inf"))]


def test_build_table_gives_every_cell_all_five_modes_with_shares_summing_to_one():
    table, log = M.build_table(_all_groups(), detour_factor=1.3, min_trips_per_band=50)
    work = table[table["purpose_group"] == "work"].set_index(["band", "mode"])
    assert work.loc[("10_15", "car"), "share"] == pytest.approx(0.8)
    assert work.loc[("10_15", "bicycle"), "share"] == pytest.approx(0.2)
    assert work.loc[("10_15", "walk"), "share"] == 0.0
    assert work.loc[("10_15", "car"), "n_band"] == 50 and work.loc[("10_15", "car"), "n_cell"] == 40
    assert np.allclose(table.groupby(["purpose_group", "band"])["share"].sum().to_numpy(), 1.0)
    assert (table.groupby(["purpose_group", "band"]).size() == len(M.MODES)).all()
    assert set(table["purpose_group"]) == {"work", "education", "other", "all"}
    # the pooled group "all": in 0_5, 40 work car trips + 25 shop car trips of 150 trips
    all_cell = table[(table["purpose_group"] == "all") & (table["band"] == "0_5")].set_index("mode")
    assert all_cell.loc["car", "share"] == pytest.approx(65 / 150)
    assert log["pooled_bands"] == {"work": [], "education": [], "other": [], "all": []}
    assert log["thin_bands_kept"] == {"work": [], "education": [], "other": [], "all": []}
    assert list(table.columns) == M.TABLE_COLUMNS


def test_build_table_weights_the_shares_by_gewicht_w_zensus():
    work = [row if row[2] != 2 else row[:5] + (3.0,) for row in _grid(1, {3: 40, 2: 10})]   # bicycle weighs 3
    table, _ = M.build_table(_all_groups(work=work), detour_factor=1.3, min_trips_per_band=50)
    cell = table[(table["purpose_group"] == "work") & (table["band"] == "0_5")].set_index("mode")
    assert cell.loc["bicycle", "share"] == pytest.approx(30 / 70)
    assert cell.loc["bicycle", "n_cell"] == 10


def test_build_table_pools_thin_top_bands_downwards():
    # education: 60 trips at 2.5 km, 60 at 7.5 km, 9 car trips at 60 km. The 45_plus band merges down through
    # the empty bands into the 5-10 band: one 5_plus band with 69 trips.
    education = ([(4, 19, 5, 2.5 * 1.3, 1, 1.0)] * 60 + [(4, 19, 5, 7.5 * 1.3, 1, 1.0)] * 60
                 + [(4, 19, 3, 60.0 * 1.3, 1, 1.0)] * 9)
    table, log = M.build_table(_all_groups(education=education), detour_factor=1.3, min_trips_per_band=50)
    bands = table[table["purpose_group"] == "education"].drop_duplicates("band").set_index("band")
    assert list(bands.index) == ["0_5", "5_plus"]
    assert bands.loc["5_plus", "n_band"] == 69 and np.isinf(bands.loc["5_plus", "band_upper_km"])
    cell = table[(table["purpose_group"] == "education") & (table["band"] == "5_plus")].set_index("mode")
    assert cell.loc["car", "share"] == pytest.approx(9 / 69)
    assert log["pooled_bands"]["education"] == ["5_plus"]
    assert log["thin_bands_kept"]["education"] == []


def test_build_table_keeps_a_thin_band_above_the_bottom_band_and_reports_it():
    education = [(4, 19, 5, 2.5 * 1.3, 1, 1.0)] * 60 + [(4, 19, 3, 60.0 * 1.3, 1, 1.0)] * 9
    table, log = M.build_table(_all_groups(education=education), detour_factor=1.3, min_trips_per_band=50)
    assert set(table.loc[table["purpose_group"] == "education", "band"]) == {"0_5", "5_plus"}
    assert log["thin_bands_kept"]["education"] == ["5_plus"]


def test_build_table_raises_when_a_band_has_no_trips_after_pooling():
    education = [(4, 19, 5, 2.5 * 1.3, 1, 1.0)] * 60          # nothing beyond 5 km: the 5_plus band is empty
    with pytest.raises(ValueError, match="education"):
        M.build_table(_all_groups(education=education), detour_factor=1.3, min_trips_per_band=50)


def test_build_table_raises_when_a_purpose_group_has_no_trips():
    with pytest.raises(ValueError, match="other"):
        M.build_table(_all_groups(other=[]), detour_factor=1.3, min_trips_per_band=50)


def test_build_table_divides_the_routed_length_by_the_detour_factor():
    extra = [(1, 19, 3, 6.0, 1, 1.0)] * 10          # 6 km routed
    table, _ = M.build_table(_all_groups(work=_grid(1, {3: 40, 2: 10}) + extra), detour_factor=1.3,
                             min_trips_per_band=50)
    n_band = table[(table["purpose_group"] == "work") & (table["mode"] == "car")].set_index("band")["n_band"]
    assert n_band["0_5"] == 60 and n_band["5_10"] == 50          # 6 / 1.3 = 4.6 km straight-line
    rows = (_grid(1, {3: 40, 2: 10}, detour=1.0) + extra + _grid(4, {5: 30, 4: 20}, detour=1.0)
            + _grid(8, {1: 25, 3: 25}, detour=1.0))
    table, _ = M.build_table(_trips(rows), detour_factor=1.0, min_trips_per_band=50)
    n_band = table[(table["purpose_group"] == "work") & (table["mode"] == "car")].set_index("band")["n_band"]
    assert n_band["0_5"] == 50 and n_band["5_10"] == 60          # 6 / 1.0 = 6.0 km


def test_load_table_raises_without_the_file_and_validates_the_columns(tmp_path):
    with pytest.raises(FileNotFoundError, match=M.TABLE_FILE_NAME):
        M.load_table(tmp_path)
    frame = pd.DataFrame({"purpose_group": ["work"], "band": ["0_5"], "band_lower_km": [0.0],
                          "band_upper_km": [5.0], "mode": ["car"], "share": [1.0], "n_band": [1], "n_cell": [1]})
    path = tmp_path / M.TABLE_FILE_NAME
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("# header line\n")
        frame.to_csv(handle, index=False)
    loaded = M.load_table(tmp_path)
    assert list(loaded.columns) == M.TABLE_COLUMNS
    assert loaded["band_upper_km"].tolist() == [5.0]
    frame.drop(columns=["n_cell"]).to_csv(path, index=False)
    with pytest.raises(ValueError, match="n_cell"):
        M.load_table(tmp_path)


def test_the_table_module_does_not_import_the_calibration_builders():
    # The synpp stage imports only the table module; its validate() token hashes the whole import closure.
    import ast
    import inspect

    from braunschweig.calibration import srv_mode_by_distance_band_table as table_module
    imported = {node.module for node in ast.walk(ast.parse(inspect.getsource(table_module)))
                if isinstance(node, ast.ImportFrom) and node.module}
    assert not any(name.startswith("braunschweig") for name in imported)
