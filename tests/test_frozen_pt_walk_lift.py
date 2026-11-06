"""Lifting the long pt-as-walk trips of frozen resident tours to car_passenger in prepare (eqasim-bs#575, ADR-0189).

API under test: ``lift_frozen_pt_walks(source_path, target_path, *, min_distance_m, warn_share) -> dict`` with the
report keys ``n_persons`` (residents examined), ``n_pt_trips_frozen`` (pt-intended trips in frozen resident tours),
``n_long_pt_as_walk_before`` and ``n_lifted``.
"""
import gzip
import logging
import xml.etree.ElementTree as ET

import pytest

from braunschweig.matsim.simulation import frozen_pt_walk_lift as L

HEADER = ('<?xml version="1.0" encoding="utf-8"?>\n'
          '<!DOCTYPE population SYSTEM "http://www.matsim.org/files/dtd/population_v6.dtd">\n')
STAMP = '<attributes><attribute name="walkFallbackFrom" class="java.lang.String">pt</attribute></attributes>'
ROUTING_PT = '<attributes><attribute name="routingMode" class="java.lang.String">pt</attribute></attributes>'
ROUTING_WALK = '<attributes><attribute name="routingMode" class="java.lang.String">walk</attribute></attributes>'
MIN_DISTANCE_M = 5000.0


def _interaction(x):
    return f'<activity type="pt interaction" x="{x}" y="0" max_dur="00:00:00"/>'


def _trip(kind, x_from, x_to):
    """The legs (and interaction activities) between the leisure and the work activity, departing 09:00."""
    if kind == "stamp":
        return (f'<leg mode="walk" dep_time="09:00:00" trav_time="01:40:00">{STAMP}'
                f'<route type="generic" start_link="a" end_link="b" trav_time="01:40:00" distance="9000"/></leg>')
    if kind == "pt_routed_walks":
        return (f'<leg mode="walk" dep_time="09:00:00" trav_time="00:10:00">{ROUTING_PT}</leg>'
                f'{_interaction(x_from + 100)}'
                f'<leg mode="walk" dep_time="09:10:00" trav_time="01:30:00">{ROUTING_PT}</leg>')
    if kind == "real_pt":
        return (f'<leg mode="walk" dep_time="09:00:00" trav_time="00:10:00">{ROUTING_PT}</leg>'
                f'{_interaction(x_from + 100)}'
                f'<leg mode="pt" dep_time="09:10:00" trav_time="00:30:00">{ROUTING_PT}</leg>'
                f'{_interaction(x_to - 100)}'
                f'<leg mode="walk" dep_time="09:40:00" trav_time="00:10:00">{ROUTING_PT}</leg>')
    if kind == "plain_walk":
        return f'<leg mode="walk" dep_time="09:00:00" trav_time="01:40:00">{ROUTING_WALK}</leg>'
    raise ValueError(kind)


def _person(pid, kind, distance_m, *, frozen=True, cut_mode="pt", subpopulation=None, extra_attributes=""):
    """home -> leisure -> (trip under test) -> work -> [cut by an outside/outside pair] -> home."""
    attributes = extra_attributes
    if subpopulation:
        attributes += f'<attribute name="subpopulation" class="java.lang.String">{subpopulation}</attribute>'
    person_attributes = f"<attributes>{attributes}</attributes>" if attributes else ""
    x_leisure, x_work = 1000.0, 1000.0 + distance_m
    if frozen:
        tail = (f'<leg mode="{cut_mode}" dep_time="17:00:00" trav_time="01:00:00"/>'
                '<activity type="outside" x="30000" y="0" end_time="18:00:00"/>'
                f'<leg mode="{cut_mode}" dep_time="18:00:00" trav_time="00:10:00"/>'
                '<activity type="outside" x="30000" y="100" end_time="19:00:00"/>'
                f'<leg mode="{cut_mode}" dep_time="19:00:00" trav_time="01:00:00"/>')
    else:
        tail = '<leg mode="car" dep_time="17:00:00" trav_time="00:30:00"/>'
    return (f'<person id="{pid}">{person_attributes}<plan selected="yes">'
            '<activity type="home" x="0" y="0" end_time="07:00:00"/>'
            '<leg mode="car" dep_time="07:00:00" trav_time="00:10:00"/>'
            f'<activity type="leisure" x="{x_leisure}" y="0" end_time="09:00:00"/>'
            f'{_trip(kind, x_leisure, x_work)}'
            f'<activity type="work" x="{x_work}" y="0" end_time="17:00:00"/>'
            f'{tail}<activity type="home" x="0" y="0"/></plan></person>')


def _write(path, *persons):
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        handle.write(HEADER + "<population>\n" + "\n".join(persons) + "\n</population>\n")
    return path


def _canon(element):
    return (element.tag, tuple(sorted(element.attrib.items())), (element.text or "").strip(),
            tuple(_canon(child) for child in element))


def _persons(path):
    with gzip.open(path, "rb") as handle:
        return {p.get("id"): p for p in ET.parse(handle).getroot().findall("person")}


def _plan_signature(path):
    return {pid: _canon(person) for pid, person in _persons(path).items()}


def _plan_elements(path, pid):
    return list(_persons(path)[pid].find("plan"))


def _attribute(element, name):
    attributes = element.find("attributes")
    if attributes is None:
        return None
    return next((a.text for a in attributes.findall("attribute") if a.get("name") == name), None)


def _lift(tmp_path, *persons, warn_share=1.0, name="out.xml.gz"):
    source = _write(tmp_path / "in.xml.gz", *persons)
    target = tmp_path / name
    report = L.lift_frozen_pt_walks(source, target, min_distance_m=MIN_DISTANCE_M, warn_share=warn_share)
    return source, target, report


@pytest.mark.parametrize("kind", ["stamp", "pt_routed_walks"])
def test_a_long_pt_as_walk_trip_of_a_frozen_tour_becomes_one_car_passenger_leg(tmp_path, kind):
    """PRIMARY path; ``stamp`` = walkFallbackFrom stamp, ``pt_routed_walks`` = routingMode pt without the stamp."""
    source, target, report = _lift(tmp_path, _person("p", kind, 8000.0))
    before, after = _plan_elements(source, "p"), _plan_elements(target, "p")

    legs = [e for e in after if e.tag == "leg"]
    assert [leg.get("mode") for leg in legs] == ["car", "car_passenger", "pt", "pt", "pt"]
    lifted = legs[1]
    assert lifted.get("dep_time") == "09:00:00"
    assert lifted.get("trav_time") is None and lifted.find("route") is None
    assert _attribute(lifted, "routingMode") == "car_passenger"
    assert _attribute(lifted, "liftedFrom") == "pt"

    activities_before = [(e.get("type"), e.get("end_time"), e.get("x")) for e in before if e.tag == "activity"]
    activities_after = [(e.get("type"), e.get("end_time"), e.get("x")) for e in after if e.tag == "activity"]
    assert activities_after == [a for a in activities_before if a[0] != "pt interaction"]
    assert "pt interaction" not in [a[0] for a in activities_after]
    assert report["n_lifted"] == 1
    assert report["n_long_pt_as_walk_before"] == 1


@pytest.mark.parametrize("case", [
    pytest.param(dict(kind="stamp", distance_m=8000.0, frozen=False), id="tour_not_frozen"),
    pytest.param(dict(kind="stamp", distance_m=MIN_DISTANCE_M), id="distance_at_threshold_is_not_above"),
    pytest.param(dict(kind="real_pt", distance_m=8000.0), id="real_pt_leg"),
    pytest.param(dict(kind="plain_walk", distance_m=8000.0), id="non_pt_walk"),
    pytest.param(dict(kind="stamp", distance_m=8000.0, subpopulation="freight"), id="freight"),
    pytest.param(dict(kind="stamp", distance_m=8000.0, subpopulation="incommuter"), id="incommuter"),
])
def test_trips_outside_the_rule_are_left_untouched(tmp_path, case):
    case = dict(case)
    source, target, report = _lift(tmp_path, _person("p", case.pop("kind"), case.pop("distance_m"), **case))
    assert report["n_lifted"] == 0
    assert _plan_signature(target) == _plan_signature(source)


def test_the_lift_is_idempotent(tmp_path):
    _, first, report = _lift(tmp_path, _person("p", "stamp", 8000.0), _person("q", "real_pt", 8000.0))
    assert report["n_lifted"] == 1
    second = tmp_path / "second.xml.gz"
    again = L.lift_frozen_pt_walks(first, second, min_distance_m=MIN_DISTANCE_M, warn_share=1.0)
    assert again["n_lifted"] == 0
    assert _plan_signature(second) == _plan_signature(first)


def _report_population():
    return [_person("lift_a", "stamp", 8000.0, cut_mode="car"), _person("lift_b", "pt_routed_walks", 9000.0, cut_mode="car"),
            _person("served", "real_pt", 8000.0, cut_mode="car"), _person("short", "stamp", 3000.0, cut_mode="car")]


def test_report_counts_and_the_warn_share(tmp_path, caplog):
    with caplog.at_level(logging.INFO):
        _, _, report = _lift(tmp_path, *_report_population(), warn_share=0.6)
    assert (report["n_persons"], report["n_pt_trips_frozen"], report["n_long_pt_as_walk_before"],
            report["n_lifted"]) == (4, 4, 2, 2)
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]   # 2 of 4 = 0.5 <= 0.6
    assert [r for r in caplog.records if r.levelno == logging.INFO and "frozen_pt_walk_lift" in r.getMessage()]

    caplog.clear()
    with caplog.at_level(logging.INFO):
        _lift(tmp_path, *_report_population(), warn_share=0.4, name="again.xml.gz")
    assert [r for r in caplog.records if r.levelno == logging.WARNING]       # 0.5 > 0.4


def test_the_output_stays_a_valid_matsim_population(tmp_path):
    attributes = '<attribute name="age" class="java.lang.Integer">42</attribute>'
    source, target, _ = _lift(tmp_path, _person("res", "stamp", 8000.0, extra_attributes=attributes),
                              _person("freight_1", "stamp", 8000.0, subpopulation="freight"))
    with gzip.open(target, "rt", encoding="utf-8") as handle:
        text = handle.read()
    assert text.startswith("<?xml")
    assert '<!DOCTYPE population SYSTEM "http://www.matsim.org/files/dtd/population_v6.dtd">' in text
    persons = _persons(target)
    assert set(persons) == {"res", "freight_1"}
    assert _attribute(persons["res"], "age") == "42"
    assert _attribute(persons["freight_1"], "subpopulation") == "freight"
    assert _canon(persons["freight_1"]) == _canon(_persons(source)["freight_1"])
