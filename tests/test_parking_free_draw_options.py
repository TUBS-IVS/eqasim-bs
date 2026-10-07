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
                                     {"03103": None}],
                         ids=["int_key", "int_value", "blank_value", "blank_key", "none_value"])
def test_proxy_keys_and_values_must_be_class_names_as_text(mapping):
    # An unquoted 03103 in YAML is an integer, never the county key: the message says so.
    with pytest.raises(ValueError, match="parking_free_share_proxy_classes.*text"):
        options.require_proxy_classes(mapping)


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
