"""Stuck agents of the final iteration by category (eqasim-bs#443, ADR-0146)."""
import gzip
import json

import pandas as pd

from braunschweig.analysis import stuck_agents as S

EVENTS = """<?xml version="1.0" encoding="utf-8"?>
<events version="1.0">
<event time="25200.0" type="actend" person="1" link="10" actType="home" />
<event time="30000.0" type="stuckAndAbort" person="2" link="20" legMode="pt" />
<event time="108000.0" type="stuckAndAbort" person="1" link="11" />
<event time="108000.0" type="stuckAndAbort" person="freight_1" link="30" legMode="truck" />
</events>
"""

PLANS = """<?xml version="1.0" encoding="utf-8"?>
<population>
  <person id="1"><plan selected="yes"><activity type="home" x="0" y="0" end_time="07:00:00"/><leg mode="walk"/>
    <activity type="outside" x="1" y="0" end_time="31:00:00"/><leg mode="walk"/><activity type="home" x="0" y="0"/></plan>
    <plan selected="no"><activity type="home" x="0" y="0"/></plan></person>
  <person id="2"><plan selected="yes"><activity type="home" x="0" y="0" end_time="08:00:00"/><leg mode="pt"/>
    <activity type="work" x="1" y="0" end_time="16:00:00"/><leg mode="pt"/><activity type="home" x="0" y="0"/></plan></person>
  <person id="3"><plan selected="yes"><activity type="home" x="0" y="0"/></plan></person>
  <person id="freight_1"><plan selected="yes"><activity type="freight_start" x="0" y="0" end_time="05:00:00"/>
    <leg mode="truck"/><activity type="freight_end" x="9" y="0"/></plan></person>
</population>
"""


def _sim_output(tmp_path):
    with gzip.open(tmp_path / "output_events.xml.gz", "wt", encoding="utf-8") as handle:
        handle.write(EVENTS)
    with gzip.open(tmp_path / "output_plans.xml.gz", "wt", encoding="utf-8") as handle:
        handle.write(PLANS)
    pd.DataFrame({"person": ["1", "2", "3", "freight_1"], "subpopulation": ["", "", "", "freight"]}).to_csv(
        tmp_path / "output_persons.csv.gz", sep=";", index=False)
    return tmp_path


def test_scan_stuck_events_keeps_only_stuck_events_with_their_attributes(tmp_path):
    events = S.scan_stuck_events(_sim_output(tmp_path) / "output_events.xml.gz")
    assert events["person"].tolist() == ["2", "1", "freight_1"]
    assert events["leg_mode"].tolist() == ["pt", "none", "truck"]
    assert events["time"].tolist() == [30000.0, 108000.0, 108000.0]


def test_frozen_persons_read_the_selected_plan_only(tmp_path):
    assert S.frozen_persons(_sim_output(tmp_path) / "output_plans.xml.gz") == {"1"}


def test_person_subpopulations_name_residents(tmp_path):
    subpopulations = S.person_subpopulations(_sim_output(tmp_path) / "output_persons.csv.gz")
    assert subpopulations.to_dict() == {"1": "residents", "2": "residents", "3": "residents", "freight_1": "freight"}


def test_stuck_summary_counts_by_subpopulation_time_class_and_mode(tmp_path):
    summary = S.stuck_summary(_sim_output(tmp_path), simulation_end_s=30 * 3600.0)
    residents = summary["by_subpopulation"]["residents"]
    assert residents["n_persons"] == 3 and residents["n_stuck"] == 2
    assert residents["share_stuck"] == 2 / 3
    assert residents["at_end"] == {"frozen": 1, "in_region": 0}
    assert residents["during_day"] == {"frozen": 0, "in_region": 1}
    assert residents["by_mode"] == {"none": 1, "pt": 1}
    assert summary["by_subpopulation"]["freight"]["n_stuck"] == 1
    assert summary["n_stuck"] == 3 and summary["warning"] is True   # 66 % of residents > 1 %


def test_write_stuck_outputs_writes_the_csv_and_the_summary(tmp_path):
    csv_path, json_path = S.write_stuck_outputs(_sim_output(tmp_path), tmp_path / "analysis" / "stuck",
                                                simulation_end_s=30 * 3600.0)
    frame = pd.read_csv(csv_path, sep=";", dtype={"person": str})
    assert list(frame.columns) == ["person", "time", "leg_mode", "link", "subpopulation", "frozen", "at_end"]
    assert frame.loc[frame["person"] == "1", "frozen"].item() and frame.loc[frame["person"] == "1", "at_end"].item()
    with open(json_path, encoding="utf-8") as handle:
        assert json.load(handle)["n_stuck"] == 3
