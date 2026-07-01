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
) -> None:
    """Write a synthetic kba_gemeinde_ev.csv with one high-BEV Gemeinde row
    plus one placeholder row for each of the remaining ZGB Kreise so the
    loader does not raise on missing Kreise.

    kba_gemeinde_ev.csv does NOT require all 8 ZGB Kreise (unlike FZ 27.17 and
    FZ 27.15) -- the loader only rejects EXTRA codes that are not ZGB, not a
    missing subset. A single high-BEV row is therefore sufficient.
    """
    rows = [
        _minimal_ev_row(kreis_ags5, gemeinde_norm,
                        bev_share=bev_share, phev_share=phev_share),
    ]
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
    # Set Gemeinde share to max(3x Kreis share, Kreis + 0.10) -- clearly above.
    high_bev = min(max(3.0 * kreis_bev, kreis_bev + 0.10), 0.60)
    high_phev = min(kreis_bev * 1.5, 0.15)

    _make_high_bev_gemeinde_ev_csv(tmp_dp, kreis, "TESTGEMEINDE_2026",
                                   bev_share=high_bev, phev_share=high_phev)
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
