"""The prepare stage declares the plan-check keys, hashes the module and runs the check last (eqasim-bs#443)."""
import pytest

from braunschweig.matsim import initial_mode_choice as I
from braunschweig.matsim.simulation import loop_mode_rule as L
from braunschweig.matsim.simulation import plan_check as C
from braunschweig.matsim.simulation import prepare

LOOP_KEY = L.KEY_ENABLED


class _RecordingContext:
    def __init__(self, values=None):
        self.calls, self.stages, self._values = {}, [], values or {}

    def config(self, key, default=None, **kwargs):
        self.calls.setdefault(key, default)  # the first call declares the key and its default
        return self._values.get(key, default)

    def stage(self, name, **kwargs):
        self.stages.append(name)


def test_configure_declares_the_plan_check_keys():
    context = _RecordingContext({"cordon_enabled": False, "freight_enabled": False})
    prepare.configure(context)
    for key, default in ((C.KEY_ENABLED, C.DEFAULT_ENABLED), (C.KEY_WALK_MAX_DISTANCE_M, C.DEFAULT_WALK_MAX_DISTANCE_M),
                         (C.KEY_BICYCLE_MAX_DISTANCE_M, C.DEFAULT_BICYCLE_MAX_DISTANCE_M),
                         (C.KEY_LEG_MAX_DURATION_S, C.DEFAULT_LEG_MAX_DURATION_S),
                         (C.KEY_MAX_FROZEN_DEFECT_SHARE, C.DEFAULT_MAX_FROZEN_DEFECT_SHARE),
                         (C.KEY_MAX_FROZEN_PT_AS_WALK_SHARE, C.DEFAULT_MAX_FROZEN_PT_AS_WALK_SHARE),
                         (C.KEY_PT_AS_WALK_BOUND_MIN_DISTANCE_M, C.DEFAULT_PT_AS_WALK_BOUND_MIN_DISTANCE_M),
                         (C.KEY_NON_MONOTONIC_BOUND_MIN_LATENESS_S, C.DEFAULT_NON_MONOTONIC_BOUND_MIN_LATENESS_S),
                         (C.KEY_FAIL_ON_THRESHOLD, C.DEFAULT_FAIL_ON_THRESHOLD)):
        assert context.calls[key] == default
    assert C.DEFAULT_FAIL_ON_THRESHOLD is False   # an uncalibrated check must not abort a run (ADR-0146)
    # plan_check.run resolves the eqasim jar through this stage for the walk fallback stamp guard (#465).
    assert "matsim.runtime.eqasim" in context.stages


def test_configure_declares_the_transit_cut_keys_and_stage_only_under_cordon():
    from braunschweig.data.transit import cut_schedule as CS
    context = _RecordingContext({"cordon_enabled": True, "freight_enabled": False})
    prepare.configure(context)
    assert context.calls[CS.KEY_TRANSIT_CUT_MODE] == CS.DEFAULT_TRANSIT_CUT_MODE
    assert context.calls[CS.KEY_HUB_STOP_NAMES] == CS.DEFAULT_HUB_STOP_NAMES
    assert "braunschweig.data.transit.cut_schedule" in context.stages
    off = _RecordingContext({"cordon_enabled": False, "freight_enabled": False})
    prepare.configure(off)
    assert CS.KEY_TRANSIT_CUT_MODE not in off.calls
    assert "braunschweig.data.transit.cut_schedule" not in off.stages


def test_configure_rejects_an_unknown_mode_and_hubs_with_extent_mode():
    import pytest
    from braunschweig.data.transit import cut_schedule as CS
    base = {"cordon_enabled": True, "freight_enabled": False}
    with pytest.raises(ValueError):
        prepare.configure(_RecordingContext({**base, CS.KEY_TRANSIT_CUT_MODE: "bogus"}))
    with pytest.raises(ValueError, match="hubs"):
        prepare.configure(_RecordingContext({**base, CS.KEY_TRANSIT_CUT_MODE: "extent",
                                             CS.KEY_HUB_STOP_NAMES: ["Hannover Hbf"]}))


def test_plan_check_module_is_part_of_the_prepare_token():
    assert C in prepare._HELPER_MODULES


def test_execute_runs_the_plan_check_last_and_only_when_enabled(monkeypatch):
    seen = []
    monkeypatch.setattr(prepare.delegate, "execute", lambda context: "config.xml")
    monkeypatch.setattr(prepare, "_attach_vrb_tariff_zones", lambda context: None)
    monkeypatch.setattr(prepare, "_inject_freight", lambda context, result: seen.append("freight"))
    monkeypatch.setattr(prepare, "_summarise_final_schedule", lambda context: {})
    monkeypatch.setattr(prepare, "_write_vrb_fare_inputs", lambda context, result, summary: seen.append("fares"))
    monkeypatch.setattr(C, "run", lambda context, result: seen.append(("check", result)))

    class _Context:
        def __init__(self, enabled):
            self._values = {prepare.VRB_FARES_KEY: True, "cordon_enabled": False, "freight_enabled": True,
                            prepare.PARKING_KEY: False, C.KEY_ENABLED: enabled,
                            I.KEY_ENABLED: False,
                   "braunschweig.frozen_pt_walk_lift.enabled": False,
                   LOOP_KEY: False}

        def config(self, key):
            return self._values[key]

    assert prepare.execute(_Context(True)) == "config.xml"
    assert seen == ["freight", "fares", ("check", "config.xml")]
    seen.clear()
    assert prepare.execute(_Context(False)) == "config.xml"
    assert seen == ["freight", "fares"]


def test_execute_writes_the_parking_inputs_before_the_plan_check(monkeypatch):
    """The check reads the FINAL config, so the parking module and the tariff model (written by the parking step) come first."""
    seen = []
    monkeypatch.setattr(prepare.delegate, "execute", lambda context: "config.xml")
    monkeypatch.setattr(prepare, "_attach_vrb_tariff_zones", lambda context: None)
    monkeypatch.setattr(prepare, "_summarise_final_schedule", lambda context: {})
    monkeypatch.setattr(prepare, "_write_vrb_fare_inputs", lambda context, result, summary: seen.append("fares"))
    monkeypatch.setattr(prepare, "_write_parking_inputs", lambda context, result: seen.append(("parking", result)))
    monkeypatch.setattr(C, "run", lambda context, result: seen.append(("check", result)))

    class _Context:
        _values = {prepare.VRB_FARES_KEY: True, "cordon_enabled": False, "freight_enabled": False,
                   prepare.PARKING_KEY: True, C.KEY_ENABLED: True, I.KEY_ENABLED: False,
                   "braunschweig.frozen_pt_walk_lift.enabled": False,
                   LOOP_KEY: False}

        def config(self, key):
            return self._values[key]

    assert prepare.execute(_Context()) == "config.xml"
    assert seen == ["fares", ("parking", "config.xml"), ("check", "config.xml")]


def test_execute_skips_the_walk_fallback_stamp_guard_when_the_plan_check_is_disabled(monkeypatch):
    """A disabled check must not resolve the eqasim jar nor demand its stamp (#465)."""
    seen = []
    monkeypatch.setattr(prepare.delegate, "execute", lambda context: "config.xml")
    monkeypatch.setattr(prepare, "_attach_vrb_tariff_zones", lambda context: None)
    monkeypatch.setattr(prepare, "_summarise_final_schedule", lambda context: {})
    monkeypatch.setattr(prepare, "_write_vrb_fare_inputs", lambda context, result, summary: None)
    monkeypatch.setattr(C, "run", lambda context, result: seen.append("run"))
    monkeypatch.setattr(C, "require_walk_fallback_stamp", lambda jar_path: seen.append("stamp"))

    class _Context:
        _values = {prepare.VRB_FARES_KEY: True, "cordon_enabled": False, "freight_enabled": False,
                   prepare.PARKING_KEY: False, C.KEY_ENABLED: False, I.KEY_ENABLED: False,
                   "braunschweig.frozen_pt_walk_lift.enabled": False,
                   LOOP_KEY: False}

        def config(self, key):
            return self._values[key]

        def stage(self, name, **kwargs):
            seen.append(("stage", name))

    assert prepare.execute(_Context()) == "config.xml"
    assert seen == []


def test_execute_runs_the_frozen_pt_walk_lift_after_the_cut_and_before_the_plan_check(monkeypatch):
    """The lift repairs the cut population (eqasim-bs#575, ADR-0189), so it runs after the cut and before the check."""
    from braunschweig.matsim.simulation import frozen_pt_walk_lift as lift
    seen = []
    monkeypatch.setattr(prepare.delegate, "execute", lambda context: "config.xml")
    monkeypatch.setattr(prepare, "_verify_gate_facility_links", lambda context: None)
    monkeypatch.setattr(prepare, "_attach_vrb_tariff_zones", lambda context: None)
    monkeypatch.setattr(prepare, "_cut_to_cordon", lambda context: seen.append("cut") or "cut_config.xml")
    monkeypatch.setattr(prepare, "_add_incommuter_strategies", lambda context, result: None)
    monkeypatch.setattr(prepare, "_summarise_final_schedule", lambda context: {})
    monkeypatch.setattr(prepare, "_write_vrb_fare_inputs", lambda context, result, summary: None)
    monkeypatch.setattr(lift, "run", lambda context, result: seen.append(("lift", result)) or {"share_lifted": 0.5})
    monkeypatch.setattr(C, "run", lambda context, result, lift_report=None: seen.append(("check", result, lift_report)))

    class _Context:
        def __init__(self, enabled):
            self._values = {prepare.VRB_FARES_KEY: True, "cordon_enabled": True, "freight_enabled": False,
                            prepare.PARKING_KEY: False, C.KEY_ENABLED: True, I.KEY_ENABLED: False,
                            "braunschweig.frozen_pt_walk_lift.enabled": enabled,
                            LOOP_KEY: False}

        def config(self, key):
            return self._values[key]

    assert prepare.execute(_Context(True)) == "cut_config.xml"
    assert seen == ["cut", ("lift", "cut_config.xml"), ("check", "cut_config.xml", {"share_lifted": 0.5})]
    seen.clear()
    assert prepare.execute(_Context(False)) == "cut_config.xml"
    assert seen == ["cut", ("check", "cut_config.xml", None)]


def test_configure_declares_the_loop_mode_rule_switch_off_by_default():
    context = _RecordingContext({"cordon_enabled": False, "freight_enabled": False})
    prepare.configure(context)
    assert context.calls[LOOP_KEY] is False
    assert L in prepare._HELPER_MODULES


@pytest.mark.parametrize("enabled", [True, False])
def test_execute_selects_the_loop_mode_rule_in_the_final_config_before_the_initial_mode_choice(
        monkeypatch, tmp_path, enabled):
    """On: jar check, then the config edit, then the mode choice. Off: no jar read, no edit (#679, ADR-0207)."""
    seen = []
    monkeypatch.setattr(prepare.delegate, "execute", lambda context: "uncut_config.xml")
    monkeypatch.setattr(prepare, "_attach_vrb_tariff_zones", lambda context: None)
    monkeypatch.setattr(prepare, "_summarise_final_schedule", lambda context: {})
    monkeypatch.setattr(prepare, "_write_vrb_fare_inputs", lambda context, result, summary: seen.append("fares"))
    monkeypatch.setattr(prepare, "_write_parking_inputs", lambda context, result: seen.append("parking"))
    monkeypatch.setattr(L, "require_constraint_in_jar", lambda jar: seen.append(("jar", jar)))
    monkeypatch.setattr(L, "apply", lambda config: seen.append(("apply", str(config))) or ("a", "a,b"))
    monkeypatch.setattr(L, "write_report", lambda *args: seen.append("report"))
    monkeypatch.setattr(I, "run", lambda context, result, excluded: seen.append("initial") or {})
    monkeypatch.setattr(C, "run", lambda context, result: seen.append("check"))

    class _Context:
        _values = {prepare.VRB_FARES_KEY: True, "cordon_enabled": False, "freight_enabled": False,
                   prepare.PARKING_KEY: True, C.KEY_ENABLED: True, I.KEY_ENABLED: True,
                   "braunschweig.frozen_pt_walk_lift.enabled": False, LOOP_KEY: enabled}

        def config(self, key):
            return self._values[key]

        def path(self, stage=None):
            assert enabled, "the stage path is only needed to find the jar and the config when the rule is on"
            return str(tmp_path / (stage or "prepare"))

        def stage(self, name):
            assert enabled, "the jar is not even resolved when the rule is off"
            assert name == "matsim.runtime.eqasim"
            return "eqasim.jar"

    assert prepare.execute(_Context()) == "uncut_config.xml"
    if enabled:
        jar = "%s/%s" % (tmp_path / "matsim.runtime.eqasim", "eqasim.jar")
        config = str(tmp_path / "prepare" / "uncut_config.xml")
        assert seen == ["fares", "parking", ("jar", jar), ("apply", config), "report", "initial", "check"]
    else:
        assert seen == ["fares", "parking", "initial", "check"]


_DMC_CONFIG = ('<?xml version="1.0" encoding="utf-8"?>\n<config>\n\t<module name="DiscreteModeChoice">\n'
               '\t\t<param name="tripConstraints" value="PassengerConstraint, OutsideConstraint, TransitWalk" />\n'
               '\t</module>\n</config>\n')


@pytest.mark.parametrize("with_class", [True, False])
def test_select_loop_mode_rule_end_to_end_with_a_stub_jar(monkeypatch, tmp_path, with_class):
    """Real jar check, real config edit, real report: the config carries the constraint and the report says so; a jar
    without the class raises and leaves the config bytes and the stage folder as they were."""
    import json
    import zipfile
    stage = tmp_path / "prepare"
    jar_directory = tmp_path / "matsim.runtime.eqasim"
    stage.mkdir()
    jar_directory.mkdir()
    (stage / "config.xml").write_bytes(_DMC_CONFIG.encode("utf-8"))
    with zipfile.ZipFile(jar_directory / "eqasim.jar", "w") as jar:
        jar.writestr(L.CONSTRAINT_CLASS if with_class else "other/Class.class", b"\xca\xfe\xba\xbe")
    before_files = sorted(path.name for path in stage.iterdir())

    class _Context:
        def path(self, name=None):
            return str(jar_directory if name == "matsim.runtime.eqasim" else stage)

        def stage(self, name):
            return "eqasim.jar"

    if not with_class:
        with pytest.raises(RuntimeError, match="SameFacilityLeisureTripConstraint"):
            prepare._select_loop_mode_rule(_Context(), "config.xml")
        assert (stage / "config.xml").read_bytes() == _DMC_CONFIG.encode("utf-8")
        assert sorted(path.name for path in stage.iterdir()) == before_files
        return
    prepare._select_loop_mode_rule(_Context(), "config.xml")
    expected = "PassengerConstraint, OutsideConstraint, TransitWalk, SameFacilityLeisureNonMotorised"
    assert (stage / "config.xml").read_bytes().decode("utf-8") == _DMC_CONFIG.replace(
        'TransitWalk"', 'TransitWalk, SameFacilityLeisureNonMotorised"')
    report = json.loads((stage / L.REPORT_FILE_NAME).read_text(encoding="utf-8"))
    assert report == {
        "constraint": "SameFacilityLeisureNonMotorised", "constraint_class_checked": L.CONSTRAINT_CLASS,
        "jar_file_name": "eqasim.jar", "module": "DiscreteModeChoice", "parameter": "tripConstraints",
        "trip_constraints_before": "PassengerConstraint, OutsideConstraint, TransitWalk",
        "trip_constraints_after": expected, "already_selected": False}
    # A second run (a re-prepare of an already selected config) changes nothing and says so.
    prepare._select_loop_mode_rule(_Context(), "config.xml")
    again = json.loads((stage / L.REPORT_FILE_NAME).read_text(encoding="utf-8"))
    assert again["already_selected"] is True
    assert again["trip_constraints_before"] == again["trip_constraints_after"] == expected


@pytest.mark.parametrize("cordon, freight, expected", [
    (True, True, ("incommuter", "student_incommuter", "freight")),
    (False, True, ("freight",)),
    (True, False, ("incommuter", "student_incommuter")),
    (False, False, ()),
])
def test_initial_mode_choice_excludes_incommuters_and_injected_freight(cordon, freight, expected):
    """Freight legs have no routing mode, so the outside constraint would fail on them (found on #678's replay)."""
    context = _RecordingContext({"cordon_enabled": cordon, "freight_enabled": freight})
    assert tuple(prepare._initial_mode_choice_excluded_subpopulations(context)) == expected
