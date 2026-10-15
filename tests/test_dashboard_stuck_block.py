"""The dashboard's matsim block gains an additive stuck block computed from the final iteration's events (#443)."""
import gzip

import pandas as pd

from braunschweig.analysis.dashboard import run_metrics

EVENTS = """<events version="1.0">
<event time="108000.0" type="stuckAndAbort" person="1" link="11" />
</events>
"""
PLANS = """<population><person id="1"><plan selected="yes"><activity type="home" x="0" y="0" end_time="07:00:00"/>
<leg mode="walk"/><activity type="outside" x="1" y="0"/></plan></person>
<person id="2"><plan selected="yes"><activity type="home" x="0" y="0"/></plan></person></population>
"""


def test_metrics_matsim_adds_the_stuck_block_and_keeps_the_existing_keys(tmp_path, monkeypatch):
    monkeypatch.setattr(run_metrics, "_load_zgb_kreise", lambda: None)
    with gzip.open(tmp_path / "output_events.xml.gz", "wt", encoding="utf-8") as handle:
        handle.write(EVENTS)
    with gzip.open(tmp_path / "output_plans.xml.gz", "wt", encoding="utf-8") as handle:
        handle.write(PLANS)
    pd.DataFrame({"person": ["1", "2"], "subpopulation": ["", ""]}).to_csv(
        tmp_path / "output_persons.csv.gz", sep=";", index=False)
    out = run_metrics.metrics_matsim(tmp_path)
    assert out["available"] is True
    assert out["stuck"]["n_stuck"] == 1
    assert out["stuck"]["by_subpopulation"]["residents"]["at_end"] == {"frozen": 1, "in_region": 0}


def test_metrics_matsim_reports_an_absent_events_file_as_not_available(tmp_path, monkeypatch):
    monkeypatch.setattr(run_metrics, "_load_zgb_kreise", lambda: None)
    (tmp_path / "eqasim_trips.csv").write_text("person_id;person_trip_id\n", encoding="utf-8")
    out = run_metrics.metrics_matsim(tmp_path)
    assert out["stuck"] == {
        "available": False,
        "reason": "output_events.xml.gz, output_plans.xml.gz or output_persons.csv.gz missing"}
