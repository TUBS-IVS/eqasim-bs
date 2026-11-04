"""Tests of the walk attribution in scripts/measure_incommuter_pt_walks.py (#528)."""
import gzip
import importlib.util
import pathlib

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "measure_incommuter_pt_walks.py"
spec = importlib.util.spec_from_file_location("measure_incommuter_pt_walks", SCRIPT)
measure = importlib.util.module_from_spec(spec)
spec.loader.exec_module(measure)


def _leg(mode, routing_mode, distance_m):
    return (f'<leg mode="{mode}" dep_time="07:00:00"><attributes>'
            f'<attribute name="routingMode" class="java.lang.String">{routing_mode}</attribute></attributes>'
            f'<route type="generic" distance="{distance_m}"></route></leg>')


def _person(person_id, facility, legs):
    activities = [f'<activity type="home" facility="home_{person_id}" end_time="07:00:00"/>',
                  f'<activity type="education" facility="{facility}" end_time="15:00:00"/>',
                  f'<activity type="home" facility="home_{person_id}"/>']
    body = activities[0] + legs[0] + activities[1] + legs[1] + activities[2]
    return f'<person id="{person_id}"><plan selected="yes">{body}</plan></person>'


def _write_population(path, persons):
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        handle.write("<population>" + "".join(persons) + "</population>")


def test_unserved_pt_trip_rewritten_to_walk_is_attributed_to_the_pt_commute(tmp_path, capsys):
    population = tmp_path / "population.xml.gz"
    _write_population(population, [
        # pt student whose return trip the router rewrote to a 30 km walk (routing mode walk)
        _person("1", "ic_edu_1", [_leg("pt", "pt", 30000.0), _leg("walk", "walk", 30000.0)]),
        # car student: no walk
        _person("2", "ic_edu_2", [_leg("car", "car", 30000.0), _leg("car", "car", 30000.0)]),
        # pt student with a walk below the 5 km bound: a walk, not a long one
        _person("4", "ic_edu_4", [_leg("pt", "pt", 30000.0), _leg("walk", "walk", 4000.0)]),
        # resident (no ic_edu_ facility): ignored
        _person("3", "school_3", [_leg("walk", "walk", 9000.0), _leg("walk", "walk", 9000.0)]),
    ])

    assert measure.main([str(population), "--prefix", "ic_edu_"]) == 0

    out = capsys.readouterr().out
    assert "in-commuters (facility prefix ic_edu_): 3" in out
    assert "walk-only trips: 2 of 2 persons" in out
    assert "of which long (> 5000 m): 1 of 1 persons, by commute mode {'pt': 1}" in out
    assert "pt persons with at least one long walk: 1 of 2" in out
