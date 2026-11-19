"""Tests for braunschweig.analysis.popsim_validation.controls.

Covers:
1. _multi_col_kreis_target: aggregation + share computation on a synthetic cells frame.
2. Realized extractors: household_size, household_type (hh_type5), tenure,
   building_type, seniorenstatus, age_sex.
3. End-to-end mini validation: build_registry -> evaluate_all -> summarize -> assess
   (all with monkeypatched target loaders).
"""
from __future__ import annotations

import pandas as pd
import numpy as np
import pytest
import geopandas as gpd
from shapely.geometry import Point

from braunschweig.analysis.popsim_validation import controls as C
from braunschweig.analysis.population_validation import control_validation as CV
from braunschweig.analysis.population_validation import quality_assessment as QA
from braunschweig.analysis.population_validation.population_source import PopulationFrames


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_geo(household_ids, ars5="03101"):
    """Minimal geo frame: household_id -> ars5 + dummy commune_id."""
    return pd.DataFrame({
        "household_id": household_ids,
        "ars5": [ars5] * len(household_ids),
        "commune_id": ["03101000"] * len(household_ids),
    })


def _make_frames(
    household_ids,
    household_sizes,
    hh_type5_vals=None,
    housing_tenure_vals=None,
    building_type_vals=None,
    ages=None,
    sexes=None,
    n_persons_per_hh=1,
):
    """Build minimal PopulationFrames for testing realized extractors."""
    # Build persons: one per household (simple: same hh attributes broadcast).
    person_ids = [f"{h}_1" for h in household_ids]
    persons_data = {
        "person_id": person_ids,
        "household_id": household_ids,
        "household_size": household_sizes,
    }
    if hh_type5_vals is not None:
        persons_data["hh_type5"] = hh_type5_vals
    if housing_tenure_vals is not None:
        persons_data["housing_tenure"] = housing_tenure_vals
    if building_type_vals is not None:
        persons_data["building_type_3class"] = building_type_vals
    if ages is not None:
        persons_data["age"] = ages
    if sexes is not None:
        persons_data["sex"] = sexes

    persons = pd.DataFrame(persons_data)
    households = pd.DataFrame({
        "household_id": household_ids,
        "household_size": household_sizes,
    })
    homes = gpd.GeoDataFrame(
        {"household_id": household_ids},
        geometry=[Point(605000, 5790000)] * len(household_ids),
        crs="EPSG:25832",
    )
    return PopulationFrames(persons, households, homes, None, "run_output", "/tmp", "test_")


# ---------------------------------------------------------------------------
# 1. _multi_col_kreis_target
# ---------------------------------------------------------------------------

def test_multi_col_kreis_target_simple_shares():
    """Two cells, two categories: shares should sum to 1.0 per geo."""
    cells = pd.DataFrame({
        "ars5": ["03101", "03101", "03102"],
        "cat_a": [80.0, 20.0, 60.0],
        "cat_b": [20.0, 80.0, 40.0],
    })
    col_cat = {"cat_a": "A", "cat_b": "B"}
    out = C._multi_col_kreis_target(cells, col_cat)
    assert set(out.columns) == {"geo_id", "category", "target_share"}
    for geo, grp in out.groupby("geo_id"):
        total = grp["target_share"].sum()
        assert abs(total - 1.0) < 1e-6, f"shares don't sum to 1 for geo {geo}"


def test_multi_col_kreis_target_with_total_col():
    """With explicit total_col, shares use that column as denominator."""
    cells = pd.DataFrame({
        "ars5": ["03101"],
        "cat_a": [30.0],
        "cat_b": [20.0],
        "total": [100.0],  # adj total is larger than sum of categories
    })
    col_cat = {"cat_a": "A", "cat_b": "B"}
    out = C._multi_col_kreis_target(cells, col_cat, total_col="total")
    share_a = out.loc[out["category"] == "A", "target_share"].iloc[0]
    share_b = out.loc[out["category"] == "B", "target_share"].iloc[0]
    assert abs(share_a - 0.30) < 1e-6
    assert abs(share_b - 0.20) < 1e-6


def test_multi_col_kreis_target_missing_col_warns(caplog):
    """Absent category column is logged as WARNING and gets zero share."""
    import logging
    cells = pd.DataFrame({
        "ars5": ["03101"],
        "cat_a": [50.0],
        # cat_b is absent
    })
    col_cat = {"cat_a": "A", "cat_b": "B"}
    with caplog.at_level(logging.WARNING, logger="braunschweig.analysis.popsim_validation.controls"):
        out = C._multi_col_kreis_target(cells, col_cat)
    assert any("cat_b" in msg for msg in caplog.messages), (
        "Should warn about missing cat_b column"
    )


def test_multi_col_kreis_target_zero_total_geo_dropped():
    """A geo with zero total (all-zero cells) should not appear in output."""
    cells = pd.DataFrame({
        "ars5": ["03101", "03102"],
        "cat_a": [0.0, 10.0],
        "cat_b": [0.0, 5.0],
    })
    col_cat = {"cat_a": "A", "cat_b": "B"}
    out = C._multi_col_kreis_target(cells, col_cat)
    assert "03101" not in out["geo_id"].values, (
        "Geo with all-zero counts should be dropped from output"
    )
    assert "03102" in out["geo_id"].values


# ---------------------------------------------------------------------------
# 2. Realized extractor: household_size
# ---------------------------------------------------------------------------

def test_realized_household_size_buckets():
    """household_size extractor must bucket 1..5 and >=6 correctly."""
    frames = _make_frames(
        household_ids=["h1", "h2", "h3", "h4", "h5", "h6"],
        household_sizes=[1, 2, 3, 4, 5, 6],
    )
    geo = _make_geo(["h1", "h2", "h3", "h4", "h5", "h6"])
    extractor = C._make_household_size_realized()
    out = extractor(frames, geo)

    assert set(out.columns) == {"geo_id", "category", "synthetic_count"}
    cats = set(out["category"])
    assert cats == {"1", "2", "3", "4", "5", "6+"}, f"Unexpected categories: {cats}"
    total = out["synthetic_count"].sum()
    assert total == 6


def test_realized_household_size_clips_above_6():
    """household_size >= 6 must all map to '6+'."""
    frames = _make_frames(
        household_ids=["h1", "h2", "h3"],
        household_sizes=[6, 7, 10],
    )
    geo = _make_geo(["h1", "h2", "h3"])
    extractor = C._make_household_size_realized()
    out = extractor(frames, geo)
    row = out[out["category"] == "6+"]
    assert len(row) == 1
    assert row["synthetic_count"].iloc[0] == 3


def test_realized_household_size_absent_column():
    """Absent household_size -> empty frame."""
    frames = _make_frames(["h1"], [2])
    # Remove household_size from persons
    frames.persons.drop(columns=["household_size"], inplace=True)
    geo = _make_geo(["h1"])
    extractor = C._make_household_size_realized()
    out = extractor(frames, geo)
    assert out.empty


# ---------------------------------------------------------------------------
# 3. Realized extractor: hh_type5
# ---------------------------------------------------------------------------

def test_realized_hh_type5_extracts():
    """_realized_hh_type5 must aggregate hh_type5 to category counts."""
    frames = _make_frames(
        household_ids=["h1", "h2", "h3"],
        household_sizes=[1, 2, 1],
        hh_type5_vals=["einpersonen", "paar_ohne_kind", "einpersonen"],
    )
    geo = _make_geo(["h1", "h2", "h3"])
    out = C._realized_hh_type5(frames, geo)
    assert set(out.columns) == {"geo_id", "category", "synthetic_count"}
    einpersonen_count = out.loc[out["category"] == "einpersonen", "synthetic_count"].iloc[0]
    assert einpersonen_count == 2


def test_realized_hh_type5_drops_nan():
    """Households with NaN hh_type5 (not_classifiable) are excluded."""
    frames = _make_frames(
        household_ids=["h1", "h2"],
        household_sizes=[1, 3],
        hh_type5_vals=["einpersonen", None],
    )
    geo = _make_geo(["h1", "h2"])
    out = C._realized_hh_type5(frames, geo)
    assert out["synthetic_count"].sum() == 1


def test_realized_hh_type5_absent_column():
    frames = _make_frames(["h1"], [1])
    geo = _make_geo(["h1"])
    out = C._realized_hh_type5(frames, geo)
    assert out.empty


# ---------------------------------------------------------------------------
# 4. Realized extractor: tenure
# ---------------------------------------------------------------------------

def test_realized_tenure_owner_renter():
    frames = _make_frames(
        household_ids=["h1", "h2", "h3"],
        household_sizes=[2, 3, 1],
        housing_tenure_vals=["owner", "renter", "owner"],
    )
    geo = _make_geo(["h1", "h2", "h3"])
    out = C._realized_housing_tenure(frames, geo)
    owner_count = out.loc[out["category"] == "owner", "synthetic_count"].iloc[0]
    renter_count = out.loc[out["category"] == "renter", "synthetic_count"].iloc[0]
    assert owner_count == 2
    assert renter_count == 1


def test_realized_tenure_excludes_unknown():
    """housing_tenure='unknown' (H_MIETE not in {1,2}) must be excluded."""
    frames = _make_frames(
        household_ids=["h1", "h2", "h3"],
        household_sizes=[1, 1, 1],
        housing_tenure_vals=["owner", "unknown", "renter"],
    )
    geo = _make_geo(["h1", "h2", "h3"])
    out = C._realized_housing_tenure(frames, geo)
    total = out["synthetic_count"].sum()
    assert total == 2, f"Expected 2 (owner+renter), got {total} (unknown should be excluded)"


def test_realized_tenure_absent_column():
    frames = _make_frames(["h1"], [1])
    geo = _make_geo(["h1"])
    out = C._realized_housing_tenure(frames, geo)
    assert out.empty


# ---------------------------------------------------------------------------
# 5. Realized extractor: building_type
# ---------------------------------------------------------------------------

def test_realized_building_type_three_classes():
    frames = _make_frames(
        household_ids=["h1", "h2", "h3", "h4"],
        household_sizes=[1, 1, 1, 1],
        building_type_vals=[
            "ein_zweifamilienhaus", "mehrfamilienhaus", "sonstiges", "mehrfamilienhaus"
        ],
    )
    geo = _make_geo(["h1", "h2", "h3", "h4"])
    out = C._realized_building_type(frames, geo)
    mfh = out.loc[out["category"] == "mehrfamilienhaus", "synthetic_count"].iloc[0]
    assert mfh == 2


def test_realized_building_type_drops_nan():
    """NaN building_type_3class (haustyp=95, n.z.) must be excluded."""
    frames = _make_frames(
        household_ids=["h1", "h2"],
        household_sizes=[1, 1],
        building_type_vals=["ein_zweifamilienhaus", None],
    )
    geo = _make_geo(["h1", "h2"])
    out = C._realized_building_type(frames, geo)
    assert out["synthetic_count"].sum() == 1


# ---------------------------------------------------------------------------
# 6. Realized extractor: seniorenstatus
# ---------------------------------------------------------------------------

def test_realized_seniorenstatus_detects_senior():
    """Households with at least one person aged >= 65 -> mit_senioren."""
    frames = _make_frames(
        household_ids=["h1", "h1", "h2"],
        household_sizes=[2, 2, 1],
        ages=[65, 40, 30],
    )
    geo = _make_geo(["h1", "h2"])
    out = C._realized_seniorenstatus(frames, geo)
    mit = out.loc[out["category"] == "mit_senioren", "synthetic_count"]
    ohne = out.loc[out["category"] == "ohne_senioren", "synthetic_count"]
    assert mit.iloc[0] == 1  # h1 has senior
    assert ohne.iloc[0] == 1  # h2 has no senior


def test_realized_seniorenstatus_boundary_age():
    """Age=64 -> ohne_senioren; age=65 -> mit_senioren."""
    frames_64 = _make_frames(["h1"], [1], ages=[64])
    frames_65 = _make_frames(["h2"], [1], ages=[65])
    geo64 = _make_geo(["h1"])
    geo65 = _make_geo(["h2"])

    out64 = C._realized_seniorenstatus(frames_64, geo64)
    assert out64.loc[out64["category"] == "ohne_senioren", "synthetic_count"].iloc[0] == 1

    out65 = C._realized_seniorenstatus(frames_65, geo65)
    assert out65.loc[out65["category"] == "mit_senioren", "synthetic_count"].iloc[0] == 1


# ---------------------------------------------------------------------------
# 7. Realized extractor: age x sex
# ---------------------------------------------------------------------------

def test_realized_age_sex_male_bands():
    """Male age extractor maps to correct 10-year band."""
    frames = _make_frames(
        household_ids=["h1", "h2", "h3"],
        household_sizes=[1, 1, 1],
        ages=[5, 25, 75],
        sexes=["male", "male", "female"],
    )
    geo = _make_geo(["h1", "h2", "h3"])
    extractor = C._make_age_sex_realized(sex="male")
    out = extractor(frames, geo)
    assert set(out.columns) == {"geo_id", "category", "synthetic_count"}
    # Only 2 male persons (h1=age5, h2=age25); h3 is female
    assert out["synthetic_count"].sum() == 2
    band_5 = out.loc[out["category"] == "0-9", "synthetic_count"]
    assert band_5.iloc[0] == 1


def test_realized_age_sex_80plus_band():
    """Persons aged 80+ must map to the '80+' band."""
    frames = _make_frames(
        household_ids=["h1", "h2"],
        household_sizes=[1, 1],
        ages=[80, 95],
        sexes=["female", "female"],
    )
    geo = _make_geo(["h1", "h2"])
    extractor = C._make_age_sex_realized(sex="female")
    out = extractor(frames, geo)
    band_80plus = out.loc[out["category"] == "80+", "synthetic_count"]
    assert band_80plus.iloc[0] == 2


# ---------------------------------------------------------------------------
# 8. End-to-end mini validation
# ---------------------------------------------------------------------------

def _mini_target(data_path: str) -> pd.DataFrame:
    """Fixed target: 50% owner / 50% renter for Kreis 03101."""
    return pd.DataFrame({
        "geo_id": ["03101", "03101"],
        "category": ["owner", "renter"],
        "target_share": [0.5, 0.5],
    })


def test_end_to_end_evaluate_assess(monkeypatch):
    """Mini end-to-end: one control (tenure), 2 households, perfect fit -> grade very good."""
    from braunschweig.analysis.popsim_validation import controls as C
    from braunschweig.analysis.population_validation import control_validation as CV
    from braunschweig.analysis.population_validation import quality_assessment as QA

    frames = _make_frames(
        household_ids=["h1", "h2"],
        household_sizes=[2, 3],
        housing_tenure_vals=["owner", "renter"],
    )
    geo = _make_geo(["h1", "h2"])

    # Build a one-control registry with monkeypatched target.
    tenure_ctrl = C.Control(
        name="tenure",
        family="popsim_hh",
        geography="kreis",
        categories=("owner", "renter"),
        realized=C._realized_housing_tenure,
        target=_mini_target,
    )
    long = CV.evaluate_all([tenure_ctrl], frames, geo, "/fake/data_path")
    assert not long.empty, "evaluate_all returned empty frame for non-empty control"
    assert set(long["control"]) == {"tenure"}

    summary = CV.summarize(long)
    assert not summary.empty

    quality = QA.assess(long)
    assert not quality.empty
    assert quality.loc[0, "control"] == "tenure"
    # Perfect 50/50 fit -> delta_pp should be ~0 -> very good grade
    grade = quality.loc[0, "grade"]
    assert grade in ("very good", "good"), f"Expected very good/good grade, got {grade!r}"

    fam = QA.family_scores(quality)
    assert not fam.empty
    assert "popsim_hh" in fam["family"].values


# ---------------------------------------------------------------------------
# 9. seniorenstatus_target — column name regression guard
# ---------------------------------------------------------------------------

def test_seniorenstatus_target_non_empty(monkeypatch):
    """seniorenstatus_target must return non-empty target shares for mit/ohne_senioren.

    Uses a synthetic cells frame with the real cleaned column names so that a
    rename in the parquet (or a typo in the target loader) is caught immediately.
    """
    _COL_OHNE  = "HH_ohneSenioren_Seniorenstatus_eines_privaten_Haushalts_100m_Gitter"
    _COL_MIT   = "HH_mitSenioren_Seniorenstatus_eines_privaten_Haushalts_100m_Gitter"
    _COL_NUR   = "HH_nurSenioren_Seniorenstatus_eines_privaten_Haushalts_100m_Gitter"
    _COL_TOTAL = "Insgesamt_Haushalte_Seniorenstatus_eines_privaten_Haushalts_100m_Gitter_adj"

    synthetic_cells = pd.DataFrame({
        "ars5":      ["03101", "03101", "03102"],
        _COL_OHNE:   [60.0,   40.0,   80.0],
        _COL_MIT:    [20.0,   30.0,   10.0],
        _COL_NUR:    [10.0,    5.0,    5.0],
        _COL_TOTAL:  [90.0,   75.0,   95.0],
    })

    # Patch _load_cells to return the synthetic frame without touching the filesystem.
    monkeypatch.setattr(C, "_load_cells", lambda data_path: synthetic_cells)

    out = C.seniorenstatus_target("/fake/data_path")

    assert not out.empty, "seniorenstatus_target returned an empty DataFrame"
    assert set(out.columns) == {"geo_id", "category", "target_share"}, (
        f"Unexpected columns: {set(out.columns)}"
    )
    cats = set(out["category"])
    assert cats == {"mit_senioren", "ohne_senioren"}, (
        f"Expected exactly {{mit_senioren, ohne_senioren}}, got {cats}"
    )

    # Shares must be in [0, 1] and sum to <=1 per geo (<=1 because total_col is adj total).
    for geo, grp in out.groupby("geo_id"):
        for _, row in grp.iterrows():
            assert 0.0 <= row["target_share"] <= 1.0, (
                f"target_share out of [0,1] for geo={geo}, category={row['category']}: "
                f"{row['target_share']}"
            )

    # Spot-check: for 03101, ohne_senioren count = 60+40=100, mit_senioren = 20+30+10+5=65,
    # total = 90+75=165. Shares: ohne=100/165, mit=65/165.
    row_ohne = out[(out["geo_id"] == "03101") & (out["category"] == "ohne_senioren")]
    row_mit  = out[(out["geo_id"] == "03101") & (out["category"] == "mit_senioren")]
    assert len(row_ohne) == 1 and len(row_mit) == 1
    expected_ohne = 100.0 / 165.0
    expected_mit  = 65.0 / 165.0
    assert abs(row_ohne["target_share"].iloc[0] - expected_ohne) < 1e-6, (
        f"ohne_senioren share mismatch: {row_ohne['target_share'].iloc[0]} != {expected_ohne}"
    )
    assert abs(row_mit["target_share"].iloc[0] - expected_mit) < 1e-6, (
        f"mit_senioren share mismatch: {row_mit['target_share'].iloc[0]} != {expected_mit}"
    )


# ---------------------------------------------------------------------------
# Employed-rate extractors: ARS leading-zero regression (2026-07-12 audit)
# ---------------------------------------------------------------------------

def test_employed_rate_extractors_keep_ars_leading_zero():
    """A numeric-typed ARS loses its leading zero, so slicing without zfill(12)
    yields Kreis keys like '3101' that never match the zero-padded target keys
    ('03101' -- every Lower Saxony Kreis). Both employed-rate extractors must
    zero-pad before slicing, consistent with the other extractors in this
    module (see e.g. the ars5 derivation) and the primary popsim helpers."""
    persons = pd.DataFrame({
        # numeric ARS: str() gives 11 digits, the leading zero is gone
        "RegionalSchlussel_ARS": [31010000000, 31010000000, 31010000000],
        "HP_ALTER": [30, 40, 65],
        "P_TAET": [1, 9, 1],  # employed, not employed, employed (60plus band)
    })

    rate = C.employed_25_64_rate(persons)
    assert rate == {"03101": 0.5}, (
        f"Kreis key must keep the leading zero ('03101'), got {rate}"
    )

    by_group = C.employed_by_age_group(persons)
    assert ("03101", "30_39") in by_group and by_group[("03101", "30_39")] == 1.0
    assert ("03101", "40_49") in by_group and by_group[("03101", "40_49")] == 0.0
    assert ("03101", "60plus") in by_group and by_group[("03101", "60plus")] == 1.0
    assert not any(k.startswith("3101") for k, _ in by_group), (
        "found an un-padded Kreis key -- zfill(12) missing before the [:5] slice"
    )


# ---------------------------------------------------------------------------
# 10. _load_cells in-process cache (audit 2026-11 package H, finding A2-F3)
# ---------------------------------------------------------------------------

def _write_synthetic_cells_parquet(data_root):
    """Write a small prepared-cells parquet with every column the cells-based target
    loaders read (raw, pre-clean names where the loader cleans them) under data_root."""
    from braunschweig.popsim.control_spec import (
        build_aggregation_map, controls_for_seed, full_catalog)

    rng = np.random.default_rng(7)
    n = 12
    columns = {
        "Insgesamt_Haushalte_Groesse_des_privaten_Haushalts_100m-Gitter_adj",
        "Insgesamt_Haushalte_Typ_priv_HH_Familie_100m-Gitter",
        "EinpersHH_SingleHH_Typ_priv_HH_Familie_100m-Gitter",
        "Paare_ohneKind_Typ_priv_HH_Familie_100m-Gitter",
        "Paare_mitKind_Typ_priv_HH_Familie_100m-Gitter",
        "Alleinerziehende_Typ_priv_HH_Familie_100m-Gitter",
        "MehrpersHHohneKernfam_Typ_priv_HH_Familie_100m-Gitter",
        "EigentuemerHH_Tenure_100m-Gitter",
        "MieterHH_Tenure_100m-Gitter",
        "HH_ohneSenioren_Seniorenstatus_eines_privaten_Haushalts_100m-Gitter",
        "HH_mitSenioren_Seniorenstatus_eines_privaten_Haushalts_100m-Gitter",
        "HH_nurSenioren_Seniorenstatus_eines_privaten_Haushalts_100m-Gitter",
        "Insgesamt_Haushalte_Seniorenstatus_eines_privaten_Haushalts_100m-Gitter_adj",
    }
    columns |= {f"{k}_Groesse_des_privaten_Haushalts_100m-Gitter"
                for k in ("1_Person", "2_Personen", "3_Personen", "4_Personen",
                          "5_Personen", "6_Personen_und_mehr")}
    for prefix in ("M", "F"):
        columns.add(f"{prefix}_TOTAL")
        columns |= {f"{prefix}_AGE_{band}_agg" for band in
                    ("0_9", "10_19", "20_29", "30_39", "40_49", "50_59", "60_69",
                     "70_79", "80_plus")}
    agg_map = build_aggregation_map(
        controls_for_seed(full_catalog(include_tiers=("tier2",)), "mid"))
    for sources in agg_map.values():
        columns |= set(sources)
    frame = pd.DataFrame({col: rng.integers(0, 50, size=n).astype(float)
                          for col in sorted(columns)})
    frame["RegionalSchlüssel_ARS"] = ["031010000000"] * 6 + ["031020000000"] * 6
    parquet = data_root / C._PARQUET_SUBPATH
    parquet.parent.mkdir(parents=True)
    frame.to_parquet(parquet)
    return parquet


_CELLS_TARGET_LOADERS = (
    C.household_size_target,
    C.household_type_target,
    C.tenure_target,
    C.building_type_target,
    lambda dp: C.age_sex_target(dp, sex="male"),
    lambda dp: C.age_sex_target(dp, sex="female"),
    C.seniorenstatus_target,
)


def test_load_cells_cache_reads_once_and_targets_are_byte_identical(tmp_path, monkeypatch):
    """The cached _load_cells reads the parquet once, every cells-based target table is
    identical to the uncached read, and no target loader mutates the shared frame."""
    parquet = _write_synthetic_cells_parquet(tmp_path)
    data_path = str(tmp_path)
    C.clear_cells_cache()

    # Reference: every loader on its own fresh, uncached read (the pre-cache behaviour).
    def _uncached(dp):
        return C._read_and_clean_cells(tmp_path / C._PARQUET_SUBPATH)
    with monkeypatch.context() as patch:
        patch.setattr(C, "_load_cells", _uncached)
        reference = [loader(data_path) for loader in _CELLS_TARGET_LOADERS]
    # Primary path: the synthetic parquet carries every target column, so each table has
    # rows for both Kreise (an empty table would make the identity check vacuous).
    for ref in reference:
        assert set(ref["geo_id"]) == {"03101", "03102"}

    reads = []
    real_read_parquet = pd.read_parquet

    def _counting_read_parquet(path, *args, **kwargs):
        reads.append(path)
        return real_read_parquet(path, *args, **kwargs)
    monkeypatch.setattr(pd, "read_parquet", _counting_read_parquet)

    try:
        shared = C._load_cells(data_path)
        before = pd.util.hash_pandas_object(shared, index=True).to_numpy().copy()
        columns_before = list(shared.columns)
        cached = [loader(data_path) for loader in _CELLS_TARGET_LOADERS]

        assert len(reads) == 1, f"parquet read {len(reads)} times, expected once"
        assert C._load_cells(data_path) is shared
        for ref, out in zip(reference, cached):
            pd.testing.assert_frame_equal(out, ref, check_exact=True)
        # No target loader mutated the shared frame (values, index or columns).
        assert list(shared.columns) == columns_before
        np.testing.assert_array_equal(
            pd.util.hash_pandas_object(shared, index=True).to_numpy(), before)
    finally:
        C.clear_cells_cache()
    assert parquet.exists()


def test_load_cells_cache_rereads_after_clear_and_on_file_change(tmp_path, monkeypatch):
    """clear_cells_cache forces a fresh read, and a rewritten parquet is not served stale."""
    parquet = _write_synthetic_cells_parquet(tmp_path)
    data_path = str(tmp_path)
    C.clear_cells_cache()
    try:
        first = C._load_cells(data_path)
        C.clear_cells_cache()
        second = C._load_cells(data_path)
        assert second is not first
        pd.testing.assert_frame_equal(first, second)

        # Rewrite the file with one more row: the key (mtime_ns, size) changes.
        frame = pd.read_parquet(parquet)
        pd.concat([frame, frame.iloc[[0]]], ignore_index=True).to_parquet(parquet)
        third = C._load_cells(data_path)
        assert len(third) == len(first) + 1
        assert len(C._CELLS_CACHE) == 1
    finally:
        C.clear_cells_cache()


# ---------------------------------------------------------------------------
# 11. Tenure target: the owner limit of issue #657 (same rule as the stage)
# ---------------------------------------------------------------------------

def _fixture_cells_with_kreis(monkeypatch):
    """The synthetic cells of the control-build fixture as ``_load_cells`` returns them."""
    import sys
    from pathlib import Path
    # append, never insert(0): 'tests' at the front of sys.path would shadow braunschweig.
    sys.path.append(str(Path(__file__).resolve().parent))
    import popsim_control_build_fixture as fixture
    from braunschweig.popsim import mid

    cells = fixture.make_cells()
    cells["ars5"] = cells[mid._ARS_COLUMN].str[:5]
    monkeypatch.setattr(C, "_load_cells", lambda data_path: cells)
    return cells


def test_validation_tenure_target_uses_the_same_rule(monkeypatch):
    """The Kreis owner share of the validation equals the share of the limited cells.

    Hand computation on the fixture cells (owner / renter households per cell). Kreis 03101
    holds cells 0-4: owners 11.3 + 12.1 + 30 + 118 + 20.9 = 192.3, renters 13.2 + 5.2 + 0 + 0 +
    20.9 = 39.3, total 231.6. The rule limits cell 2 (30 households, 2 dwellings: owners 30 ->
    2) and cell 3 (118 households, 0 dwellings: owners 118 -> 0), so the owners fall to
    11.3 + 12.1 + 2 + 0 + 20.9 = 46.3 of the same 231.6 households. Kreis 03158 (cells 5-7) has
    no cell with 10 or more households outside its dwellings: 21.2 owners of 49.0.
    """
    _fixture_cells_with_kreis(monkeypatch)

    def owner_share(**kwargs):
        target = C.tenure_target("unused", **kwargs)
        owner = target[target["category"] == "owner"].set_index("geo_id")["target_share"]
        renter = target[target["category"] == "renter"].set_index("geo_id")["target_share"]
        assert (owner + renter).tolist() == pytest.approx([1.0, 1.0])
        return owner

    default = owner_share()
    off = owner_share(owner_limited_to_dwellings=False)
    on = owner_share(owner_limited_to_dwellings=True, min_households_outside=10.0)

    assert default["03101"] == pytest.approx(192.3 / 231.6)
    assert off["03101"] == pytest.approx(192.3 / 231.6)
    assert on["03101"] == pytest.approx(46.3 / 231.6)
    assert on["03158"] == off["03158"] == pytest.approx(21.2 / 49.0)


@pytest.mark.parametrize("mode", ["zensus_family5", "none", "minor_child_1km"])
def test_family_controls_mode_shapes_the_household_registry(monkeypatch, tmp_path, mode):
    """The run's family_controls (#659) decides which family rows are scored as controls.

    Defect caught: a run without the census family-type controls scored against them as if
    PopulationSim had fitted them, and the minor-child controls it did fit left unvalidated.
    Hand computation: every fixture square has the shares 0.1 (one adult) and 0.2 (multi adult)
    of its households, so each Kreis has the same shares and 0.7 without a minor.
    """
    from braunschweig.popsim import minor_child_targets as mct
    from braunschweig.popsim.seed import HH_MINOR_CLASSES

    cells = _fixture_cells_with_kreis(monkeypatch)
    cells["GITTER_ID_100m"] = cells["ZENSUS100m"]
    squares = pd.DataFrame({mct.SQUARE_ID_COLUMN: sorted(cells["ZENSUS1km"].unique())})
    for column in mct.LONE_PARENT_SOURCES:
        squares[column] = 0.5
    for column in mct.COUPLE_SOURCES:
        squares[column] = 2.0 / 3.0
    squares[mct.FAMILY_TABLE_HOUSEHOLDS] = 10.0
    parquet_1km = tmp_path / C.PARQUET_1KM_SUBPATH
    parquet_1km.parent.mkdir(parents=True)
    squares.to_parquet(parquet_1km, index=False)

    registry = {c.name: c for c in C.build_registry(str(tmp_path), family_controls=mode)}

    assert registry["household_type"].family == (
        "popsim_hh" if mode == "zensus_family5" else "popsim_reference")
    assert ("household_minor" in registry) == (mode == "minor_child_1km")
    if mode != "minor_child_1km":
        return
    control = registry["household_minor"]
    assert control.family == "popsim_hh"
    none, one_adult, multi_adult = HH_MINOR_CLASSES
    target = control.target(str(tmp_path)).set_index(["geo_id", "category"])["target_share"]
    for kreis in ("03101", "03158"):
        assert target[(kreis, one_adult)] == pytest.approx(0.1)
        assert target[(kreis, multi_adult)] == pytest.approx(0.2)
        assert target[(kreis, none)] == pytest.approx(0.7)

    persons = pd.DataFrame({
        "person_id": ["a1", "a2", "b1", "b2", "b3", "c1"],
        "household_id": ["a", "a", "b", "b", "b", "c"],
        "age": [40, 10, 40, 38, 5, 30]})
    homes = gpd.GeoDataFrame({"household_id": ["a", "b", "c"]},
                             geometry=[Point(605000, 5790000)] * 3, crs="EPSG:25832")
    frames = PopulationFrames(persons, pd.DataFrame({"household_id": ["a", "b", "c"]}), homes,
                              None, "run_output", "/tmp", "test_")
    geo = pd.DataFrame({"household_id": ["a", "b", "c"], "ars5": ["03101"] * 3})
    realized = control.realized(frames, geo).set_index("category")["synthetic_count"]
    assert realized.to_dict() == {none: 1, one_adult: 1, multi_adult: 1}
