import gzip
import pytest

from braunschweig.freight.trips import parse_freight_trips, TRIP_COLUMNS

PLANS_XML = """<?xml version="1.0" encoding="utf-8"?>
<population>
  <attributes>
    <attribute name="coordinateReferenceSystem" class="java.lang.String">EPSG:25832</attribute>
  </attributes>
  <person id="freight_1">
    <attributes>
      <attribute name="trip_type" class="java.lang.String">TRANSIT</attribute>
    </attributes>
    <plan selected="yes">
      <activity type="freight_start" x="600000.0" y="5800000.0" end_time="06:30:00"></activity>
      <leg mode="truck"></leg>
      <activity type="freight_end" x="610000.0" y="5810000.0"></activity>
    </plan>
  </person>
  <person id="freight_2">
    <attributes>
      <attribute name="trip_type" class="java.lang.String">INCOMING</attribute>
    </attributes>
    <plan selected="yes">
      <activity type="freight_start" x="620000.0" y="5790000.0" end_time="14:00:00"></activity>
      <leg mode="truck"></leg>
      <activity type="freight_end" x="601000.0" y="5805000.0"></activity>
    </plan>
  </person>
</population>
"""


def _write(tmp_path, text=PLANS_XML):
    path = tmp_path / "plans.xml.gz"
    with gzip.open(path, "wt", encoding="utf-8") as f:
        f.write(text)
    return str(path)


def test_parse_freight_trips_extracts_od_time_type(tmp_path):
    rows = parse_freight_trips(_write(tmp_path))
    assert list(rows.columns) == list(TRIP_COLUMNS)
    assert len(rows) == 2
    first = rows.iloc[0]
    assert first["person_id"] == "freight_1"
    assert first["origin_x"] == 600000.0
    assert first["destination_y"] == 5810000.0
    assert first["departure_time"] == 6 * 3600 + 30 * 60
    assert first["trip_type"] == "TRANSIT"


def test_parse_freight_trips_raises_on_empty(tmp_path):
    with pytest.raises(RuntimeError, match="no freight trips"):
        parse_freight_trips(_write(tmp_path, '<?xml version="1.0"?><population></population>'))
