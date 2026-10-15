"""The analysis suite declares and runs the stuck-agents sub-analysis before the dashboard (#443)."""
from braunschweig.analysis import analysis_suite


class _RecordingContext:
    def __init__(self, values=None):
        self.calls, self.stages, self._values = {}, [], values or {}

    def config(self, key, default=None):
        self.calls[key] = default
        return self._values.get(key, default)

    def stage(self, name, **kwargs):
        self.stages.append(name)


def test_configure_declares_the_stuck_agents_flag_default_on():
    context = _RecordingContext()
    analysis_suite.configure(context)
    assert context.calls[analysis_suite.KEY_STUCK_AGENTS] is True


def test_sub_analysis_order_runs_stuck_agents_before_the_dashboard():
    source = open(analysis_suite.__file__, encoding="utf-8").read()
    assert source.index('"stuck_agents"') < source.index('"dashboard"')
