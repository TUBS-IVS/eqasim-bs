"""Every agent chooses its modes once at the first replanning (eqasim-bs#465, mechanism 4).

The discrete mode choice runs with weight 0.05 against KeepLastSelected 0.95 in every iteration, so after the 86
replanning rounds of run i465-wpe-smoke-1pct-2026-10-23 about 0.95**86 = 1.2 % of the residents had never chosen
again and kept the synthesis mode; 34 of the 49 in-region persons with a walk trip over 5 km in the final plans had
an unchanged plan. ``braunschweig.matsim.initial_mode_choice`` adds a second DiscreteModeChoice strategy with a very
high weight that MATSim disables after iteration 1 (``disableAfterIteration`` 1): in the first replanning almost
every agent chooses, afterwards the 5 % of the loop apply again.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from braunschweig.matsim import initial_mode_choice as I
from braunschweig.matsim.simulation import plan_check as C
from braunschweig.matsim.simulation import prepare

_STRATEGY = """		<parameterset type="strategysettings" >
			<param name="disableAfterIteration" value="-1" />
			<param name="executionPath" value="null" />
			<param name="strategyName" value="{name}" />
			<param name="subpopulation" value="{subpopulation}" />
			<param name="weight" value="{weight}" />
		</parameterset>
"""
_CONFIG = ("""<?xml version="1.0" ?>
<!DOCTYPE config SYSTEM "http://www.matsim.org/files/dtd/config_v2.dtd">
<config>
	<module name="replanning" >
		<param name="maxAgentPlanMemorySize" value="1" />
""" + _STRATEGY.format(name="DiscreteModeChoice", subpopulation="null", weight="0.05")
           + _STRATEGY.format(name="KeepLastSelected", subpopulation="null", weight="0.95")
           + _STRATEGY.format(name="KeepLastSelected", subpopulation="freight", weight="0.95")
           + _STRATEGY.format(name="ReRoute", subpopulation="freight", weight="0.05")
           + """	</module>
	<module name="qsim" >
		<param name="endTime" value="30:00:00" />
	</module>
</config>
""")


def _strategies(path):
    module = next(m for m in ET.parse(path).getroot().findall("module") if m.get("name") == "replanning")
    return [{p.get("name"): p.get("value") for p in s.findall("param")} for s in module.findall("parameterset")]


def test_the_first_replanning_strategy_is_added_for_the_mode_choice_subpopulation_only(tmp_path):
    path = tmp_path / "config.xml"
    path.write_text(_CONFIG, encoding="utf-8")

    report = I.add_initial_mode_choice(path, weight=1.0e6)

    strategies = _strategies(path)
    added = [s for s in strategies if s["disableAfterIteration"] == "1"]
    assert added == [{"disableAfterIteration": "1", "executionPath": "null", "strategyName": "DiscreteModeChoice",
                      "subpopulation": "null", "weight": "1000000.0"}]
    assert len(strategies) == 5                                # the four of the loop are kept as they were
    # share of the agents of the subpopulation that choose in iteration 1: (1e6 + 0.05) / (1e6 + 1.0)
    assert report["null"] == pytest.approx((1.0e6 + 0.05) / (1.0e6 + 1.0))
    assert "freight" not in report
    text = path.read_text(encoding="utf-8")
    assert text.startswith('<?xml version="1.0" ?>\n<!DOCTYPE config')   # the DOCTYPE survives
    assert '<module name="qsim" >' in text


def test_adding_twice_changes_nothing(tmp_path):
    path = tmp_path / "config.xml"
    path.write_text(_CONFIG, encoding="utf-8")
    I.add_initial_mode_choice(path, weight=1.0e6)
    first = path.read_bytes()
    I.add_initial_mode_choice(path, weight=1.0e6)
    assert path.read_bytes() == first


def test_a_config_without_the_mode_choice_in_the_loop_is_refused(tmp_path):
    path = tmp_path / "config.xml"
    path.write_text(_CONFIG.replace('value="DiscreteModeChoice"', 'value="ChangeTripMode"'), encoding="utf-8")
    with pytest.raises(ValueError, match="DiscreteModeChoice"):
        I.add_initial_mode_choice(path, weight=1.0e6)


@pytest.mark.parametrize("weight", [0.0, -1.0, float("nan"), float("inf"), True, "1e6"])
def test_an_invalid_weight_is_refused(tmp_path, weight):
    path = tmp_path / "config.xml"
    path.write_text(_CONFIG, encoding="utf-8")
    with pytest.raises(ValueError, match="weight"):
        I.add_initial_mode_choice(path, weight=weight)


@pytest.mark.parametrize("enabled", [True, False])
def test_prepare_adds_the_strategy_before_the_plan_check_only_when_enabled(monkeypatch, enabled):
    seen = []
    monkeypatch.setattr(prepare.delegate, "execute", lambda context: "config.xml")
    monkeypatch.setattr(prepare, "_attach_vrb_tariff_zones", lambda context: None)
    monkeypatch.setattr(prepare, "_summarise_final_schedule", lambda context: {})
    monkeypatch.setattr(prepare, "_write_vrb_fare_inputs", lambda context, result, summary: seen.append("fares"))
    monkeypatch.setattr(I, "add_initial_mode_choice",
                        lambda path, weight: seen.append(("initial", str(path).replace("\\", "/"), weight)) or {})
    monkeypatch.setattr(C, "run", lambda context, result: seen.append("check"))

    class _Context:
        _values = {prepare.VRB_FARES_KEY: True, "cordon_enabled": False, "freight_enabled": False,
                   prepare.PARKING_KEY: False, C.KEY_ENABLED: True, I.KEY_ENABLED: enabled, I.KEY_WEIGHT: 1.0e6}

        def config(self, key):
            return self._values[key]

        def path(self):
            return "stage"

    assert prepare.execute(_Context()) == "config.xml"
    expected = ["fares", ("initial", "stage/config.xml", 1.0e6), "check"] if enabled else ["fares", "check"]
    assert seen == expected
