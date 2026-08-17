import gzip
import logging

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


# The real matsim contrib tool tags the category as "geographical_Trip_Type"
# (lowercase values) and may write end_time as raw float seconds; the parser
# must accept both that attribute name and both time formats.
GEO_PLANS_XML = """<?xml version="1.0" encoding="utf-8"?>
<population>
  <person id="freight_99">
    <attributes>
      <attribute name="geographical_Trip_Type" class="java.lang.String">transit</attribute>
    </attributes>
    <plan selected="yes">
      <activity type="freight_start" x="600000.0" y="5800000.0" end_time="23400.0"></activity>
      <leg mode="truck"></leg>
      <activity type="freight_end" x="610000.0" y="5810000.0"></activity>
    </plan>
  </person>
</population>
"""


def test_parse_freight_trips_reads_geographical_trip_type_and_float_seconds(tmp_path):
    rows = parse_freight_trips(_write(tmp_path, GEO_PLANS_XML))
    assert len(rows) == 1
    row = rows.iloc[0]
    assert row["trip_type"] == "transit"
    assert row["departure_time"] == 23400


# The matsim 2025.0-PR3568 extraction writes NO category attribute at all; the
# stage runs the tool once per category and labels rows via default_trip_type.
NO_ATTRIBUTE_PLANS_XML = """<?xml version="1.0" encoding="utf-8"?>
<population>
  <person id="freight_0">
    <attributes>
      <attribute name="subpopulation" class="java.lang.String">freight</attribute>
    </attributes>
    <plan selected="yes">
      <activity type="freight_start" x="600000.0" y="5800000.0" end_time="06:30:00"></activity>
      <leg mode="truck"></leg>
      <activity type="freight_end" x="610000.0" y="5810000.0"></activity>
    </plan>
  </person>
</population>
"""


def test_parse_freight_trips_uses_default_trip_type_when_attribute_missing(tmp_path):
    rows = parse_freight_trips(_write(tmp_path, NO_ATTRIBUTE_PLANS_XML),
                               default_trip_type="transit")
    assert rows.iloc[0]["trip_type"] == "transit"


def test_parse_freight_trips_allow_empty_returns_empty_frame(tmp_path):
    empty = '<?xml version="1.0"?><population></population>'
    rows = parse_freight_trips(_write(tmp_path, empty), allow_empty=True)
    assert len(rows) == 0
    assert list(rows.columns) == list(TRIP_COLUMNS)


# --- fallback transparency: too-few / too-many activities, missing end_time ---

TOO_FEW_ACTIVITIES_XML = """<?xml version="1.0" encoding="utf-8"?>
<population>
  <person id="freight_ok">
    <attributes>
      <attribute name="trip_type" class="java.lang.String">TRANSIT</attribute>
    </attributes>
    <plan selected="yes">
      <activity type="freight_start" x="600000.0" y="5800000.0" end_time="06:30:00"></activity>
      <leg mode="truck"></leg>
      <activity type="freight_end" x="610000.0" y="5810000.0"></activity>
    </plan>
  </person>
  <person id="freight_broken">
    <attributes>
      <attribute name="trip_type" class="java.lang.String">TRANSIT</attribute>
    </attributes>
    <plan selected="yes">
      <activity type="freight_start" x="600000.0" y="5800000.0" end_time="06:30:00"></activity>
    </plan>
  </person>
</population>
"""


def test_parse_freight_trips_skips_and_warns_on_too_few_activities(tmp_path, caplog):
    path = _write(tmp_path, TOO_FEW_ACTIVITIES_XML)
    with caplog.at_level(logging.WARNING, logger="braunschweig.freight.trips"):
        rows = parse_freight_trips(path)
    # Only the well-formed person yields a row; the broken one is skipped.
    assert len(rows) == 1
    assert rows.iloc[0]["person_id"] == "freight_ok"
    assert any("fewer than 2 activities" in r.message for r in caplog.records)
    assert any("1 person" in r.message for r in caplog.records)


TOO_MANY_ACTIVITIES_XML = """<?xml version="1.0" encoding="utf-8"?>
<population>
  <person id="freight_multi">
    <attributes>
      <attribute name="trip_type" class="java.lang.String">TRANSIT</attribute>
    </attributes>
    <plan selected="yes">
      <activity type="freight_start" x="600000.0" y="5800000.0" end_time="06:30:00"></activity>
      <leg mode="truck"></leg>
      <activity type="via" x="605000.0" y="5805000.0"></activity>
      <leg mode="truck"></leg>
      <activity type="freight_end" x="610000.0" y="5810000.0"></activity>
    </plan>
  </person>
</population>
"""


def test_parse_freight_trips_warns_on_too_many_activities_but_still_picks_first_last(tmp_path, caplog):
    path = _write(tmp_path, TOO_MANY_ACTIVITIES_XML)
    with caplog.at_level(logging.WARNING, logger="braunschweig.freight.trips"):
        rows = parse_freight_trips(path)
    assert len(rows) == 1
    row = rows.iloc[0]
    # First and last activity used, unaffected by the extra "via" activity.
    assert row["origin_x"] == 600000.0
    assert row["destination_x"] == 610000.0
    assert any("more than 2 activities" in r.message for r in caplog.records)


MISSING_END_TIME_XML = """<?xml version="1.0" encoding="utf-8"?>
<population>
  <person id="freight_no_time">
    <attributes>
      <attribute name="trip_type" class="java.lang.String">TRANSIT</attribute>
    </attributes>
    <plan selected="yes">
      <activity type="freight_start" x="600000.0" y="5800000.0"></activity>
      <leg mode="truck"></leg>
      <activity type="freight_end" x="610000.0" y="5810000.0"></activity>
    </plan>
  </person>
</population>
"""


def test_parse_freight_trips_warns_on_missing_end_time(tmp_path, caplog):
    path = _write(tmp_path, MISSING_END_TIME_XML)
    with caplog.at_level(logging.WARNING, logger="braunschweig.freight.trips"):
        rows = parse_freight_trips(path)
    assert rows.iloc[0]["departure_time"] == 0
    assert any("missing/empty start end_time" in r.message for r in caplog.records)


def test_parse_freight_trips_no_warnings_on_well_formed_file(tmp_path, caplog):
    from tests.test_freight_trips import PLANS_XML  # noqa: PLC0415 (self-import for clarity)
    path = _write(tmp_path, PLANS_XML)
    with caplog.at_level(logging.WARNING, logger="braunschweig.freight.trips"):
        parse_freight_trips(path)
    assert not any(
        "fewer than 2 activities" in r.message or "more than 2 activities" in r.message
        or "missing/empty start end_time" in r.message
        for r in caplog.records
    )


# --- freight_crs read from context.config (not hardcoded) ---------------------

def test_trips_stage_configures_freight_crs_default():
    """configure() declares freight_crs with the same default as extraction.py,
    so an override propagates to both stages consistently."""
    import braunschweig.freight.trips as trips_module

    class _StubContext:
        def __init__(self):
            self.stages = []
            self.configs = {}

        def stage(self, name):
            self.stages.append(name)

        def config(self, key, default=None):
            self.configs[key] = default
            return default

    context = _StubContext()
    trips_module.configure(context)
    assert context.configs["freight_crs"] == "EPSG:25832"


def test_execute_uses_configured_freight_crs_for_gpkg(tmp_path, monkeypatch):
    """A non-default freight_crs override is used for the emitted GeoDataFrame's
    CRS instead of the hardcoded module constant."""
    import braunschweig.freight.trips as trips_module

    extraction_dir = tmp_path / "extraction"
    extraction_dir.mkdir()
    plans_name = "zgb_freight.internal.100pct.plans.xml.gz"
    with gzip.open(extraction_dir / plans_name, "wt", encoding="utf-8") as f:
        f.write(PLANS_XML)

    output_dir = tmp_path / "output"
    output_dir.mkdir()

    class _StubContext:
        def __init__(self, config_value):
            self._config_value = config_value

        def stage(self, name):
            return {"internal": plans_name}

        def path(self, name=None):
            return str(extraction_dir) if name else str(output_dir)

        def config(self, key, default=None):
            assert key == "freight_crs"
            return self._config_value

    context = _StubContext("EPSG:4326")
    df = trips_module.execute(context)
    assert len(df) == 2

    import geopandas as gpd
    gdf = gpd.read_file(str(output_dir / trips_module.OUTPUT_GPKG))
    assert gdf.crs is not None
    assert gdf.crs.to_string() == "EPSG:4326"
