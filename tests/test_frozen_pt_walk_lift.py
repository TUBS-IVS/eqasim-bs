"""Lifting the long pt-as-walk trips of frozen resident tours to car_passenger in prepare (eqasim-bs#575, ADR-0189).

API under test: ``lift_frozen_pt_walks(source_path, target_path, *, min_distance_m, max_share) -> dict`` with the
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


def _lift(tmp_path, *persons, max_share=1.0, name="out.xml.gz"):
    source = _write(tmp_path / "in.xml.gz", *persons)
    target = tmp_path / name
    report = L.lift_frozen_pt_walks(source, target, min_distance_m=MIN_DISTANCE_M, max_share=max_share)
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
    assert (report["n_lifted_stamped"], report["n_lifted_routing_mode_only"]) == ((1, 0) if kind == "stamp" else (0, 1))


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
    again = L.lift_frozen_pt_walks(first, second, min_distance_m=MIN_DISTANCE_M, max_share=1.0)
    assert again["n_lifted"] == 0
    assert _plan_signature(second) == _plan_signature(first)


def _report_population():
    return [_person("lift_a", "stamp", 8000.0, cut_mode="car"), _person("lift_b", "pt_routed_walks", 9000.0, cut_mode="car"),
            _person("served", "real_pt", 8000.0, cut_mode="car"), _person("short", "stamp", 3000.0, cut_mode="car")]


def test_report_counts_and_the_plan_check_bound(tmp_path, caplog):
    with caplog.at_level(logging.INFO):
        _, _, report = _lift(tmp_path, *_report_population(), max_share=0.6)
    # 4 pt-intended trips; 3 realised as walk at any distance (the short one included); 2 of them over the bound.
    assert (report["n_persons"], report["n_pt_trips_frozen"], report["n_pt_as_walk_frozen"],
            report["n_lifted"]) == (4, 4, 3, 2)
    assert (report["max_share"], report["bound_exceeded"]) == (0.6, False)
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]   # 2 of 4 = 0.5 <= 0.6
    assert [r for r in caplog.records if r.levelno == logging.INFO and "frozen_pt_walk_lift" in r.getMessage()]

    caplog.clear()
    with caplog.at_level(logging.INFO):
        _, _, exceeded = _lift(tmp_path, *_report_population(), max_share=0.4, name="again.xml.gz")
    assert exceeded["bound_exceeded"] is True
    assert [r for r in caplog.records if r.levelno == logging.ERROR]         # 0.5 > 0.4


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


def test_only_the_selected_plan_is_lifted(tmp_path):
    person = _person("p", "stamp", 8000.0)
    unselected = person[person.index("<plan "):person.index("</plan>") + len("</plan>")].replace(
        'selected="yes"', 'selected="no"')
    two_plans = person.replace("</person>", unselected + "</person>")
    source, target, report = _lift(tmp_path, two_plans)
    before, after = _persons(source)["p"].findall("plan"), _persons(target)["p"].findall("plan")
    assert report["n_lifted"] == 1
    assert [leg.get("mode") for leg in after[0].findall("leg")][1] == "car_passenger"
    assert _canon(after[1]) == _canon(before[1])


def test_an_activity_without_coordinates_fails_early_naming_person_and_activity(tmp_path):
    broken = _person("p_no_xy", "stamp", 8000.0).replace('<activity type="work" x="9000.0" y="0"',
                                                         '<activity type="work" link="l1"')
    assert 'link="l1"' in broken
    with pytest.raises(ValueError, match=r"person p_no_xy: activity \d+ \(work\) has no x/y"):
        _lift(tmp_path, broken)


class _RunContext:
    def __init__(self, directory):
        self._directory = directory

    def path(self):
        return str(self._directory)

    def config(self, key):
        return {"braunschweig.plan_check.pt_as_walk_bound_min_distance_m": MIN_DISTANCE_M,
                "braunschweig.plan_check.max_frozen_pt_as_walk_share": 1.0}[key]


def _stage_directory(tmp_path, *persons):
    _write(tmp_path / "pop.xml.gz", *persons)
    (tmp_path / "config.xml").write_text(
        '<config><module name="plans"><param name="inputPlansFile" value="pop.xml.gz"/></module></config>',
        encoding="utf-8")
    return tmp_path


def test_run_replaces_the_population_and_a_failure_leaves_original_and_no_temporary_file(tmp_path):
    stage = tmp_path / "stage"
    stage.mkdir()
    _stage_directory(stage, _person("p", "stamp", 8000.0))
    report = L.run(_RunContext(stage), "config.xml")
    assert report["n_lifted"] == 1 and (stage / "frozen_pt_walk_lift.json").exists()
    assert [leg.get("mode") for leg in _plan_elements(stage / "pop.xml.gz", "p") if leg.tag == "leg"][1] == "car_passenger"

    bad = tmp_path / "bad"
    bad.mkdir()
    corrupt = _person("q", "stamp", 8000.0).replace('<activity type="work" x="9000.0" y="0"', '<activity type="work" link="l"')
    _stage_directory(bad, corrupt)
    original = (bad / "pop.xml.gz").read_bytes()
    with pytest.raises(ValueError):
        L.run(_RunContext(bad), "config.xml")
    assert (bad / "pop.xml.gz").read_bytes() == original
    assert sorted(p.name for p in bad.iterdir()) == ["config.xml", "pop.xml.gz"]


def test_the_encoding_of_the_xml_declaration_is_kept(tmp_path):
    source = tmp_path / "latin.xml"
    source.write_bytes((HEADER.replace("utf-8", "iso-8859-1") + "<population>\n"
                        + _person("p", "stamp", 8000.0, extra_attributes='<attribute name="n" class="java.lang.String">M\u00fcller</attribute>')
                        + "\n</population>\n").encode("iso-8859-1"))
    target = tmp_path / "out.xml"
    L.lift_frozen_pt_walks(source, target, min_distance_m=MIN_DISTANCE_M, max_share=1.0)
    text = target.read_bytes().decode("iso-8859-1")
    assert 'encoding="iso-8859-1"' in text and "M\u00fcller" in text
