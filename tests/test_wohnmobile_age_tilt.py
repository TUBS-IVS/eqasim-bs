"""Tests for the wohnmobile holder-age tilt (issue #315, ADR-0093).

Covers: (a) the schema-validated loader; (b) the Bayes ratio r(a) and the
E_pop[r] = 1 identity; (c) the covariance case -- plain Bayes drifts the
wohnmobile mass, the calibration scalar restores it exactly; (d) composition
invariance to c; (e) pmf integrity + fallback counting; (f) sampler wiring
(flag OFF never consults the tilt; absent CSV with flag ON raises; missing
owner_age column is a loud 100% fallback); (g) the acceptance bands on the
committed tables.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "eqasim-data" / "data"
sys.path.insert(0, str(REPO))

from braunschweig.data.kba import fleet_tables as ft  # noqa: E402

DATA_PATH = str(DATA)


def _reference_df(vehicles=None) -> pd.DataFrame:
    """Synthetic reference table with the canonical 8 + residual rows."""
    base = {
        "up_to_20": 700, "21_29": 16000, "30_39": 79000, "40_49": 132000,
        "50_59": 265000, "60_69": 318000, "70_79": 124000, "80_plus": 27000,
    }
    if vehicles:
        base.update(vehicles)
    rows = []
    for label, count in base.items():
        rows.append({"age_class": label, "vehicles": count})
    rows.append({"age_class": ft.WOHNMOBILE_AGE_NOT_ATTRIBUTED, "vehicles": 39000})
    df = pd.DataFrame(rows)
    bounds = {"up_to_20": (np.nan, 20), "21_29": (21, 29), "30_39": (30, 39),
              "40_49": (40, 49), "50_59": (50, 59), "60_69": (60, 69),
              "70_79": (70, 79), "80_plus": (80, np.nan),
              ft.WOHNMOBILE_AGE_NOT_ATTRIBUTED: (np.nan, np.nan)}
    df["age_min_years"] = [bounds[a][0] for a in df["age_class"]]
    df["age_max_years"] = [bounds[a][1] for a in df["age_class"]]
    df["published_share_pct"] = np.nan
    att = df["age_class"] != ft.WOHNMOBILE_AGE_NOT_ATTRIBUTED
    att_total = df.loc[att, "vehicles"].sum()
    df["share_of_attributed"] = np.where(att, df["vehicles"] / att_total, np.nan)
    df["total_stock"] = int(df["vehicles"].sum())
    df["stichtag"] = "2025-04-01"
    return df


def _write_reference_csv(tmp_path: Path, df: pd.DataFrame) -> str:
    derived = tmp_path / "braunschweig" / "kba" / "derived"
    derived.mkdir(parents=True)
    (derived / "kba_wohnmobile_holder_age.csv").write_text(
        df.to_csv(index=False), encoding="utf-8")
    return str(tmp_path)


def test_loader_accepts_valid_table(tmp_path):
    data_path = _write_reference_csv(tmp_path, _reference_df())
    df = ft.load_wohnmobile_holder_age(data_path)
    att = df[df["age_class"] != ft.WOHNMOBILE_AGE_NOT_ATTRIBUTED]
    assert set(att["age_class"]) == set(ft.WOHNMOBILE_AGE_CLASS_LABELS)
    assert float(att["share_of_attributed"].sum()) == pytest.approx(1.0)


def test_loader_rejects_missing_age_class(tmp_path):
    df = _reference_df()
    df = df[df["age_class"] != "60_69"]
    data_path = _write_reference_csv(tmp_path, df)
    with pytest.raises(RuntimeError, match="60_69"):
        ft.load_wohnmobile_holder_age(data_path)


def test_loader_rejects_share_drift(tmp_path):
    df = _reference_df()
    df.loc[df["age_class"] == "60_69", "share_of_attributed"] += 0.05
    data_path = _write_reference_csv(tmp_path, df)
    with pytest.raises(RuntimeError, match="share_of_attributed"):
        ft.load_wohnmobile_holder_age(data_path)


def test_loader_rejects_count_total_mismatch(tmp_path):
    df = _reference_df()
    df.loc[df["age_class"] == "50_59", "vehicles"] += 1  # breaks the sum check
    data_path = _write_reference_csv(tmp_path, df)
    with pytest.raises(RuntimeError, match="total_stock"):
        ft.load_wohnmobile_holder_age(data_path)


def test_committed_reference_loads_and_matches_transcription():
    """Loads the COMMITTED derived table; pins one count to the cited KBA pages.

    The pinned 318,589 (class 60_69) is traceable to the committed raw CSV and
    the two KBA source URLs in its header -- a transcription guard, not an
    invented reference.
    """
    df = ft.load_wohnmobile_holder_age(DATA_PATH)
    att = df[df["age_class"] != ft.WOHNMOBILE_AGE_NOT_ATTRIBUTED].set_index("age_class")
    assert len(att) == 8
    assert int(att.loc["60_69", "vehicles"]) == 318589
    assert float(att["share_of_attributed"].sum()) == pytest.approx(1.0)
    # Renormalisation is over the attributed classes, not the published total.
    assert float(att.loc["60_69", "share_of_attributed"]) == pytest.approx(
        318589 / float(att["vehicles"].sum()))
    assert set(df["stichtag"]) == {"2025-04-01"}


from braunschweig.synthesis.vehicles import wohnmobile_age as wa  # noqa: E402


class _StubSegmentModel:
    """Two-segment stub: P(wohnmobile | status) is set per status."""

    def __init__(self, wm_by_status):
        self.segments = ["wohnmobile", "rest"]
        self._wm = wm_by_status

    def segment_probabilities(self, status, raumtyp):
        p = self._wm[status]
        return np.array([p, 1.0 - p], dtype=float)


def _fitted_model(df_cars, wm_by_status, vehicles=None):
    model = wa.WohnmobileHolderAgeTilt._from_dataframe(_reference_df(vehicles))
    model.fit_population(df_cars, _StubSegmentModel(wm_by_status))
    return model


def _cars(ages, statuses):
    return pd.DataFrame({
        "owner_age": ages,
        "economic_status": statuses,
        "raumtyp": [None] * len(ages),
    })


def test_ratio_is_ref_over_population():
    # Population: 50% in 30_39, 25% in 50_59, 25% in 60_69.
    df_cars = _cars([35, 35, 55, 65], ["medium"] * 4)
    model = _fitted_model(df_cars, {"medium": 0.02})
    ref = model.ref_share
    assert model._ratio["30_39"] == pytest.approx(ref["30_39"] / 0.5)
    assert model._ratio["50_59"] == pytest.approx(ref["50_59"] / 0.25)
    assert model._ratio["60_69"] == pytest.approx(ref["60_69"] / 0.25)


def test_expected_ratio_is_one_under_full_coverage():
    # One car per reference class -> P_pop uniform over all 8 classes.
    ages = [19, 25, 35, 45, 55, 65, 75, 85]
    df_cars = _cars(ages, ["medium"] * 8)
    model = _fitted_model(df_cars, {"medium": 0.02})
    p_pop = model._p_pop
    total = sum(p_pop[a] * model._ratio[a] for a in p_pop)
    assert total == pytest.approx(1.0)


def test_age_independent_base_needs_no_calibration():
    # base_wm constant across cars -> covariance zero -> c == 1 exactly.
    # Must cover all 8 age classes so E[r] = 1.
    ages = [19, 25, 35, 45, 55, 65, 75, 85]
    df_cars = _cars(ages, ["medium"] * 8)
    model = _fitted_model(df_cars, {"medium": 0.02})
    assert model.calibration == pytest.approx(1.0)


def test_covariance_case_calibration_restores_the_marginal():
    """THE CENTRAL TEST (spec 3.2 / 6.3a): correlated age x status drifts plain
    Bayes; the fitted c restores E[wohnmobile mass] exactly."""
    # Young owners high status (low wm base), old owners low status (high wm base).
    ages = [30] * 50 + [65] * 50
    statuses = ["very_high"] * 50 + ["low"] * 50
    wm_by_status = {"very_high": 0.005, "low": 0.03}
    df_cars = _cars(ages, statuses)
    model = _fitted_model(df_cars, wm_by_status)
    stub = _StubSegmentModel(wm_by_status)

    base = np.array([stub.segment_probabilities(s, None)[0] for s in statuses])
    r = np.array([model._ratio[model.age_class_for(a)] for a in ages])
    untilted = base.mean()
    plain_bayes = (base * r).mean()
    # The fixture must actually exercise the covariance, else the test is void.
    assert abs(plain_bayes - untilted) > 1e-4
    calibrated = (base * model.calibration * r).mean()
    assert calibrated == pytest.approx(untilted, abs=1e-12)
    assert model.expected_wm_share == pytest.approx(untilted, abs=1e-12)


def test_calibration_leaves_composition_invariant():
    """Spec 6.3b: the renormalised P(a | wohnmobile) is identical for the fitted
    c and for c forced to 1 -- the scalar spends no age signal."""
    ages = [30] * 50 + [65] * 50
    statuses = ["very_high"] * 50 + ["low"] * 50
    wm_by_status = {"very_high": 0.005, "low": 0.03}
    model = _fitted_model(_cars(ages, statuses), wm_by_status)
    stub = _StubSegmentModel(wm_by_status)

    def composition(c):
        mass = {}
        for a, s in zip(ages, statuses):
            label = model.age_class_for(a)
            base = stub.segment_probabilities(s, None)[0]
            mass[label] = mass.get(label, 0.0) + base * c * model._ratio[label]
        total = sum(mass.values())
        return {k: v / total for k, v in mass.items()}

    with_c = composition(model.calibration)
    without_c = composition(1.0)
    for label in with_c:
        assert with_c[label] == pytest.approx(without_c[label], abs=1e-12)


def test_tilt_pmf_integrity_and_relative_proportions():
    df_cars = _cars([65, 35], ["medium", "medium"])
    model = _fitted_model(df_cars, {"medium": 0.02})
    seg_pmf = np.array([0.02, 0.5, 0.3, 0.18])  # wohnmobile at index 0
    out = model.tilt(seg_pmf, 65, wm_index=0)
    assert out.sum() == pytest.approx(1.0)
    assert out[0] != pytest.approx(seg_pmf[0])  # tilt moved the wm mass
    # Non-wohnmobile segments keep their relative proportions.
    rest_before = seg_pmf[1:] / seg_pmf[1:].sum()
    rest_after = out[1:] / out[1:].sum()
    np.testing.assert_allclose(rest_before, rest_after, atol=1e-12)


def test_invalid_owner_age_falls_back_and_counts():
    df_cars = _cars([65, 35], ["medium", "medium"])
    model = _fitted_model(df_cars, {"medium": 0.02})
    seg_pmf = np.array([0.02, 0.98])
    for bad in [np.nan, None, 12, -1.0]:
        out = model.tilt(seg_pmf, bad, wm_index=0)
        np.testing.assert_array_equal(out, seg_pmf)
    primary, fallback = model.log_fallback_rate()
    assert primary == 0 and fallback == 4


def test_tilt_before_fit_raises():
    model = wa.WohnmobileHolderAgeTilt._from_dataframe(_reference_df())
    with pytest.raises(RuntimeError, match="fit_population"):
        model.tilt(np.array([0.02, 0.98]), 65, wm_index=0)


from braunschweig.synthesis.vehicles import fleet_sampling_de as fleet  # noqa: E402


def _synthetic_frame(n=400, seed=7, with_owner_age=True):
    rng = np.random.default_rng(seed)
    statuses = rng.choice(list(ft.STATUS_LABELS), size=n)
    df = pd.DataFrame({
        "economic_status": statuses,
        "kreis_ags5": "03101",
        "gemeinde": np.nan,
        "raumtyp": np.nan,
    })
    if with_owner_age:
        # Ages correlated with status so the covariance path is exercised.
        base = rng.integers(20, 85, size=n).astype(float)
        df["owner_age"] = np.where(statuses == "very_high", base - 10, base)
        df["owner_age"] = df["owner_age"].clip(18, 95)
    return df


@pytest.fixture(scope="module")
def sampler():
    return fleet.FleetSampler.from_data_path(DATA_PATH)


def test_sampler_builds_the_tilt_from_committed_data(sampler):
    assert sampler.wohnmobile_age_tilt is not None
    assert set(sampler.wohnmobile_age_tilt.ref_share) == set(
        ft.WOHNMOBILE_AGE_CLASS_LABELS)


def test_flag_off_never_consults_tilt(sampler, monkeypatch):
    df = _synthetic_frame()
    monkeypatch.setattr(
        sampler.wohnmobile_age_tilt, "fit_population",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("consulted")))
    monkeypatch.setattr(
        sampler.wohnmobile_age_tilt, "tilt",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("consulted")))
    df_spec, _, _ = fleet.sample_fleet(
        df, DATA_PATH, random_seed=1, sampler=sampler,
        wohnmobile_age_tilt=False)
    assert len(df_spec) == len(df)


def test_flag_on_with_absent_reference_raises(sampler):
    stripped = fleet.FleetSampler(**{**sampler.__dict__, "wohnmobile_age_tilt": None})
    with pytest.raises(RuntimeError, match="COMMITTED"):
        fleet.sample_fleet(_synthetic_frame(), DATA_PATH, random_seed=1,
                           sampler=stripped, wohnmobile_age_tilt=True)


def test_missing_owner_age_column_is_loud_untilted_fallback(sampler, caplog):
    df = _synthetic_frame(with_owner_age=False)
    with caplog.at_level(logging.WARNING):
        df_a, _, _ = fleet.sample_fleet(df, DATA_PATH, random_seed=11,
                                        sampler=sampler, wohnmobile_age_tilt=True)
    assert any("owner_age" in rec.message and "100%" in rec.message
               for rec in caplog.records)
    # Untilted fallback must be byte-identical to the flag-OFF draw (same seed).
    df_b, _, _ = fleet.sample_fleet(df, DATA_PATH, random_seed=11,
                                    sampler=sampler, wohnmobile_age_tilt=False)
    pd.testing.assert_frame_equal(
        df_a.drop(columns=["owner_age"], errors="ignore"),
        df_b.drop(columns=["owner_age"], errors="ignore"))


def test_tilt_changes_motorhome_owner_ages(sampler):
    """With the tilt, drawn motorhomes concentrate on older owners.

    NOTE (plan-defect fix, verified 2026-08-20): the brief's draft used
    ``model_brands=False`` here, but that flag forces ``consistency_v2=False``
    (a pre-existing, 2026-06-18 gate in sample_fleet unrelated to issue #315:
    "consistency_v2 relies on drawing brand/model; disable it when
    model_brands=False"). The wohnmobile tilt only fires on the consistency_v2
    path, so with that flag the test degenerates to the legacy path -- confirmed
    empirically (``ValueError: not enough values to unpack (expected 3, got
    2)``, i.e. the legacy 2-tuple return). Dropping ``model_brands=False``
    (default ``True``) restores the v2 path this test is meant to exercise;
    the test does not care about the drawn brand/model, only about segment and
    owner_age, so this preserves the test's intent without touching production
    code or the shared consistency_v2/model_brands gate (out of scope for #315).
    """
    df = _synthetic_frame(n=20000, seed=3)
    on, _, _ = fleet.sample_fleet(df, DATA_PATH, random_seed=5, sampler=sampler,
                                  wohnmobile_age_tilt=True)
    off, _, _ = fleet.sample_fleet(df, DATA_PATH, random_seed=5, sampler=sampler,
                                   wohnmobile_age_tilt=False)
    mean_on = on.loc[on["segment"] == "wohnmobile", "owner_age"].mean()
    mean_off = off.loc[off["segment"] == "wohnmobile", "owner_age"].mean()
    assert mean_on > mean_off + 2.0  # reference is strongly old-skewed


def test_incommuter_fleet_passes_owner_age(monkeypatch):
    from braunschweig.synthesis import incommuters

    captured = {}

    def _fake_sample_fleet(df_cars, data_path, random_seed, population_label=""):
        captured["df_cars"] = df_cars.copy()
        n = len(df_cars)
        df_spec = df_cars.copy()
        for col, val in [("type_id", "t"), ("powertrain", "petrol"),
                         ("age", 5.0), ("euro_class", "euro6"),
                         ("segment", "kompaktklasse"), ("brand", ""), ("model", "")]:
            df_spec[col] = val
        types = pd.DataFrame([{"type_id": "t", "length": 4.0, "width": 1.8,
                               "mode": "car", "hbefa_cat": "c", "hbefa_tech": "t",
                               "hbefa_size": "s", "hbefa_emission": "e"}])
        return df_spec, types, {}

    monkeypatch.setattr(
        "braunschweig.synthesis.vehicles.fleet_sampling_de.sample_fleet",
        _fake_sample_fleet)
    person_ids = np.array([1, 2, 3])
    modes = np.array(["car", "pt", "car"])
    orig_ars = np.array(["12345", "12345", "54321"])
    income = np.array([3000.0, 3000.0, 3000.0])
    ages = np.array([62.0, 30.0, 41.0])
    rng = np.random.default_rng(0)
    _, vehicles = incommuters.build_incommuter_fleet(
        person_ids, modes, orig_ars, income, ages, DATA_PATH, rng)
    assert "owner_age" in captured["df_cars"].columns
    # Only the car-mode agents (indices 0 and 2), in order.
    np.testing.assert_array_equal(
        captured["df_cars"]["owner_age"].to_numpy(), np.array([62.0, 41.0]))
    assert len(vehicles) == 2


def test_acceptance_composition_and_preserved_aggregate(sampler):
    """Issue #315 acceptance: realised P(a | wohnmobile) within the +/-2pp band
    (max'd with the MC band); the aggregate wohnmobile share within 4 sigma of
    the EFFECTIVE (redistribution-inclusive) expectation actually fed into the
    draw -- review Finding I2, the unbiased implementation check; the UNTILTED
    expectation is reported alongside as ``expected_untilted``/
    ``dev_untilted_pp`` (tilt-neutrality evidence, never flagged, carries the
    sonstige-redraw leak per ADR-0093); fallback rate ~0 on a frame with full
    owner ages."""
    df = _synthetic_frame(n=60000, seed=13)
    df_spec, _, summary = fleet.sample_fleet(
        df, DATA_PATH, random_seed=99, sampler=sampler,
        wohnmobile_age_tilt=True)
    wm_summary = summary["wohnmobile_holder_age"]
    assert wm_summary["skipped_reason"] is None
    assert wm_summary["aggregate"]["flagged"] is False
    assert wm_summary["flagged"] is False
    assert wm_summary["aggregate"]["expected_untilted"] is not None
    # PRIMARY-path coverage (no-silent-fallback rule): everything tilted.
    tilt = sampler.wohnmobile_age_tilt
    assert tilt._fallback == 0 and tilt._guard == 0
    # review Finding M7: the same counts are surfaced in the summary itself.
    tilt_counters = wm_summary["tilt_counters"]
    assert tilt_counters["fallback"] == 0 and tilt_counters["guard"] == 0
