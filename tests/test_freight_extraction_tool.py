import os

import pytest

from braunschweig.freight import extraction_tool

POM = """<project><properties>
  <maven.compiler.source>25</maven.compiler.source>
  <matsim.version>2026.0-2026w12</matsim.version>
</properties></project>"""


class _Context:
    def __init__(self, cfg, path):
        self._cfg, self._path, self.stages = cfg, path, []
        # Every declared option with its volatile flag, as synpp's ConfigurationContext sees it.
        self.declared = {}

    def config(self, key, default=None, volatile=False):
        self.declared[key] = volatile
        return self._cfg.get(key, default)

    def stage(self, name, *args, **kwargs):
        self.stages.append(name)

    def path(self, *args):
        return self._path


def test_read_pom_property(tmp_path):
    pom = tmp_path / "pom.xml"
    pom.write_text(POM, encoding="utf-8")
    assert extraction_tool.read_pom_property(str(pom), "matsim.version") == "2026.0-2026w12"
    assert extraction_tool.read_pom_property(str(pom), "maven.compiler.source") == "25"
    with pytest.raises(RuntimeError, match="missing"):
        extraction_tool.read_pom_property(str(pom), "does.not.exist")


def test_render_pom_pins_version_release_and_entry_point():
    pom = extraction_tool.render_pom("2026.0-2026w12", "25")
    assert "<artifactId>application</artifactId>" in pom
    assert "<version>2026.0-2026w12</version>" in pom
    assert "<maven.compiler.release>25</maven.compiler.release>" in pom
    assert extraction_tool.ENTRY_POINT in pom
    assert "https://repo.matsim.org/repository/matsim" in pom


def test_configure_declares_the_lockstep_keys_and_runtime_stages():
    context = _Context({}, "")
    extraction_tool.configure(context)
    assert "matsim.runtime.java" in context.stages and "matsim.runtime.maven" in context.stages
    # execute() calls maven.run(), which reads these; synpp scopes config per stage, so an
    # undeclared key would make the real call fail with a missing-option error.
    for key in ("eqasim_source_path", "freight_tool_matsim_version",
                "maven_binary", "maven_skip_tests", "java_home", "java_binary", "java_memory"):
        assert key in context.declared, key
    # The token already carries the pom's MATSim version and Java release, so the path string
    # is only a locator and must not re-key the tool stage (ADR-0148).
    assert context.declared["eqasim_source_path"] is True
    assert context.declared["freight_tool_matsim_version"] is False


def test_validate_names_the_pom_when_the_fork_pom_is_missing(tmp_path):
    context = _Context({"eqasim_source_path": str(tmp_path), "freight_tool_matsim_version": ""}, "")
    with pytest.raises(RuntimeError, match="eqasim_source_path") as error:
        extraction_tool.validate(context)
    assert "pom.xml" in str(error.value)


def test_validate_token_is_content_only(tmp_path):
    pom = tmp_path / "pom.xml"
    pom.write_text(POM, encoding="utf-8")
    context = _Context({"eqasim_source_path": str(tmp_path), "freight_tool_matsim_version": ""}, "")
    token = extraction_tool.validate(context)
    assert token["format"] == 1 and token["matsim_version"] == "2026.0-2026w12" and token["java_release"] == "25"
    assert len(token["wrapper"]) == 64 and len(token["pom"]) == 64
    assert extraction_tool.validate(context) == token


def test_version_override_wins(tmp_path):
    pom = tmp_path / "pom.xml"
    pom.write_text(POM, encoding="utf-8")
    context = _Context({"eqasim_source_path": str(tmp_path), "freight_tool_matsim_version": "2025.0"}, "")
    assert extraction_tool.validate(context)["matsim_version"] == "2025.0"


def _run_execute_with_a_stub_build(tmp_path, monkeypatch, override):
    source = tmp_path / "fork"
    source.mkdir()
    (source / "pom.xml").write_text(POM, encoding="utf-8")
    stage = tmp_path / "stage"
    stage.mkdir()
    version = override or "2026.0-2026w12"

    def fake_maven_run(context, arguments, cwd=None):
        jar = stage / (extraction_tool.JAR_RELATIVE % version)
        jar.parent.mkdir(parents=True)
        jar.write_bytes(b"jar")
    monkeypatch.setattr(extraction_tool.maven, "run", fake_maven_run)
    context = _Context({"eqasim_source_path": str(source), "freight_tool_matsim_version": override}, str(stage))
    extraction_tool.execute(context)
    return str(source / "pom.xml")


def test_execute_logs_the_resolved_versions_and_the_pom(tmp_path, monkeypatch, caplog):
    with caplog.at_level("INFO", logger=extraction_tool.logger.name):
        pom = _run_execute_with_a_stub_build(tmp_path, monkeypatch, "")
    text = " ".join(record.getMessage() for record in caplog.records)
    assert "2026.0-2026w12" in text and "Java release 25" in text and pom in text


def test_execute_logs_a_version_override_against_the_pom_value(tmp_path, monkeypatch, caplog):
    with caplog.at_level("INFO", logger=extraction_tool.logger.name):
        _run_execute_with_a_stub_build(tmp_path, monkeypatch, "2025.0")
    text = " ".join(record.getMessage() for record in caplog.records)
    assert "2025.0" in text and "2026.0-2026w12" in text and "override" in text.lower()
    assert [r.levelname for r in caplog.records if "override" in r.getMessage()] == ["WARNING"]


def test_execute_does_not_warn_when_the_override_equals_the_pom_value(tmp_path, monkeypatch, caplog):
    with caplog.at_level("INFO", logger=extraction_tool.logger.name):
        _run_execute_with_a_stub_build(tmp_path, monkeypatch, "2026.0-2026w12")
    overrides = [r for r in caplog.records if "override" in r.getMessage()]
    assert [r.levelname for r in overrides] == ["INFO"]
    assert "matches" in overrides[0].getMessage()


@pytest.mark.skipif(os.environ.get("EQASIM_BS_FREIGHT_TOOL_BUILD") != "1",
                    reason="Maven build of the tool jar; set EQASIM_BS_FREIGHT_TOOL_BUILD=1 to run")
def test_execute_builds_the_shaded_jar(tmp_path):
    source = tmp_path / "fork"
    source.mkdir()
    (source / "pom.xml").write_text(POM, encoding="utf-8")
    context = _Context({"eqasim_source_path": str(source), "freight_tool_matsim_version": "",
                        "maven_binary": "mvn", "maven_skip_tests": True, "java_home": ""}, str(tmp_path))
    jar = extraction_tool.execute(context)
    assert os.path.exists(os.path.join(str(tmp_path), jar))
