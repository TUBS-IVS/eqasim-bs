import gzip

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


def test_format_time_allows_hours_beyond_24():
    assert t.format_time(29.5 * 3600) == "29:30:00"
