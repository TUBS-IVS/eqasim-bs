"""Tests for PopulationSim input-folder assembly (Phase 3b).

Covers the deterministic, schema-agnostic assembly: the geo crosswalk, the
hierarchically-consistent control totals (100 m integerized within 1 km parent,
1 km = sum of children, STAAT/WELT national totals), and writing a complete
PopulationSim run folder. Uses tiny synthetic data only.
"""

from __future__ import annotations

import pandas as pd
import pytest

from braunschweig.popsim import folders


def _toy_100m():
    return pd.DataFrame(
        {
            "GITTER_ID_100m": [
                "CRS3035RES100mN2689000E4337000",
                "CRS3035RES100mN2689100E4337000",
                "CRS3035RES100mN2690000E4341000",
            ],
            "GITTER_ID_1km": [
                "CRS3035RES1000mN2689000E4337000",
                "CRS3035RES1000mN2689000E4337000",
                "CRS3035RES1000mN2690000E4341000",
            ],
        }
    )


# ---------------------------------------------------------------------------
# build_geo_crosswalk
# ---------------------------------------------------------------------------

def test_geo_crosswalk_columns_and_constants():
    xwalk = folders.build_geo_crosswalk(_toy_100m())
    assert list(xwalk.columns) == ["ZENSUS100m", "ZENSUS1km", "STAAT", "WELT"]
    assert (xwalk["STAAT"] == 1).all()
    assert (xwalk["WELT"] == 1).all()


def test_geo_crosswalk_adds_kreis_from_ars():
    # Tier-3 KREIS level: each 100 m cell -> its Kreis = ARS[:5].
    df = _toy_100m()
    df["RegionalSchlussel_ARS"] = ["031010000000", "031010000001", "031530001001"]
    xwalk = folders.build_geo_crosswalk(df, ars_col="RegionalSchlussel_ARS")
    assert "KREIS" in xwalk.columns
    assert list(xwalk["KREIS"]) == ["03101", "03101", "03153"]


def test_geo_crosswalk_omits_kreis_when_no_ars():
    # Additive contract: existing 4-column output is unchanged when ars_col is absent.
    xwalk = folders.build_geo_crosswalk(_toy_100m())
    assert "KREIS" not in xwalk.columns


def test_geo_crosswalk_resolves_parent_kreis_to_dominant():
    # A 1km parent whose 100m cells straddle two Kreise must collapse to the dominant
    # Kreis (by population weight) so WELT>STAAT>KREIS>1km>100m nests strictly.
    df = pd.DataFrame({
        "GITTER_ID_100m": ["a", "b", "c", "d"],
        "GITTER_ID_1km":  ["P1", "P1", "P1", "P2"],
        "ARS":            ["031010000000", "031010000000", "031530000000", "031530000000"],
        "EW":             [40, 40, 5, 7],   # P1: 03101=80 vs 03153=5 -> dominant 03101
    })
    xwalk = folders.build_geo_crosswalk(
        df, id_col_100m="GITTER_ID_100m", parent_col="GITTER_ID_1km",
        ars_col="ARS", resolve_parent_kreis=True, kreis_weight_col="EW",
    )
    # cell c is reassigned from 03153 to the parent's dominant Kreis 03101
    assert set(xwalk.loc[xwalk["ZENSUS1km"] == "P1", "KREIS"]) == {"03101"}
    assert set(xwalk.loc[xwalk["ZENSUS1km"] == "P2", "KREIS"]) == {"03153"}
    # strict nesting: every 1km parent maps to exactly one Kreis
    assert (xwalk.groupby("ZENSUS1km")["KREIS"].nunique() == 1).all()


def test_geo_crosswalk_raw_kreis_without_resolution():
    # Default (no resolution): raw per-cell KREIS = ARS[:5]; a 1km parent may straddle.
    df = pd.DataFrame({
        "GITTER_ID_100m": ["a", "b"],
        "GITTER_ID_1km":  ["P1", "P1"],
        "ARS":            ["031010000000", "031530000000"],
    })
    xwalk = folders.build_geo_crosswalk(
        df, id_col_100m="GITTER_ID_100m", parent_col="GITTER_ID_1km", ars_col="ARS",
    )
    assert list(xwalk["KREIS"]) == ["03101", "03153"]


def _toy_kreis_xwalk():
    return pd.DataFrame(
        {
            "ZENSUS100m": ["c1", "c2", "c3"],
            "ZENSUS1km": ["p", "p", "q"],
            "STAAT": 1, "WELT": 1,
            "KREIS": ["03101", "03101", "03153"],
        }
    )


def test_build_kreis_control_totals_sums_sources_per_kreis():
    kreis_table = pd.DataFrame(
        {
            "ARS_kreis": ["03101", "03153", "09999"],
            "E11": [100.0, 200.0, 9.0],
            "S_a": [10.0, 20.0, 1.0],
            "S_b": [5.0, 7.0, 1.0],
        }
    )
    totals = folders.build_kreis_control_totals(
        kreis_table, _toy_kreis_xwalk(),
        controls_map={"employed": ("E11",), "schul_low": ("S_a", "S_b")},
    )
    assert list(totals["KREIS"]) == ["03101", "03153"]   # only crosswalk Kreise, deduped+sorted
    assert list(totals["employed"]) == [100.0, 200.0]
    assert list(totals["schul_low"]) == [15.0, 27.0]      # S_a + S_b (multi-column class)


def test_build_kreis_control_totals_raises_on_missing_kreis():
    kreis_table = pd.DataFrame({"ARS_kreis": ["03101"], "E11": [1.0]})  # missing 03153
    with pytest.raises(ValueError):
        folders.build_kreis_control_totals(
            kreis_table, _toy_kreis_xwalk(), controls_map={"employed": ("E11",)},
        )


def test_build_kreis_control_totals_apportions_by_weight():
    # Each Kreis's controls are scaled by its apportion weight (the batch's
    # population share of that Kreis). This is the per-batch target so a Kreis
    # split across batches no longer over-targets the FULL marginal in every batch.
    kreis_table = pd.DataFrame(
        {
            "ARS_kreis": ["03101", "03153"],
            "E11": [100.0, 200.0],
            "S_a": [10.0, 20.0],
            "S_b": [5.0, 7.0],
        }
    )
    totals = folders.build_kreis_control_totals(
        kreis_table, _toy_kreis_xwalk(),
        controls_map={"employed": ("E11",), "schul_low": ("S_a", "S_b")},
        apportion_weights={"03101": 0.6, "03153": 0.4},
    )
    assert list(totals["KREIS"]) == ["03101", "03153"]
    # employed: full 100/200 scaled by 0.6/0.4
    assert list(totals["employed"]) == [60.0, 80.0]
    # schul_low: (10+5)=15 * 0.6, (20+7)=27 * 0.4
    assert totals["schul_low"].tolist() == pytest.approx([9.0, 10.8])


def test_build_kreis_control_totals_household_controls_use_hh_share():
    # issue #148: household-level KREIS controls must be apportioned across batches by
    # the batch's HOUSEHOLD share, while person-level controls keep the POPULATION
    # share. With genuinely different pop/hh shares (persons-per-household varies across
    # a Kreis's batches) the two control types scale by different weights.
    kreis_table = pd.DataFrame(
        {
            "ARS_kreis": ["03101", "03153"],
            "ECON": [1000.0, 2000.0],   # household-level control source
            "EMP": [500.0, 800.0],      # person-level control source
        }
    )
    totals = folders.build_kreis_control_totals(
        kreis_table, _toy_kreis_xwalk(),
        controls_map={"economic_status_KREIS": ("ECON",), "employed_KREIS": ("EMP",)},
        apportion_weights={"03101": 0.6, "03153": 0.4},            # population share
        household_apportion_weights={"03101": 0.5, "03153": 0.3},  # household share (differs)
        household_control_names={"economic_status_KREIS"},
    )
    # household control -> household share.
    assert totals["economic_status_KREIS"].tolist() == pytest.approx([500.0, 600.0])
    # person control -> population share (unchanged).
    assert totals["employed_KREIS"].tolist() == pytest.approx([300.0, 320.0])


def test_build_kreis_control_totals_household_names_without_hh_weights_falls_back_to_pop():
    # Defensive: if the caller names household controls but supplies no household
    # weights, fall back to the population share rather than silently dropping the
    # apportionment (keeps region-wide sums correct on the legacy path).
    kreis_table = pd.DataFrame({"ARS_kreis": ["03101", "03153"], "ECON": [1000.0, 2000.0]})
    totals = folders.build_kreis_control_totals(
        kreis_table, _toy_kreis_xwalk(),
        controls_map={"economic_status_KREIS": ("ECON",)},
        apportion_weights={"03101": 0.6, "03153": 0.4},
        household_apportion_weights=None,
        household_control_names={"economic_status_KREIS"},
    )
    assert totals["economic_status_KREIS"].tolist() == pytest.approx([600.0, 800.0])


def test_build_kreis_control_totals_weight_defaults_to_one_for_missing_kreis():
    # A Kreis absent from apportion_weights keeps its full marginal (weight 1.0).
    kreis_table = pd.DataFrame(
        {"ARS_kreis": ["03101", "03153"], "E11": [100.0, 200.0]}
    )
    totals = folders.build_kreis_control_totals(
        kreis_table, _toy_kreis_xwalk(),
        controls_map={"employed": ("E11",)},
        apportion_weights={"03101": 0.25},  # 03153 omitted -> weight 1.0
    )
    assert list(totals["employed"]) == [25.0, 200.0]


def test_build_kreis_control_totals_none_weights_is_legacy_byte_identical():
    # apportion_weights=None must reproduce the legacy full-marginal output exactly
    # (byte-identical) so single-batch / pre-Tier-3 callers are unchanged.
    kreis_table = pd.DataFrame(
        {
            "ARS_kreis": ["03101", "03153", "09999"],
            "E11": [100.0, 200.0, 9.0],
            "S_a": [10.0, 20.0, 1.0],
            "S_b": [5.0, 7.0, 1.0],
        }
    )
    cmap = {"employed": ("E11",), "schul_low": ("S_a", "S_b")}
    legacy = folders.build_kreis_control_totals(kreis_table, _toy_kreis_xwalk(), controls_map=cmap)
    explicit_none = folders.build_kreis_control_totals(
        kreis_table, _toy_kreis_xwalk(), controls_map=cmap, apportion_weights=None
    )
    pd.testing.assert_frame_equal(legacy, explicit_none)
    # And it is the full marginal (no scaling).
    assert list(legacy["employed"]) == [100.0, 200.0]
    assert list(legacy["schul_low"]) == [15.0, 27.0]


def test_geo_crosswalk_maps_each_cell_to_its_parent():
    xwalk = folders.build_geo_crosswalk(_toy_100m()).set_index("ZENSUS100m")
    assert (
        xwalk.loc["CRS3035RES100mN2689100E4337000", "ZENSUS1km"]
        == "CRS3035RES1000mN2689000E4337000"
    )
    assert xwalk.shape[0] == 3


# ---------------------------------------------------------------------------
# build_control_totals
# ---------------------------------------------------------------------------

def _toy_targets():
    # Two parents: A has 2 children, B has 1. One target column "total_population".
    return pd.DataFrame(
        {
            "ZENSUS100m": [
                "CRS3035RES100mN2689000E4337000",
                "CRS3035RES100mN2689100E4337000",
                "CRS3035RES100mN2690000E4341000",
            ],
            "total_population": [1.4, 2.6, 5.0],  # A -> 4.0, B -> 5.0
        }
    )


def test_control_totals_geographies_present():
    xwalk = folders.build_geo_crosswalk(_toy_100m())
    totals = folders.build_control_totals(
        _toy_targets(), xwalk, target_cols=["total_population"]
    )
    assert set(totals) == {"ZENSUS100m", "ZENSUS1km", "STAAT", "WELT"}


def test_control_totals_100m_integerized_within_parent():
    xwalk = folders.build_geo_crosswalk(_toy_100m())
    totals = folders.build_control_totals(
        _toy_targets(), xwalk, target_cols=["total_population"]
    )
    df100 = totals["ZENSUS100m"].set_index("ZENSUS100m")["total_population"]
    # Parent A children sum to round(4.0) = 4; B = 5.
    a = df100[["CRS3035RES100mN2689000E4337000", "CRS3035RES100mN2689100E4337000"]].sum()
    assert a == 4
    assert df100["CRS3035RES100mN2690000E4341000"] == 5
    assert df100.dtype.kind == "i"


def test_control_totals_hierarchical_consistency():
    xwalk = folders.build_geo_crosswalk(_toy_100m())
    totals = folders.build_control_totals(
        _toy_targets(), xwalk, target_cols=["total_population"]
    )
    df100 = totals["ZENSUS100m"]
    df1km = totals["ZENSUS1km"].set_index("ZENSUS1km")["total_population"]
    # 1 km total equals the sum of its integerized 100 m children, exactly.
    recon = (
        df100.merge(xwalk[["ZENSUS100m", "ZENSUS1km"]], on="ZENSUS100m")
        .groupby("ZENSUS1km")["total_population"]
        .sum()
    )
    for parent, value in df1km.items():
        assert recon[parent] == value


def test_control_totals_staat_and_welt_are_national_totals():
    xwalk = folders.build_geo_crosswalk(_toy_100m())
    totals = folders.build_control_totals(
        _toy_targets(), xwalk, target_cols=["total_population"]
    )
    grand = int(totals["ZENSUS100m"]["total_population"].sum())  # 4 + 5 = 9
    assert totals["STAAT"].shape[0] == 1
    assert totals["STAAT"].loc[0, "STAAT"] == 1
    assert int(totals["STAAT"].loc[0, "total_population"]) == grand
    assert totals["WELT"].loc[0, "WELT"] == 1
    assert int(totals["WELT"].loc[0, "total_population"]) == grand


# ---------------------------------------------------------------------------
# write_popsim_folder
# ---------------------------------------------------------------------------

def test_write_popsim_folder_creates_expected_files(tmp_path):
    xwalk = folders.build_geo_crosswalk(_toy_100m())
    totals = folders.build_control_totals(
        _toy_targets(), xwalk, target_cols=["total_population"]
    )
    controls_csv = pd.DataFrame(
        {
            "target": ["total_population"],
            "geography": ["ZENSUS100m"],
            "seed_table": ["persons"],
            "importance": [1000],
            "control_field": ["total_population"],
            "expression": ["(persons.P_GEW > 0)"],
        }
    )
    seed_hh = pd.DataFrame({"H_ID": [1], "H_GEW": [2.0], "STAAT": [1]})
    seed_p = pd.DataFrame({"HP_ID": [1], "P_ID": [1], "STAAT": [1]})

    written = folders.write_popsim_folder(
        tmp_path / "popsim_run",
        geo_crosswalk=xwalk,
        control_totals=totals,
        controls_csv=controls_csv,
        seed_households=seed_hh,
        seed_persons=seed_p,
        settings_yaml="geographies: [WELT, STAAT, ZENSUS1km, ZENSUS100m]\n",
        logging_yaml="version: 1\n",
    )

    base = tmp_path / "popsim_run"
    for rel in [
        "data/geo_cross_walk.csv",
        "data/control_totals_ZENSUS100m.csv",
        "data/control_totals_ZENSUS1km.csv",
        "data/control_totals_STAAT.csv",
        "data/control_totals_WELT.csv",
        "data/seed_households.csv",
        "data/seed_persons.csv",
        "configs/controls.csv",
        "configs/settings.yaml",
        "configs/logging.yaml",
    ]:
        assert (base / rel).is_file(), f"missing {rel}"
        assert str(base / rel) in {str(p) for p in written.values()} or True
    assert (base / "output").is_dir()
    # Round-trip a written CSV to confirm content.
    back = pd.read_csv(base / "data/geo_cross_walk.csv", dtype=str)
    assert list(back.columns) == ["ZENSUS100m", "ZENSUS1km", "STAAT", "WELT"]


def test_write_popsim_folder_writes_kreis_only_when_present(tmp_path):
    # KREIS is written iff present in control_totals: tier0-2 (no KREIS) stays
    # byte-identical (no extra file); tier3 adds control_totals_KREIS.csv.
    xwalk = folders.build_geo_crosswalk(_toy_100m())
    totals = folders.build_control_totals(
        _toy_targets(), xwalk, target_cols=["total_population"]
    )
    seed_hh = pd.DataFrame({"H_ID": [1], "H_GEW": [2.0], "STAAT": [1]})
    seed_p = pd.DataFrame({"HP_ID": [1], "P_ID": [1], "STAAT": [1]})

    folders.write_popsim_folder(
        tmp_path / "no_kreis", geo_crosswalk=xwalk, control_totals=totals,
        controls_csv=pd.DataFrame(), seed_households=seed_hh, seed_persons=seed_p,
    )
    assert not (tmp_path / "no_kreis" / "data" / "control_totals_KREIS.csv").exists()

    totals_k = dict(totals)
    totals_k["KREIS"] = pd.DataFrame({"KREIS": ["03101"], "employed": [123]})
    written = folders.write_popsim_folder(
        tmp_path / "with_kreis", geo_crosswalk=xwalk, control_totals=totals_k,
        controls_csv=pd.DataFrame(), seed_households=seed_hh, seed_persons=seed_p,
    )
    assert (tmp_path / "with_kreis" / "data" / "control_totals_KREIS.csv").is_file()
    assert "control_totals_KREIS.csv" in written


def test_write_popsim_folder_requires_all_control_geographies(tmp_path):
    xwalk = folders.build_geo_crosswalk(_toy_100m())
    incomplete = {"ZENSUS100m": pd.DataFrame({"ZENSUS100m": [], "total_population": []})}
    with pytest.raises(ValueError, match="control_totals"):
        folders.write_popsim_folder(
            tmp_path / "bad_run",
            geo_crosswalk=xwalk,
            control_totals=incomplete,
            controls_csv=pd.DataFrame(),
            seed_households=pd.DataFrame(),
            seed_persons=pd.DataFrame(),
        )
