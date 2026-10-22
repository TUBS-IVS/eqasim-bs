import gzip

import pytest

from braunschweig.matsim.scenario import transit_next_day as t

SCHEDULE = b"""<?xml version="1.0"?><transitSchedule><transitStops>
<stopFacility id="s1" x="0" y="0"/><stopFacility id="s2" x="1000" y="0"/></transitStops>
<transitLine id="L"><transitRoute id="R"><transportMode>bus</transportMode>
<routeProfile><stop refId="s1" departureOffset="00:00:00"/><stop refId="s2" arrivalOffset="00:10:00"/></routeProfile>
<departures><departure id="d1" departureTime="05:30:00" vehicleRefId="v1"/>
<departure id="d2" departureTime="07:00:00" vehicleRefId="v1"/>
<departure id="d3" departureTime="24:30:00" vehicleRefId="v2"/></departures>
</transitRoute><transitRoute id="E"><transportMode>bus</transportMode><routeProfile><stop refId="s1"/></routeProfile>
</transitRoute></transitLine></transitSchedule>"""
VEHICLES = b"""<?xml version="1.0"?><vehicleDefinitions xmlns="http://www.matsim.org/files/dtd">
<vehicleType id="bus"><capacity><seats persons="50"/></capacity></vehicleType>
<vehicle id="v1" type="bus"/><vehicle id="v2" type="bus"/></vehicleDefinitions>"""


def _write(tmp_path):
    s, v = tmp_path / "s.xml.gz", tmp_path / "v.xml.gz"
    s.write_bytes(gzip.compress(SCHEDULE))
    v.write_bytes(gzip.compress(VEHICLES))
    return str(s), str(v)


def test_extend_duplicates_only_departures_before_the_bound_with_new_ids_and_vehicles(tmp_path):
    s, v = _write(tmp_path)
    out = t.extend_schedule(s, v, str(tmp_path / "so.xml.gz"), str(tmp_path / "vo.xml.gz"), next_day_hours=6.0)
    assert out["n_departures"] == 3 and out["n_duplicated"] == 1 and out["n_vehicles_added"] == 1
    text = gzip.decompress((tmp_path / "so.xml.gz").read_bytes()).decode()
    assert 'id="d1_d2" departureTime="29:30:00" vehicleRefId="v1_d2"' in text
    assert "d2_d2" not in text and "d3_d2" not in text  # 07:00 is beyond 6 h, 24:30 is already next day
    vtext = gzip.decompress((tmp_path / "vo.xml.gz").read_bytes()).decode()
    assert vtext.count('id="v1_d2"') == 1 and "v2_d2" not in vtext
    assert out["departures_per_hour_after"][29] == 1 and out["max_departure_h"] == 29.5


def test_extend_with_zero_hours_writes_byte_identical_files(tmp_path):
    s, v = _write(tmp_path)
    out = t.extend_schedule(s, v, str(tmp_path / "so.xml.gz"), str(tmp_path / "vo.xml.gz"), next_day_hours=0.0)
    assert out["n_duplicated"] == 0
    assert gzip.decompress((tmp_path / "so.xml.gz").read_bytes()) == SCHEDULE
    assert gzip.decompress((tmp_path / "vo.xml.gz").read_bytes()) == VEHICLES


def test_extend_duplicates_a_shared_vehicle_once(tmp_path):
    s, v = _write(tmp_path)
    out = t.extend_schedule(s, v, str(tmp_path / "so.xml.gz"), str(tmp_path / "vo.xml.gz"), next_day_hours=8.0)
    assert out["n_duplicated"] == 2 and out["n_vehicles_added"] == 1  # d1 and d2 both use v1
    vtext = gzip.decompress((tmp_path / "vo.xml.gz").read_bytes()).decode()
    assert vtext.count('id="v1_d2"') == 1


def test_format_time_allows_hours_beyond_24():
    assert t.format_time(29.5 * 3600) == "29:30:00"


def _run(tmp_path, schedule=SCHEDULE, vehicles=VEHICLES, hours=6.0):
    s, v = tmp_path / "s.xml.gz", tmp_path / "v.xml.gz"
    s.write_bytes(gzip.compress(schedule))
    v.write_bytes(gzip.compress(vehicles))
    out = t.extend_schedule(str(s), str(v), str(tmp_path / "so.xml.gz"), str(tmp_path / "vo.xml.gz"),
                            next_day_hours=hours)
    return (out, gzip.decompress((tmp_path / "so.xml.gz").read_bytes()).decode(),
            gzip.decompress((tmp_path / "vo.xml.gz").read_bytes()).decode())


def test_vehicles_keep_xsi_prefix_and_child_elements(tmp_path):
    vehicles = b"""<?xml version="1.0"?><vehicleDefinitions xmlns="http://www.matsim.org/files/dtd"
xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:schemaLocation="http://www.matsim.org/files/dtd x.xsd">
<vehicleType id="bus"/><vehicle id="v1" type="bus"><attributes><attribute name="a" class="java.lang.String">x</attribute></attributes></vehicle>
<vehicle id="v2" type="bus"/></vehicleDefinitions>"""
    _, _, vtext = _run(tmp_path, vehicles=vehicles)
    assert "xsi:schemaLocation" in vtext
    assert vtext.count('name="a"') == 2  # the duplicate keeps the <attributes> child


def test_route_without_departures_is_unchanged(tmp_path):
    _, text, _ = _run(tmp_path)
    assert '<transitRoute id="E">' in text and text.count("<departures") == 1


def test_bound_is_half_open(tmp_path):
    schedule = SCHEDULE.replace(b"07:00:00", b"06:00:00")
    out, text, _ = _run(tmp_path, schedule=schedule, hours=6.0)
    assert out["n_duplicated"] == 1 and "d2_d2" not in text  # exactly 06:00 is NOT duplicated


@pytest.mark.parametrize("schedule, vehicles, hours", [
    (SCHEDULE.replace(b' vehicleRefId="v1"/>', b"/>", 1), VEHICLES, 6.0),
    (SCHEDULE, VEHICLES.replace(b'<vehicle id="v1" type="bus"/>', b""), 6.0),
    (SCHEDULE, VEHICLES, -1.0),
    (SCHEDULE.replace(b'id="d3"', b'id="d1_d2"'), VEHICLES, 6.0),
    (SCHEDULE, VEHICLES.replace(b'id="v2"', b'id="v1_d2"'), 6.0),
])
def test_invalid_input_raises(tmp_path, schedule, vehicles, hours):
    with pytest.raises(ValueError):
        _run(tmp_path, schedule=schedule, vehicles=vehicles, hours=hours)


def test_estimated_service_day_counts_calendar_and_exceptions(tmp_path):
    # 2026-06-08 is a Monday. Service A runs Mon-Fri, service B Saturday only, over that week.
    calendar = ("service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date\n"
                "A,1,1,1,1,1,0,0,20260608,20260614\n"
                "B,0,0,0,0,0,1,0,20260608,20260614\n")
    # One removal on the Monday leaves Tuesday as the earliest date with the most services.
    dates = "service_id,date,exception_type\nA,20260608,2\n"
    (tmp_path / "calendar.txt").write_text(calendar, encoding="utf-8")
    (tmp_path / "calendar_dates.txt").write_text(dates, encoding="utf-8")
    assert t.estimated_service_day(str(tmp_path)) == ("2026-06-09", 1)


def test_estimated_service_day_adds_exception_services(tmp_path):
    calendar = ("service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date\n"
                "A,1,0,0,0,0,0,0,20260608,20260608\n")
    dates = "service_id,date,exception_type\nB,20260609,1\nC,20260609,1\n"
    (tmp_path / "calendar.txt").write_text(calendar, encoding="utf-8")
    (tmp_path / "calendar_dates.txt").write_text(dates, encoding="utf-8")
    assert t.estimated_service_day(str(tmp_path)) == ("2026-06-09", 2)
