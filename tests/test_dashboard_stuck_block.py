"""The dashboard's matsim block gains an additive stuck block computed from the final iteration's events (#443).

The block is guarded: a failing stuck analysis is recorded with its reason and never loses the other metrics
(C1); a summary the analysis suite already wrote is reused when it is newer than the events file (C2).
"""
import gzip
import json
import os

import pandas as pd
import pytest

from braunschweig.analysis import stuck_agents
from braunschweig.analysis.dashboard import run_metrics

EVENTS = """<events version="1.0">
<event time="108000.0" type="stuckAndAbort" person="1" link="11" />
</events>
"""
PLANS = """<population><person id="1"><plan selected="yes"><activity type="home" x="0" y="0" end_time="07:00:00"/>
<leg mode="walk"/><activity type="outside" x="1" y="0"/></plan></person>
<person id="2"><plan selected="yes"><activity type="home" x="0" y="0"/></plan></person></population>
"""
MODESTATS = "iteration;car;walk\n0;0.4;0.6\n1;0.5;0.5\n"
OTHER_KEYS = ("modes", "mode_share_pct_final", "terminated_early")


@pytest.fixture(autouse=True)
def _no_vg250(monkeypatch):
    monkeypatch.setattr(run_metrics, "_load_zgb_kreise", lambda: None)


def _write_events(directory, text=EVENTS):
    with gzip.open(directory / "output_events.xml.gz", "wt", encoding="utf-8") as handle:
        handle.write(text)


def _write_simulation_output(directory):
    """A complete tiny simulation output, including a modestats table so that the other keys exist."""
    directory.mkdir(parents=True, exist_ok=True)
    _write_events(directory)
    with gzip.open(directory / "output_plans.xml.gz", "wt", encoding="utf-8") as handle:
        handle.write(PLANS)
    pd.DataFrame({"person": ["1", "2"], "subpopulation": ["", ""]}).to_csv(
        directory / "output_persons.csv.gz", sep=";", index=False)
    (directory / "modestats.csv").write_text(MODESTATS, encoding="utf-8")
    return directory


def test_metrics_matsim_adds_the_stuck_block_and_keeps_the_existing_keys(tmp_path):
    out = run_metrics.metrics_matsim(_write_simulation_output(tmp_path))
    assert out["available"] is True
    assert all(key in out for key in OTHER_KEYS)
    assert out["stuck"]["n_stuck"] == 1
    assert out["stuck"]["by_subpopulation"]["residents"]["at_end"] == {"frozen": 1, "in_region": 0}


def test_metrics_matsim_reports_absent_inputs_naming_the_missing_file(tmp_path, caplog):
    (tmp_path / "eqasim_trips.csv").write_text("person_id;person_trip_id\n", encoding="utf-8")
    with caplog.at_level("WARNING", logger=run_metrics.logger.name):
        out = run_metrics.metrics_matsim(tmp_path)
    assert out["stuck"]["available"] is False
    assert out["stuck"]["reason"].startswith("FileNotFoundError:")
    assert "output_events.xml.gz" in out["stuck"]["reason"]
    assert "output_events.xml.gz" in caplog.text


def test_a_persons_file_without_subpopulation_is_recorded_and_the_other_keys_survive(tmp_path, caplog):
    sim_output = _write_simulation_output(tmp_path)
    pd.DataFrame({"person": ["1", "2"]}).to_csv(sim_output / "output_persons.csv.gz", sep=";", index=False)
    with caplog.at_level("WARNING", logger=run_metrics.logger.name):
        out = run_metrics.metrics_matsim(sim_output)
    assert out["stuck"]["available"] is False
    assert out["stuck"]["reason"].startswith("ValueError:") and "subpopulation" in out["stuck"]["reason"]
    assert "subpopulation" in caplog.text
    assert out["available"] is True and all(key in out for key in OTHER_KEYS)


def test_a_truncated_events_file_is_recorded_and_the_other_keys_survive(tmp_path, caplog):
    sim_output = _write_simulation_output(tmp_path)
    complete = gzip.compress(EVENTS.encode("utf-8"))
    (sim_output / "output_events.xml.gz").write_bytes(complete[: len(complete) // 2])
    with caplog.at_level("WARNING", logger=run_metrics.logger.name):
        out = run_metrics.metrics_matsim(sim_output)
    assert out["stuck"]["available"] is False and out["stuck"]["reason"]
    assert "output_events.xml.gz" in caplog.text or "stuck" in caplog.text
    assert out["available"] is True and all(key in out for key in OTHER_KEYS)


def _write_precomputed(path, sim_output, offset_seconds, n_stuck):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"available": True, "n_stuck": n_stuck, "by_subpopulation": {}}), encoding="utf-8")
    events_time = (sim_output / "output_events.xml.gz").stat().st_mtime
    os.utime(path, (events_time + offset_seconds, events_time + offset_seconds))


def test_a_fresh_precomputed_summary_is_reused_without_streaming_the_events(tmp_path, monkeypatch, caplog):
    sim_output = _write_simulation_output(tmp_path / "matsim_output")
    summary_path = tmp_path / "analysis" / "stuck" / "stuck_summary.json"
    _write_precomputed(summary_path, sim_output, +10, n_stuck=99)

    def _must_not_stream(*args, **kwargs):
        raise AssertionError("the events file must not be streamed again")

    monkeypatch.setattr(stuck_agents, "scan_stuck_events", _must_not_stream)
    with caplog.at_level("INFO", logger=run_metrics.logger.name):
        out = run_metrics.metrics_matsim(sim_output, stuck_summary_path=summary_path)
    assert out["stuck"]["n_stuck"] == 99
    assert "reused" in caplog.text and str(summary_path) in caplog.text


def test_a_stale_precomputed_summary_is_recomputed_from_the_events(tmp_path, caplog):
    sim_output = _write_simulation_output(tmp_path / "matsim_output")
    summary_path = tmp_path / "analysis" / "stuck" / "stuck_summary.json"
    _write_precomputed(summary_path, sim_output, -10, n_stuck=99)
    with caplog.at_level("INFO", logger=run_metrics.logger.name):
        out = run_metrics.metrics_matsim(sim_output, stuck_summary_path=summary_path)
    assert out["stuck"]["n_stuck"] == 1
    assert "computed from" in caplog.text


def test_without_a_summary_path_the_block_is_computed_as_before(tmp_path):
    out = run_metrics.metrics_matsim(_write_simulation_output(tmp_path))
    assert out["stuck"]["n_stuck"] == 1


def test_assemble_run_record_hands_the_suite_summary_path_to_the_matsim_metrics(tmp_path, monkeypatch):
    from braunschweig.analysis.dashboard import run_records

    seen = {}

    def _fake_metrics_matsim(sim_output, stuck_summary_path=None):
        seen["sim_output"], seen["stuck_summary_path"] = sim_output, stuck_summary_path
        return {"available": False}

    monkeypatch.setattr(run_records, "metrics_matsim", _fake_metrics_matsim)
    monkeypatch.setattr(run_records, "metrics_eqasim", lambda *args, **kwargs: {})
    monkeypatch.setattr(run_records, "load_mid_reference", lambda: {})
    monkeypatch.setattr(run_records, "build_comparisons", lambda *args, **kwargs: {})
    monkeypatch.setattr(run_records, "_find_sim_output", lambda sim_cache: sim_cache)
    run_records.assemble_run_record("label", tmp_path / "out", tmp_path / "matsim_output", None)
    assert seen["stuck_summary_path"] == tmp_path / "out" / "analysis" / "stuck" / "stuck_summary.json"
