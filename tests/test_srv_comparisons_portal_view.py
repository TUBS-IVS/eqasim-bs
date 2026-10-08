"""The two SrV comparisons keep reading the pre-portal trips while the portal layer is on (eqasim-bs#442, R33).

``plan_structure_vs_srv`` and ``departure_time_vs_srv`` compare the synthetic day with SrV 2023, whose trip table
has no ``outside`` stays: a portal stay replaces the donor's far legs by two legs to the gate and would shift trip
counts, purposes and departure profiles against the unchanged reference (spec criterion 5). With
``braunschweig.portal.enabled`` true and the view ``final`` they therefore read
``braunschweig.synthesis.commute_day.trips_day_stage`` (the reporting-day trips the portal stage takes as its
input) instead of ``synthesis.population.trips.final`` (the portal-rewritten trips). With the flag off nothing
changes.
"""
import pytest

from braunschweig.analysis.synthesis import departure_time_vs_srv as departure_time
from braunschweig.analysis.synthesis import plan_structure_vs_srv as plan_structure
from braunschweig.synthesis.portal_trips import config_keys
from braunschweig.synthesis.portal_trips import stage as portal_stage

PRE_PORTAL_STAGE = "braunschweig.synthesis.commute_day.trips_day_stage"
FINAL_STAGE = "synthesis.population.trips.final"
PRE_ASSIGNMENT_STAGE = "synthesis.population.trips"

STAGES = [plan_structure, departure_time]
TRIPS_VIEW_KEYS = {plan_structure: plan_structure.KEY_TRIPS_VIEW, departure_time: departure_time.KEY_TRIPS_VIEW}


class _Recorder:
    """synpp ConfigurationContext stand-in recording the stage declarations and their aliases."""

    def __init__(self, config=None):
        self.stages = []
        self.aliases = {}
        self.config_keys = {}
        self._config = dict(config) if config else {}

    def stage(self, name, alias=None, **kwargs):
        self.stages.append(name)
        if alias is not None:
            self.aliases[alias] = name

    def config(self, key, *args, **kwargs):
        value = self._config.get(key, args[0] if args else None)
        self.config_keys[key] = value
        return value


def test_the_pre_portal_stage_name_has_one_home_and_is_the_input_of_the_portal_stage():
    assert config_keys.PRE_PORTAL_TRIPS_STAGE == PRE_PORTAL_STAGE == portal_stage.TRIPS_STAGE


@pytest.mark.parametrize("module", STAGES)
def test_the_final_view_reads_the_pre_portal_trips_while_the_portal_layer_is_on(module):
    recorder = _Recorder(config={config_keys.KEY_ENABLED: True})
    module.configure(recorder)
    assert recorder.aliases["trips"] == PRE_PORTAL_STAGE
    assert FINAL_STAGE not in recorder.stages and PRE_ASSIGNMENT_STAGE not in recorder.stages


@pytest.mark.parametrize("module", STAGES)
def test_the_final_view_reads_the_reporting_day_trips_with_the_portal_layer_off(module):
    recorder = _Recorder(config={config_keys.KEY_ENABLED: False})
    module.configure(recorder)
    assert recorder.aliases["trips"] == FINAL_STAGE
    assert PRE_PORTAL_STAGE not in recorder.stages


@pytest.mark.parametrize("module", STAGES)
def test_the_pre_assignment_view_is_unaffected_by_the_portal_flag(module):
    for enabled in (True, False):
        recorder = _Recorder(config={config_keys.KEY_ENABLED: enabled, TRIPS_VIEW_KEYS[module]: "pre_assignment"})
        module.configure(recorder)
        assert recorder.aliases["trips"] == PRE_ASSIGNMENT_STAGE
        assert PRE_PORTAL_STAGE not in recorder.stages and FINAL_STAGE not in recorder.stages


@pytest.mark.parametrize("module", STAGES)
def test_the_flag_is_declared_with_the_shared_default_in_every_view(module):
    for view in ("final", "pre_assignment"):
        recorder = _Recorder(config={TRIPS_VIEW_KEYS[module]: view})
        module.configure(recorder)
        assert config_keys.KEY_ENABLED in recorder.config_keys
        # unset in the config: the shared default applies (the same one every other reader declares)
        assert recorder.config_keys[config_keys.KEY_ENABLED] == config_keys.DEFAULT_ENABLED


@pytest.mark.parametrize("module", STAGES)
def test_the_portal_flag_key_is_the_single_home_key(module):
    assert module.KEY_PORTAL_ENABLED == config_keys.KEY_ENABLED
    assert module.DEFAULT_PORTAL_ENABLED == config_keys.DEFAULT_ENABLED
