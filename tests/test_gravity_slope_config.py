"""Regression tests for the per-RegioStaR-7 gravity slope override config.

Guards against the synpp ``flatten()`` empty-dict pitfall that broke
``braunschweig.gravity.model`` whenever a config omitted
``gravity_slope_by_regiostar7`` (e.g. ``configs/fixtures/config_dryrun_braunschweig.yml``).

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


# --- Fallback transparency (CLAUDE.md): per-RS7 primary vs scalar fallback ---


def _regiostar_frame(rows):
    """Build a minimal RegioStaR lookup frame with [commune_id, regiostar7]."""
    import pandas as pd

    return pd.DataFrame(rows, columns=["commune_id", "regiostar7"])


def test_origin_slope_all_overridden_no_fallback(capsys):
    """PRIMARY path: every origin has a per-RS7 override -> fallback count 0."""
    from braunschweig.gravity.model import _build_origin_slope_vector

    # commune_ids in 12-digit ARS form; AGS8 = ARS[0:5]+ARS[9:12].
    municipalities = ["031010000000", "031530000019"]
    df_regiostar = _regiostar_frame([
        ("03101000", 72),  # AGS8 of 031010000000
        ("03153019", 77),  # AGS8 of 031530000019
    ])
    overrides = {72: -0.0333, 77: -0.0900}

    vector = _build_origin_slope_vector(municipalities, -0.065, overrides, df_regiostar)

    assert np.isclose(vector[0], -0.0333)
    assert np.isclose(vector[1], -0.0900)
    # No origin used the scalar default fallback.
    assert not np.any(np.isclose(vector, -0.065))

    out = capsys.readouterr().out
    assert "primary (per-RS7 override) 2/2 (100.0%)" in out
    assert "fallback (scalar default=-0.065) 0/2 (0.0%)" in out
    assert "WARNING" not in out


def test_origin_slope_unmapped_rs7_is_counted_as_fallback(capsys):
    """FALLBACK path: an origin whose RS7 has no override falls back to scalar.

    Two of three origins lack an override (one RS7 not in the map, one not in
    the RegioStaR table), so the fallback share (66.7%) exceeds the 10%
    threshold and a WARNING is emitted.
    """
    from braunschweig.gravity.model import _build_origin_slope_vector

    municipalities = [
        "031010000000",  # AGS8 03101000, RS7 72 -> overridden (primary)
        "031530000019",  # AGS8 03153019, RS7 77 -> NOT in override map (fallback)
        "039999000000",  # AGS8 03999000, absent from RegioStaR table (fallback)
    ]
    df_regiostar = _regiostar_frame([
        ("03101000", 72),
        ("03153019", 77),
    ])
    overrides = {72: -0.0333}  # only RS7 72 has an override

    vector = _build_origin_slope_vector(municipalities, -0.065, overrides, df_regiostar)

    assert np.isclose(vector[0], -0.0333)          # primary
    assert np.isclose(vector[1], -0.065)           # fallback: RS7 not mapped
    assert np.isclose(vector[2], -0.065)           # fallback: no RS7 in table

    out = capsys.readouterr().out
    assert "primary (per-RS7 override) 1/3 (33.3%)" in out
    assert "fallback (scalar default=-0.065) 2/3 (66.7%)" in out
    assert "RS7 not in override map: 1" in out
    assert "no RS7 in table: 1" in out
    assert "WARNING" in out


def test_origin_slope_no_override_map_reports_inactive(capsys):
    """No override map -> scalar for all, reported as inactive (no WARNING)."""
    from braunschweig.gravity.model import _build_origin_slope_vector

    municipalities = ["031010000000", "031530000019"]
    vector = _build_origin_slope_vector(municipalities, -0.065, None, None)

    assert np.allclose(vector, -0.065)
    out = capsys.readouterr().out
    assert "per-RegioStaR slope inactive" in out
    assert "all 2/2 origins" in out
    assert "WARNING" not in out
