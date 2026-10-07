"""Validation of the two options of the free-parking draw (issue #436, parking cost zones v2, spec Amendment D5, D6).

``parking_free_share_proxy_classes`` (workplace class -> SrV class whose share the draw uses) and
``parking_campus_free_share`` (share of the campus work/education persons who park free) are checked by
``braunschweig.parking.free_draw_options``, which the plans-writer wrapper calls at configure time and the draw calls
again on whatever it is handed. The draw itself is tested in ``tests/test_parking_attach.py``.
"""
from __future__ import annotations

import pytest

from braunschweig.parking import free_draw_options as options

CLASSES = ("bs_zentrum", "bs_innenbereich", "03102", "03103")


def test_the_config_keys_are_the_documented_names():
    assert options.KEY_PROXY_CLASSES == "parking_free_share_proxy_classes"
    assert options.KEY_CAMPUS_FREE_SHARE == "parking_campus_free_share"


def test_the_code_defaults_switch_both_options_off():
    # The production values live in configs/base_bs.yml, never in the code (the defaults reproduce the v1 draw).
    assert options.default_proxy_classes() == {}
    assert options.DEFAULT_CAMPUS_FREE_SHARE == 0.0


def test_the_default_mapping_is_a_fresh_object_every_time():
    options.default_proxy_classes()["03103"] = "bs_zentrum"
    assert options.default_proxy_classes() == {}


def test_a_valid_mapping_is_returned_as_a_plain_copy():
    configured = {"03103": "bs_zentrum"}
    result = options.require_proxy_classes(configured)
    assert result == {"03103": "bs_zentrum"} and result is not configured


def test_the_empty_mapping_is_valid_and_restores_the_class_shares():
    assert options.require_proxy_classes({}) == {}


@pytest.mark.parametrize("value", [None, [("03103", "bs_zentrum")], "03103: bs_zentrum", 3],
                         ids=["none", "list", "text", "number"])
def test_a_proxy_mapping_must_be_a_mapping(value):
    with pytest.raises(ValueError, match="parking_free_share_proxy_classes.*mapping"):
        options.require_proxy_classes(value)


@pytest.mark.parametrize("mapping", [{3103: "bs_zentrum"}, {"03103": 7}, {"03103": ""}, {" ": "bs_zentrum"},
                                     {"03103": 1.5}],
                         ids=["int_key", "int_value", "blank_value", "blank_key", "float_value"])
def test_proxy_keys_and_values_must_be_class_names_as_text(mapping):
    # An unquoted 03103 in YAML is an integer, never the county key: the message says so.
    with pytest.raises(ValueError, match="parking_free_share_proxy_classes.*text"):
        options.require_proxy_classes(mapping)


def test_a_none_value_is_the_explicit_no_proxy_marker_and_is_kept():
    # R-4c-9: {"03103": null} survives the deep merge of a config overlay and means "own class share".
    assert options.require_proxy_classes({"03103": None}) == {"03103": None}
    assert options.require_proxy_classes({"03103": None, "03102": "bs_zentrum"}) == {
        "03103": None, "03102": "bs_zentrum"}


def test_a_none_marked_class_may_be_a_proxy_source_of_another_class():
    # 03103 uses its own share; 03102 reads the class row of 03103: resolved once, no chain.
    assert options.require_proxy_classes({"03103": None, "03102": "03103"}) == {"03103": None, "03102": "03103"}


def test_a_none_marker_cannot_hide_a_blank_key_or_a_chain():
    with pytest.raises(ValueError, match="text"):
        options.require_proxy_classes({"": None})
    with pytest.raises(ValueError, match="chain"):
        options.require_proxy_classes({"03103": "bs_zentrum", "bs_zentrum": "03102", "03102": None})


def test_a_self_mapping_is_rejected():
    with pytest.raises(ValueError, match="self-mapping.*03103"):
        options.require_proxy_classes({"03103": "03103"})


def test_a_chain_is_rejected():
    with pytest.raises(ValueError, match="chain.*03103.*bs_zentrum"):
        options.require_proxy_classes({"03103": "bs_zentrum", "bs_zentrum": "03102"})


def test_a_proxy_pair_that_closes_a_cycle_is_a_chain():
    with pytest.raises(ValueError, match="chain"):
        options.require_proxy_classes({"03103": "03102", "03102": "03103"})


def test_two_classes_may_share_one_source_class():
    assert options.require_proxy_classes({"03103": "bs_zentrum", "03102": "bs_zentrum"}) == {
        "03103": "bs_zentrum", "03102": "bs_zentrum"}


@pytest.mark.parametrize("mapping, unknown", [({"03199": "bs_zentrum"}, "03199"), ({"03103": "bs_nowhere"}, "bs_nowhere")],
                         ids=["unknown_key", "unknown_value"])
def test_proxy_keys_and_values_must_be_srv_class_rows(mapping, unknown):
    with pytest.raises(ValueError, match=f"{unknown}.*class row") as error:
        options.require_proxy_classes_in_table(mapping, CLASSES)
    assert "parking_free_share_proxy_classes" in str(error.value)


def test_the_table_check_covers_the_keys_of_none_entries_and_no_value_for_them():
    options.require_proxy_classes_in_table({"03103": None}, CLASSES)
    with pytest.raises(ValueError, match="03199.*class row"):
        options.require_proxy_classes_in_table({"03199": None}, CLASSES)


def test_a_mapping_inside_the_class_rows_passes_the_table_check():
    options.require_proxy_classes_in_table({"03103": "bs_zentrum"}, CLASSES)
    options.require_proxy_classes_in_table({}, CLASSES)


@pytest.mark.parametrize("value, expected", [(0.0, 0.0), (0.2, 0.2), (1.0, 1.0), (0, 0.0), (1, 1.0), (0.4, 0.4)])
def test_the_campus_free_share_accepts_the_closed_unit_interval(value, expected):
    result = options.require_campus_free_share(value)
    assert result == expected and isinstance(result, float)


@pytest.mark.parametrize("value", [-0.01, 1.01, 20, float("nan"), float("inf"), "0.2", True, False, None],
                         ids=["below", "above", "percent", "nan", "inf", "text", "true", "false", "none"])
def test_the_campus_free_share_rejects_everything_else(value):
    with pytest.raises(ValueError, match="parking_campus_free_share.*\\[0, 1\\]"):
        options.require_campus_free_share(value)


def _compose_with_overlay(tmp_path, overlay_mapping_yaml):
    """The REAL composition (``braunschweig.config_compose.compose``) of configs/base_bs.yml and a minimal overlay."""
    from pathlib import Path

    from braunschweig.config_compose import compose

    base = Path(__file__).resolve().parents[1] / "configs" / "base_bs.yml"
    overlay = tmp_path / "overlay.yml"
    overlay.write_text("working_directory: eqasim-data/cache_test\nrun:\n  - synthesis.output\nconfig:\n"
                       + overlay_mapping_yaml, encoding="utf-8")
    return compose(str(base), str(overlay))["config"]


def test_an_overlay_with_an_empty_mapping_does_not_remove_the_base_entry(tmp_path):
    """Documents the merge semantics (deep_merge merges nested mappings recursively): an overlay {} leaves the
    base proxy in place, which is why the no-proxy arm is written {"03103": null}."""
    config = _compose_with_overlay(tmp_path, "  parking_free_share_proxy_classes: {}\n")
    assert config[options.KEY_PROXY_CLASSES] == {"03103": "bs_zentrum"}


def test_an_overlay_with_the_null_marker_survives_the_composition_and_the_draw_uses_the_class_share(tmp_path):
    import numpy as np
    import pandas as pd

    from braunschweig.parking import attach

    config = _compose_with_overlay(tmp_path, '  parking_free_share_proxy_classes: {"03103": null}\n')
    assert config[options.KEY_PROXY_CLASSES] == {"03103": None}
    assert options.require_proxy_classes(config[options.KEY_PROXY_CLASSES]) == {"03103": None}
    tariffs = pd.DataFrame({"zone_id": ["z_wob"], "zone_type": ["street_paid"], "workplace_class": ["03103"]})
    shares = pd.DataFrame({"workplace_class": ["bs_zentrum", "03103", "total"], "level": ["class", "class", "total"],
                           "share_free_total": [0.3, 0.9, 0.8]})
    activities = pd.DataFrame({"person_id": np.repeat(np.arange(1, 1001), 2), "activity_index": [0, 1] * 1000,
                               "purpose": ["home", "work"] * 1000, "parking_zone": [np.nan, "z_wob"] * 1000})
    proxied = attach.draw_parking_free(activities, tariffs, shares, 1234, proxy_classes={"03103": "bs_zentrum"})
    marked = attach.draw_parking_free(activities, tariffs, shares, 1234,
                                      proxy_classes=config[options.KEY_PROXY_CLASSES])
    own = attach.draw_parking_free(activities, tariffs, shares, 1234)
    pd.testing.assert_frame_equal(marked, own)
    assert marked["parking_free"].sum() > proxied["parking_free"].sum() + 400  # 0.9 versus 0.3 of 1000 persons


def test_the_canonical_configuration_states_both_options_with_the_owner_values_and_validates_them():
    """configs/base_bs.yml carries the production values (the code defaults are OFF): the Wolfsburg proxy of
    ASSUMPTION A1-b and the campus share of ASSUMPTION C2, both valid under the configure-time checks."""
    from pathlib import Path

    import yaml

    base = Path(__file__).resolve().parents[1] / "configs" / "base_bs.yml"
    with open(base, encoding="utf-8") as stream:
        config = yaml.safe_load(stream)["config"]
    assert config[options.KEY_PROXY_CLASSES] == {"03103": "bs_zentrum"}
    assert config[options.KEY_CAMPUS_FREE_SHARE] == 0.2
    assert options.require_proxy_classes(config[options.KEY_PROXY_CLASSES]) == {"03103": "bs_zentrum"}
    assert options.require_campus_free_share(config[options.KEY_CAMPUS_FREE_SHARE]) == 0.2
