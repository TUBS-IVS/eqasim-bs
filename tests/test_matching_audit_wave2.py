"""Wave-2 matching audit: verified findings from the project-wide join/fallback sweep.

Covers (per finding, RED before fix):
1. calibration/targets.load_p13_band_shares — ars5 dtype at READ (leading zero).
2. ipf/model._map_departement_index — silent dropna made observable; raise when a
   configured employment-by-hhsize CSV maps ZERO rows (feature silently inert).
3. ipf/attributed hh_size guard — descriptive RuntimeError must be reachable
   (previously IntCastingNaNError preempted it).
4. run_mid_validation household-size counts — sizes >= 5 ("5", "6", "7", "6+")
   must not be silently dropped by a hardcoded reindex.
5. popsim/folders.build_geo_crosswalk — WARN when the requested kreis_weight_col
   is absent (uniform-weight fallback).
6. popsim/cells.sum_columns_logging_nan — WARN when the column list is EMPTY
   (all-zero aggregate would silently look like "no employment").
7. calibration/run_building_fit._load_stage — WARN when several config-hash
   generations of one stage exist (mtime pick can mix runs).
8. analysis/home_match_validation.home_match_metrics — match coverage logged.
9. analysis/run_education_validation.enrollment_vs_capacity — assignments whose
   location_id has no facility row are counted and logged.
"""
from __future__ import annotations

import logging
import pickle

import numpy as np
import pandas as pd
import pytest


# ---------------------------------------------------------------------------
# 1. calibration/targets.py — dtype at read
# ---------------------------------------------------------------------------

def test_load_p13_band_shares_keeps_leading_zero(tmp_path):
    from braunschweig.calibration import targets

    (tmp_path / "mid2023_P13.csv").write_text(
        "kreis,ars5,d_0,d_0_5,d_5_10,d_10_20,d_20_30,d_30_50,d_50_100,d_100p\n"
        "Braunschweig,03101,1,20,20,20,20,10,5,4\n"
        "Gifhorn,03151,1,20,20,20,20,10,5,4\n",
        encoding="utf-8",
    )
    out = targets.load_p13_band_shares(str(tmp_path))
    assert set(out) == {"03101", "03151"}


# ---------------------------------------------------------------------------
# 2. ipf/model.py — employment-margin departement mapping coverage
# ---------------------------------------------------------------------------

def test_map_departement_index_raises_when_nothing_matches():
    from braunschweig.ipf import model as ipf_model

    emp = pd.DataFrame({
        "departement_id": ["3101", "3151"],  # un-padded: matches nothing
        "hh_size": ["1", "2"],
        "employed": [True, False],
        "weight": [10.0, 20.0],
    })
    with pytest.raises(RuntimeError, match="0(/| of )|zero|none"):
        ipf_model._map_departement_index(emp, {"03101": 0, "03151": 1})


def test_map_departement_index_logs_partial_drop(capsys):
    from braunschweig.ipf import model as ipf_model

    emp = pd.DataFrame({
        "departement_id": ["03101", "99999"],  # 99999 = outside scope, dropped
        "hh_size": ["1", "2"],
        "employed": [True, False],
        "weight": [10.0, 20.0],
    })
    out = ipf_model._map_departement_index(emp, {"03101": 0})
    captured = capsys.readouterr().out
    assert "1" in captured and "99999" in captured
    assert len(out) == 1
    assert out["departement_index"].iloc[0] == 0


# ---------------------------------------------------------------------------
# 3. ipf/attributed.py — reachable hh_size guard
# ---------------------------------------------------------------------------

def test_hh_size_to_int_unknown_label_raises_descriptive():
    from braunschweig.ipf import attributed

    series = pd.Series(["1", "2", "7"])  # "7" is not a known bin label
    with pytest.raises(RuntimeError, match="hh_size"):
        attributed._hh_size_to_int(series)


def test_hh_size_to_int_known_labels():
    from braunschweig.ipf import attributed

    series = pd.Series(["1", "5", "6+"])
    out = attributed._hh_size_to_int(series)
    assert list(out) == [1, 5, 6]
    assert out.dtype == np.int64


# ---------------------------------------------------------------------------
# 4. run_mid_validation — household-size counts keep large households
# ---------------------------------------------------------------------------

def test_hhsize_counts_keep_large_households():
    run_mid_validation = pytest.importorskip(
        "braunschweig.analysis.run_mid_validation"
    )
    households = pd.DataFrame({
        "household_size": [1, 2, 5, 6, 7, "6+"],
    })
    counts = run_mid_validation._hhsize_counts(households)
    # Nothing may be dropped: 6 households in, 6 in the plotted counts.
    assert int(counts.sum()) == 6
    assert "7" in counts.index and "6+" in counts.index


# ---------------------------------------------------------------------------
# 4b. kba/hsn_tsn — HSN leading zero survives the CSV read path
# ---------------------------------------------------------------------------

def test_hsn_tsn_read_path_keeps_leading_zero(tmp_path):
    from braunschweig.data.kba import hsn_tsn

    path = tmp_path / hsn_tsn.HSN_TSN_RELATIVE_PATH
    path.parent.mkdir(parents=True)
    path.write_text(
        "brand,hsn,tsn,model,power_ps,power_kw,displacement_ccm,fuel\n"
        "VW,0603,ABC,GOLF,110,81,1498,Benzin\n",
        encoding="utf-8",
    )
    lookup = hsn_tsn.HsnTsnLookup.from_data_path(str(tmp_path))
    hsns = {rec.hsn for rec in lookup.brand_model_records.values()}
    assert hsns == {"0603"}


# ---------------------------------------------------------------------------
# 5. popsim/folders.build_geo_crosswalk — absent weight column WARNs
# ---------------------------------------------------------------------------

def _cells_frame():
    return pd.DataFrame({
        "GITTER_ID_100m": ["CRS3035RES100mN2689100E4337000",
                           "CRS3035RES100mN2689200E4337000"],
        "RegionalSchlussel_ARS": ["031010000000", "031510000000"],
    })


def test_build_geo_crosswalk_warns_on_missing_weight_col(caplog):
    from braunschweig.popsim import folders

    with caplog.at_level(logging.WARNING):
        folders.build_geo_crosswalk(
            _cells_frame(),
            ars_col="RegionalSchlussel_ARS",
            resolve_parent_kreis=True,
            kreis_weight_col="POP_TOTAL_100m_adj",  # absent from the frame
        )
    assert any("POP_TOTAL_100m_adj" in r.message for r in caplog.records)


def test_build_geo_crosswalk_no_warning_when_weight_col_present(caplog):
    from braunschweig.popsim import folders

    cells = _cells_frame()
    cells["POP_TOTAL_100m_adj"] = [10.0, 20.0]
    with caplog.at_level(logging.WARNING):
        folders.build_geo_crosswalk(
            cells,
            ars_col="RegionalSchlussel_ARS",
            resolve_parent_kreis=True,
            kreis_weight_col="POP_TOTAL_100m_adj",
        )
    assert not [r for r in caplog.records if "POP_TOTAL" in r.message]


# ---------------------------------------------------------------------------
# 6. popsim/cells.sum_columns_logging_nan — empty column list WARNs
# ---------------------------------------------------------------------------

def test_sum_columns_empty_list_warns(caplog):
    from braunschweig.popsim import cells

    frame = pd.DataFrame({"a": [1.0, 2.0]})
    with caplog.at_level(logging.WARNING):
        out = cells.sum_columns_logging_nan(frame, [], label="EMPLOYED_total")
    assert (out == 0.0).all()
    assert any("EMPLOYED_total" in r.message and "empty" in r.message.lower()
               for r in caplog.records)


# ---------------------------------------------------------------------------
# 7. run_building_fit._load_stage — ambiguity warning
# ---------------------------------------------------------------------------

def test_load_stage_warns_on_multiple_generations(tmp_path, caplog):
    from braunschweig.calibration import run_building_fit as rbf

    for suffix in ("aaa", "bbb"):
        with open(tmp_path / f"my.stage__{suffix}.p", "wb") as fh:
            pickle.dump({"which": suffix}, fh)
    with caplog.at_level(logging.WARNING):
        rbf._load_stage(str(tmp_path), "my.stage")
    assert any("2" in r.message and "my.stage" in r.message
               for r in caplog.records if r.levelno >= logging.WARNING)


# ---------------------------------------------------------------------------
# 8. home_match_validation.home_match_metrics — coverage logged
# ---------------------------------------------------------------------------

def test_home_match_metrics_logs_unmatched(caplog):
    from braunschweig.analysis import home_match_validation as hmv

    placed = pd.DataFrame({
        "home_location_id": ["b1", "b_missing", pd.NA],
        "household_size": [2, 3, 1],
        "building_type_3class": ["ein_zweifamilienhaus"] * 3,
    })
    buildings = pd.DataFrame({
        "building_id": ["b1"],
        "btype": ["efh_zfh"],
        "size": [2.0],
    })
    with caplog.at_level(logging.INFO):
        metrics = hmv.home_match_metrics(placed, buildings)
    assert metrics["n_households"] == 3
    assert any("1/3" in r.message or "matched" in r.message.lower()
               for r in caplog.records)


# ---------------------------------------------------------------------------
# 9. run_education_validation.enrollment_vs_capacity — dropped assignments logged
# ---------------------------------------------------------------------------

def test_enrollment_vs_capacity_logs_unmatched_assignments(caplog):
    from braunschweig.analysis import run_education_validation as rev

    assignments = pd.DataFrame({
        "location_id": ["abs_1", "abs_1", "uni_local_7"],  # uni id has no facility
    })
    facilities = pd.DataFrame({
        "school_id": ["abs_1"],
        "name": ["School 1"],
        "level": ["primary"],
        "capacity": [100.0],
    })
    with caplog.at_level(logging.INFO):
        out = rev.enrollment_vs_capacity(assignments, facilities, sampling_rate=0.1)
    assert len(out) == 1
    assert any("uni_local_7" in r.message or "1" in r.message
               for r in caplog.records)
