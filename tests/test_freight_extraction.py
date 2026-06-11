import gzip
import pytest

from braunschweig.freight.extraction import count_trip_types

PLANS_XML = """<?xml version="1.0" encoding="utf-8"?>
<population>
  <person id="freight_1">
    <attributes>
      <attribute name="subpopulation" class="java.lang.String">freight</attribute>
      <attribute name="trip_type" class="java.lang.String">TRANSIT</attribute>
    </attributes>
    <plan selected="yes"></plan>
  </person>
  <person id="freight_2">
    <attributes>
      <attribute name="trip_type" class="java.lang.String">INCOMING</attribute>
    </attributes>
    <plan selected="yes"></plan>
  </person>
  <person id="freight_3">
    <attributes>
      <attribute name="trip_type" class="java.lang.String">TRANSIT</attribute>
    </attributes>
    <plan selected="yes"></plan>
  </person>
</population>
"""


def _write_plans(tmp_path, text=PLANS_XML):
    path = tmp_path / "plans.xml.gz"
    with gzip.open(path, "wt", encoding="utf-8") as f:
        f.write(text)
    return str(path)


def test_count_trip_types(tmp_path):
    persons, counts = count_trip_types(_write_plans(tmp_path))
    assert persons == 3
    assert counts == {"TRANSIT": 2, "INCOMING": 1}


def test_count_trip_types_raises_on_empty_population(tmp_path):
    persons_xml = '<?xml version="1.0"?><population></population>'
    with pytest.raises(RuntimeError, match="no persons"):
        count_trip_types(_write_plans(tmp_path, persons_xml))


def test_count_trip_types_warns_unknown_attribute(tmp_path, caplog):
    xml = PLANS_XML.replace("trip_type", "some_other_attribute")
    persons, counts = count_trip_types(_write_plans(tmp_path, xml))
    assert persons == 3
    assert counts == {"unknown": 3}
    assert any("trip-type attribute" in r.message for r in caplog.records)
