"""Behaviour tests of the transit schedule reader with departures (#465, WP-C)."""
import gzip

import numpy as np
import pytest

from braunschweig.data.transit.schedule import departures_at_stop, read_schedule

SCHEDULE = b"""<?xml version="1.0"?><transitSchedule><transitStops>
<stopFacility id="s1" x="0" y="0"/><stopFacility id="s2" x="1000" y="0"/></transitStops>
<transitLine id="L"><transitRoute id="R"><transportMode>bus</transportMode>
<routeProfile><stop refId="s1" departureOffset="00:00:00"/><stop refId="s2" arrivalOffset="00:10:00"/></routeProfile>
<departures><departure id="d1" departureTime="05:30:00" vehicleRefId="v1"/>
<departure id="d2" departureTime="07:00:00" vehicleRefId="v1"/>
<departure id="d3" departureTime="24:30:00" vehicleRefId="v2"/></departures>
</transitRoute><transitRoute id="E"><transportMode>bus</transportMode><routeProfile><stop refId="s1"/></routeProfile>
</transitRoute></transitLine></transitSchedule>"""


def _write(tmp_path, data, name="schedule.xml"):
    path = tmp_path / name
    path.write_bytes(data)
    return str(path)


@pytest.mark.parametrize("gz", [False, True])
def test_read_schedule_routes_and_departures(tmp_path, gz):
    if gz:
        path = tmp_path / "schedule.xml.gz"
        path.write_bytes(gzip.compress(SCHEDULE))
        path = str(path)
    else:
        path = _write(tmp_path, SCHEDULE)
    schedule = read_schedule(path)

    assert schedule.stops == {"s1": (0.0, 0.0), "s2": (1000.0, 0.0)}
    assert len(schedule.routes) == 2
    route = next(r for r in schedule.routes if r.route_id == "R")
    assert (route.line_id, route.mode, route.stop_ids) == ("L", "bus", ("s1", "s2"))
    assert route.departures_s == (19800.0, 25200.0, 88200.0)
    assert route.departure_offsets_s == (0.0, 600.0)
    empty = next(r for r in schedule.routes if r.route_id == "E")
    assert empty.departures_s == ()

    np.testing.assert_array_equal(departures_at_stop(schedule, "s2"), [20400.0, 25800.0, 88800.0])
    np.testing.assert_array_equal(departures_at_stop(schedule, "s1"), [19800.0, 25200.0, 88200.0])
    assert departures_at_stop(schedule, "s2", {("L", "other")}).size == 0


def test_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        read_schedule(str(tmp_path / "absent.xml"))


def test_route_stop_absent_from_stops_raises(tmp_path):
    bad = SCHEDULE.replace(b'<stop refId="s2" arrivalOffset', b'<stop refId="ghost" arrivalOffset')
    with pytest.raises(ValueError, match="ghost"):
        read_schedule(_write(tmp_path, bad))


def _schedule_xml(profile: bytes, departures: bytes = b'<departure id="d" departureTime="06:00:00"/>',
                  stops: bytes = b'<stopFacility id="s1" x="0" y="0"/><stopFacility id="s2" x="1" y="0"/>'
                                 b'<stopFacility id="s3" x="2" y="0"/>') -> bytes:
    return (b'<?xml version="1.0"?><transitSchedule><transitStops>' + stops + b'</transitStops>'
            b'<transitLine id="L"><transitRoute id="R"><transportMode>bus</transportMode><routeProfile>'
            + profile + b'</routeProfile><departures>' + departures
            + b'</departures></transitRoute></transitLine></transitSchedule>')


def test_departure_offset_takes_precedence_over_arrival_offset(tmp_path):
    xml = _schedule_xml(b'<stop refId="s1" departureOffset="00:00:00"/>'
                        b'<stop refId="s2" arrivalOffset="00:09:00" departureOffset="00:10:00"/>')
    route = read_schedule(_write(tmp_path, xml)).routes[0]
    assert route.departure_offsets_s == (0.0, 600.0)


def test_route_keys_filtering_keeps_only_the_listed_routes(tmp_path):
    schedule = read_schedule(_write(tmp_path, SCHEDULE))
    np.testing.assert_array_equal(departures_at_stop(schedule, "s2", {("L", "R")}),
                                  [20400.0, 25800.0, 88800.0])
    assert departures_at_stop(schedule, "s1", {("L", "E")}).size == 0  # route E has no departures


def test_stop_visited_twice_in_one_route_contributes_both_times(tmp_path):
    xml = _schedule_xml(b'<stop refId="s1" departureOffset="00:00:00"/>'
                        b'<stop refId="s2" departureOffset="00:05:00"/>'
                        b'<stop refId="s1" departureOffset="00:20:00"/>')
    schedule = read_schedule(_write(tmp_path, xml))
    np.testing.assert_array_equal(departures_at_stop(schedule, "s1"), [21600.0, 22800.0])


def test_stop_facility_without_coordinates_raises_naming_the_stop(tmp_path):
    xml = _schedule_xml(b'<stop refId="s1"/>', stops=b'<stopFacility id="s1" y="0"/>')
    with pytest.raises(ValueError, match="s1"):
        read_schedule(_write(tmp_path, xml))


def test_stops_without_any_offset_are_counted_and_warned(tmp_path, caplog):
    # one of three non-first stops has no offsets -> 1/3: warn, no raise
    xml = _schedule_xml(b'<stop refId="s1"/><stop refId="s2" departureOffset="00:05:00"/>'
                        b'<stop refId="s3" arrivalOffset="00:09:00"/><stop refId="s2"/>')
    with caplog.at_level("WARNING"):
        route = read_schedule(_write(tmp_path, xml)).routes[0]
    assert route.departure_offsets_s == (0.0, 300.0, 540.0, 0.0)
    assert any("1/3" in r.getMessage() and r.levelname == "WARNING" for r in caplog.records)


def test_first_stop_without_offset_is_not_counted(tmp_path, caplog):
    xml = _schedule_xml(b'<stop refId="s1"/><stop refId="s2" departureOffset="00:05:00"/>')
    with caplog.at_level("WARNING"):
        read_schedule(_write(tmp_path, xml))
    assert not caplog.records


def test_more_than_half_of_non_first_stops_without_offsets_raises(tmp_path):
    xml = _schedule_xml(b'<stop refId="s1"/><stop refId="s2"/><stop refId="s3"/>')
    with pytest.raises(ValueError, match="offset"):
        read_schedule(_write(tmp_path, xml))
