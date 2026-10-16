"""The analysis suite declares and runs the stuck-agents sub-analysis before the dashboard (#443)."""
import json
import sys

import pytest

from braunschweig.analysis import analysis_suite, stuck_agents

#: Every other sub-analysis off, so that the suite runs exactly the stuck analysis and the dashboard.
_OTHER_ANALYSES_OFF = {
    analysis_suite.KEY_POPULATION_VALIDATION: False, analysis_suite.KEY_MID_VALIDATION: False,
    analysis_suite.KEY_POPSIM_VALIDATION: False, analysis_suite.KEY_INTEGERIZER_QUALITY: False,
    analysis_suite.KEY_EDUCATION_VALIDATION: False, analysis_suite.KEY_HOUSEHOLD_COMPOSITION: False,
}


class _RecordingContext:
    def __init__(self, values=None):
        self.calls, self.stages, self._values = {}, [], values or {}

    def config(self, key, default=None):
        self.calls.setdefault(key, default)   # the first declaration carries the default
        return self._values.get(key, default)

    def stage(self, name, **kwargs):
        self.stages.append(name)


class _ExecuteContext:
    """Key-only config like synpp's ExecuteContext; unset keys take the default declared in configure()."""

    def __init__(self, output_path, **overrides):
        declared = _RecordingContext()
        analysis_suite.configure(declared)
        self._values = {key: default for key, default in declared.calls.items()}
        self._values.update({"output_path": str(output_path), "sampling_rate": 0.01, "data_path": str(output_path),
                             analysis_suite.KEY_INCLUDE_MATSIM: True, **_OTHER_ANALYSES_OFF, **overrides})

    def config(self, key):
        return self._values[key]


def _stage_output(tmp_path, with_archive=True):
    (tmp_path / "run_persons.csv").write_text("person_id\n1\n")
    if with_archive:
        archive = tmp_path / "matsim_output"
        archive.mkdir()
        (archive / "output_events.xml.gz").write_bytes(b"")
    return tmp_path


@pytest.fixture
def calls(monkeypatch):
    """Record the order and arguments of the stuck analysis and of the dashboard."""
    from braunschweig.analysis.dashboard import build_dashboard
    recorded = []

    def _fake_stuck(sim_output, out_dir, **kwargs):
        recorded.append(("stuck_agents", str(sim_output), str(out_dir), kwargs))

    monkeypatch.setattr(stuck_agents, "write_stuck_outputs", _fake_stuck)
    monkeypatch.setattr(build_dashboard, "main", lambda: recorded.append(("dashboard", list(sys.argv))))
    return recorded


def _summary(tmp_path):
    return json.loads((tmp_path / "analysis" / "analysis_suite_summary.json").read_text())


def test_configure_declares_the_stuck_agents_flag_default_on():
    context = _RecordingContext()
    analysis_suite.configure(context)
    assert context.calls[analysis_suite.KEY_STUCK_AGENTS] is True


def test_configure_declares_the_warn_share_with_the_module_default():
    context = _RecordingContext()
    analysis_suite.configure(context)
    assert analysis_suite.KEY_STUCK_WARN_SHARE == "analysis_stuck_warn_share"
    assert context.calls[analysis_suite.KEY_STUCK_WARN_SHARE] == stuck_agents.STUCK_WARN_SHARE == 0.01


def test_the_stuck_analysis_runs_on_the_archive_before_the_dashboard(tmp_path, calls):
    _stage_output(tmp_path)
    analysis_suite.execute(_ExecuteContext(tmp_path))
    assert [entry[0] for entry in calls] == ["stuck_agents", "dashboard"]
    _, sim_output, out_dir, kwargs = calls[0]
    assert sim_output == str(tmp_path / "matsim_output")
    assert out_dir == str(tmp_path / "analysis" / "stuck")
    assert _summary(tmp_path)["ran"] == ["stuck_agents", "dashboard"]


def test_the_configured_warn_share_reaches_the_stuck_analysis(tmp_path, calls):
    _stage_output(tmp_path)
    analysis_suite.execute(_ExecuteContext(tmp_path, **{analysis_suite.KEY_STUCK_WARN_SHARE: 0.05}))
    assert calls[0][3]["warn_share"] == 0.05


def test_the_default_warn_share_reaches_the_stuck_analysis(tmp_path, calls):
    _stage_output(tmp_path)
    analysis_suite.execute(_ExecuteContext(tmp_path))
    assert calls[0][3]["warn_share"] == stuck_agents.STUCK_WARN_SHARE


def test_the_stuck_analysis_is_skipped_with_a_named_reason_without_an_archive(tmp_path, calls):
    _stage_output(tmp_path, with_archive=False)
    analysis_suite.execute(_ExecuteContext(tmp_path))
    assert calls == []
    reasons = {entry["analysis"]: entry["reason"] for entry in _summary(tmp_path)["skipped"]}
    assert "no MATSim output archive" in reasons["stuck_agents"]


def test_the_flag_off_skips_the_stuck_analysis_but_the_dashboard_still_runs(tmp_path, calls):
    _stage_output(tmp_path)
    analysis_suite.execute(_ExecuteContext(tmp_path, **{analysis_suite.KEY_STUCK_AGENTS: False}))
    assert [entry[0] for entry in calls] == ["dashboard"]
    assert {"analysis": "stuck_agents", "reason": "flag off"} in _summary(tmp_path)["skipped"]


def test_a_failing_stuck_analysis_is_recorded_and_the_dashboard_still_runs(tmp_path, calls, monkeypatch):
    _stage_output(tmp_path)

    def _boom(*args, **kwargs):
        raise RuntimeError("stuck boom")

    monkeypatch.setattr(stuck_agents, "write_stuck_outputs", _boom)
    analysis_suite.execute(_ExecuteContext(tmp_path))
    summary = _summary(tmp_path)
    assert [entry[0] for entry in calls] == ["dashboard"]
    assert summary["failed"] == [{"analysis": "stuck_agents", "error": "stuck boom"}]
