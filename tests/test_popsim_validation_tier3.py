"""Tests for the Tier-3 employment + education KREIS controls added to
braunschweig.analysis.popsim_validation.controls.build_registry.

Mirrors the patterns in test_popsim_validation_controls.py. The realized
extractors key on the synthetic persons' own RegionalSchlussel_ARS (the cell ARS
the KREIS control was applied at); the target loaders read the merged kreis
control table (the SAME GENESIS marginals PopulationSim was fitted to)."""
from __future__ import annotations

import pandas as pd
import geopandas as gpd
from shapely.geometry import Point

from braunschweig.analysis.popsim_validation import controls as C
from braunschweig.analysis.population_validation import control_validation as CV
from braunschweig.analysis.population_validation import quality_assessment as QA
from braunschweig.analysis.population_validation.population_source import PopulationFrames


def _tier3_frames(p_taet, bildung1, bildung2, ars="031010000000", ages=None):
    """Minimal PopulationFrames carrying the tier3 seed columns + a 12-digit ARS."""
    n = len(p_taet)
    hh = [f"h{i}" for i in range(n)]
    ages = ages if ages is not None else [40] * n
    persons = pd.DataFrame({
        "person_id": [f"{h}_1" for h in hh],
        "household_id": hh,
        "P_TAET": p_taet, "bildung1": bildung1, "bildung2": bildung2,
        "RegionalSchlussel_ARS": [ars] * n, "age": ages,
    })
    households = pd.DataFrame({"household_id": hh})
    homes = gpd.GeoDataFrame({"household_id": hh},
                             geometry=[Point(605000, 5790000)] * n, crs="EPSG:25832")
    return PopulationFrames(persons, households, homes, None, "run_output", "/tmp", "test_")


def _geo(hh, ars5="03101"):
    return pd.DataFrame({"household_id": hh, "ars5": [ars5] * len(hh),
                         "commune_id": ["03101000"] * len(hh)})


# Real 03101 (Braunschweig) census marginals (verified from the kreis_controls parquets).
_FAKE_KT = pd.DataFrame({
    "ARS_kreis": ["03101"],
    "ERWERBSTAT_KURZ_STP": [247130.0],          # total universe (= __11 + __12 + __2)
    "ERWERBSTAT_KURZ_STP__11": [128080.0],       # Erwerbstaetige (employed)
    "ERWERBSTAT_KURZ_STP__12": [7770.0],
    "ERWERBSTAT_KURZ_STP__2": [111280.0],
    "SCHULABS_STP__21": [42780.0], "SCHULABS_STP__22": [3140.0],   # low
    "SCHULABS_STP__23": [47760.0],                                 # mid
    "SCHULABS_STP__24": [100540.0],                                # high
    "BERUFABS_AUSF_STP__2": [60330.0],                             # none
    "BERUFABS_AUSF_STP__11": [71490.0], "BERUFABS_AUSF_STP__12": [21240.0],
    "BERUFABS_AUSF_STP__13": [1310.0],                             # vocational
    "BERUFABS_AUSF_STP__14": [12790.0], "BERUFABS_AUSF_STP__15": [11190.0],
    "BERUFABS_AUSF_STP__16": [31420.0], "BERUFABS_AUSF_STP__17": [6740.0],  # tertiary
})


def test_registry_has_tier3_controls():
    names = {c.name for c in C.build_registry("/fake")}
    assert {"employed", "schulabschluss", "beruflabschluss"} <= names


def test_realized_employed_partition():
    # P_TAET 1,6 -> employed; 7 EXCLUDED (-> not_employed); 9,11 -> not_employed.
    frames = _tier3_frames([1, 6, 7, 9, 11], [2] * 5, [1] * 5)
    ctrl = next(c for c in C.build_registry("/fake") if c.name == "employed")
    out = ctrl.realized(frames, _geo([f"h{i}" for i in range(5)])).set_index("category")["synthetic_count"]
    assert out.get("employed", 0) == 2
    assert out.get("not_employed", 0) == 3


def test_realized_schulabschluss_partition():
    # bildung1 2,3,4 -> low,mid,high; 1,5 excluded (NaN).
    frames = _tier3_frames([1] * 5, [2, 3, 4, 1, 5], [1] * 5)
    ctrl = next(c for c in C.build_registry("/fake") if c.name == "schulabschluss")
    out = ctrl.realized(frames, _geo([f"h{i}" for i in range(5)])).set_index("category")["synthetic_count"]
    assert out.get("low", 0) == 1 and out.get("mid", 0) == 1 and out.get("high", 0) == 1
    assert set(out.index) <= {"low", "mid", "high"}


def test_realized_beruflabschluss_partition():
    # bildung2 1->vocational, 2,3->tertiary, 5->none; 4,9 excluded.
    frames = _tier3_frames([1] * 6, [2] * 6, [1, 2, 3, 5, 4, 9])
    ctrl = next(c for c in C.build_registry("/fake") if c.name == "beruflabschluss")
    out = ctrl.realized(frames, _geo([f"h{i}" for i in range(6)])).set_index("category")["synthetic_count"]
    assert out.get("vocational", 0) == 1 and out.get("tertiary", 0) == 2 and out.get("none", 0) == 1
    assert set(out.index) <= {"none", "vocational", "tertiary"}


def test_tier3_target_shares_sum_to_one(monkeypatch):
    monkeypatch.setattr(C, "_load_kreis_control_table", lambda dp: _FAKE_KT)
    for fn in (C.employed_target, C.schulabschluss_target, C.beruflabschluss_target):
        out = fn("/fake")
        assert set(out.columns) == {"geo_id", "category", "target_share"}
        for geo_id, g in out.groupby("geo_id"):
            assert abs(g["target_share"].sum() - 1.0) < 1e-6
    emp = C.employed_target("/fake").set_index("category")["target_share"]
    assert abs(emp["employed"] - 128080.0 / 247130.0) < 1e-6
    sch = C.schulabschluss_target("/fake").set_index("category")["target_share"]
    assert abs(sch["high"] - 100540.0 / 194220.0) < 1e-6   # 194220 = 42780+3140+47760+100540
    ber = C.beruflabschluss_target("/fake").set_index("category")["target_share"]
    assert abs(ber["none"] - 60330.0 / 216510.0) < 1e-6     # 216510 = 60330+94040+62140


def test_tier3_end_to_end(monkeypatch):
    monkeypatch.setattr(C, "_load_kreis_control_table", lambda dp: _FAKE_KT)
    frames = _tier3_frames([1, 1, 7, 9, 9], [2, 3, 4, 2, 1], [1, 2, 5, 1, 4])
    reg = [c for c in C.build_registry("/fake")
           if c.name in ("employed", "schulabschluss", "beruflabschluss")]
    long = CV.evaluate_all(reg, frames, _geo([f"h{i}" for i in range(5)]), "/fake")
    assert not long.empty
    assert set(long["control"]) == {"employed", "schulabschluss", "beruflabschluss"}
    assert "delta_pp" in long.columns
    q = QA.assess(long)
    assert set(q["control"]) == {"employed", "schulabschluss", "beruflabschluss"}


def test_employed_25_64_band_rate_is_reported():
    import pandas as pd
    from braunschweig.analysis.popsim_validation import controls as vc
    persons = pd.DataFrame({
        "RegionalSchlussel_ARS": ["03102000000"] * 4,
        "HP_ALTER": [30, 40, 50, 70],
        "P_TAET": [1, 11, 1, 1],   # ages 30,50 employed (in band); 40 not; 70 employed but out of band
    })
    rate = vc.employed_25_64_rate(persons)
    # band 25-64 = ages 30,40,50 -> 2 of 3 employed
    assert round(rate["03102"], 3) == round(2 / 3, 3)
