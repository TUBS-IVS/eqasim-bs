import gzip

from braunschweig.freight.extraction import count_persons, TRIP_CATEGORIES, OUTPUT_TEMPLATE

PLANS_XML = """<?xml version="1.0" encoding="utf-8"?>
<population>
  <person id="freight_1">
    <attributes>
      <attribute name="subpopulation" class="java.lang.String">freight</attribute>
    </attributes>
    <plan selected="yes"></plan>
  </person>
  <person id="freight_2">
    <plan selected="yes"></plan>
  </person>
  <person id="freight_3">
    <plan selected="yes"></plan>
  </person>
</population>
"""


def _write_plans(tmp_path, text=PLANS_XML):
    path = tmp_path / "plans.xml.gz"
    with gzip.open(path, "wt", encoding="utf-8") as f:
        f.write(text)
    return str(path)


def test_count_persons(tmp_path):
    assert count_persons(_write_plans(tmp_path)) == 3


def test_count_persons_zero_on_empty_population(tmp_path):
    empty = '<?xml version="1.0"?><population></population>'
    assert count_persons(_write_plans(tmp_path, empty)) == 0


def test_trip_categories_cover_the_published_partition():
    # The four categories of Lu et al. (2022) partition the ZGB-relevant trips;
    # the extraction runs the unmodified tool once per category.
    assert TRIP_CATEGORIES == ("internal", "incoming", "outgoing", "transit")


def test_output_template_is_category_unique():
    names = {OUTPUT_TEMPLATE % category for category in TRIP_CATEGORIES}
    assert len(names) == len(TRIP_CATEGORIES)
