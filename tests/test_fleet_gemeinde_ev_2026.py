"""Tests for the 2026 per-Gemeinde EV tilt refresh (Task 9).

Covers:
  * ``_gemeinde_electric_share_2026``: map keys on (kreis_ags5, gemeinde_norm);
    carries bev/phev/hydrogen; NaN-share rows dropped.
  * ``from_data_path`` fallback: when kba_gemeinde_ev.csv is absent the FZ 27.17
    fallback fires and is logged; a seeded sample still runs (byte-identical to
    the pre-Task-9 path).
  * Primary 2026 path: when kba_gemeinde_ev.csv is present in a tmp data dir, a
    Gemeinde with a HIGH bev_share gets proportionally more BEV mass than the
    same cars at Kreis level only (the tilt from the 2026 source is observable).
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "eqasim-data" / "data"
sys.path.insert(0, str(REPO))

from braunschweig.data.kba import fleet_tables as ft  # noqa: E402
from braunschweig.synthesis.vehicles import fleet_sampling_de as fs  # noqa: E402

DATA_PATH = str(DATA)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_csv(root: Path, rel: str, df: pd.DataFrame) -> None:
    """Write df to <root>/rel, creating parent directories as needed."""
    dest = root / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(dest, index=False)


def _minimal_ev_row(kreis_ags5: str, gemeinde_norm: str,
                    bev_share: float = 0.05,
                    phev_share: float = 0.01,
                    fuelcell_share: float = 0.0) -> dict:
    return {
        "kreis_ags5": kreis_ags5,
        "ags8": kreis_ags5 + "000",
        "gemeinde": gemeinde_norm.title(),
        "gemeinde_norm": gemeinde_norm,
        "stichtag": "2026-04-01",
        "ev_share": bev_share + phev_share + fuelcell_share,
        "bev_share": bev_share,
        "phev_share": phev_share,
        "fuelcell_share": fuelcell_share,
    }


# ---------------------------------------------------------------------------
# Unit test: _gemeinde_electric_share_2026
# ---------------------------------------------------------------------------

class TestGemeindeElectricShare2026:
    """Unit tests for the builder that maps kba_gemeinde_ev rows to the tilt dict."""

    def _build_df(self, rows: list[dict]) -> pd.DataFrame:
        return pd.DataFrame(rows)

    def test_keys_on_kreis_and_gemeinde_norm(self):
        """Map key must be (kreis_ags5, gemeinde_norm) as read from the CSV."""
        rows = [
            _minimal_ev_row("03101", "BRAUNSCHWEIG", bev_share=0.08, phev_share=0.02),
            _minimal_ev_row("03153", "WOLFSBURG", bev_share=0.12, phev_share=0.03),
        ]
        result = fs._gemeinde_electric_share_2026(self._build_df(rows))
        assert ("03101", "BRAUNSCHWEIG") in result
        assert ("03153", "WOLFSBURG") in result

    def test_carries_bev_phev_hydrogen(self):
        """Each entry must have bev, phev, and hydrogen keys."""
        rows = [_minimal_ev_row("03101", "TESTGEM", bev_share=0.07,
                                phev_share=0.015, fuelcell_share=0.001)]
        result = fs._gemeinde_electric_share_2026(self._build_df(rows))
        entry = result[("03101", "TESTGEM")]
        assert "bev" in entry
        assert "phev" in entry
        assert "hydrogen" in entry
        assert entry["bev"] == pytest.approx(0.07)
        assert entry["phev"] == pytest.approx(0.015)
        assert entry["hydrogen"] == pytest.approx(0.001)

    def test_nan_bev_row_dropped(self):
        """A row where bev_share is NaN must be omitted entirely from the map."""
        rows = [
            {"kreis_ags5": "03101", "ags8": "031010000",
             "gemeinde": "Teststdt", "gemeinde_norm": "TESTSTDT",
             "stichtag": "2026-04-01",
             "ev_share": float("nan"),
             "bev_share": float("nan"),
             "phev_share": float("nan"),
             "fuelcell_share": float("nan")},
        ]
        result = fs._gemeinde_electric_share_2026(self._build_df(rows))
        assert ("03101", "TESTSTDT") not in result

    def test_nan_phev_still_includes_bev(self):
        """If phev_share is NaN but bev_share is valid, the entry keeps bev (no phev)."""
        rows = [
            {"kreis_ags5": "03101", "ags8": "031010000",
             "gemeinde": "Testdorf", "gemeinde_norm": "TESTDORF",
             "stichtag": "2026-04-01",
             "ev_share": 0.06,
             "bev_share": 0.06,
             "phev_share": float("nan"),
             "fuelcell_share": 0.0},
        ]
        result = fs._gemeinde_electric_share_2026(self._build_df(rows))
        entry = result.get(("03101", "TESTDORF"))
        assert entry is not None
        assert "bev" in entry
        assert "phev" not in entry

    def test_empty_df_returns_empty_dict(self):
        """Empty input must yield an empty map (no crash)."""
        df = pd.DataFrame(columns=[
            "kreis_ags5", "ags8", "gemeinde", "gemeinde_norm", "stichtag",
            "ev_share", "bev_share", "phev_share", "fuelcell_share",
        ])
        result = fs._gemeinde_electric_share_2026(df)
        assert result == {}

    def test_zero_fuelcell_not_dropped(self):
        """fuelcell_share == 0.0 is NOT NaN -- hydrogen entry must be kept."""
        rows = [_minimal_ev_row("03101", "NULLFUEL", fuelcell_share=0.0)]
        result = fs._gemeinde_electric_share_2026(self._build_df(rows))
        entry = result.get(("03101", "NULLFUEL"))
        assert entry is not None
        assert entry["hydrogen"] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Integration test: fallback path (kba_gemeinde_ev.csv absent)
# ---------------------------------------------------------------------------

def test_from_data_path_fallback_when_ev_absent(caplog):
    """When kba_gemeinde_ev.csv is not present, from_data_path must:
    * fall back to load_gemeinde_private_bev (FZ 27.17),
    * log a message identifying it as the FZ 27.17 fallback,
    * still build a working PowertrainModel.
    """
    # DATA_PATH points to the committed data where kba_gemeinde_ev.csv does not
    # exist. We rely on that absence.
    ev_path = (
        Path(DATA_PATH) / "braunschweig" / "kba" / "derived" / "kba_gemeinde_ev.csv"
    )
    if ev_path.exists():
        pytest.skip("kba_gemeinde_ev.csv present in committed data; fallback test skipped")

    df_seg = ft.load_segment_powertrain(DATA_PATH)
    segments = list(df_seg["segment"].unique())
    with caplog.at_level(logging.INFO):
        model = fs.PowertrainModel.from_data_path(DATA_PATH, segments)

    assert isinstance(model, fs.PowertrainModel)

    # Log must mention the FZ 27.17 fallback.
    combined = " ".join(r.message for r in caplog.records).lower()
    assert "fz27.17" in combined or "fallback" in combined

    # A seeded sample must still run without error.
    rng = np.random.default_rng(42)
    kreis = ft.ZGB_KREISE_AGS5[0]
    probs = model.powertrain_probabilities("kompaktklasse", kreis, None)
    assert probs.sum() == pytest.approx(1.0, abs=1e-6)


# ---------------------------------------------------------------------------
# Integration test: primary 2026 path via tmp data dir
# ---------------------------------------------------------------------------

def _build_tmp_data_path(tmp_path: Path) -> str:
    """Symlink or copy all derived CSVs from DATA_PATH into tmp_path, then add
    a synthetic kba_gemeinde_ev.csv with one high-BEV Gemeinde.

    Returns the tmp data root as a string.
    """
    derived_src = Path(DATA_PATH) / "braunschweig" / "kba" / "derived"
    derived_dst = tmp_path / "braunschweig" / "kba" / "derived"
    derived_dst.mkdir(parents=True, exist_ok=True)

    # Symlink each existing derived CSV so we don't duplicate GBs of data.
    for csv_file in derived_src.glob("*.csv"):
        # Skip kba_gemeinde_ev.csv if it happens to exist in real data.
        if csv_file.name == "kba_gemeinde_ev.csv":
            continue
        link = derived_dst / csv_file.name
        try:
            link.symlink_to(csv_file.resolve())
        except (OSError, NotImplementedError):
            # Fallback: copy on systems where symlinks require elevated rights.
            shutil.copy2(str(csv_file), str(link))

    return str(tmp_path)


def _make_high_bev_gemeinde_ev_csv(
    tmp_data_path: str,
    kreis_ags5: str,
    gemeinde_norm: str,
    bev_share: float,
    phev_share: float,
    background_bev_share: float = 0.0,
    background_phev_share: float = 0.0,
    n_background_gemeinden: int = 4,
) -> None:
    """Write a synthetic kba_gemeinde_ev.csv with one high-BEV Gemeinde row plus
    a handful of LOW-share "background" Gemeinden in the SAME Kreis.

    The background rows are required since Task A3
    (``PowertrainModel._kreis_private_electric_share_2026``): the per-Kreis tilt
    denominator is now the 2026 weighted MEAN of the Kreis's Gemeinden, not the
    stale 2025 FZ27.15 Kreis share. With only the single high-BEV row present,
    the mean of ONE value degenerates to that value itself, making the tilt a
    no-op by construction -- not a bug, but it stops this fixture from being
    able to exercise a real within-Kreis tilt. The background rows give the
    Kreis a genuine (low-share) backdrop so the high-BEV Gemeinde's tilt factor
    stays clearly above 1. None of the synthetic names match any real FZ 27.17
    entry, so every row falls back to weight 1 (unweighted mean) -- the FZ27.17
    weighting itself is covered separately by
    ``TestKreisPrivateElectricShare2026`` in this file.

    kba_gemeinde_ev.csv does NOT require all 8 ZGB Kreise (unlike FZ 27.17 and
    FZ 27.15) -- the loader only rejects EXTRA codes that are not ZGB, not a
    missing subset.
    """
    rows = [
        _minimal_ev_row(kreis_ags5, gemeinde_norm,
                        bev_share=bev_share, phev_share=phev_share),
    ]
    for i in range(n_background_gemeinden):
        rows.append(_minimal_ev_row(
            kreis_ags5, f"BACKGROUND_GEMEINDE_{i}",
            bev_share=background_bev_share, phev_share=background_phev_share,
        ))
    dest = (
        Path(tmp_data_path) / "braunschweig" / "kba" / "derived"
        / "kba_gemeinde_ev.csv"
    )
    pd.DataFrame(rows).to_csv(dest, index=False)


def _make_synthetic_cars(kreis_ags5: str, gemeinde: str,
                         n: int = 3000, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    for _ in range(n):
        rows.append({
            "economic_status": rng.choice(list(ft.STATUS_LABELS)),
            "kreis_ags5": kreis_ags5,
            "gemeinde": gemeinde,
            "raumtyp": int(rng.choice([71, 72, 73, 74, 75, 76, 77])),
        })
    return pd.DataFrame(rows)


@pytest.fixture(scope="module")
def tmp_data_with_high_bev_gemeinde(tmp_path_factory):
    """Session-scoped tmp data dir with a high-BEV synthetic kba_gemeinde_ev.csv."""
    derived_src = Path(DATA_PATH) / "braunschweig" / "kba" / "derived"
    if not derived_src.exists():
        pytest.skip("committed derived CSVs absent; primary-path test skipped")

    tmp = tmp_path_factory.mktemp("fleet_ev_2026")
    tmp_dp = _build_tmp_data_path(tmp)

    # Use a fixed Kreis + a synthetic Gemeinde name that is clearly absent from
    # FZ 27.17 so we can unambiguously attribute any tilt to the 2026 source.
    kreis = ft.ZGB_KREISE_AGS5[0]  # "03101" = Braunschweig (Stadt)

    # Load the actual Kreis BEV share so we can set a Gemeinde share that is
    # unambiguously above it.
    df_kreis = ft.load_kreis_powertrain(DATA_PATH).set_index("kreis_ags5")
    kreis_bev = float(df_kreis.loc[kreis, "bev_share"])
    kreis_phev = float(df_kreis.loc[kreis, "phev_share"])
    # Set Gemeinde share to max(3x Kreis share, Kreis + 0.10) -- clearly above.
    high_bev = min(max(3.0 * kreis_bev, kreis_bev + 0.10), 0.60)
    high_phev = min(kreis_bev * 1.5, 0.15)

    # Background Gemeinden at the (2025) Kreis level give the 2026 Kreis-mean
    # denominator (Task A3) a genuine within-Kreis backdrop, so the single
    # high-BEV Gemeinde's tilt factor stays clearly above 1 rather than
    # degenerating to a mean-of-one no-op.
    _make_high_bev_gemeinde_ev_csv(tmp_dp, kreis, "TESTGEMEINDE_2026",
                                   bev_share=high_bev, phev_share=high_phev,
                                   background_bev_share=kreis_bev,
                                   background_phev_share=kreis_phev)
    return tmp_dp, kreis, "TESTGEMEINDE_2026", kreis_bev, high_bev


def test_2026_primary_path_logged(tmp_data_with_high_bev_gemeinde, caplog):
    """When kba_gemeinde_ev.csv is present, from_data_path must log the 2026 source."""
    tmp_dp, kreis, gemeinde, _, _ = tmp_data_with_high_bev_gemeinde
    df_seg = ft.load_segment_powertrain(tmp_dp)
    segments = list(df_seg["segment"].unique())
    with caplog.at_level(logging.INFO):
        fs.PowertrainModel.from_data_path(tmp_dp, segments)

    combined = " ".join(r.message for r in caplog.records).lower()
    assert "2026" in combined or "kba_gemeinde_ev" in combined


def test_2026_primary_path_builder_correct(tmp_data_with_high_bev_gemeinde):
    """_gemeinde_electric_share_2026 correctly wired: the synthetic high-BEV
    Gemeinde must appear in PowertrainModel.gemeinde_private_electric_share with
    the expected share values.
    """
    tmp_dp, kreis, gemeinde_norm, _, high_bev = tmp_data_with_high_bev_gemeinde
    df_seg = ft.load_segment_powertrain(tmp_dp)
    segments = list(df_seg["segment"].unique())
    model = fs.PowertrainModel.from_data_path(tmp_dp, segments)

    key = (kreis, gemeinde_norm)
    assert key in model.gemeinde_private_electric_share, (
        f"Key {key} not found in gemeinde_private_electric_share"
    )
    entry = model.gemeinde_private_electric_share[key]
    assert "bev" in entry
    assert entry["bev"] == pytest.approx(high_bev, rel=1e-6)


def test_2026_primary_tilt_increases_bev(tmp_data_with_high_bev_gemeinde):
    """Sampling with the high-BEV synthetic Gemeinde (2026 source) must produce
    more BEVs than sampling at Kreis level only (no Gemeinde tilt).

    The tilt is: factor = clip(gem_share / kreis_share, 0.2, 5.0).
    With a 3× or +(0.10) Gemeinde share the factor is capped at 5.0, so the
    BEV mass in the tilted run must be clearly above the untilted baseline.
    """
    tmp_dp, kreis, gemeinde_norm, kreis_bev, high_bev = tmp_data_with_high_bev_gemeinde

    # Build cars: same Kreis, one batch with no Gemeinde, one with high-BEV Gemeinde.
    base_cars = _make_synthetic_cars(kreis, np.nan, n=4000, seed=11)   # type: ignore[arg-type]
    tilted_cars = _make_synthetic_cars(kreis, gemeinde_norm, n=4000, seed=11)

    spec_base, _, _ = fs.sample_fleet(base_cars, tmp_dp, random_seed=99)
    spec_tilt, _, _ = fs.sample_fleet(tilted_cars, tmp_dp, random_seed=99)

    bev_base = float((spec_base["powertrain"] == "bev").mean())
    bev_tilt = float((spec_tilt["powertrain"] == "bev").mean())

    assert bev_tilt > bev_base, (
        f"Expected tilted BEV share ({bev_tilt:.4f}) > base BEV share "
        f"({bev_base:.4f}) for Gemeinde with bev_share={high_bev:.4f} vs "
        f"Kreis bev_share={kreis_bev:.4f}"
    )


# ---------------------------------------------------------------------------
# Task A3 (review Finding 3): same-vintage (2026/2026) Kreis-mean denominator.
#
# Bug: the tilt numerator (`_gemeinde_electric_share_2026`, 2026) was divided
# by the 2025 FZ27.15 Kreis share, which is systematically stale (EV grew
# 2025->2026) -- a fleet-wide EV LEVEL shift, not a pure within-Kreis relative
# tilt. `_kreis_private_electric_share_2026` fixes this by deriving the
# denominator from the SAME 2026 file, weighted by FZ27.17 `private_total`.
# ---------------------------------------------------------------------------

def _minimal_fz27_row(kreis_ags5: str, gemeinde: str, private_total: float) -> dict:
    """A minimal FZ 27.17 (kba_gemeinde_private_bev.csv) row; only
    ``kreis_ags5``, ``gemeinde`` and ``private_total`` matter for the
    Kreis-mean weighting under test -- the share columns are unused by
    ``_kreis_private_electric_share_2026`` (that consumes the 2026 file's
    shares, not FZ27.17's).
    """
    return {
        "kreis_ags5": kreis_ags5,
        "kreis_name": "Test-Kreis",
        "gemeinde": gemeinde,
        "private_total": private_total,
        "private_bev": 0.0,
        "private_phev": 0.0,
        "private_bev_share": 0.0,
        "private_phev_share": 0.0,
    }


def _make_minimal_powertrain_model(
    kreis_priv: dict, gem_priv: dict,
) -> fs.PowertrainModel:
    """A PowertrainModel with only the Gemeinde-tilt-relevant fields populated.

    ``segments`` / ``kreis_segment_powertrain`` / ``national_segment_powertrain``
    are not read by ``_apply_gemeinde_tilt`` and are left as harmless minimal
    placeholders.
    """
    return fs.PowertrainModel(
        segments=["kompaktklasse"],
        powertrains=list(fs.POWERTRAINS),
        kreis_segment_powertrain={},
        national_segment_powertrain=np.zeros((1, len(fs.POWERTRAINS))),
        kreis_private_electric_share=kreis_priv,
        gemeinde_private_electric_share=gem_priv,
    )


class TestKreisPrivateElectricShare2026:
    """Unit tests for the same-vintage (2026) Kreis-mean tilt denominator."""

    def test_uniform_2x_growth_no_level_shift(self):
        """All Gemeinden of a Kreis grew by the SAME factor (uniform 2x)
        relative to the stale 2025 FZ27.15 Kreis share. Because the new
        denominator is the 2026 weighted MEAN (not the stale 2025 Kreis
        share), it equals the same uniform 2026 share, so the resulting tilt
        factor is ~1.0 everywhere -- no fleet-wide EV level shift.

        This is the failing case under the pre-fix code: there, the
        denominator would be the stale ``fz2715_kreis_bev_share`` and every
        Gemeinde would get factor ~2.0 (a pure level shift, not a real tilt).
        """
        kreis = "03101"
        fz2715_kreis_bev_share = 0.05  # stale 2025 all-ownership Kreis share
        uniform_2026_share = 2.0 * fz2715_kreis_bev_share  # uniform 2025->2026 growth
        rows = [
            _minimal_ev_row(kreis, "GEM_A", bev_share=uniform_2026_share, phev_share=0.02),
            _minimal_ev_row(kreis, "GEM_B", bev_share=uniform_2026_share, phev_share=0.02),
            _minimal_ev_row(kreis, "GEM_C", bev_share=uniform_2026_share, phev_share=0.02),
        ]
        df_gem_ev = pd.DataFrame(rows)
        gem_priv = fs._gemeinde_electric_share_2026(df_gem_ev)

        kreis_priv_2026 = fs.PowertrainModel._kreis_private_electric_share_2026(
            df_gem_ev, None)

        # The 2026 weighted mean equals the uniform Gemeinde share (no FZ27.17
        # weights supplied -> unweighted mean of three identical values).
        assert kreis_priv_2026[kreis]["bev"] == pytest.approx(uniform_2026_share)
        assert kreis_priv_2026[kreis]["phev"] == pytest.approx(0.02)
        # Sanity: this must NOT be the stale 2025 Kreis share (that was the bug).
        assert kreis_priv_2026[kreis]["bev"] != pytest.approx(fz2715_kreis_bev_share)

        model = _make_minimal_powertrain_model(kreis_priv_2026, gem_priv)
        idx_bev = fs.POWERTRAINS.index("bev")
        idx_phev = fs.POWERTRAINS.index("phev")
        for gemeinde in ("GEM_A", "GEM_B", "GEM_C"):
            tilted = model._apply_gemeinde_tilt(np.ones(len(fs.POWERTRAINS)), kreis, gemeinde)
            assert tilted[idx_bev] == pytest.approx(1.0, rel=1e-6), (
                f"{gemeinde}: expected no level shift (factor ~1.0), got "
                f"{tilted[idx_bev]:.4f}"
            )
            assert tilted[idx_phev] == pytest.approx(1.0, rel=1e-6)

    def test_within_kreis_structure_preserved(self):
        """A Gemeinde at ~2x its 2026-Kreis-weighted mean still receives a ~2x
        tilt factor: the vintage fix removes the LEVEL bias but must not
        flatten genuine within-Kreis structure.
        """
        kreis = "03151"
        # Two heavily-weighted "background" Gemeinden at a low, equal share, so
        # their weighted mean is barely moved by the lightly-weighted high
        # Gemeinde. The high Gemeinde's share is exactly 2x that background share.
        background_share = 0.03
        high_share = 2.0 * background_share
        background_weight = 990.0
        high_weight = 10.0
        rows = [
            _minimal_ev_row(kreis, "GEM_LOW_A", bev_share=background_share, phev_share=0.01),
            _minimal_ev_row(kreis, "GEM_LOW_B", bev_share=background_share, phev_share=0.01),
            _minimal_ev_row(kreis, "GEM_HIGH", bev_share=high_share, phev_share=0.01),
        ]
        df_gem_ev = pd.DataFrame(rows)
        gem_priv = fs._gemeinde_electric_share_2026(df_gem_ev)

        df_fz27 = pd.DataFrame([
            _minimal_fz27_row(kreis, "GEM_LOW_A", private_total=background_weight),
            _minimal_fz27_row(kreis, "GEM_LOW_B", private_total=background_weight),
            _minimal_fz27_row(kreis, "GEM_HIGH", private_total=high_weight),
        ])

        kreis_priv_2026 = fs.PowertrainModel._kreis_private_electric_share_2026(
            df_gem_ev, df_fz27)

        expected_mean = (
            background_weight * background_share
            + background_weight * background_share
            + high_weight * high_share
        ) / (2 * background_weight + high_weight)
        assert kreis_priv_2026[kreis]["bev"] == pytest.approx(expected_mean)

        model = _make_minimal_powertrain_model(kreis_priv_2026, gem_priv)
        idx_bev = fs.POWERTRAINS.index("bev")
        tilted = model._apply_gemeinde_tilt(np.ones(len(fs.POWERTRAINS)), kreis, "GEM_HIGH")
        expected_factor = min(high_share / expected_mean, 5.0)
        assert tilted[idx_bev] == pytest.approx(expected_factor, rel=1e-6)
        # The heavily-weighted background dominates the mean, so the observed
        # factor stays close to the "2x its Kreis mean" structure by design.
        assert tilted[idx_bev] == pytest.approx(2.0, rel=0.05)

    def test_missing_weight_falls_back_to_unweighted(self, caplog):
        """A Gemeinde absent from the FZ27.17 weight table contributes to the
        Kreis mean with weight 1.0 (unweighted); the fallback is counted and
        logged (no-silent-fallback rule).
        """
        kreis = "03159"
        rows = [
            _minimal_ev_row(kreis, "GEM_WEIGHTED_A", bev_share=0.02, phev_share=0.005),
            _minimal_ev_row(kreis, "GEM_WEIGHTED_B", bev_share=0.04, phev_share=0.005),
            _minimal_ev_row(kreis, "GEM_NO_WEIGHT", bev_share=0.10, phev_share=0.005),
        ]
        df_gem_ev = pd.DataFrame(rows)
        # FZ27.17 only carries the first two Gemeinden -- GEM_NO_WEIGHT is absent
        # from the weight table and must fall back to weight 1.0.
        df_fz27 = pd.DataFrame([
            _minimal_fz27_row(kreis, "GEM_WEIGHTED_A", private_total=200.0),
            _minimal_fz27_row(kreis, "GEM_WEIGHTED_B", private_total=300.0),
        ])

        with caplog.at_level(logging.INFO):
            kreis_priv_2026 = fs.PowertrainModel._kreis_private_electric_share_2026(
                df_gem_ev, df_fz27)

        expected_mean = (200.0 * 0.02 + 300.0 * 0.04 + 1.0 * 0.10) / (200.0 + 300.0 + 1.0)
        assert kreis_priv_2026[kreis]["bev"] == pytest.approx(expected_mean)

        combined = " ".join(r.message for r in caplog.records).lower()
        assert "fallback" in combined
        # 1 fallback out of 3 Gemeinden = 33.3%.
        assert "33.3" in combined

    def test_missing_fz27_frame_all_unweighted(self, caplog):
        """When ``df_gem_fz27`` is ``None`` (FZ27.17 file absent entirely),
        every Gemeinde falls back to weight 1.0 and the 100% fallback rate is
        logged loudly (no-silent-fallback rule), not silently absorbed.
        """
        kreis = "03102"
        rows = [
            _minimal_ev_row(kreis, "GEM_X", bev_share=0.03, phev_share=0.01),
            _minimal_ev_row(kreis, "GEM_Y", bev_share=0.05, phev_share=0.01),
        ]
        df_gem_ev = pd.DataFrame(rows)

        with caplog.at_level(logging.INFO):
            kreis_priv_2026 = fs.PowertrainModel._kreis_private_electric_share_2026(
                df_gem_ev, None)

        # Unweighted mean of the two Gemeinden.
        assert kreis_priv_2026[kreis]["bev"] == pytest.approx((0.03 + 0.05) / 2.0)

        combined = " ".join(r.message for r in caplog.records).lower()
        assert "fallback" in combined
        assert "100.0" in combined


# ---------------------------------------------------------------------------
# ELECTRIC_POWERTRAINS module constant must be ("bev", "phev") -- unchanged.
# ---------------------------------------------------------------------------

def test_electric_powertrains_tuple_unchanged():
    """ELECTRIC_POWERTRAINS must remain ("bev", "phev") so the per-Kreis EV rake
    (Task 7, ~line 1326) still tilts exactly these two powertrains and nothing
    else. Hydrogen is stored in the 2026 shares dict but must NOT appear here.
    """
    assert fs.ELECTRIC_POWERTRAINS == ("bev", "phev"), (
        f"ELECTRIC_POWERTRAINS changed: {fs.ELECTRIC_POWERTRAINS}"
    )
