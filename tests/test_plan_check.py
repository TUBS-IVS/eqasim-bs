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
    assert [p.person_id for p in plans] == ["1", "2", "freight_1"]
    assert [p.subpopulation for p in plans] == [C.RESIDENTS, C.RESIDENTS, "freight"]
    assert plans[0].activities[1] == ("outside", 31 * 3600.0, 20000.0, 0.0)
    assert plans[0].activities[2] == ("home", None, 0.0, 0.0)
    assert plans[0].legs == [("walk", 5.5 * 3600.0), ("walk", 5.5 * 3600.0)]


def test_leg_tour_frozen_marks_the_legs_of_tours_touching_outside():
    activities = [("home", 1.0, 0, 0), ("outside", 2.0, 1, 0), ("home", 3.0, 0, 0), ("shop", 4.0, 0, 1),
                  ("home", None, 0, 0)]
    assert C.leg_tour_frozen(activities) == [True, True, False, False]


def test_summarise_counts_late_non_monotonic_long_and_slow_legs_per_subpopulation_and_class(population_path):
    summary = C.summarise(C.iter_person_plans(population_path), simulation_end_s=30 * 3600.0,
                          walk_max_distance_m=5000.0, bicycle_max_distance_m=30000.0, leg_max_duration_s=10800.0)
    residents = summary["subpopulations"][C.RESIDENTS]
    frozen, inside = residents["frozen"], residents["in_region"]
    assert frozen["n_persons"] == 1 and inside["n_persons"] == 1
    assert frozen["n_plans_late"] == 1 and inside["n_plans_late"] == 0
    assert frozen["n_plans_non_monotonic"] == 0 and inside["n_plans_non_monotonic"] == 1   # person 2: 18:00 then 17:00
    assert frozen["n_walk_legs_over"] == 2 and inside["n_walk_legs_over"] == 2               # person 2's two 6 km walks
    assert frozen["n_legs_over_duration"] == 2 and inside["n_legs_over_duration"] == 0
    assert inside["n_bicycle_legs_over"] == 0
    assert frozen["share_plans_late"] == 1.0
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
        C.check_thresholds(summary, 0.005)
    summary["subpopulations"][C.RESIDENTS]["frozen"]["share_plans_late"] = 0.004
    C.check_thresholds(summary, 0.005)


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
