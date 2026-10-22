"""The prepare stage declares the plan-check keys, hashes the module and runs the check last (eqasim-bs#443)."""
from braunschweig.matsim.simulation import plan_check as C
from braunschweig.matsim.simulation import prepare


class _RecordingContext:
    def __init__(self, values=None):
        self.calls, self.stages, self._values = {}, [], values or {}

    def config(self, key, default=None, **kwargs):
        self.calls[key] = default
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
                         (C.KEY_FAIL_ON_THRESHOLD, C.DEFAULT_FAIL_ON_THRESHOLD)):
        assert context.calls[key] == default
    assert C.DEFAULT_FAIL_ON_THRESHOLD is False   # an uncalibrated check must not abort a run (ADR-0146)
    # plan_check.run resolves the eqasim jar through this stage for the walk fallback stamp guard (#465).
    assert "matsim.runtime.eqasim" in context.stages


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
                            prepare.PARKING_KEY: False, C.KEY_ENABLED: enabled}

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
                   prepare.PARKING_KEY: True, C.KEY_ENABLED: True}

        def config(self, key):
            return self._values[key]

    assert prepare.execute(_Context()) == "config.xml"
    assert seen == ["fares", ("parking", "config.xml"), ("check", "config.xml")]


def test_execute_skips_the_walk_fallback_stamp_guard_when_the_plan_check_is_disabled(monkeypatch):
    """A disabled check must not resolve the eqasim jar nor demand its stamp (#465)."""
    seen = []
    monkeypatch.setattr(prepare.delegate, "execute", lambda context: "config.xml")
    monkeypatch.setattr(prepare, "_attach_vrb_tariff_zones", lambda context: None)
    monkeypatch.setattr(prepare, "_write_vrb_fare_inputs", lambda context, result: None)
    monkeypatch.setattr(C, "run", lambda context, result: seen.append("run"))
    monkeypatch.setattr(C, "require_walk_fallback_stamp", lambda jar_path: seen.append("stamp"))

    class _Context:
        _values = {prepare.VRB_FARES_KEY: True, "cordon_enabled": False, "freight_enabled": False,
                   prepare.PARKING_KEY: False, C.KEY_ENABLED: False}

        def config(self, key):
            return self._values[key]

        def stage(self, name, **kwargs):
            seen.append(("stage", name))

    assert prepare.execute(_Context()) == "config.xml"
    assert seen == []
