"""Tests for the blended employment_status per-Kreis control target (feature #172,
Task 3): combining the MiD P9 side
(``braunschweig.popsim.mid_p9.mid_p9_employment_status_by_kreis``, Task 2) with the
SrV V_ERW side (``eqasim-data/data/braunschweig/srv/srv2023_employment_status_by_kreis.csv``,
Task 1) via the shared ``braunschweig.popsim.blended_targets.blend_kreis_target``
rules, exactly like the existing ``target2026_*`` tables built by
``scripts/build_blended_kreis_targets.py``.

Both prior tasks share the ``code`` column name for the Kreis identifier, but
``blend_kreis_target`` indexes the MiD frame on ``ars5`` (it was designed for the
existing MiD H4/H7/H12.3 tables, which already use ``ars5``) and the SrV frame on
``code``. The build step must therefore rename the MiD-P9 frame's ``code`` column
to ``ars5`` before calling it -- this file pins that exact wiring on synthetic data,
plus a structural check on the committed output CSV.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from braunschweig.popsim.attributes import EMPLOYMENT_STATUS_BY_P_BKAT
from braunschweig.popsim.blended_targets import blend_kreis_target
from braunschweig.popsim.kreis_attribute_control import KreisAttributeControl, load_kreis_target

# Class taxonomy imported from the source of truth (feature #172 requirement),
# never re-listed literally: vollzeit, teilzeit, geringfuegig, sonstiges,
# erwerbstaetig_unspec, in_ausbildung, nicht_erwerbstaetig (P_BKAT code order).
CLASSES = list(EMPLOYMENT_STATUS_BY_P_BKAT.values())

REPO = Path(__file__).resolve().parents[1]
DATA_PATH = REPO / "eqasim-data" / "data"
TARGET_RELPATH = "braunschweig/targets/target2026_employment_status_by_kreis.csv"
ALL_KREISE = {"03101", "03102", "03103", "03151", "03153", "03154", "03157", "03158"}


def _shares(vollzeit, teilzeit, geringfuegig, sonstiges, erwerbstaetig_unspec,
            in_ausbildung, nicht_erwerbstaetig) -> dict:
    values = [vollzeit, teilzeit, geringfuegig, sonstiges, erwerbstaetig_unspec,
              in_ausbildung, nicht_erwerbstaetig]
    assert sum(values) == pytest.approx(1.0)
    return dict(zip(CLASSES, values))


def mid_p9_shaped_frame() -> pd.DataFrame:
    """Synthetic frame in the exact shape ``mid_p9_employment_status_by_kreis``
    returns: columns ``code`` (NOT ``ars5``), the 7 class shares, ``n_unweighted``."""
    rows = [
        {"code": "03101", "n_unweighted": 1000,
         **_shares(0.40, 0.12, 0.02, 0.01, 0.0, 0.02, 0.43)},
        {"code": "03103", "n_unweighted": 500,
         **_shares(0.35, 0.10, 0.03, 0.00, 0.0, 0.01, 0.51)},
        {"code": "Gesamt", "n_unweighted": 3000,
         **_shares(0.38, 0.11, 0.02, 0.01, 0.0, 0.02, 0.46)},
    ]
    return pd.DataFrame(rows)[["code", *CLASSES, "n_unweighted"]]


def srv_shaped_frame() -> pd.DataFrame:
    """Synthetic SrV-shaped frame: only covers 03101 (03103 is Wolfsburg-like,
    absent from SrV coverage), values agreeing with MiD within tolerance."""
    rows = [
        {"code": "03101", "n_unweighted": 1000,
         **_shares(0.41, 0.115, 0.018, 0.012, 0.0, 0.018, 0.427)},
    ]
    return pd.DataFrame(rows)[["code", *CLASSES, "n_unweighted"]]


def test_blend_wiring_renames_mid_code_to_ars5():
    mid = mid_p9_shaped_frame().rename(columns={"code": "ars5"})
    srv = srv_shaped_frame()
    out = blend_kreis_target(mid, srv, categories=CLASSES)

    assert list(out.columns) == ["ars5", "source", "n_effective", *CLASSES]
    assert set(out["ars5"]) == {"03101", "03103", "Gesamt"}

    row_sums = out[CLASSES].sum(axis=1)
    assert (abs(row_sums - 1.0) < 1e-9).all()


def test_kreis_covered_by_both_surveys_is_blended():
    mid = mid_p9_shaped_frame().rename(columns={"code": "ars5"})
    srv = srv_shaped_frame()
    out = blend_kreis_target(mid, srv, categories=CLASSES).set_index("ars5")

    assert out.loc["03101", "source"] == "blend"
    assert out.loc["03101", "n_effective"] == 2000


def test_kreis_absent_from_srv_falls_back_to_mid():
    mid = mid_p9_shaped_frame().rename(columns={"code": "ars5"})
    srv = srv_shaped_frame()
    out = blend_kreis_target(mid, srv, categories=CLASSES).set_index("ars5")

    # 03103 is present in the MiD-P9 frame but not covered by the (synthetic)
    # SrV frame -- per blend_kreis_target's no-arbiter fallback rule this must
    # take the "mid" branch unchanged (fallback transparency: no silent blend).
    assert out.loc["03103", "source"] == "mid"
    assert out.loc["03103", CLASSES].sum() == pytest.approx(1.0)


def test_gesamt_row_is_always_mid():
    mid = mid_p9_shaped_frame().rename(columns={"code": "ars5"})
    srv = srv_shaped_frame()
    out = blend_kreis_target(mid, srv, categories=CLASSES).set_index("ars5")
    assert out.loc["Gesamt", "source"] == "mid"


def test_committed_target_csv_loads_via_kreis_attribute_control():
    """The build step's committed output must satisfy load_kreis_target's
    contract: an ars5 column, the 7 class-share columns (named exactly as the
    EMPLOYMENT_STATUS_BY_P_BKAT class labels), a region-aggregate row, every
    ZGB Kreis present, and each row's shares summing to 1."""
    ctl = KreisAttributeControl(
        name="employment_status",
        seed_column="employment_status",
        level="person",
        categories=tuple((label, f"== '{label}'") for label in CLASSES),
        target_csv_relpath=TARGET_RELPATH,
        target_columns=tuple(CLASSES),
        tier="soft",
    )
    # Shares in the committed CSV are rounded to 4 decimals (see write_target in
    # scripts/build_blended_kreis_targets.py), so a row summing to 1.0 in float
    # can land at 0.9999/1.0001; use the same share_tolerance=1e-3 the real
    # kreis_attribute_control stage wiring uses for these rounded tables
    # (braunschweig/popsim/stage.py), not load_kreis_target's tighter default.
    out = load_kreis_target(DATA_PATH, ctl, expected_ars5=sorted(ALL_KREISE),
                            share_tolerance=1e-3)

    assert set(out.columns) == {"ars5", *CLASSES}
    assert ALL_KREISE.issubset(set(out["ars5"]))
    assert (out["ars5"].isin(["Gesamt", "03ZGB"])).any()
