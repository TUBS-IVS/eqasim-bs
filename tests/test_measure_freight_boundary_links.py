"""Unit tests of the boundary-link rule re-implemented in scripts/measure_freight_boundary_links.py (#444)."""
import importlib.util
import pathlib

import pytest

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "measure_freight_boundary_links.py"
spec = importlib.util.spec_from_file_location("measure_freight_boundary_links", SCRIPT)
measure = importlib.util.module_from_spec(spec)
spec.loader.exec_module(measure)

# Dual carriageway along x, eastbound at y=0 and westbound at y=20 (same layout as the Java test).
NODES = {"w": (0.0, 0.0), "e": (400.0, 0.0), "er": (400.0, 20.0), "wr": (0.0, 20.0)}
LINKS = {"eastbound": {"from": "w", "to": "e"}, "westbound": {"from": "er", "to": "wr"}}


def nearby(point):
    return sorted((measure.segment_distance_m(point, NODES[l["from"]], NODES[l["to"]]), link_id)
                  for link_id, l in LINKS.items())


def test_segment_distance_is_perpendicular_inside_and_to_the_endpoint_outside():
    assert measure.segment_distance_m((200.0, 7.0), (0.0, 0.0), (400.0, 0.0)) == pytest.approx(7.0)
    assert measure.segment_distance_m((403.0, 4.0), (0.0, 0.0), (400.0, 0.0)) == pytest.approx(5.0)


def test_rule_picks_the_carriageway_of_the_travel_direction_even_if_farther():
    point = (200.0, 15.0)  # 5 m from westbound, 15 m from eastbound
    assert measure.select_link((1.0, 0.0), nearby(point), NODES, LINKS, 300.0)[1] == "eastbound"
    assert measure.select_link((-1.0, 0.0), nearby(point), NODES, LINKS, 300.0)[1] == "westbound"


def test_rule_returns_none_beyond_the_maximum_distance():
    assert measure.select_link((1.0, 0.0), nearby((200.0, 500.0)), NODES, LINKS, 300.0) is None
