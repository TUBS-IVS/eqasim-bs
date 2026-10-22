"""Plan feasibility check on the written MATSim population (eqasim-bs#443, ADR-0146)."""
import gzip

import pytest

from braunschweig.matsim.simulation import plan_check as C

POPULATION = """<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE population SYSTEM "http://www.matsim.org/files/dtd/population_v6.dtd">
<population>
  <person id="1">
    <plan selected="yes">
      <activity type="home" x="0" y="0" end_time="07:00:00"/>
      <leg mode="walk" dep_time="07:00:00" trav_time="05:30:00"/>
      <activity type="outside" x="20000" y="0" end_time="31:00:00"/>
      <leg mode="walk" dep_time="31:00:00" trav_time="05:30:00"/>
      <activity type="home" x="0" y="0"/>
    </plan>
  </person>
  <person id="2">
    <plan selected="yes">
      <activity type="home" x="0" y="0" end_time="08:00:00"/>
      <leg mode="bicycle" dep_time="08:00:00" trav_time="00:30:00"/>
      <activity type="work" x="3000" y="4000" end_time="16:00:00"/>
      <leg mode="bicycle" dep_time="16:00:00" trav_time="00:30:00"/>
      <activity type="home" x="0" y="0" end_time="18:00:00"/>
      <leg mode="walk" dep_time="18:00:00" trav_time="00:10:00"/>
      <activity type="shop" x="0" y="6000" end_time="17:00:00"/>
      <leg mode="walk" dep_time="17:00:00" trav_time="00:10:00"/>
      <activity type="home" x="0" y="0"/>
    </plan>
  </person>
  <person id="freight_1">
    <attributes>
      <attribute name="subpopulation" class="java.lang.String">freight</attribute>
    </attributes>
    <plan selected="yes">
      <activity type="freight_start" x="0" y="0" end_time="05:00:00"/>
      <leg mode="truck" dep_time="05:00:00" trav_time="01:00:00"/>
      <activity type="freight_end" x="50000" y="0"/>
    </plan>
  </person>
  <person id="3">
    <plan selected="yes">
      <activity type="home" x="0" y="0" end_time="22:00:00"/>
      <leg mode="car" dep_time="22:00:00" trav_time="00:20:00"/>
      <activity type="outside" x="100" y="0" end_time="23:00:00"/>
      <leg mode="walk" dep_time="23:00:00" trav_time="00:20:00">
        <attributes><attribute name="walkFallbackFrom" class="java.lang.String">pt</attribute></attributes>
      </leg>
      <activity type="work" x="200" y="0" end_time="24:00:00"/>
      <leg mode="pt" dep_time="24:00:00" trav_time="00:20:00">
        <attributes><attribute name="walkFallbackFrom" class="java.lang.String">pt</attribute></attributes>
      </leg>
      <activity type="home" x="0" y="0"/>
    </plan>
  </person>
  <person id="in_1">
    <attributes>
      <attribute name="subpopulation" class="java.lang.String">incommuter</attribute>
    </attributes>
    <plan selected="yes">
      <activity type="outside" x="0" y="0" end_time="03:00:00"/>
      <leg mode="walk" dep_time="03:00:00" trav_time="00:10:00">
        <attributes><attribute name="walkFallbackFrom" class="java.lang.String">pt</attribute></attributes>
      </leg>
      <activity type="work" x="100" y="0"/>
    </plan>
  </person>
</population>
"""

CONFIG = """<?xml version="1.0" encoding="utf-8"?>
<config>
  <module name="qsim">
    <param name="endTime" value="{end}" />
  </module>
</config>
"""

CONFIG_WITH_PLANS = """<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE config SYSTEM "http://www.matsim.org/files/dtd/config_v2.dtd">
<config>
  <module name="plans">
    <param name="inputPlansFile" value="{plans}" />
  </module>
  <module name="qsim">
    <param name="endTime" value="30:00:00" />
  </module>
</config>
"""


@pytest.fixture
def population_path(tmp_path):
    path = tmp_path / "test_population.xml.gz"
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        handle.write(POPULATION)
    return path


def test_parse_time_s_accepts_hours_beyond_24():
    assert C.parse_time_s("31:00:00") == 31 * 3600.0
    assert C.parse_time_s("00:10:30") == 630.0


def test_iter_person_plans_reads_subpopulation_activities_and_legs(population_path):
    plans = list(C.iter_person_plans(population_path))
    assert [p.person_id for p in plans] == ["1", "2", "freight_1", "3", "in_1"]
    assert [p.subpopulation for p in plans] == [C.RESIDENTS, C.RESIDENTS, "freight", C.RESIDENTS, "incommuter"]
    assert plans[0].activities[1] == ("outside", 31 * 3600.0, 20000.0, 0.0)
    assert plans[0].activities[2] == ("home", None, 0.0, 0.0)
    assert plans[0].legs == [("walk", 5.5 * 3600.0, None), ("walk", 5.5 * 3600.0, None)]


def test_leg_tour_frozen_marks_the_legs_of_tours_touching_outside():
    activities = [("home", 1.0, 0, 0), ("outside", 2.0, 1, 0), ("home", 3.0, 0, 0), ("shop", 4.0, 0, 1),
                  ("home", None, 0, 0)]
    assert C.leg_tour_frozen(activities) == [True, True, False, False]


def test_summarise_counts_late_non_monotonic_long_and_slow_legs_per_subpopulation_and_class(population_path):
    summary = C.summarise(C.iter_person_plans(population_path), simulation_end_s=30 * 3600.0,
                          walk_max_distance_m=5000.0, bicycle_max_distance_m=30000.0, leg_max_duration_s=10800.0)
    residents = summary["subpopulations"][C.RESIDENTS]
    frozen, inside = residents["frozen"], residents["in_region"]
    assert frozen["n_persons"] == 2 and inside["n_persons"] == 1
    assert frozen["n_plans_late"] == 1 and inside["n_plans_late"] == 0
    assert frozen["n_plans_non_monotonic"] == 0 and inside["n_plans_non_monotonic"] == 1   # person 2: 18:00 then 17:00
    assert frozen["n_walk_legs_over"] == 2 and inside["n_walk_legs_over"] == 2               # person 2's two 6 km walks
    assert frozen["n_legs_over_duration"] == 2 and inside["n_legs_over_duration"] == 0
    assert inside["n_bicycle_legs_over"] == 0
    assert frozen["share_plans_late"] == 0.5
    assert summary["subpopulations"]["freight"]["in_region"]["n_persons"] == 1
    assert summary["subpopulations"]["freight"]["frozen"]["n_persons"] == 0


def test_read_and_assert_qsim_end_time(tmp_path):
    path = tmp_path / "config.xml"
    path.write_text(CONFIG.format(end="30:00:00"), encoding="utf-8")
    assert C.read_qsim_end_time_s(path) == 30 * 3600.0
    C.assert_qsim_end_time(path, 30 * 3600.0)


def test_assert_qsim_end_time_raises_on_drift(tmp_path):
    path = tmp_path / "config.xml"
    path.write_text(CONFIG.format(end="36:00:00"), encoding="utf-8")
    with pytest.raises(ValueError, match="qsim.endTime"):
        C.assert_qsim_end_time(path, 30 * 3600.0)


def test_check_thresholds_fails_on_the_frozen_resident_class_only():
    summary = {"subpopulations": {
        C.RESIDENTS: {"frozen": {"n_persons": 100, "share_plans_late": 0.01, "share_plans_non_monotonic": 0.0},
                      "in_region": {"n_persons": 900, "share_plans_late": 0.5, "share_plans_non_monotonic": 0.5}},
        "freight": {"frozen": {"n_persons": 0, "share_plans_late": 0.0, "share_plans_non_monotonic": 0.0},
                    "in_region": {"n_persons": 10, "share_plans_late": 1.0, "share_plans_non_monotonic": 0.0}}}}
    with pytest.raises(ValueError, match="frozen"):
        C.check_thresholds(summary, 0.005, 0.02, fail_on_threshold=True)
    summary["subpopulations"][C.RESIDENTS]["frozen"]["share_plans_late"] = 0.004
    assert C.check_thresholds(summary, 0.005, 0.02, fail_on_threshold=True) == []


def test_resolve_population_path_resolves_a_relative_input_plans_file_next_to_the_config(tmp_path):
    plans = tmp_path / "bs_population.xml.gz"
    plans.write_bytes(b"")
    config = tmp_path / "bs_config.xml"
    config.write_text(CONFIG_WITH_PLANS.format(plans="bs_population.xml.gz"), encoding="utf-8")
    resolved = C.resolve_population_path(config)
    assert resolved == str(plans)


def test_resolve_population_path_raises_when_the_config_has_no_input_plans_file(tmp_path):
    config = tmp_path / "bs_config.xml"
    config.write_text(CONFIG.format(end="30:00:00"), encoding="utf-8")
    with pytest.raises(ValueError, match="bs_config.xml"):
        C.resolve_population_path(config)


def test_resolve_population_path_raises_when_the_population_file_is_missing(tmp_path):
    config = tmp_path / "bs_config.xml"
    config.write_text(CONFIG_WITH_PLANS.format(plans="absent.xml.gz"), encoding="utf-8")
    with pytest.raises(FileNotFoundError, match="absent.xml.gz"):
        C.resolve_population_path(config)


def _plan(person_id, activities, legs, subpopulation=C.RESIDENTS):
    return C.PersonPlan(person_id=person_id, subpopulation=subpopulation, activities=activities, legs=legs)


def _summarise(plans):
    return C.summarise(plans, simulation_end_s=30 * 3600.0, walk_max_distance_m=5000.0,
                       bicycle_max_distance_m=30000.0, leg_max_duration_s=10800.0)


def test_summarise_counts_plans_whose_last_planned_arrival_is_after_the_simulated_day_per_class():
    h = 3600.0
    # Every activity END time is inside the day; only the last leg (departs 29:00, travels 1:30) arrives at 30:30.
    late_arrival_frozen = _plan("a", [("home", 7 * h, 0, 0), ("outside", 29 * h, 1, 0), ("home", None, 0, 0)],
                                [("car", 0.5 * h, None), ("car", 1.5 * h, None)])
    on_time_frozen = _plan("b", [("home", 7 * h, 0, 0), ("outside", 29 * h, 1, 0), ("home", None, 0, 0)],
                           [("car", 0.5 * h, None), ("car", 0.5 * h, None)])
    late_arrival_inside = _plan("c", [("home", 8 * h, 0, 0), ("work", 29.5 * h, 1, 0), ("home", None, 0, 0)],
                                [("car", 0.5 * h, None), ("car", 1.0 * h, None)])
    no_travel_time = _plan("d", [("home", 8 * h, 0, 0), ("work", None, 1, 0)], [("car", None, None)])
    summary = _summarise([late_arrival_frozen, on_time_frozen, late_arrival_inside, no_travel_time])
    residents = summary["subpopulations"][C.RESIDENTS]
    assert residents["frozen"]["n_plans_late"] == 0 and residents["in_region"]["n_plans_late"] == 0
    assert residents["frozen"]["n_plans_arrival_late"] == 1
    assert residents["frozen"]["share_plans_arrival_late"] == 0.5
    assert residents["in_region"]["n_plans_arrival_late"] == 1
    # A plan whose last leg carries no trav_time cannot be evaluated: it is not counted as late.
    assert residents["in_region"]["share_plans_arrival_late"] == 0.5


def test_arrival_late_is_report_only_and_never_fails_the_thresholds():
    summary = {"subpopulations": {C.RESIDENTS: {"frozen": {
        "n_persons": 10, "share_plans_late": 0.0, "share_plans_non_monotonic": 0.0, "share_plans_arrival_late": 0.9}}}}
    assert C.check_thresholds(summary, 0.005, 0.02, fail_on_threshold=True) == []


def test_summarise_raises_naming_the_person_when_a_plan_has_more_legs_than_activities_minus_one():
    broken = _plan("broken_42", [("home", 1.0, 0, 0), ("work", None, 1, 0)], [("car", 10.0, None), ("car", 10.0, None)])
    with pytest.raises(ValueError, match="broken_42"):
        _summarise([broken])


def test_summarise_accepts_a_plan_without_activities_and_legs():
    summary = _summarise([_plan("empty", [], [])])
    assert summary["subpopulations"][C.RESIDENTS][C.IN_REGION]["n_persons"] == 1


def test_iter_person_plans_keeps_the_root_element_empty_while_streaming(population_path, monkeypatch):
    seen = []
    real_iterparse = C.ET.iterparse

    def spying_iterparse(*args, **kwargs):
        iterator = real_iterparse(*args, **kwargs)
        seen.append(iterator)
        return iterator

    monkeypatch.setattr(C.ET, "iterparse", spying_iterparse)
    assert len(list(C.iter_person_plans(population_path))) == 5
    # The cleared person elements must not stay attached to the root (memory stays bounded at 100 % scale).
    assert len(seen[0].root) == 0


def _frozen_summary(share_late):
    return {"subpopulations": {C.RESIDENTS: {"frozen": {
        "n_persons": 200, "share_plans_late": share_late, "share_plans_non_monotonic": 0.0}}}}


def test_check_thresholds_logs_an_error_and_continues_when_failing_is_off(caplog):
    with caplog.at_level("ERROR", logger=C.logger.name):
        violations = C.check_thresholds(_frozen_summary(0.02), 0.005, 0.02, fail_on_threshold=False)
    assert violations == ["share_plans_late"]
    assert any(record.levelname == "ERROR" and "share_plans_late" in record.getMessage() and "2.00%" in
               record.getMessage() for record in caplog.records)


def test_check_thresholds_raises_when_failing_is_on():
    with pytest.raises(ValueError, match="share_plans_late"):
        C.check_thresholds(_frozen_summary(0.02), 0.005, 0.02, fail_on_threshold=True)


# --- run(): the whole check against a written config and population (reviewer probe of the final review) ---

RUN_CONFIG = """<?xml version="1.0" ?>
<!DOCTYPE config SYSTEM "http://www.matsim.org/files/dtd/config_v2.dtd">
<config>
  <module name="plans"><param name="inputPlansFile" value="test_population.xml.gz" /></module>
  <module name="qsim"><param name="endTime" value="30:00:00" /></module>
</config>
"""

RUN_POPULATION = """<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE population SYSTEM "http://www.matsim.org/files/dtd/population_v6.dtd">
<population>
  <person id="late"><plan selected="yes">
    <activity type="home" x="0" y="0" end_time="07:00:00"/><leg mode="walk" trav_time="08:00:00"/>
    <activity type="outside" x="9000" y="0" end_time="31:00:00"/><leg mode="walk" trav_time="01:00:00"/>
    <activity type="home" x="0" y="0"/></plan></person>
  <person id="fine"><plan selected="yes">
    <activity type="home" x="0" y="0" end_time="08:00:00"/><leg mode="car" trav_time="00:40:00"/>
    <activity type="work" x="1000" y="0"/></plan></person>
</population>
"""


class _RunContext:
    def __init__(self, directory, fail_on_threshold, max_share=0.1):
        self._directory = directory
        self._values = {C.KEY_WALK_MAX_DISTANCE_M: 5000.0, C.KEY_BICYCLE_MAX_DISTANCE_M: 30000.0,
                        C.KEY_LEG_MAX_DURATION_S: 10800.0, C.KEY_MAX_FROZEN_DEFECT_SHARE: max_share,
                        C.KEY_MAX_FROZEN_PT_AS_WALK_SHARE: 0.02,
                        C.KEY_FAIL_ON_THRESHOLD: fail_on_threshold}

    def path(self):
        return str(self._directory)

    def config(self, key):
        return self._values[key]


def _write_run_inputs(directory):
    (directory / "bs_config.xml").write_text(RUN_CONFIG, encoding="utf-8")
    with gzip.open(directory / "test_population.xml.gz", "wt", encoding="utf-8") as handle:
        handle.write(RUN_POPULATION)


def test_run_writes_plan_check_json_with_the_counts_and_the_threshold_decision(tmp_path):
    import json
    _write_run_inputs(tmp_path)
    summary = C.run(_RunContext(tmp_path, fail_on_threshold=False), "bs_config.xml")
    written = json.loads((tmp_path / C.PLAN_CHECK_FILE_NAME).read_text(encoding="utf-8"))
    frozen = written["subpopulations"][C.RESIDENTS][C.FROZEN]
    assert frozen["n_persons"] == 1 and frozen["n_plans_late"] == 1 and frozen["n_walk_legs_over"] == 2
    assert frozen["n_plans_arrival_late"] == 1
    assert written["subpopulations"][C.RESIDENTS][C.IN_REGION]["n_persons"] == 1
    assert written["simulation_end_s"] == 30 * 3600.0
    assert written["threshold"]["fail_on_threshold"] is False
    assert written["threshold"]["exceeded"] == ["share_plans_late"]
    assert summary["subpopulations"][C.RESIDENTS][C.FROZEN]["n_plans_late"] == 1


def test_run_raises_after_writing_the_json_when_failing_is_on_and_the_frozen_defect_is_above_the_share(tmp_path):
    _write_run_inputs(tmp_path)
    with pytest.raises(ValueError, match="share_plans_late"):
        C.run(_RunContext(tmp_path, fail_on_threshold=True), "bs_config.xml")
    assert (tmp_path / C.PLAN_CHECK_FILE_NAME).exists()


def test_run_does_not_raise_when_the_frozen_defect_is_inside_the_share(tmp_path):
    _write_run_inputs(tmp_path)
    C.run(_RunContext(tmp_path, fail_on_threshold=True, max_share=1.0), "bs_config.xml")
    assert (tmp_path / C.PLAN_CHECK_FILE_NAME).exists()


# --- pt trips realised as walks (eqasim-bs#465): the leg attribute walkFallbackFrom, per class and hour ---

def test_iter_person_plans_reads_the_walk_fallback_attribute_of_a_leg(population_path):
    plans = {p.person_id: p for p in C.iter_person_plans(population_path)}
    assert [leg[2] for leg in plans["3"].legs] == [None, "pt", "pt"]
    assert plans["1"].legs[0][2] is None


def test_summarise_counts_pt_as_walk_per_class_and_hour(population_path):
    summary = _summarise(C.iter_person_plans(population_path))
    frozen = summary["subpopulations"][C.RESIDENTS][C.FROZEN]
    assert frozen["n_pt_as_walk"] == 1 and frozen["pt_as_walk_by_hour"] == {"23": 1}
    # The leg that kept the attribute but is a pt leg again is NOT a pt-as-walk trip.
    assert frozen["n_pt_as_walk_attr_without_walk"] == 1
    assert frozen["share_pt_as_walk"] == 1 / frozen["n_legs"]
    # The in-commuter subpopulation is counted in its own block.
    assert summary["subpopulations"]["incommuter"][C.FROZEN]["pt_as_walk_by_hour"] == {"3": 1}
    assert summary["subpopulations"][C.RESIDENTS][C.IN_REGION]["n_pt_as_walk"] == 0


def test_summarise_files_a_pt_as_walk_leg_without_origin_end_time_under_unknown():
    plan = _plan("u", [("outside", None, 0, 0), ("work", None, 1, 0)], [("walk", 60.0, "pt")])
    frozen = _summarise([plan])["subpopulations"][C.RESIDENTS][C.FROZEN]
    assert frozen["pt_as_walk_by_hour"] == {"unknown": 1}


def test_walk_fallback_attribute_name_matches_the_java_constant():
    assert C.WALK_FALLBACK_ATTRIBUTE == "walkFallbackFrom"


def test_find_threshold_violations_names_the_pt_as_walk_share():
    summary = {"subpopulations": {C.RESIDENTS: {C.FROZEN: {
        "n_persons": 100, "n_legs": 100, "share_plans_late": 0.0, "share_plans_non_monotonic": 0.0,
        "n_pt_as_walk": 5, "share_pt_as_walk": 0.05}}}}
    assert any("pt_as_walk" in v for v in C.find_threshold_violations(summary, 0.005, 0.02))
    assert C.find_threshold_violations(summary, 0.005, 0.10) == []
