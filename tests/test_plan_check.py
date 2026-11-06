"""Plan feasibility check on the written MATSim population (eqasim-bs#443, ADR-0146)."""
import gzip
import zipfile

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
    assert plans[0].legs == [("walk", 5.5 * 3600.0, None, None), ("walk", 5.5 * 3600.0, None, None)]


def test_leg_tour_frozen_marks_the_legs_of_tours_touching_outside():
    activities = [("home", 1.0, 0, 0), ("outside", 2.0, 1, 0), ("home", 3.0, 0, 0), ("shop", 4.0, 0, 1),
                  ("home", None, 0, 0)]
    assert C.leg_tour_frozen(activities) == [True, True, False, False]


def test_summarise_counts_late_non_monotonic_long_and_slow_legs_per_subpopulation_and_class(population_path):
    summary = C.summarise(C.iter_person_plans(population_path), simulation_end_s=30 * 3600.0,
                          walk_max_distance_m=5000.0, bicycle_max_distance_m=30000.0, leg_max_duration_s=10800.0,
                          pt_as_walk_bound_min_distance_m=5000.0, non_monotonic_bound_min_step_s=900.0)
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
                       bicycle_max_distance_m=30000.0, leg_max_duration_s=10800.0,
                       pt_as_walk_bound_min_distance_m=5000.0, non_monotonic_bound_min_step_s=900.0)


def test_summarise_counts_plans_whose_last_planned_arrival_is_after_the_simulated_day_per_class():
    h = 3600.0
    # Every activity END time is inside the day; only the last leg (departs 29:00, travels 1:30) arrives at 30:30.
    late_arrival_frozen = _plan("a", [("home", 7 * h, 0, 0), ("outside", 29 * h, 1, 0), ("home", None, 0, 0)],
                                [("car", 0.5 * h, None, None), ("car", 1.5 * h, None, None)])
    on_time_frozen = _plan("b", [("home", 7 * h, 0, 0), ("outside", 29 * h, 1, 0), ("home", None, 0, 0)],
                           [("car", 0.5 * h, None, None), ("car", 0.5 * h, None, None)])
    late_arrival_inside = _plan("c", [("home", 8 * h, 0, 0), ("work", 29.5 * h, 1, 0), ("home", None, 0, 0)],
                                [("car", 0.5 * h, None, None), ("car", 1.0 * h, None, None)])
    no_travel_time = _plan("d", [("home", 8 * h, 0, 0), ("work", None, 1, 0)], [("car", None, None, None)])
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
    broken = _plan("broken_42", [("home", 1.0, 0, 0), ("work", None, 1, 0)], [("car", 10.0, None, None), ("car", 10.0, None, None)])
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


JAR_NAME = "eqasim.jar"


def _write_stub_jar(path, class_bytes=b"\xca\xfe\xba\xbe walkFallbackFrom", entry=None,
                    cutter_bytes=b"\xca\xfe\xba\xbe walkFallbackFrom"):
    """A tiny zip standing in for the eqasim jar: the plan router class with ``class_bytes`` (or, with ``entry``,
    that entry instead) and the plan cutter class with ``cutter_bytes`` (``None`` leaves it out)."""
    with zipfile.ZipFile(path, "w") as jar:
        jar.writestr(entry or C.PLAN_ROUTER_CLASS_ENTRY, class_bytes)
        if cutter_bytes is not None:
            jar.writestr(C.PLAN_CUTTER_CLASS_ENTRY, cutter_bytes)
    return path


class _RunContext:
    """Stub of the synpp context: stage directory, config values and the jar of ``matsim.runtime.eqasim``."""

    def __init__(self, directory, fail_on_threshold, max_share=0.1):
        self._directory = directory
        self._values = {C.KEY_WALK_MAX_DISTANCE_M: 5000.0, C.KEY_BICYCLE_MAX_DISTANCE_M: 30000.0,
                        C.KEY_LEG_MAX_DURATION_S: 10800.0, C.KEY_MAX_FROZEN_DEFECT_SHARE: max_share,
                        C.KEY_MAX_FROZEN_PT_AS_WALK_SHARE: 0.02,
                        C.KEY_PT_AS_WALK_BOUND_MIN_DISTANCE_M: 5000.0, C.KEY_NON_MONOTONIC_BOUND_MIN_STEP_S: 900.0,
                        C.KEY_FAIL_ON_THRESHOLD: fail_on_threshold}
        (directory / "runtime").mkdir(exist_ok=True)
        _write_stub_jar(directory / "runtime" / JAR_NAME)

    def path(self, name=None):
        return str(self._directory / "runtime") if name == "matsim.runtime.eqasim" else str(self._directory)

    def stage(self, name):
        assert name == "matsim.runtime.eqasim"
        return JAR_NAME

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
    # Denominator: the two pt-intended trips (the stamped walk and the stamped pt leg), not the five legs.
    assert frozen["n_pt_trips"] == 2 and frozen["share_pt_as_walk"] == 1 / 2
    # The in-commuter subpopulation is counted in its own block.
    assert summary["subpopulations"]["incommuter"][C.FROZEN]["pt_as_walk_by_hour"] == {"3": 1}
    assert summary["subpopulations"][C.RESIDENTS][C.IN_REGION]["n_pt_as_walk"] == 0


def test_summarise_files_a_pt_as_walk_leg_without_origin_end_time_under_unknown():
    plan = _plan("u", [("outside", None, 0, 0), ("work", None, 1, 0)], [("walk", 60.0, "pt", "pt")])
    frozen = _summarise([plan])["subpopulations"][C.RESIDENTS][C.FROZEN]
    assert frozen["pt_as_walk_by_hour"] == {"unknown": 1}


def test_summarise_counts_a_walk_only_pt_trip_without_the_stamp_as_pt_as_walk():
    # The mode choice's own router sets routingMode but never the walkFallbackFrom stamp (eqasim-bs#542).
    h = 3600.0
    plan = _plan("w", [("home", 8 * h, 0, 0), ("pt interaction", 8 * h + 300, 1, 0), ("work", None, 2, 0)],
                 [("walk", 300.0, None, "pt"), ("walk", 120.0, None, "pt")])
    residents = _summarise([plan])["subpopulations"][C.RESIDENTS]
    block = residents[C.FROZEN if residents[C.FROZEN]["n_pt_trips"] else C.IN_REGION]
    assert block["n_pt_trips"] == 1 and block["n_pt_as_walk"] == 1 and block["share_pt_as_walk"] == 1.0
    assert block["pt_as_walk_by_hour"] == {"8": 1}


def test_summarise_does_not_count_a_pt_trip_with_a_pt_leg_as_walk():
    h = 3600.0
    plan = _plan("p", [("home", 8 * h, 0, 0), ("pt interaction", 8 * h + 60, 1, 0),
                       ("pt interaction", 8 * h + 700, 2, 0), ("work", None, 3, 0)],
                 [("walk", 60.0, None, "pt"), ("pt", 600.0, None, "pt"), ("walk", 60.0, None, "pt")])
    block = _summarise([plan])["subpopulations"][C.RESIDENTS][C.IN_REGION]
    assert block["n_pt_trips"] == 1 and block["n_pt_as_walk"] == 0


def test_summarise_counts_a_stamped_walk_and_a_walk_only_pt_trip_once_each():
    h = 3600.0
    both_rules = [("walk", 60.0, "pt", "pt")]
    plan = _plan("b", [("home", 8 * h, 0, 0), ("work", 9 * h, 1, 0), ("home", 17 * h, 0, 0),
                       ("leisure", None, 1, 0)],
                 both_rules + [("car", 60.0, None, "car")] + [("walk", 60.0, None, "pt")])
    block = _summarise([plan])["subpopulations"][C.RESIDENTS][C.IN_REGION]
    assert block["n_pt_trips"] == 2 and block["n_pt_as_walk"] == 2
    assert block["pt_as_walk_by_hour"] == {"8": 1, "17": 1}


def test_walk_fallback_attribute_name_matches_the_java_constant():
    assert C.WALK_FALLBACK_ATTRIBUTE == "walkFallbackFrom"


def test_find_threshold_violations_names_the_pt_as_walk_share():
    summary = {"subpopulations": {C.RESIDENTS: {C.FROZEN: {
        "n_persons": 100, "n_legs": 100, "share_plans_late": 0.0, "share_plans_non_monotonic": 0.0,
        "n_pt_as_walk": 5, "share_pt_as_walk": 0.05, "n_pt_as_walk_long": 5, "share_pt_as_walk_long": 0.05}}}}
    assert C.find_threshold_violations(summary, 0.005, 0.02) == ["share_pt_as_walk_long"]
    assert C.find_threshold_violations(summary, 0.005, 0.10) == []


def test_format_by_hour_orders_numerically_with_unknown_last():
    assert list(C._format_by_hour({"23": 1, "unknown": 2, "3": 4})) == ["3", "23", "unknown"]
    assert list(C._format_by_hour({"10": 1, "2": 1})) == ["2", "10"]


def test_format_by_hour_empty_returns_empty_dict():
    assert C._format_by_hour({}) == {}


# --- the bound judges long pt-as-walk trips and large backward steps; the totals and the bands stay reported (#576) ---

def _pt_as_walk_plan(person_id, distance_m):
    """A frozen resident whose single pt-intended trip is a stamped walk over a straight line of ``distance_m``."""
    return _plan(person_id, [("outside", 8 * 3600.0, 0, 0), ("work", None, distance_m, 0)],
                 [("walk", 60.0, "pt", "pt")])


def _backward_step_plan(person_id, *end_times):
    """A resident whose activity end times are ``end_times`` (one activity per time, plus a final open one)."""
    activities = [("home" if index == 0 else "work", end, 0, 0) for index, end in enumerate(end_times)]
    activities.append(("home", None, 0, 0))
    return _plan(person_id, activities, [("car", 60.0, None, "car")] * (len(activities) - 1))


def _all_blocks(summary):
    return [block for classes in summary["subpopulations"].values() for block in classes.values()]


def test_pt_as_walk_trips_are_banded_by_straight_line_distance_and_only_those_above_the_threshold_are_long():
    plans = [_pt_as_walk_plan(str(index), distance)
             for index, distance in enumerate((500.0, 1500.0, 3000.0, 7000.0, 12000.0))]
    frozen = _summarise(plans)["subpopulations"][C.RESIDENTS][C.FROZEN]
    assert frozen["pt_as_walk_by_distance_band"] == {"0_1km": 1, "1_2km": 1, "2_5km": 1, "5_10km": 1,
                                                     "10km_plus": 1}
    assert frozen["n_pt_as_walk"] == 5 and frozen["n_pt_as_walk_long"] == 2
    assert frozen["share_pt_as_walk"] == 1.0 and frozen["share_pt_as_walk_long"] == pytest.approx(2 / 5)


def test_a_pt_as_walk_trip_exactly_at_the_distance_threshold_is_not_long():
    frozen = _summarise([_pt_as_walk_plan("edge", 5000.0)])["subpopulations"][C.RESIDENTS][C.FROZEN]
    assert frozen["n_pt_as_walk"] == 1 and frozen["n_pt_as_walk_long"] == 0
    assert frozen["share_pt_as_walk_long"] == 0.0
    assert frozen["pt_as_walk_by_distance_band"]["5_10km"] == 1   # half-open bands: the edge belongs upward


def test_pt_as_walk_distance_runs_from_the_first_leg_origin_to_the_last_leg_destination_of_the_trip():
    # Two walk legs joined by an interaction stop 3 km out: the trip is 6 km straight line from end to end.
    plan = _plan("t", [("outside", 8 * 3600.0, 0, 0), ("pt interaction", 8 * 3600.0 + 60, 3000, 0),
                       ("work", None, 6000, 0)],
                 [("walk", 60.0, None, "pt"), ("walk", 60.0, None, "pt")])
    frozen = _summarise([plan])["subpopulations"][C.RESIDENTS][C.FROZEN]
    assert frozen["n_pt_as_walk"] == 1 and frozen["n_pt_as_walk_long"] == 1
    assert frozen["pt_as_walk_by_distance_band"]["5_10km"] == 1


def test_a_block_always_carries_every_band_label_with_zeros():
    block = _summarise([_plan("e", [], [])])["subpopulations"][C.RESIDENTS][C.IN_REGION]
    assert block["pt_as_walk_by_distance_band"] == dict.fromkeys(
        ("0_1km", "1_2km", "2_5km", "5_10km", "10km_plus"), 0)
    assert block["non_monotonic_by_step_band"] == dict.fromkeys(
        ("0_1min", "1_5min", "5_15min", "15_60min", "60min_plus"), 0)
    assert (block["n_pt_as_walk_long"], block["share_pt_as_walk_long"]) == (0, 0.0)
    assert (block["n_plans_non_monotonic_large"], block["share_plans_non_monotonic_large"]) == (0, 0.0)


def test_non_monotonic_plans_are_banded_by_backward_step_and_only_those_above_the_threshold_are_large():
    plans = [_backward_step_plan(str(index), 36000.0 + step, 36000.0)
             for index, step in enumerate((30.0, 120.0, 600.0, 1200.0, 4000.0))]
    block = _summarise(plans)["subpopulations"][C.RESIDENTS][C.IN_REGION]
    assert block["non_monotonic_by_step_band"] == {"0_1min": 1, "1_5min": 1, "5_15min": 1, "15_60min": 1,
                                                   "60min_plus": 1}
    assert block["n_plans_non_monotonic"] == 5 and block["n_plans_non_monotonic_large"] == 2
    assert block["share_plans_non_monotonic"] == 1.0
    assert block["share_plans_non_monotonic_large"] == pytest.approx(2 / 5)


def test_the_backward_step_of_a_plan_is_its_largest_drop_below_an_earlier_end_and_exactly_the_threshold_is_not_large():
    # Backward pairs of 100 s and 1000 s (and a forward pair): the plan counts once, with the 1000 s step.
    multi = _backward_step_plan("multi", 36000.0, 35900.0, 36500.0, 35500.0)
    # Two chained 600 s pairs: the last end lies 1200 s before the first, so the step is 1200 s, not 600 s (#586).
    chained = _backward_step_plan("chained", 36000.0, 35400.0, 34800.0)
    edge = _backward_step_plan("edge", 36900.0, 36000.0)   # exactly 900 s
    block = _summarise([multi, chained, edge])["subpopulations"][C.RESIDENTS][C.IN_REGION]
    assert block["n_plans_non_monotonic"] == 3
    assert block["non_monotonic_by_step_band"]["15_60min"] == 3   # 900 s is in [900, 3600)
    assert block["non_monotonic_by_step_band"]["0_1min"] == 0     # not the 100 s step of the multi plan
    assert block["non_monotonic_by_step_band"]["5_15min"] == 0    # not the 600 s pairs of the chained plan
    assert block["n_plans_non_monotonic_large"] == 2               # 1000 s and 1200 s; 900 s is not above 900 s


def test_summary_thresholds_carry_the_two_new_bound_thresholds():
    thresholds = _summarise([])["thresholds"]
    assert thresholds["pt_as_walk_bound_min_distance_m"] == 5000.0
    assert thresholds["non_monotonic_bound_min_step_s"] == 900.0


def test_short_pt_as_walk_trips_and_small_backward_steps_do_not_violate_the_threshold_but_long_and_large_do():
    h = 3600.0

    def frozen_step_plan(person_id, step):
        return _plan(person_id, [("outside", 10 * h + step, 0, 0), ("work", 10 * h, 1, 0), ("home", None, 0, 0)],
                     [("car", 60.0, None, "car"), ("car", 60.0, None, "car")])

    small = [_pt_as_walk_plan(f"s{index}", 1000.0) for index in range(10)] + [frozen_step_plan("f", 60.0)]
    summary = _summarise(small)
    frozen = summary["subpopulations"][C.RESIDENTS][C.FROZEN]
    assert frozen["share_pt_as_walk"] > 0.02 and frozen["share_plans_non_monotonic"] > 0.005
    assert C.find_threshold_violations(summary, 0.005, 0.02) == []

    large = [_pt_as_walk_plan(f"l{index}", 9000.0) for index in range(10)] + [frozen_step_plan("g", 2000.0)]
    assert set(C.find_threshold_violations(_summarise(large), 0.005, 0.02)) == {
        "share_pt_as_walk_long", "share_plans_non_monotonic_large"}


def test_run_writes_the_new_block_keys_and_thresholds_and_names_the_new_bound_keys(tmp_path):
    import json
    _write_run_inputs(tmp_path)
    C.run(_RunContext(tmp_path, fail_on_threshold=False), "bs_config.xml")
    written = json.loads((tmp_path / C.PLAN_CHECK_FILE_NAME).read_text(encoding="utf-8"))
    for block in _all_blocks(written):
        for key in ("pt_as_walk_by_distance_band", "n_pt_as_walk_long", "share_pt_as_walk_long",
                    "non_monotonic_by_step_band", "n_plans_non_monotonic_large",
                    "share_plans_non_monotonic_large"):
            assert key in block
    assert written["thresholds"]["pt_as_walk_bound_min_distance_m"] == 5000.0
    assert written["thresholds"]["non_monotonic_bound_min_step_s"] == 900.0
    assert "share_pt_as_walk" not in written["threshold"]["exceeded"]
    assert "share_plans_non_monotonic" not in written["threshold"]["exceeded"]


def test_band_edges_are_the_report_only_constants_of_the_spec():
    assert C.PT_AS_WALK_DISTANCE_BAND_EDGES_M == (1000.0, 2000.0, 5000.0, 10000.0)
    assert C.NON_MONOTONIC_STEP_BAND_EDGES_S == (60.0, 300.0, 900.0, 3600.0)


# --- pt-intended trips, the routingMode guard and the jar guard (eqasim-bs#465): 0 must not hide a stale jar ---

def _attributes(**attributes):
    items = "".join(f'<attribute name="{name}" class="java.lang.String">{value}</attribute>'
                    for name, value in attributes.items())
    return f"<attributes>{items}</attributes>"


# Realistic legs: MATSim's TripRouter gives every leg the REQUESTED routing mode and eqasim's fallback re-routes as
# walk, so a replacement leg carries routingMode walk plus the stamp walkFallbackFrom=pt.
PT_TRIPS_POPULATION = f"""<?xml version="1.0" encoding="utf-8"?>
<population>
  <person id="frozen_fallback">
    <plan selected="yes">
      <activity type="home" x="0" y="0" end_time="07:00:00"/>
      <leg mode="walk" dep_time="07:00:00" trav_time="00:10:00">{_attributes(routingMode="walk", walkFallbackFrom="pt")}</leg>
      <activity type="outside" x="100" y="0" end_time="17:00:00"/>
      <leg mode="car" dep_time="17:00:00" trav_time="00:10:00">{_attributes(routingMode="car")}</leg>
      <activity type="home" x="0" y="0"/>
    </plan>
  </person>
  <person id="frozen_served">
    <plan selected="yes">
      <activity type="home" x="0" y="0" end_time="07:00:00"/>
      <leg mode="walk" dep_time="07:00:00" trav_time="00:05:00">{_attributes(routingMode="pt")}</leg>
      <activity type="pt interaction" x="10" y="0" end_time="07:05:00"/>
      <leg mode="pt" dep_time="07:05:00" trav_time="00:20:00">{_attributes(routingMode="pt")}</leg>
      <activity type="pt interaction" x="90" y="0" end_time="07:25:00"/>
      <leg mode="walk" dep_time="07:25:00" trav_time="00:05:00">{_attributes(routingMode="pt")}</leg>
      <activity type="outside" x="100" y="0"/>
    </plan>
  </person>
  <person id="in_region_fallback">
    <plan selected="yes">
      <activity type="home" x="0" y="0" end_time="08:00:00"/>
      <leg mode="walk" dep_time="08:00:00" trav_time="00:10:00">{_attributes(routingMode="walk", walkFallbackFrom="pt")}</leg>
      <activity type="work" x="100" y="0"/>
    </plan>
  </person>
</population>
"""


def test_pt_as_walk_is_counted_per_trip_in_the_class_of_the_trip_so_numerator_and_denominator_share_a_block(tmp_path):
    path = tmp_path / "pt_trips.xml"
    path.write_text(PT_TRIPS_POPULATION, encoding="utf-8")
    summary = _summarise(C.iter_person_plans(path))
    frozen = summary["subpopulations"][C.RESIDENTS][C.FROZEN]
    in_region = summary["subpopulations"][C.RESIDENTS][C.IN_REGION]
    # frozen: the stamped fallback trip (routingMode walk, stamp pt) and the served three-leg trip; the car trip is none.
    assert (frozen["n_pt_trips"], frozen["n_pt_as_walk"]) == (2, 1)
    assert frozen["share_pt_as_walk"] == pytest.approx(1 / 2)
    assert (in_region["n_pt_trips"], in_region["n_pt_as_walk"]) == (1, 1) and in_region["share_pt_as_walk"] == 1.0
    assert frozen["pt_as_walk_by_hour"] == {"7": 1} and in_region["pt_as_walk_by_hour"] == {"8": 1}
    assert summary["n_legs_with_routing_mode"] > 0


def test_a_population_without_any_routing_mode_warns_that_the_pt_trip_counts_are_meaningless(population_path, caplog):
    summary = _summarise(C.iter_person_plans(population_path))     # POPULATION: pt and stamped legs, no routingMode
    assert summary["n_legs_with_routing_mode"] == 0 and summary["n_pt_or_stamped_legs"] > 0
    with caplog.at_level("WARNING"):
        C._log_summary(summary)
    assert any(r.levelname == "WARNING" and "routingMode" in r.getMessage() and "meaningless" in r.getMessage()
               for r in caplog.records)


def test_no_routing_mode_warning_when_routing_modes_are_present_or_there_is_no_pt(tmp_path, caplog):
    path = tmp_path / "pt_trips.xml"
    path.write_text(PT_TRIPS_POPULATION, encoding="utf-8")
    car_only = _plan("c", [("home", 0.0, 0, 0), ("work", None, 1, 0)], [("car", 60.0, None, None)])
    with caplog.at_level("WARNING"):
        C._log_summary(_summarise(C.iter_person_plans(path)))
        C._log_summary(_summarise([car_only]))
    assert not [r for r in caplog.records if r.levelname == "WARNING"]


def test_require_walk_fallback_stamp_passes_on_a_jar_whose_plan_router_holds_the_attribute_and_logs_once(tmp_path, caplog):
    jar = _write_stub_jar(tmp_path / "ok.jar")
    with caplog.at_level("INFO"):
        C.require_walk_fallback_stamp(jar)
    assert len([r for r in caplog.records if r.levelname == "INFO" and "stamps the walk fallback" in r.getMessage()]) == 1


def _not_a_zip(path):
    path.write_bytes(b"not a zip")
    return path


@pytest.mark.parametrize("make_jar, error, match", [
    (lambda p: _write_stub_jar(p, b"\xca\xfe\xba\xbe no stamp here"), RuntimeError, "predates the walk fallback stamp"),
    (lambda p: _write_stub_jar(p, entry="org/eqasim/core/Other.class"), RuntimeError, "does not contain"),
    (lambda p: p.parent / "absent.jar", FileNotFoundError, "does not exist"),
    (_not_a_zip, RuntimeError, "zip archive"),
])
def test_require_walk_fallback_stamp_raises_naming_the_jar(tmp_path, make_jar, error, match):
    jar = make_jar(tmp_path / "bad.jar")
    with pytest.raises(error, match=match) as excinfo:
        C.require_walk_fallback_stamp(jar)
    assert str(jar) in str(excinfo.value)


@pytest.mark.parametrize("cutter_bytes, match", [
    (b"\xca\xfe\xba\xbe the cutter drops the stamp", "PlanCutter.class does not contain walkFallbackFrom"),
    (None, "does not contain org/eqasim/core/scenario/cutter/population/PlanCutter.class"),
])
def test_require_walk_fallback_stamp_raises_on_a_jar_whose_cutter_drops_the_stamp(tmp_path, cutter_bytes, match):
    # eqasim-bs#487: a jar whose PlanRouter stamps but whose PlanCutter rebuilds the cut trips without the stamp
    # lets the plan check count only the fallbacks of the cutter's own routing; the cutter class must hold the
    # attribute name too (javac inlines the PlanRouter constant into its constant pool).
    jar = _write_stub_jar(tmp_path / "old_cutter.jar", cutter_bytes=cutter_bytes)
    with pytest.raises(RuntimeError, match=match) as excinfo:
        C.require_walk_fallback_stamp(jar)
    assert str(jar) in str(excinfo.value) and "#487" in str(excinfo.value)


def test_a_stale_jar_message_names_the_fix_and_the_issue(tmp_path):
    jar = _write_stub_jar(tmp_path / "stale.jar", b"no stamp")
    with pytest.raises(RuntimeError) as excinfo:
        C.require_walk_fallback_stamp(jar)
    assert "eqasim_source_path" in str(excinfo.value) and "#465" in str(excinfo.value)


def test_run_raises_before_writing_the_report_when_the_jar_predates_the_stamp(tmp_path):
    _write_run_inputs(tmp_path)
    context = _RunContext(tmp_path, fail_on_threshold=False)
    _write_stub_jar(tmp_path / "runtime" / JAR_NAME, b"no stamp")
    with pytest.raises(RuntimeError, match="predates the walk fallback stamp"):
        C.run(context, "bs_config.xml")
    assert not (tmp_path / C.PLAN_CHECK_FILE_NAME).exists()
