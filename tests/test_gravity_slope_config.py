"""Regression tests for the per-RegioStaR-7 gravity slope override config.

Guards against the synpp ``flatten()`` empty-dict pitfall that broke
``braunschweig.gravity.model`` whenever a config omitted
``gravity_slope_by_regiostar7`` (e.g. ``config_dryrun_braunschweig.yml``).

Root cause: synpp's ``flatten()`` recurses into dict-valued config options
and emits one leaf per nested key. An *empty* dict therefore produces no
leaves at all, so the option vanishes from the flattened ``required_config``
and ``ExecuteContext.config(option)`` raises "Config option ... is not
requested" at execute time. A ``None`` default is not a mapping, survives
flattening, and is treated as "no overrides" by
``_build_origin_slope_vector`` (scalar slope for every origin).
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from synpp.pipeline import ConfigurationContext, has_config_value  # noqa: E402

OPTION = "gravity_slope_by_regiostar7"


def _required_config_for(base_config: dict, default) -> dict:
    """Mimic synpp's configure phase for a single option and return the
    resulting ``required_config`` dict (what ExecuteContext later reads)."""
    context = ConfigurationContext(base_config)
    context.config(OPTION, default)
    return context.required_config


def test_none_default_survives_flatten_when_option_absent():
    # The dry-run case: config omits the override -> the configure() default
    # applies. With a None default the option stays requestable at execute.
    required = _required_config_for({"gravity_slope": -0.065}, None)
    assert has_config_value(OPTION, required), (
        "None default must remain readable in ExecuteContext.config()"
    )


def test_empty_dict_default_is_dropped_by_flatten():
    # Documents the exact regression the None default fixes: an empty-dict
    # default disappears from the flattened required_config.
    required = _required_config_for({"gravity_slope": -0.065}, {})
    assert not has_config_value(OPTION, required), (
        "empty-dict default is expected to vanish from flatten() -- this is "
        "why the configure() default must be None, not {}"
    )


def test_nonempty_override_in_config_survives_flatten():
    # The production case: a non-empty dict survives flatten() via its leaves.
    base = {"gravity_slope": -0.065, OPTION: {72: -0.0333, 74: -0.0782}}
    required = _required_config_for(base, None)
    assert has_config_value(OPTION, required)


def test_model_configure_registers_none_default():
    """The shipped stage must declare the override with a None default."""
    from braunschweig.gravity import model

    recorded: dict = {}

    class _StubContext:
        def config(self, option, default=None):
            recorded[option] = default
            return default

        def stage(self, name, config=None):
            return None

    model.configure(_StubContext())
    assert OPTION in recorded, "configure() must request the override option"
    assert recorded[OPTION] is None, (
        "configure() default must be None (empty dict regresses, see "
        "test_empty_dict_default_is_dropped_by_flatten)"
    )


def test_build_origin_slope_vector_falls_back_to_scalar_without_overrides():
    """Both None and {} must yield the scalar slope for every origin."""
    from braunschweig.gravity.model import _build_origin_slope_vector

    municipalities = ["031010000000", "031580210000", "031530000000"]
    for overrides in (None, {}):
        vector = _build_origin_slope_vector(municipalities, -0.065, overrides, None)
        assert vector.shape == (len(municipalities),)
        assert np.allclose(vector, -0.065)
