"""Tests for the ``--escort-passive-education`` CLI declaration on the
Braunschweig 10 % validation report (issue #256, backlog [0.6]).

A population built with ``escort_purpose`` ON exists in two modes --
``escort_passive_education`` ON (escort = active side only; the W1
comparator must be the active-adjusted baseline) or OFF (both sides; raw
Begleitung baseline). The validation report is post-hoc: it only reads
output CSV/XML files and cannot distinguish the two modes by inspection --
that difference IS what is being validated. So the mode must be DECLARED
via ``--escort-passive-education`` instead of guessed, and the declaration
must be visible in both the JSON payload and the HTML report.
"""
from __future__ import annotations

import logging

import pandas as pd
import pytest

from scripts.validate_bs_10pct import __main__ as cli
from scripts.validate_bs_10pct import report


# ---------------------------------------------------------------------------
# argparse
# ---------------------------------------------------------------------------
def test_parser_escort_passive_education_flag_defaults_off():
    off = cli._parse_args(["--out", "some/dir"])
    assert off.escort_passive_education is False

    on = cli._parse_args(["--out", "some/dir", "--escort-passive-education"])
    assert on.escort_passive_education is True


def test_parser_out_flag_still_works_alongside_new_flag():
    """The pre-existing --out flag must remain unaffected by the new flag."""
    from pathlib import Path

    ns = cli._parse_args(["--out", "some/dir", "--escort-passive-education"])
    assert ns.out == Path("some/dir")


# ---------------------------------------------------------------------------
# _build_json_payload -- declaration threading
# ---------------------------------------------------------------------------
def _stub_purpose_frame(purposes):
    n = len(purposes)
    return pd.DataFrame({
        "purpose": list(purposes),
        "synth_share": [1.0 / n] * n,
        "mid_share": [1.0 / n] * n,
        "deviation_pp": [0.0] * n,
    })


def _patch_common_metrics(monkeypatch, purposes_no_home, purposes_no_home_active=None):
    """Monkeypatch every metrics/diagnostics call inside
    ``report._build_json_payload`` with small deterministic stand-ins, so the
    payload can be built without any real 10 % output data (mirrors the
    monkeypatch idiom in test_purpose_mix_no_home_variants_exclude... and the
    e2e smoke test in test_run_population_validation.py)."""
    if purposes_no_home_active is None:
        purposes_no_home_active = purposes_no_home

    monkeypatch.setattr(report.metrics, "trip_summary", lambda: {
        "n_persons": 1, "n_trips": 1, "trips_per_person": 1.0,
        "mean_distance_km": 1.0, "median_distance_km": 1.0,
        "mean_duration_min": 1.0, "daily_distance_km": 1.0,
        "mid_baseline": {},
    })
    monkeypatch.setattr(report.metrics, "population_per_kreis", lambda: pd.DataFrame({
        "ars5": ["TOTAL"], "kreis_name": ["ZGB-8"], "zensus_2022": [1],
        "synth_sample": [1], "synth_expanded": [1], "deviation_pct": [0.0],
    }))
    monkeypatch.setattr(report.metrics, "mode_share_overall", lambda: pd.DataFrame({
        "mode": ["miv"], "synth_share": [1.0], "mid_share": [1.0], "deviation_pp": [0.0],
    }))
    monkeypatch.setattr(report.metrics, "purpose_mix_raw", lambda: _stub_purpose_frame(["work"]))
    monkeypatch.setattr(report.metrics, "purpose_mix", lambda: _stub_purpose_frame(["work"]))
    monkeypatch.setattr(report.diagnostics, "purpose_mix_remapped", lambda: _stub_purpose_frame(["work"]))
    monkeypatch.setattr(report.metrics, "purpose_mix_no_home",
                        lambda: _stub_purpose_frame(purposes_no_home))
    monkeypatch.setattr(report.metrics, "purpose_mix_no_home_active",
                        lambda: _stub_purpose_frame(purposes_no_home_active))
    monkeypatch.setattr(report.metrics, "mobility_quote", lambda: {
        "synth_total": 0.8, "mid_total": 0.8, "deviation_pp": 0.0, "per_kreis": [],
    })


_OD_STATS = {"n_pairs": 0, "r2": float("nan"), "rmse": 0.0, "mape_pct": 0.0,
             "bias_pct": 0.0, "ba_total": 0.0, "synth_total": 0.0}
_HH_SUMMARY = pd.DataFrame(columns=["ars5", "kreis_name", "n_synth_hh", "chi2", "dof", "tvd_pp"])


def test_payload_default_scores_raw_baseline_and_keeps_both_tables(monkeypatch):
    _patch_common_metrics(monkeypatch, purposes_no_home=["work", "escort", "leisure"])

    payload = report._build_json_payload(_OD_STATS, _HH_SUMMARY)

    assert payload["escort_passive_education"] is False
    assert payload["purpose_mix_scored_baseline"] == "raw_w1"
    # Both tables are always present, never removed by the declaration.
    assert "purpose_mix_no_home" in payload
    assert "purpose_mix_no_home_active" in payload
    assert len(payload["purpose_mix_no_home"]) == 3


def test_payload_flag_true_flips_scored_baseline_pointer(monkeypatch):
    _patch_common_metrics(monkeypatch, purposes_no_home=["work", "escort", "leisure"])

    payload = report._build_json_payload(_OD_STATS, _HH_SUMMARY, escort_passive_education=True)

    assert payload["escort_passive_education"] is True
    assert payload["purpose_mix_scored_baseline"] == "active_adjusted"
    # Both tables still present -- only the pointer changed.
    assert "purpose_mix_no_home" in payload
    assert "purpose_mix_no_home_active" in payload


def test_payload_unaffected_fields_unchanged_by_declaration(monkeypatch):
    """Everything except the two new declaration keys must be identical
    whether the flag is declared or not (existing outputs unchanged)."""
    _patch_common_metrics(monkeypatch, purposes_no_home=["work", "escort", "leisure"])
    payload_off = report._build_json_payload(_OD_STATS, _HH_SUMMARY, escort_passive_education=False)

    _patch_common_metrics(monkeypatch, purposes_no_home=["work", "escort", "leisure"])
    payload_on = report._build_json_payload(_OD_STATS, _HH_SUMMARY, escort_passive_education=True)

    new_keys = {"escort_passive_education", "purpose_mix_scored_baseline", "generated_at"}
    for key in set(payload_off) - new_keys:
        assert payload_off[key] == payload_on[key], f"payload[{key!r}] changed with the declaration"


def test_payload_warns_when_declaration_inapplicable(monkeypatch, caplog):
    """Declaring --escort-passive-education on a population with NO 'escort'
    purpose present must not crash, and must log a loud warning that the
    declaration does not apply (mirrors the presence-guard language in
    metrics.purpose_mix_w1_active_baseline)."""
    _patch_common_metrics(monkeypatch, purposes_no_home=["work", "leisure"])  # no escort

    with caplog.at_level(logging.WARNING, logger=report.LOG.name):
        payload = report._build_json_payload(_OD_STATS, _HH_SUMMARY, escort_passive_education=True)

    assert payload["escort_passive_education"] is True
    assert payload["purpose_mix_scored_baseline"] == "active_adjusted"
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("inapplicable" in r.message for r in warnings), caplog.text
    assert any("escort" in r.message for r in warnings), caplog.text


def test_payload_no_warning_when_flag_false_and_escort_absent(monkeypatch, caplog):
    """No declaration means no claim is made, so no warning should fire even
    on an escort-absent population."""
    _patch_common_metrics(monkeypatch, purposes_no_home=["work", "leisure"])

    with caplog.at_level(logging.WARNING, logger=report.LOG.name):
        report._build_json_payload(_OD_STATS, _HH_SUMMARY, escort_passive_education=False)

    assert not any(r.levelno == logging.WARNING for r in caplog.records)


def test_payload_no_warning_when_escort_present(monkeypatch, caplog):
    """Declaring the flag on a population that DOES carry 'escort' is the
    applicable case and must not warn."""
    _patch_common_metrics(monkeypatch, purposes_no_home=["work", "escort", "leisure"])

    with caplog.at_level(logging.WARNING, logger=report.LOG.name):
        report._build_json_payload(_OD_STATS, _HH_SUMMARY, escort_passive_education=True)

    assert not any(r.levelno == logging.WARNING for r in caplog.records)


# ---------------------------------------------------------------------------
# HTML badge helper (section 5.3 / 5.3b tagging)
# ---------------------------------------------------------------------------
def test_html_badge_empty_when_flag_not_declared():
    """No declaration -> no tag at all on either section (byte-identical
    HTML to the pre-#256-CLI report)."""
    assert report._scored_baseline_badge(False, False) == ""
    assert report._scored_baseline_badge(True, False) == ""


def test_html_badge_tags_active_adjusted_as_scored_when_declared():
    badge = report._scored_baseline_badge(True, True)
    assert "SCORED" in badge
    assert "--escort-passive-education" in badge


def test_html_badge_tags_raw_as_informational_when_declared():
    badge = report._scored_baseline_badge(False, True)
    assert "informational" in badge
    assert "SCORED" not in badge
