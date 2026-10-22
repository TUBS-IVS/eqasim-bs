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


def test_configure_depends_on_the_tool_jar_not_the_eqasim_jar():
    from braunschweig.freight import extraction

    class _Context:
        def __init__(self):
            self.stages, self.cfg = [], {}

        def stage(self, name, *args, **kwargs):
            self.stages.append(name)

        def config(self, key, default=None, volatile=False):
            self.cfg[key] = default

    context = _Context()
    extraction.configure(context)
    assert "braunschweig.freight.extraction_tool" in context.stages
    assert "matsim.runtime.eqasim" not in context.stages


def test_execute_runs_the_tool_entry_point_from_the_tool_jar(tmp_path, monkeypatch):
    """The real call site must pass the tool jar as class_path; None would run `java -cp None`."""
    from braunschweig.freight import extraction, extraction_tool

    stage_dir, tool_dir = tmp_path / "stage", tmp_path / "tool"
    stage_dir.mkdir()
    tool_dir.mkdir()
    jar_relative = "freight-tool/target/freight-tool-x.jar"
    calls = []

    class _StudyArea:
        def to_file(self, path):
            pass

    class _Context:
        def stage(self, name, *args, **kwargs):
            return {"braunschweig.data.freight.german_wide":
                        {"plans_path": "plans.xml.gz", "network_path": "network.xml.gz"},
                    "braunschweig.freight.extraction_tool": jar_relative}[name]

        def config(self, key, default=None, volatile=False):
            return {"freight_crs": "EPSG:25832"}[key]

        def path(self, name=None):
            return str(tool_dir if name == "braunschweig.freight.extraction_tool" else stage_dir)

    def _fake_java_run(context, entry_point, arguments, class_path=None, **kwargs):
        calls.append((entry_point, class_path))
        output = arguments[arguments.index("--output") + 1]
        with gzip.open(output, "wt", encoding="utf-8") as f:
            f.write(PLANS_XML)

    monkeypatch.setattr(extraction, "_study_area", lambda context: _StudyArea())
    monkeypatch.setattr(extraction.java, "run", _fake_java_run)

    outputs = extraction.execute(_Context())

    expected_jar = "%s/%s" % (tool_dir, jar_relative)
    assert calls == [(extraction_tool.ENTRY_POINT, expected_jar)] * len(TRIP_CATEGORIES)
    assert set(outputs) == set(TRIP_CATEGORIES)


def test_tool_java_invocation_names_the_entry_point_and_the_jar():
    from braunschweig.freight import extraction, extraction_tool
    assert extraction.tool_java_invocation("/cache/tool", "freight-tool/target/freight-tool-x.jar") == (
        extraction_tool.ENTRY_POINT, "/cache/tool/freight-tool/target/freight-tool-x.jar")
