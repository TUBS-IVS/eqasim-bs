"""Stuck agents of the final iteration by category (eqasim-bs#443, ADR-0146)."""
import gzip
import json
import os

import pandas as pd
import pytest

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


CONFIG_TEMPLATE = """<?xml version="1.0" ?>
<!DOCTYPE config SYSTEM "http://www.matsim.org/files/dtd/config_v2.dtd">
<config><module name="qsim"><param name="endTime" value="{end_time}" /></module></config>
"""


def _write_output_config(directory, end_time, name="output_config.xml"):
    (directory / name).write_text(CONFIG_TEMPLATE.format(end_time=end_time), encoding="utf-8")


def test_simulation_end_is_read_from_the_output_config(tmp_path):
    _write_output_config(tmp_path, "36:00:00")
    assert S.simulation_end_seconds(tmp_path) == 36 * 3600.0


def test_simulation_end_falls_back_to_the_reduced_output_config(tmp_path):
    _write_output_config(tmp_path, "28:00:00", name="output_config_reduced.xml")
    assert S.simulation_end_seconds(tmp_path) == 28 * 3600.0


def test_simulation_end_prefers_the_full_output_config(tmp_path):
    _write_output_config(tmp_path, "36:00:00")
    _write_output_config(tmp_path, "28:00:00", name="output_config_reduced.xml")
    assert S.simulation_end_seconds(tmp_path) == 36 * 3600.0


def test_simulation_end_uses_the_plan_time_bound_and_warns_naming_both_files(tmp_path, caplog):
    with caplog.at_level("WARNING", logger=S.logger.name):
        end = S.simulation_end_seconds(tmp_path)
    assert end == S.MAX_PLAN_TIME_SECONDS
    assert "output_config.xml" in caplog.text and "output_config_reduced.xml" in caplog.text


def test_simulation_end_skips_a_config_without_a_readable_end_time(tmp_path, caplog):
    _write_output_config(tmp_path, "undefined")
    _write_output_config(tmp_path, "29:00:00", name="output_config_reduced.xml")
    with caplog.at_level("WARNING", logger=S.logger.name):
        assert S.simulation_end_seconds(tmp_path) == 29 * 3600.0
    assert "output_config.xml" in caplog.text


def test_stuck_summary_reads_the_simulation_end_from_the_output_config_by_default(tmp_path):
    _write_output_config(_sim_output(tmp_path), "31:00:00")
    summary = S.stuck_summary(tmp_path)
    assert summary["simulation_end_s"] == 31 * 3600.0
    # the events at 30:00 are before a 31:00 end: during the day, not at the end
    assert summary["by_subpopulation"]["residents"]["at_end"] == {"frozen": 0, "in_region": 0}
    assert summary["by_subpopulation"]["residents"]["during_day"] == {"frozen": 1, "in_region": 1}


def test_the_warn_share_is_configurable_and_recorded(tmp_path):
    sim_output = _sim_output(tmp_path)
    strict = S.stuck_summary(sim_output, simulation_end_s=30 * 3600.0, warn_share=0.9)
    assert strict["warn_share"] == 0.9 and strict["warning"] is False   # 2/3 < 0.9
    default = S.stuck_summary(sim_output, simulation_end_s=30 * 3600.0)
    assert default["warn_share"] == S.STUCK_WARN_SHARE and default["warning"] is True


def test_write_stuck_outputs_passes_the_warn_share_through(tmp_path):
    _, json_path = S.write_stuck_outputs(_sim_output(tmp_path), tmp_path / "out", simulation_end_s=30 * 3600.0,
                                         warn_share=0.9)
    with open(json_path, encoding="utf-8") as handle:
        assert json.load(handle)["warn_share"] == 0.9


def test_a_persons_file_without_the_subpopulation_column_names_the_file(tmp_path):
    sim_output = _sim_output(tmp_path)
    pd.DataFrame({"person": ["1"]}).to_csv(sim_output / "output_persons.csv.gz", sep=";", index=False)
    with pytest.raises(ValueError, match="output_persons.csv.gz.*subpopulation"):
        S.stuck_summary(sim_output, simulation_end_s=30 * 3600.0)


def test_reusable_summary_is_returned_only_when_newer_than_the_events_file(tmp_path):
    sim_output = _sim_output(tmp_path)
    summary_path = tmp_path / "stuck_summary.json"
    assert S.reusable_stuck_summary(sim_output, summary_path) is None          # absent
    summary_path.write_text(json.dumps({"available": True, "n_stuck": 7, "by_subpopulation": {}}), encoding="utf-8")
    events_time = (sim_output / "output_events.xml.gz").stat().st_mtime
    os.utime(summary_path, (events_time + 10, events_time + 10))
    assert S.reusable_stuck_summary(sim_output, summary_path)["n_stuck"] == 7  # newer
    os.utime(summary_path, (events_time - 10, events_time - 10))
    assert S.reusable_stuck_summary(sim_output, summary_path) is None          # stale


def test_reusable_summary_rejects_an_unreadable_file_loudly(tmp_path, caplog):
    sim_output = _sim_output(tmp_path)
    summary_path = tmp_path / "stuck_summary.json"
    summary_path.write_text("{not json", encoding="utf-8")
    events_time = (sim_output / "output_events.xml.gz").stat().st_mtime
    os.utime(summary_path, (events_time + 10, events_time + 10))
    with caplog.at_level("WARNING", logger=S.logger.name):
        assert S.reusable_stuck_summary(sim_output, summary_path) is None
    assert "stuck_summary.json" in caplog.text


def test_the_summary_written_by_the_suite_is_reusable_by_the_dashboard(tmp_path):
    sim_output = _sim_output(tmp_path)
    events_path = sim_output / "output_events.xml.gz"
    os.utime(events_path, (events_path.stat().st_mtime - 60, events_path.stat().st_mtime - 60))
    _, json_path = S.write_stuck_outputs(sim_output, tmp_path / "analysis" / "stuck", simulation_end_s=30 * 3600.0)
    reused = S.reusable_stuck_summary(sim_output, json_path)
    assert reused is not None and reused["n_stuck"] == 3
