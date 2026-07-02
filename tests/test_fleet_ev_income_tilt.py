"""Tests for the EV-income tilt (Task B2).

``EvIncomeTiltModel`` builds ``f_pt(status) = clip(P(pt|status) / P(pt|all), 0.2,
5.0)`` for ``pt in {bev, phev}`` from
``braunschweig.data.kba.fleet_tables.load_mid_antrieb_by_status``. PASS 1 in
:func:`~braunschweig.synthesis.vehicles.fleet_sampling_de.sample_fleet` applies
this factor multiplicatively to the car's WORKING powertrain pmf, strictly
AFTER the ``unmasked_pmf`` (Task 7 rake-target) snapshot is taken and BEFORE the
model-feasibility mask. Covers:

  (a) factor math: a synthetic table where ``P(bev|very_high) = 2 x P(bev|all)``
      yields a factor of 2.0; a large ratio clips at 5.0; the lower clip is 0.2;
      non-electric powertrains always carry factor 1.0.
  (b) placement: with the tilt active, a very_high-status car draws bev more
      often than a very_low-status car in the SAME Kreis (within-Kreis
      redistribution), while the per-Kreis electric AGGREGATE (the Task 7 rake
      target, built from the untilted ``unmasked_pmf``) stays anchored to the
      no-tilt value -- i.e. the aggregate is preserved.
  (c) absent CSV (the normal state of this local checkout --
      mid2023_antrieb_by_status.csv is server-generated) -> the tilt is
      inactive and ``sample_fleet`` is byte-identical regardless of the
      ``ev_income_tilt`` flag.
  (d) a thin MiD cell (base_weighted < 30) forces factor=1.0 for that cell and
      is counted as a fallback.
  (e) ``ev_income_tilt=False`` is byte-identical to the tilt being fully
      absent, even when the sampler carries an ACTIVE (non-None) tilt model --
      the flag, not just data availability, gates the feature.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pandas.testing as pdt
import pytest

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "eqasim-data" / "data"
DATA_PATH = str(DATA)
sys.path.insert(0, str(REPO))

from braunschweig.data.kba import fleet_tables as ft  # noqa: E402
from braunschweig.synthesis.vehicles import fleet_sampling_de as fs  # noqa: E402

_ANTRIEB_CSV = DATA / "braunschweig" / "kba" / "derived" / "mid2023_antrieb_by_status.csv"


# --------------------------------------------------------------------------- #
# Synthetic mid2023_antrieb_by_status-shaped frames
# --------------------------------------------------------------------------- #
def _antrieb_row(status: str, powertrain: str, share: float, base_weighted: float) -> dict:
    return {"status": status, "powertrain": powertrain, "share": share,
            "base_weighted": base_weighted}


def _synthetic_antrieb_df(bev_very_high: float = 0.30, bev_very_low: float = 0.02,
                          all_bev: float = 0.10, all_phev: float = 0.05,
                          base_weighted: float = 1000.0) -> pd.DataFrame:
    """A well-formed synthetic table with a clear bev income gradient.

    Every (status, powertrain) cell is well above ``EV_INCOME_MIN_CELL_WEIGHT``
    so no thin-cell fallback fires; only ``very_high``/``very_low`` bev shares
    deviate from the pooled ``all`` mix (the other statuses carry the pooled
    share, i.e. a neutral factor of 1.0).
    """
    rows = []
    for status in ft.STATUS_LABELS:
        bev_share = {"very_high": bev_very_high, "very_low": bev_very_low}.get(status, all_bev)
        rows.append(_antrieb_row(status, "bev", bev_share, base_weighted))
        rows.append(_antrieb_row(status, "phev", all_phev, base_weighted))
    rows.append(_antrieb_row("all", "bev", all_bev, base_weighted * len(ft.STATUS_LABELS)))
    rows.append(_antrieb_row("all", "phev", all_phev, base_weighted * len(ft.STATUS_LABELS)))
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture()
def sampler():
    """A real FleetSampler built from the committed local data.

    ``mid2023_antrieb_by_status.csv`` is a server-generated derived CSV that is
    NOT present in this local checkout, so ``sampler.ev_income_tilt`` is always
    ``None`` here -- exactly the environment the absent-CSV fallback (case c)
    must degrade gracefully in.
    """
    return fs.FleetSampler.from_data_path(DATA_PATH)


def _make_status_cars(kreis: str, statuses: list[str], n_per_status: int,
                      raumtyp: int = 72) -> pd.DataFrame:
    rows = []
    for status in statuses:
        for _ in range(n_per_status):
            rows.append({
                "economic_status": status,
                "kreis_ags5": kreis,
                "gemeinde": np.nan,   # no Gemeinde tilt -- isolate the EV-income tilt
                "raumtyp": raumtyp,
            })
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- #
# (a) Factor math
# --------------------------------------------------------------------------- #
def test_factor_is_ratio_of_status_share_to_pooled_share():
    """P(bev|very_high) = 2 x P(bev|all) -> factor 2.0 (no clipping)."""
    df = _synthetic_antrieb_df(bev_very_high=0.20, all_bev=0.10)
    model = fs.EvIncomeTiltModel._from_dataframe(df)
    bev_idx = fs.POWERTRAINS.index("bev")
    factor = model.tilt("very_high")[bev_idx]
    assert factor == pytest.approx(2.0)


def test_factor_clips_at_upper_bound():
    """A ratio far above 5.0 clips to the anti-explosion ceiling."""
    df = _synthetic_antrieb_df(bev_very_high=0.90, all_bev=0.10)  # ratio = 9.0
    model = fs.EvIncomeTiltModel._from_dataframe(df)
    bev_idx = fs.POWERTRAINS.index("bev")
    factor = model.tilt("very_high")[bev_idx]
    assert factor == pytest.approx(5.0)


def test_factor_clips_at_lower_bound():
    """A ratio far below 0.2 clips to the anti-explosion floor."""
    df = _synthetic_antrieb_df(bev_very_low=0.005, all_bev=0.10)  # ratio = 0.05
    model = fs.EvIncomeTiltModel._from_dataframe(df)
    bev_idx = fs.POWERTRAINS.index("bev")
    factor = model.tilt("very_low")[bev_idx]
    assert factor == pytest.approx(0.2)


def test_non_electric_powertrains_always_carry_factor_one():
    """Only bev/phev can deviate from 1.0; every other powertrain stays 1.0."""
    df = _synthetic_antrieb_df(bev_very_high=0.90, bev_very_low=0.005)
    model = fs.EvIncomeTiltModel._from_dataframe(df)
    for status in ft.STATUS_LABELS:
        vec = model.tilt(status)
        assert vec.shape == (len(fs.POWERTRAINS),)
        for pt in fs.POWERTRAINS:
            if pt not in fs.ELECTRIC_POWERTRAINS:
                idx = fs.POWERTRAINS.index(pt)
                assert vec[idx] == pytest.approx(1.0), (
                    f"non-electric powertrain '{pt}' must never be tilted"
                )
        assert np.all(np.isfinite(vec))


# --------------------------------------------------------------------------- #
# (b) Placement: within-Kreis redistribution + aggregate preservation
# --------------------------------------------------------------------------- #
def test_ev_income_tilt_redistributes_within_kreis_and_preserves_aggregate(
    sampler, monkeypatch,
):
    """End-to-end through the real ``sample_fleet`` PASS-1 + Task 7 rake.

    Forces every car onto the SAME segment (monkeypatching
    ``segment_model.segment_probabilities``) so the only remaining source of a
    powertrain-pmf difference between statuses is the EV-income tilt itself
    (removing the pre-existing income->segment->powertrain confound). With the
    tilt injected (very_high bev factor 5.0, very_low bev factor 0.2):

      * the REALISED bev share of the very_high group must be clearly higher
        than that of the very_low group (the within-Kreis redistribution the
        tilt is designed to produce);
      * the POOLED bev share across both groups must match the untilted
        spatial value (``unmasked_pmf[bev]``, i.e. what the Task 7 rake target
        would be without any tilt) -- proving the tilt cannot drift the
        per-Kreis electric AGGREGATE, because it is applied strictly AFTER the
        ``unmasked_pmf`` snapshot the rake targets.
    """
    kreis = ft.ZGB_KREISE_AGS5[0]
    fixed_segment = "kompaktklasse"
    assert fixed_segment in sampler.segment_model.segments

    one_hot = np.zeros(len(sampler.segment_model.segments), dtype=float)
    one_hot[sampler.segment_model.segments.index(fixed_segment)] = 1.0
    monkeypatch.setattr(
        sampler.segment_model, "segment_probabilities",
        lambda economic_status, raumtyp: one_hot.copy(),
    )

    # Strong synthetic tilt: very_high tilts hard toward bev, very_low away.
    antrieb_df = _synthetic_antrieb_df(bev_very_high=0.50, bev_very_low=0.02, all_bev=0.10)
    sampler.ev_income_tilt = fs.EvIncomeTiltModel._from_dataframe(antrieb_df)

    # The untilted spatial pmf for this (segment, kreis, no-gemeinde) cell --
    # this is exactly what PASS-1 captures as ``unmasked_pmf`` for every car in
    # this test (status plays no role in this call), and therefore what the
    # Task 7 rake targets regardless of the tilt.
    unmasked_pmf = sampler.powertrain_model.powertrain_probabilities(
        fixed_segment, kreis, None)
    bev_idx = fs.POWERTRAINS.index("bev")
    target_bev_share = float(unmasked_pmf[bev_idx])
    assert 0.0 < target_bev_share < 1.0, (
        "test needs a non-degenerate bev share to detect both redistribution "
        "and aggregate preservation"
    )

    n_per_status = 6000
    df_cars = _make_status_cars(kreis, ["very_high", "very_low"], n_per_status)
    df_spec, _df_types, _summary = fs.sample_fleet(
        df_cars, DATA_PATH, random_seed=7, sampler=sampler, ev_income_tilt=True,
    )

    bev_high = float((df_spec.loc[df_spec["economic_status"] == "very_high", "powertrain"] == "bev").mean())
    bev_low = float((df_spec.loc[df_spec["economic_status"] == "very_low", "powertrain"] == "bev").mean())
    bev_pooled = float((df_spec["powertrain"] == "bev").mean())

    assert bev_high > bev_low + 0.02, (
        f"very_high bev share ({bev_high:.4f}) should be clearly higher than "
        f"very_low ({bev_low:.4f}) -- the within-Kreis redistribution effect"
    )
    assert bev_pooled == pytest.approx(target_bev_share, abs=0.02), (
        f"pooled bev share ({bev_pooled:.4f}) should stay anchored to the "
        f"untilted spatial target ({target_bev_share:.4f}) -- the per-Kreis "
        f"electric AGGREGATE must be preserved by the Task 7 rake"
    )


# --------------------------------------------------------------------------- #
# (c) Absent CSV -> byte-identical (this checkout's real, unmodified state)
# --------------------------------------------------------------------------- #
def test_local_checkout_has_no_antrieb_csv():
    """Sanity precondition for case (c): this is the actual local state."""
    assert not _ANTRIEB_CSV.exists(), (
        f"expected {_ANTRIEB_CSV} to be absent locally (server-generated); "
        "if this now exists the absent-CSV assumption behind this test no "
        "longer holds and the test should be revisited."
    )


def test_absent_csv_disables_the_tilt(sampler):
    assert sampler.ev_income_tilt is None


def test_absent_csv_sample_fleet_byte_identical_regardless_of_flag(sampler):
    """With the CSV absent, ev_income_tilt=True and =False must be identical."""
    df_cars = _make_status_cars(
        ft.ZGB_KREISE_AGS5[0], list(ft.STATUS_LABELS), n_per_status=50)

    df_spec_on, df_types_on, _ = fs.sample_fleet(
        df_cars, DATA_PATH, random_seed=42, sampler=sampler, ev_income_tilt=True)
    df_spec_off, df_types_off, _ = fs.sample_fleet(
        df_cars, DATA_PATH, random_seed=42, sampler=sampler, ev_income_tilt=False)

    pdt.assert_frame_equal(df_spec_on, df_spec_off)
    pdt.assert_frame_equal(df_types_on, df_types_off)


# --------------------------------------------------------------------------- #
# (d) Thin MiD cell -> factor forced to 1.0 + fallback counted
# --------------------------------------------------------------------------- #
def test_thin_cell_forces_factor_one_and_counts_fallback(caplog):
    """A (status, powertrain) cell below the min weight must not inject noise."""
    df = _synthetic_antrieb_df()
    # Overwrite the "high" status's bev AND phev cells with a thin base_weighted
    # (10 < EV_INCOME_MIN_CELL_WEIGHT=30) so "high" has NO real electric signal
    # at all -- both cell-level forcing (factor=1.0) and the call-level fallback
    # classification (this status is a pure fallback) are exercised together.
    df.loc[(df["status"] == "high") & (df["powertrain"].isin(fs.ELECTRIC_POWERTRAINS)),
           "base_weighted"] = 10.0

    with caplog.at_level(logging.WARNING, logger="braunschweig.synthesis.vehicles.fleet_sampling_de"):
        model = fs.EvIncomeTiltModel._from_dataframe(df)
        vec = model.tilt("high")

    bev_idx = fs.POWERTRAINS.index("bev")
    phev_idx = fs.POWERTRAINS.index("phev")
    assert vec[bev_idx] == pytest.approx(1.0)
    assert vec[phev_idx] == pytest.approx(1.0)

    primary, fallback = model.log_fallback_rate()
    assert fallback >= 1, "the thin-cell status must be counted as a fallback"
    assert any("thin MiD cell" in rec.message for rec in caplog.records), (
        "the thin-cell rule must be logged (no-silent-fallback rule)"
    )

    # A different, non-thin status in the same table must still carry a real
    # (non-1.0) signal -- the thin-cell rule is per-status here, not global.
    other_vec = model.tilt("very_high")
    assert other_vec[bev_idx] != pytest.approx(1.0)


def test_thin_cell_is_per_cell_not_per_status(caplog):
    """A status with ONE thin electric cell still forces just that cell.

    ``very_high`` here has a thin bev cell (forced to 1.0) but a healthy phev
    cell with a genuine (non-1.0) ratio; the call must still be classified as
    a PRIMARY hit (real MiD signal was used for phev), not a fallback -- the
    thin-cell rule operates per (status, powertrain) cell, not per status.
    """
    df = _synthetic_antrieb_df(bev_very_high=0.30, all_bev=0.10,
                               all_phev=0.05)
    # Thin the bev cell only for very_high; give it a distinct, healthy phev
    # share so the phev factor is a genuine (non-1.0) ratio.
    df.loc[(df["status"] == "very_high") & (df["powertrain"] == "bev"),
           "base_weighted"] = 5.0
    df.loc[(df["status"] == "very_high") & (df["powertrain"] == "phev"),
           "share"] = 0.20  # ratio 0.20/0.05 = 4.0, well above 1.0

    with caplog.at_level(logging.WARNING, logger="braunschweig.synthesis.vehicles.fleet_sampling_de"):
        model = fs.EvIncomeTiltModel._from_dataframe(df)
        vec = model.tilt("very_high")

    bev_idx = fs.POWERTRAINS.index("bev")
    phev_idx = fs.POWERTRAINS.index("phev")
    assert vec[bev_idx] == pytest.approx(1.0), "thin bev cell must be forced to 1.0"
    assert vec[phev_idx] == pytest.approx(4.0), "healthy phev cell keeps its real ratio"
    assert np.all(np.isfinite(vec))

    primary, fallback = model.log_fallback_rate()
    assert primary == 1 and fallback == 0, (
        "a status with at least one non-thin electric cell must count as a "
        "PRIMARY hit even though its OTHER electric cell was forced to 1.0"
    )


# --------------------------------------------------------------------------- #
# (e) Flag OFF -> byte-identical, even with an ACTIVE tilt model
# --------------------------------------------------------------------------- #
def test_flag_off_byte_identical_to_no_tilt(sampler):
    """ev_income_tilt=False must equal the tilt being entirely absent."""
    df_cars = _make_status_cars(
        ft.ZGB_KREISE_AGS5[0], list(ft.STATUS_LABELS), n_per_status=50)

    # Inject a strong, clearly-active synthetic tilt onto the sampler.
    antrieb_df = _synthetic_antrieb_df(bev_very_high=0.50, bev_very_low=0.02)
    sampler.ev_income_tilt = fs.EvIncomeTiltModel._from_dataframe(antrieb_df)

    df_spec_flag_off, df_types_flag_off, _ = fs.sample_fleet(
        df_cars, DATA_PATH, random_seed=99, sampler=sampler, ev_income_tilt=False)

    # A second sampler with the tilt entirely absent (as in the real absent-CSV
    # case) must produce the exact same output for the same seed.
    sampler_no_tilt = fs.FleetSampler.from_data_path(DATA_PATH)
    assert sampler_no_tilt.ev_income_tilt is None
    df_spec_no_model, df_types_no_model, _ = fs.sample_fleet(
        df_cars, DATA_PATH, random_seed=99, sampler=sampler_no_tilt, ev_income_tilt=True)

    pdt.assert_frame_equal(df_spec_flag_off, df_spec_no_model)
    pdt.assert_frame_equal(df_types_flag_off, df_types_no_model)


def test_flag_on_with_active_tilt_differs_from_flag_off(sampler):
    """Sanity: the feature is not an accidental no-op when actually active.

    The POOLED bev share is deliberately near-invariant between ON and OFF
    (that is the whole point of aggregate preservation, see the placement
    test above), so this checks the PER-STATUS shares instead -- those are
    exactly what the tilt is supposed to move.
    """
    df_cars = _make_status_cars(
        ft.ZGB_KREISE_AGS5[0], ["very_high", "very_low"], n_per_status=2000)

    antrieb_df = _synthetic_antrieb_df(bev_very_high=0.50, bev_very_low=0.02)
    sampler.ev_income_tilt = fs.EvIncomeTiltModel._from_dataframe(antrieb_df)

    df_spec_on, _, _ = fs.sample_fleet(
        df_cars, DATA_PATH, random_seed=13, sampler=sampler, ev_income_tilt=True)
    df_spec_off, _, _ = fs.sample_fleet(
        df_cars, DATA_PATH, random_seed=13, sampler=sampler, ev_income_tilt=False)

    def _bev_share(df_spec: pd.DataFrame, status: str) -> float:
        sub = df_spec.loc[df_spec["economic_status"] == status, "powertrain"]
        return float((sub == "bev").mean())

    high_on, high_off = _bev_share(df_spec_on, "very_high"), _bev_share(df_spec_off, "very_high")
    low_on, low_off = _bev_share(df_spec_on, "very_low"), _bev_share(df_spec_off, "very_low")

    assert high_on > high_off + 0.02, (
        f"tilt ON should raise the very_high bev share ({high_on:.4f}) above "
        f"the flag-OFF baseline ({high_off:.4f})"
    )
    assert low_on < low_off - 0.02, (
        f"tilt ON should lower the very_low bev share ({low_on:.4f}) below "
        f"the flag-OFF baseline ({low_off:.4f})"
    )
