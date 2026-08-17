"""Tests for extract_model_fuel() and extract_segment_model_2026() in extract_kba_fleet.py.

Uses tmp_path fixtures with inline utf-8-sig CSV content — no real server files required.

Fixture design:
- Two Berichtszeitpunkt periods (only "01.01.2026" must be kept).
- Two segments: one mapped by KBA_SEGMENT_MAP ("Minis"), one unmapped ("Unbekannt Segment").
- Two models with known count breakdowns, so all share arithmetic can be verified exactly.
"""
import io
import logging
import textwrap

import pandas as pd
import pytest

import scripts.extract_kba_fleet as ex

# ---------------------------------------------------------------------------
# Minimal Modellreihen CSV fixture (utf-8-sig, semicolon-separated)
# ---------------------------------------------------------------------------
# Columns: Berichtszeitpunkt; Segment; Marke; Modellreihe; Anzahl; Diesel;
#          Hybrid; Hybrid_Plugin; BEV; gewerblich
#
# Two models in the "Minis" segment (maps -> "minis"):
#   ALPHA MINI:  Anzahl=1000, Diesel=100, Hybrid=200, Hybrid_Plugin=80, BEV=50
#     petrol = max(1000 - 100 - 200 - 50, 0) = 650
#     hybrid = max(200 - 80, 0) = 120   (non-plugin)
#     phev   = 80
#   BETA CITY:   Anzahl=500,  Diesel=0,   Hybrid=0,   Hybrid_Plugin=0,  BEV=500
#     petrol = max(500 - 0 - 0 - 500, 0) = 0
#     hybrid = 0, phev = 0
# One model in "Unbekannt Segment" (no KBA_SEGMENT_MAP entry -> skipped):
#   GAMMA X: Anzahl=100, Diesel=10, Hybrid=5, Hybrid_Plugin=2, BEV=1
# Plus one row from an earlier period (01.01.2025) that must be dropped:
#   ALPHA MINI: same brand/model, different counts

MODELLREIHEN_FIXTURE = (
    "﻿"  # utf-8-sig BOM
    "Berichtszeitpunkt;Segment;Marke;Modellreihe;Anzahl;Diesel;Hybrid;Hybrid_Plugin;BEV;gewerblich\n"
    # Old period — must be excluded
    "01.01.2025;Minis;ALPHA;MINI;800;80;150;60;40;100\n"
    # Current period — must be kept
    "01.01.2026;Minis;ALPHA;MINI;1000;100;200;80;50;120\n"
    "01.01.2026;Minis;BETA;CITY;500;0;0;0;500;30\n"
    "01.01.2026;Unbekannt Segment;GAMMA;X;100;10;5;2;1;5\n"
)


def _write_modellreihen(tmp_path) -> str:
    """Write the fixture CSV and return its path string."""
    path = tmp_path / "kba_modellreihen_bestand_2020_2026.csv"
    path.write_text(MODELLREIHEN_FIXTURE, encoding="utf-8-sig")
    return str(path)


# ---------------------------------------------------------------------------
# Tests for extract_model_fuel
# ---------------------------------------------------------------------------

def test_extract_model_fuel_only_2026_rows(tmp_path):
    """Only rows with Berichtszeitpunkt == '01.01.2026' must be kept."""
    csv_path = _write_modellreihen(tmp_path)
    df = ex.extract_model_fuel(csv_path)
    # The 2025 ALPHA MINI row must not appear alongside the 2026 one
    assert df["stichtag"].unique().tolist() == ["2026-01-01"]
    # Three source rows in 2026 but one segment unmapped -> two models expected
    assert len(df) == 2


def test_extract_model_fuel_model_string_convention(tmp_path):
    """model column must be 'MARKE MODELLREIHE' (uppercase, space-joined)."""
    csv_path = _write_modellreihen(tmp_path)
    df = ex.extract_model_fuel(csv_path)
    assert "ALPHA MINI" in df["model"].values
    assert "BETA CITY" in df["model"].values
    # The unmapped segment must NOT appear in the output
    assert "GAMMA X" not in df["model"].values


def test_extract_model_fuel_hybrid_split(tmp_path):
    """hybrid = Hybrid - Hybrid_Plugin (non-plugin); phev = Hybrid_Plugin."""
    csv_path = _write_modellreihen(tmp_path)
    df = ex.extract_model_fuel(csv_path).set_index("model")
    alpha = df.loc["ALPHA MINI"]
    # Hybrid_Plugin = 80 -> phev_share = 80/1000 = 0.08
    assert pytest.approx(alpha["phev_share"], abs=1e-9) == 80 / 1000
    # Hybrid = 200, Hybrid_Plugin = 80 -> non-plugin hybrid = 120 -> hybrid_share = 0.12
    assert pytest.approx(alpha["hybrid_share"], abs=1e-9) == 120 / 1000


def test_extract_model_fuel_petrol_residual(tmp_path):
    """petrol = max(Anzahl - Diesel - Hybrid - BEV, 0) (Hybrid includes plugin)."""
    csv_path = _write_modellreihen(tmp_path)
    df = ex.extract_model_fuel(csv_path).set_index("model")
    alpha = df.loc["ALPHA MINI"]
    # petrol = max(1000 - 100 - 200 - 50, 0) = 650
    assert pytest.approx(alpha["petrol_share"], abs=1e-9) == 650 / 1000


def test_extract_model_fuel_diesel_share(tmp_path):
    """diesel_share = Diesel / Anzahl."""
    csv_path = _write_modellreihen(tmp_path)
    df = ex.extract_model_fuel(csv_path).set_index("model")
    alpha = df.loc["ALPHA MINI"]
    assert pytest.approx(alpha["diesel_share"], abs=1e-9) == 100 / 1000


def test_extract_model_fuel_bev_share(tmp_path):
    """bev_share = BEV / Anzahl."""
    csv_path = _write_modellreihen(tmp_path)
    df = ex.extract_model_fuel(csv_path).set_index("model")
    beta = df.loc["BETA CITY"]
    assert pytest.approx(beta["bev_share"], abs=1e-9) == 500 / 500


def test_extract_model_fuel_shares_sum_at_most_one(tmp_path):
    """All five shares sum to <= 1.0 per row (petrol + diesel + hybrid + phev + bev)."""
    csv_path = _write_modellreihen(tmp_path)
    df = ex.extract_model_fuel(csv_path)
    total_share = (
        df["petrol_share"] + df["diesel_share"] + df["hybrid_share"]
        + df["phev_share"] + df["bev_share"]
    )
    assert (total_share <= 1.0 + 1e-9).all(), f"Row sums exceed 1: {total_share.tolist()}"


def test_extract_model_fuel_segment_mapped(tmp_path):
    """segment column uses the canonical KBA_SEGMENT_MAP value (e.g. 'minis')."""
    csv_path = _write_modellreihen(tmp_path)
    df = ex.extract_model_fuel(csv_path)
    assert set(df["segment"]) == {"minis"}


def test_extract_model_fuel_unmapped_segment_skipped_and_counted(tmp_path, caplog):
    """Rows with unmapped segments are skipped; the skip count is logged."""
    import logging
    csv_path = _write_modellreihen(tmp_path)
    with caplog.at_level(logging.INFO, logger="extract_kba_fleet"):
        ex.extract_model_fuel(csv_path)
    # At least one log message must mention the skipped/unmapped count
    log_text = " ".join(caplog.messages)
    assert "unmapped" in log_text.lower() or "skip" in log_text.lower(), (
        f"No unmapped/skip log found. Log messages: {caplog.messages}"
    )


def test_extract_model_fuel_stichtag_column(tmp_path):
    """stichtag column must be '2026-01-01'."""
    csv_path = _write_modellreihen(tmp_path)
    df = ex.extract_model_fuel(csv_path)
    assert (df["stichtag"] == "2026-01-01").all()


def test_extract_model_fuel_columns(tmp_path):
    """Output must have exactly the required columns."""
    csv_path = _write_modellreihen(tmp_path)
    df = ex.extract_model_fuel(csv_path)
    expected = {"segment", "model", "stichtag", "petrol_share", "diesel_share",
                "hybrid_share", "phev_share", "bev_share"}
    assert expected.issubset(set(df.columns)), (
        f"Missing columns: {expected - set(df.columns)}"
    )


# ---------------------------------------------------------------------------
# Tests for extract_segment_model_2026
# ---------------------------------------------------------------------------

def test_extract_segment_model_2026_schema(tmp_path):
    """Output must have segment, model, count, share, stichtag columns."""
    csv_path = _write_modellreihen(tmp_path)
    df = ex.extract_segment_model_2026(csv_path)
    required = {"segment", "model", "count", "share", "stichtag"}
    assert required.issubset(set(df.columns)), (
        f"Missing columns: {required - set(df.columns)}"
    )


def test_extract_segment_model_2026_model_string_convention(tmp_path):
    """model column must be 'MARKE MODELLREIHE' (matching the FZ 12.1 convention)."""
    csv_path = _write_modellreihen(tmp_path)
    df = ex.extract_segment_model_2026(csv_path)
    assert "ALPHA MINI" in df["model"].values
    assert "BETA CITY" in df["model"].values


def test_extract_segment_model_2026_only_2026(tmp_path):
    """Only 01.01.2026 rows; old-period rows must be absent."""
    csv_path = _write_modellreihen(tmp_path)
    df = ex.extract_segment_model_2026(csv_path)
    # If the 2025 row were included we'd see count=800 for ALPHA MINI
    alpha = df[df["model"] == "ALPHA MINI"]
    assert len(alpha) == 1
    assert alpha.iloc[0]["count"] == 1000  # 2026 count, not 2025 (800)


def test_extract_segment_model_2026_unmapped_segment_excluded(tmp_path):
    """Rows with unmapped segments (e.g. 'Unbekannt Segment') are excluded."""
    csv_path = _write_modellreihen(tmp_path)
    df = ex.extract_segment_model_2026(csv_path)
    assert "GAMMA X" not in df["model"].values


def test_extract_segment_model_2026_share_within_segment(tmp_path):
    """Within-segment shares must sum to 1.0 per segment."""
    csv_path = _write_modellreihen(tmp_path)
    df = ex.extract_segment_model_2026(csv_path)
    for seg, grp in df.groupby("segment"):
        assert pytest.approx(grp["share"].sum(), abs=1e-9) == 1.0, (
            f"Segment '{seg}' shares do not sum to 1: {grp['share'].sum()}"
        )


def test_extract_segment_model_2026_count_is_anzahl(tmp_path):
    """count column equals the raw Anzahl from the CSV."""
    csv_path = _write_modellreihen(tmp_path)
    df = ex.extract_segment_model_2026(csv_path).set_index("model")
    assert df.loc["ALPHA MINI", "count"] == 1000
    assert df.loc["BETA CITY", "count"] == 500


def test_extract_segment_model_2026_stichtag(tmp_path):
    """stichtag column must be '2026-01-01'."""
    csv_path = _write_modellreihen(tmp_path)
    df = ex.extract_segment_model_2026(csv_path)
    assert (df["stichtag"] == "2026-01-01").all()


# ---------------------------------------------------------------------------
# Task B6: Hybrid_ohne_Plugin consistency assertion
# ---------------------------------------------------------------------------
# Two models in the "Minis" segment, both carrying the OPTIONAL direct
# Hybrid_ohne_Plugin column (absent from MODELLREIHEN_FIXTURE above):
#   ALPHA MINI:  Hybrid=200, Hybrid_Plugin=80 -> computed non-plugin = 120;
#                Hybrid_ohne_Plugin=120 -> AGREES.
#   BETA CITY:   Hybrid=50,  Hybrid_Plugin=20 -> computed non-plugin = 30;
#                Hybrid_ohne_Plugin=25 -> DISAGREES by 5 vehicles.
MODELLREIHEN_FIXTURE_WITH_HYBRID_OHNE_PLUGIN = (
    "﻿"  # utf-8-sig BOM
    "Berichtszeitpunkt;Segment;Marke;Modellreihe;Anzahl;Diesel;Hybrid;Hybrid_Plugin;"
    "BEV;gewerblich;Hybrid_ohne_Plugin\n"
    "01.01.2026;Minis;ALPHA;MINI;1000;100;200;80;50;120;120\n"
    "01.01.2026;Minis;BETA;CITY;500;0;50;20;100;30;25\n"
)


def _write_modellreihen_with_hybrid_column(tmp_path) -> str:
    path = tmp_path / "kba_modellreihen_bestand_2020_2026.csv"
    path.write_text(MODELLREIHEN_FIXTURE_WITH_HYBRID_OHNE_PLUGIN, encoding="utf-8-sig")
    return str(path)


def test_hybrid_ohne_plugin_disagreement_logs_warning_with_row_count(tmp_path, caplog):
    """A disagreeing Hybrid_ohne_Plugin row must log a WARNING naming the row count."""
    csv_path = _write_modellreihen_with_hybrid_column(tmp_path)
    with caplog.at_level(logging.WARNING, logger="extract_kba_fleet"):
        ex.extract_model_fuel(csv_path)
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("Hybrid_ohne_Plugin" in msg for msg in warnings), (
        f"Expected a Hybrid_ohne_Plugin consistency warning, got: {warnings}"
    )
    # Exactly one of the two rows disagrees (BETA CITY).
    assert any("1/2" in msg or "1 " in msg for msg in warnings)


def test_hybrid_ohne_plugin_disagreement_keeps_computed_value(tmp_path):
    """The COMPUTED hybrid_share (Hybrid - Hybrid_Plugin) must be kept even when it
    disagrees with the source's own Hybrid_ohne_Plugin column."""
    csv_path = _write_modellreihen_with_hybrid_column(tmp_path)
    df = ex.extract_model_fuel(csv_path).set_index("model")
    beta = df.loc["BETA CITY"]
    # Computed: (50 - 20) / 500 = 0.06, NOT the direct column's 25 / 500 = 0.05.
    assert pytest.approx(beta["hybrid_share"], abs=1e-9) == 30 / 500
    assert beta["hybrid_share"] != pytest.approx(25 / 500, abs=1e-9)


def test_hybrid_ohne_plugin_agreement_row_not_counted_as_mismatch(tmp_path, caplog):
    """A single-row fixture where Hybrid_ohne_Plugin agrees must log no warning."""
    fixture = (
        "﻿"
        "Berichtszeitpunkt;Segment;Marke;Modellreihe;Anzahl;Diesel;Hybrid;Hybrid_Plugin;"
        "BEV;gewerblich;Hybrid_ohne_Plugin\n"
        "01.01.2026;Minis;ALPHA;MINI;1000;100;200;80;50;120;120\n"
    )
    csv_path = tmp_path / "kba_modellreihen_bestand_2020_2026.csv"
    csv_path.write_text(fixture, encoding="utf-8-sig")
    with caplog.at_level(logging.WARNING, logger="extract_kba_fleet"):
        ex.extract_model_fuel(str(csv_path))
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert not any("Hybrid_ohne_Plugin" in msg for msg in warnings)


def test_hybrid_ohne_plugin_absent_column_no_warning(tmp_path, caplog):
    """When the raw CSV has no Hybrid_ohne_Plugin column at all (the case for every
    raw file seen so far), the consistency check must be silently skipped -- no
    warning, no crash, byte-identical to the pre-B6 behaviour."""
    csv_path = _write_modellreihen(tmp_path)  # base fixture, no Hybrid_ohne_Plugin column
    with caplog.at_level(logging.WARNING, logger="extract_kba_fleet"):
        ex.extract_model_fuel(csv_path)
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert not any("Hybrid_ohne_Plugin" in msg for msg in warnings)
