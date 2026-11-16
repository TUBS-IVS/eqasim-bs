"""Run targets resolve through the config's ``aliases:`` (patch ``run-target-aliases``).

synpp 1.6.2 resolves the run targets of every ``ConfigurationContext`` (its
``config_requested_stages``, read by ``stage_is_config_requested``) with the aliases it is given,
but ``braunschweig.synpp_deterministic.process_stages`` constructed the context without them. A run
target that exists only as an alias key then resolved to ``None`` and the graph build crashed with
``AttributeError: 'NoneType' object has no attribute 'instance'`` (the overlay
``smoke_kreis_control_fit.yml`` broke this way, repository audit M-LEAD-F1). A run target whose
alias key is ALSO an importable module (``synthesis.output`` in six overlays) resolved to that
module instead of the alias target.
"""
import importlib
import sys

import pytest

#: The stage the aliases point at; its configure records what the context reports.
TARGET = """
SEEN = []
def configure(context):
    SEEN.append(context.stage_is_config_requested("{name}"))
def execute(context):
    return 1
"""
#: A module under the alias key itself, as the inherited ``synthesis/output.py`` is for ``synthesis.output``.
SHADOW = """
def configure(context):
    pass
def execute(context):
    return 0
"""


@pytest.fixture
def stage_modules(tmp_path, monkeypatch):
    from braunschweig import synpp_fork
    synpp_fork.install()
    names = {"alias_probe_target": TARGET.format(name="alias_probe_target"), "alias_probe_shadow": SHADOW}
    for name, source in names.items():
        (tmp_path / (name + ".py")).write_text(source, encoding="utf-8")
        monkeypatch.delitem(sys.modules, name, raising=False)
    monkeypatch.syspath_prepend(str(tmp_path))
    importlib.invalidate_caches()
    yield
    for name in names:
        sys.modules.pop(name, None)


@pytest.mark.usefixtures("stage_modules")
@pytest.mark.parametrize("alias_key", ["alias_only.target", "alias_probe_shadow"],
                         ids=["alias-only-key", "key-shadowed-by-a-module"])
def test_a_run_target_resolves_through_the_aliases(alias_key):
    from braunschweig.synpp_deterministic import process_stages
    registry = process_stages([{"descriptor": alias_key}], {}, {}, {alias_key: "alias_probe_target"})

    assert [stage["name"] for stage in registry.values()] == ["alias_probe_target"]
    # The run target is the alias target for the configure contexts too, not None or the shadow module.
    assert sys.modules["alias_probe_target"].SEEN == [True]
