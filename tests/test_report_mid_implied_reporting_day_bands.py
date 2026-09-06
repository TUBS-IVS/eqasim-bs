"""Tests for scripts.report_mid_implied_reporting_day_bands: mapping and renormalisation.

Two things are tested on tiny synthetic frames, never on the real committed data (CLAUDE.md
"Tests" -- deterministic, small, synthetic): (1) the pure ``compute_mid_implied_shares`` formula
and its band -> MiD-class mapping (0_5/5_10 -> lt10, 10_20 -> 10_25, 20_30/30_50 -> 25_50,
50_100 -> 50_100, 100_plus -> 100_200); (2) the full CLI against tiny synthetic CSVs, including
the renormalisation and the 100_plus/50_100 headline values pinned in the maintainer decision of
2026-09-06 (ADR-0104 check 2, issue #244) on the real committed proof artefact.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.report_mid_implied_reporting_day_bands import (
    BAND_ORDER,
    BAND_TO_MID_CLASS,
    OUTPUT_FILE,
    compute_mid_implied_shares,
    main,
)


def test_band_to_mid_class_mapping_is_complete_and_as_specified():
    assert set(BAND_TO_MID_CLASS) == set(BAND_ORDER)
    assert BAND_TO_MID_CLASS["0_5"] == "lt10"
    assert BAND_TO_MID_CLASS["5_10"] == "lt10"
    assert BAND_TO_MID_CLASS["10_20"] == "10_25"
    assert BAND_TO_MID_CLASS["20_30"] == "25_50"
    assert BAND_TO_MID_CLASS["30_50"] == "25_50"
    assert BAND_TO_MID_CLASS["50_100"] == "50_100"
    assert BAND_TO_MID_CLASS["100_plus"] == "100_200"


def test_compute_mid_implied_shares_sums_to_one():
    # A uniform assigned distribution and a uniform MiD at-workplace probability: the implied
    # distribution must come back out uniform too, and sum to 1.
    assigned = {band: 1.0 / len(BAND_ORDER) for band in BAND_ORDER}
    mid_share = {mid_class: 0.5 for mid_class in set(BAND_TO_MID_CLASS.values())}
    implied = compute_mid_implied_shares(assigned, mid_share)
    assert set(implied) == set(BAND_ORDER)
    assert implied["0_5"] == pytest.approx(1.0 / len(BAND_ORDER))
    assert sum(implied.values()) == pytest.approx(1.0)


def test_compute_mid_implied_shares_reweights_by_mid_class_probability():
    # Two bands with equal assigned mass but different MiD at-workplace probability for their
    # class: the one reading the HIGHER probability must come out with the larger implied share,
    # and the renormalisation must make the two (which exhaust the whole mass here) sum to 1.
    assigned = {band: 0.0 for band in BAND_ORDER}
    assigned["0_5"] = 0.5  # -> lt10
    assigned["50_100"] = 0.5  # -> 50_100
    mid_share = {mid_class: 0.1 for mid_class in set(BAND_TO_MID_CLASS.values())}
    mid_share["lt10"] = 0.8
    mid_share["50_100"] = 0.2

    implied = compute_mid_implied_shares(assigned, mid_share)

    assert implied["0_5"] > implied["50_100"]
    assert implied["0_5"] == pytest.approx(0.8 / (0.8 + 0.2))
    assert implied["50_100"] == pytest.approx(0.2 / (0.8 + 0.2))
    assert sum(implied.values()) == pytest.approx(1.0)
    # every band with zero assigned mass must come back out at exactly zero, not NaN
    for band in BAND_ORDER:
        if band not in ("0_5", "50_100"):
            assert implied[band] == 0.0


def test_compute_mid_implied_shares_raises_on_all_zero_input():
    assigned = {band: 0.0 for band in BAND_ORDER}
    mid_share = {mid_class: 0.5 for mid_class in set(BAND_TO_MID_CLASS.values())}
    with pytest.raises(ValueError, match="sum to"):
        compute_mid_implied_shares(assigned, mid_share)


def _write_commute_by_kreis(path: Path, shares: dict[str, float], code: str = "zgb",
                            scope: str = "inter") -> None:
    row = {"code": code, "scope": scope}
    row.update({f"model_share_{band}": shares[band] for band in BAND_ORDER})
    pd.DataFrame([row]).to_csv(path, index=False)


def _write_mid_table(path: Path, share_at_workplace: dict[str, float]) -> None:
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write("# synthetic test fixture, not the real MiD table\n")
        handle.write("distance_class,share_at_workplace\n")
        for mid_class, value in share_at_workplace.items():
            handle.write(f"{mid_class},{value}\n")


def test_cli_writes_expected_columns_and_renormalises(tmp_path):
    # A single band ("0_5") carries all the assigned mass, so after renormalisation the
    # MiD-implied share must collapse onto it entirely (1.0), independent of the MiD
    # share_at_workplace value read for that band's class.
    off_csv = tmp_path / "off_commute_by_kreis.csv"
    on_csv = tmp_path / "on_commute_by_kreis.csv"
    mid_csv = tmp_path / "mid_table.csv"
    out_dir = tmp_path / "out"

    assigned = {band: 0.0 for band in BAND_ORDER}
    assigned["0_5"] = 1.0
    on_shares = {band: 1.0 / len(BAND_ORDER) for band in BAND_ORDER}
    _write_commute_by_kreis(off_csv, assigned)
    _write_commute_by_kreis(on_csv, on_shares)
    _write_mid_table(mid_csv, {"lt10": 0.6, "10_25": 0.5, "25_50": 0.4, "50_100": 0.3, "100_200": 0.2})

    exit_code = main([
        "--off-csv", str(off_csv), "--on-csv", str(on_csv), "--mid-csv", str(mid_csv),
        "--out-dir", str(out_dir), "--source-commit", "deadbeef",
    ])
    assert exit_code == 0

    out_path = out_dir / OUTPUT_FILE
    assert out_path.exists()
    written = pd.read_csv(out_path, comment="#")
    assert list(written.columns) == [
        "band", "assigned_share_off", "mid_share_at_workplace",
        "mid_implied_reporting_day_share", "model_on_reporting_day_share",
    ]
    assert list(written["band"]) == list(BAND_ORDER)
    row_0_5 = written[written["band"] == "0_5"].iloc[0]
    assert row_0_5["mid_implied_reporting_day_share"] == pytest.approx(1.0)
    assert row_0_5["model_on_reporting_day_share"] == pytest.approx(1.0 / len(BAND_ORDER))
    assert written["mid_implied_reporting_day_share"].sum() == pytest.approx(1.0)


def test_cli_raises_on_row_not_summing_to_one(tmp_path):
    off_csv = tmp_path / "off_commute_by_kreis.csv"
    on_csv = tmp_path / "on_commute_by_kreis.csv"
    mid_csv = tmp_path / "mid_table.csv"

    assigned = {band: 0.0 for band in BAND_ORDER}
    assigned["0_5"] = 0.5  # deliberately does not sum to 1
    on_shares = {band: 1.0 / len(BAND_ORDER) for band in BAND_ORDER}
    _write_commute_by_kreis(off_csv, assigned)
    _write_commute_by_kreis(on_csv, on_shares)
    _write_mid_table(mid_csv, {"lt10": 0.6, "10_25": 0.5, "25_50": 0.4, "50_100": 0.3, "100_200": 0.2})

    with pytest.raises(ValueError, match="sum to"):
        main([
            "--off-csv", str(off_csv), "--on-csv", str(on_csv), "--mid-csv", str(mid_csv),
            "--out-dir", str(tmp_path / "out"), "--source-commit", "deadbeef",
        ])


def test_cli_against_the_real_committed_proof_artefact_matches_the_pinned_2026_09_06_decision():
    """End-to-end pin against the committed proof artefact used by the maintainer decision.

    These four values (100_plus and 50_100, assigned/MiD-implied/model-ON) are exactly the ones
    quoted in ADR-0104's "Decision 2026-09-06 on check 2 (maintainer)" subsection and in the
    manifest note it points to; if this test ever fails, that subsection's numbers are stale and
    must be regenerated from a fresh run of this script, not hand-edited.
    """
    artefact_dir = (REPO_ROOT / "eqasim-data" / "data" / "braunschweig" / "calibration" /
                    "commute_day_state_phase_b_proof_100pct_2026-09-06_rerun")
    mid_csv = (REPO_ROOT / "eqasim-data" / "data" / "braunschweig" / "mid" /
              "mid2023_workday_location_by_commute_distance.csv")
    if not artefact_dir.exists() or not mid_csv.exists():
        pytest.skip("committed proof artefact or MiD reference table not present in this checkout")

    off_shares = _read_row(artefact_dir / "off" / "commute_by_kreis.csv")
    on_shares = _read_row(artefact_dir / "on" / "commute_by_kreis.csv")
    mid_share = pd.read_csv(mid_csv, comment="#").set_index("distance_class")["share_at_workplace"]

    from scripts.report_mid_implied_reporting_day_bands import BAND_TO_MID_CLASS as MAPPING
    mid_share_by_class = {mid_class: float(mid_share[mid_class]) for mid_class in set(MAPPING.values())}
    implied = compute_mid_implied_shares(off_shares, mid_share_by_class)

    assert off_shares["100_plus"] == pytest.approx(0.1001, abs=5e-5)
    assert implied["100_plus"] == pytest.approx(0.0602, abs=5e-5)
    assert on_shares["100_plus"] == pytest.approx(0.0737, abs=5e-5)
    assert off_shares["50_100"] == pytest.approx(0.1330, abs=5e-5)
    assert implied["50_100"] == pytest.approx(0.1185, abs=5e-5)
    assert on_shares["50_100"] == pytest.approx(0.1304, abs=5e-5)


def _read_row(path: Path, code: str = "zgb", scope: str = "inter") -> dict[str, float]:
    frame = pd.read_csv(path)
    row = frame[(frame["code"] == code) & (frame["scope"] == scope)].iloc[0]
    return {band: float(row[f"model_share_{band}"]) for band in BAND_ORDER}
