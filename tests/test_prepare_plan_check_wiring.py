"""The prepare stage declares the plan-check keys, hashes the module and runs the check last (eqasim-bs#443)."""
import pytest

from braunschweig.matsim import initial_mode_choice as I
from braunschweig.matsim.simulation import frozen_pt_walk_lift as L
from braunschweig.matsim.simulation import mode_choice_reach as R
from braunschweig.matsim.simulation import plan_check as C
from braunschweig.matsim.simulation import prepare

#: The values of the keys ``execute`` reads for the reach settings (#678), for the contexts that do not test them.
_REACH_VALUES = {R.KEY_LOOP_ISOLATE: R.DEFAULT_LOOP_ISOLATE, R.KEY_LOOP_MAX_TOUR_LENGTH: R.DEFAULT_LOOP_MAX_TOUR_LENGTH,
                 R.KEY_INITIAL_ISOLATE: R.DEFAULT_INITIAL_ISOLATE,
                 R.KEY_INITIAL_MAX_TOUR_LENGTH: R.DEFAULT_INITIAL_MAX_TOUR_LENGTH}


_REAL_APPLY = R.apply


@pytest.fixture(autouse=True)
def _no_config_edit(monkeypatch):
    # Stubs ``apply`` for the whole file; test_execute_with_todays_values_does_not_edit_the_config runs the real edit.
    """The older tests use a config name that is no file: the reach edit is a no-op there (tested below)."""
    monkeypatch.setattr(R, "apply", lambda config_path, settings: {})


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
            self._values = {**_REACH_VALUES, prepare.VRB_FARES_KEY: True, "cordon_enabled": False, "freight_enabled": True,
                            prepare.PARKING_KEY: False, C.KEY_ENABLED: enabled,
                            I.KEY_ENABLED: False,
                   "braunschweig.frozen_pt_walk_lift.enabled": False}

        def config(self, key):
            return self._values[key]

        def path(self, name=None):
            return "."

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
        _values = {**_REACH_VALUES, prepare.VRB_FARES_KEY: True, "cordon_enabled": False, "freight_enabled": False,
                   prepare.PARKING_KEY: True, C.KEY_ENABLED: True, I.KEY_ENABLED: False,
                   "braunschweig.frozen_pt_walk_lift.enabled": False}

        def config(self, key):
            return self._values[key]

        def path(self, name=None):
            return "."

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
        _values = {**_REACH_VALUES, prepare.VRB_FARES_KEY: True, "cordon_enabled": False, "freight_enabled": False,
                   prepare.PARKING_KEY: False, C.KEY_ENABLED: False, I.KEY_ENABLED: False,
                   "braunschweig.frozen_pt_walk_lift.enabled": False}

        def config(self, key):
            return self._values[key]

        def path(self, name=None):
            return "."

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
            self._values = {**_REACH_VALUES, prepare.VRB_FARES_KEY: True, "cordon_enabled": True, "freight_enabled": False,
                            prepare.PARKING_KEY: False, C.KEY_ENABLED: True, I.KEY_ENABLED: False,
                            "braunschweig.frozen_pt_walk_lift.enabled": enabled}

        def config(self, key):
            return self._values[key]

        def path(self, name=None):
            return "."

    assert prepare.execute(_Context(True)) == "cut_config.xml"
    assert seen == ["cut", ("lift", "cut_config.xml"), ("check", "cut_config.xml", {"share_lifted": 0.5})]
    seen.clear()
    assert prepare.execute(_Context(False)) == "cut_config.xml"
    assert seen == ["cut", ("check", "cut_config.xml", None)]


def test_configure_declares_the_reach_and_repair_keys():
    """Every key the steps read is declared without a condition (synpp: execute reads only declared keys)."""
    for cordon in (False, True):
        context = _RecordingContext({"cordon_enabled": cordon, "freight_enabled": False})
        prepare.configure(context)
        for key, default in ((R.KEY_LOOP_ISOLATE, False), (R.KEY_LOOP_MAX_TOUR_LENGTH, 11),
                             (R.KEY_INITIAL_ISOLATE, True), (R.KEY_INITIAL_MAX_TOUR_LENGTH, 13),
                             (L.KEY_ALL_MODES, True), (L.KEY_MAX_SHARE_OTHER_MODES, 0.002)):
            assert context.calls[key] == default


class _ReachContext:
    """An execute context with the stage directory ``tmp_path`` and the eqasim jar ``tmp_path/eqasim.jar``."""

    def __init__(self, tmp_path, **overrides):
        self._tmp_path = tmp_path
        self._values = {**_REACH_VALUES, prepare.VRB_FARES_KEY: True, "cordon_enabled": False,
                        "freight_enabled": False, prepare.PARKING_KEY: False, C.KEY_ENABLED: True,
                        I.KEY_ENABLED: True, L.KEY_ENABLED: True, **overrides}

    def config(self, key):
        return self._values[key]

    def path(self, name=None):
        return str(self._tmp_path)

    def stage(self, name, **kwargs):
        return "eqasim.jar"


def _record_steps(monkeypatch, seen, apply=None):
    monkeypatch.setattr(prepare.delegate, "execute", lambda context: "config.xml")
    monkeypatch.setattr(prepare, "_attach_vrb_tariff_zones", lambda context: None)
    monkeypatch.setattr(prepare, "_summarise_final_schedule", lambda context: {})
    monkeypatch.setattr(prepare, "_write_vrb_fare_inputs", lambda context, result, summary: None)
    monkeypatch.setattr(R, "apply", apply or (lambda config_path, settings: seen.append(("apply", settings)) or {}))
    monkeypatch.setattr(R, "require_border_vehicle_constraint", lambda jar_path: seen.append(("jar", str(jar_path))))
    monkeypatch.setattr(I, "run", lambda context, result, excluded=(), reach=None: seen.append(("initial", reach)))
    monkeypatch.setattr(L, "run", lambda context, result: seen.append("lift") or {"share_lifted": 0.0})
    monkeypatch.setattr(C, "run", lambda context, result, lift_report=None: seen.append("plan_check"))


def test_execute_applies_the_loop_settings_before_the_initial_mode_choice(monkeypatch, tmp_path):
    seen = []
    _record_steps(monkeypatch, seen)
    context = _ReachContext(tmp_path, **{R.KEY_LOOP_ISOLATE: True, R.KEY_LOOP_MAX_TOUR_LENGTH: "9",
                                         R.KEY_INITIAL_ISOLATE: True})
    assert prepare.execute(context) == "config.xml"
    jar = str(tmp_path) + "/eqasim.jar"
    assert seen == [("jar", jar), ("apply", R.ReachSettings(True, 9)),
                    ("initial", R.ReachSettings(True, R.DEFAULT_INITIAL_MAX_TOUR_LENGTH)), "lift", "plan_check"]


@pytest.mark.parametrize("loop, initial_enabled, initial, checked", [
    (False, True, False, False), (True, True, False, True), (False, True, True, True),
    (True, False, False, True), (False, False, True, False)])
def test_execute_checks_the_jar_only_when_a_finer_rule_is_selected(monkeypatch, tmp_path, loop, initial_enabled,
                                                                   initial, checked):
    """The initial value counts only while the initial mode choice runs; a jar without the class stops prepare
    before the config is edited and before the Java run."""
    from tests.test_config_modules import DMC_CONFIG, _config
    seen = []
    config = _config(tmp_path, DMC_CONFIG)
    _record_steps(monkeypatch, seen, apply=_REAL_APPLY)
    monkeypatch.setattr(prepare.delegate, "execute", lambda context: config.name)
    context = _ReachContext(tmp_path, **{R.KEY_LOOP_ISOLATE: loop, R.KEY_INITIAL_ISOLATE: initial,
                                         I.KEY_ENABLED: initial_enabled})
    prepare.execute(context)
    assert any(step[0] == "jar" for step in seen if isinstance(step, tuple)) is checked

    def refuse(jar_path):
        raise RuntimeError("no border constraint")
    seen.clear()
    config.write_bytes(DMC_CONFIG.encode("utf-8"))  # the first run edited it (the loop length 11 differs from 6)
    monkeypatch.setattr(R, "require_border_vehicle_constraint", refuse)
    if checked:
        with pytest.raises(RuntimeError, match="no border constraint"):
            prepare.execute(context)
        assert config.read_bytes() == DMC_CONFIG.encode("utf-8")
        assert not any(step[0] == "initial" for step in seen if isinstance(step, tuple))  # before the Java run


def test_execute_with_todays_values_does_not_edit_the_config(monkeypatch, tmp_path):
    """The stock values (finder off, length 6) leave the written config byte-identical; the real edit runs."""
    from tests.test_config_modules import DMC_CONFIG, _config
    seen = []
    config = _config(tmp_path, DMC_CONFIG)
    _record_steps(monkeypatch, seen, apply=_REAL_APPLY)
    monkeypatch.setattr(prepare.delegate, "execute", lambda context: config.name)
    context = _ReachContext(tmp_path, **{R.KEY_LOOP_ISOLATE: False, R.KEY_LOOP_MAX_TOUR_LENGTH: 6})
    assert prepare.execute(context) == config.name
    assert config.read_bytes() == DMC_CONFIG.encode("utf-8")
    context = _ReachContext(tmp_path, **{R.KEY_LOOP_ISOLATE: False, R.KEY_LOOP_MAX_TOUR_LENGTH: 11})
    prepare.execute(context)  # a positive control: another value does edit it
    assert config.read_bytes() != DMC_CONFIG.encode("utf-8")
