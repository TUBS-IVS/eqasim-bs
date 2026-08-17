"""Pins the code-level default of the ``simwrapper_dashboards`` config key.

Issue #253 reported the key's code default (``True``) as contradicting three
sources that said ``False``. The contradiction was real but resolved in the
opposite direction: ADR-0074 (2026-07-22, later than both the issue and
ADR-0033) decides the flag defaults **ON** under the project feature-flag
policy, and ``braunschweig.documentation.checks.CODE_DEFAULT_TRUE`` records it
as such. The stale sources were the documentation, not the code.

No test pinned the default before, which is why the drift went unnoticed. These
tests pin it from both sides -- the shipped ``configure()`` and the
documentation-governance registry that the generated views are built from -- so
a future change to either alone fails here instead of producing a fourth
contradicting source.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

OPTION = "simwrapper_dashboards"


class _StubContext:
    """Minimal synpp ConfigurationContext stand-in recording config defaults."""

    def __init__(self):
        self.recorded: dict = {}

    def config(self, option, default=None):
        self.recorded[option] = default
        return default

    def stage(self, name, config=None):
        return None


def test_configure_registers_simwrapper_dashboards_default_true():
    """The shipped stage must declare the flag with a ``True`` default (ADR-0074).

    An analysis-only Java module: the simulation results are unaffected and the
    run gains dashboard files, so the feature-flag policy's default-ON applies.
    """
    from matsim.simulation import run

    context = _StubContext()
    run.configure(context)

    assert OPTION in context.recorded, "configure() must request the option"
    assert context.recorded[OPTION] is True, (
        "code default must stay True per ADR-0074; flipping it to False "
        "re-opens issue #253 from the other side and desynchronises "
        "braunschweig.documentation.checks.CODE_DEFAULT_TRUE"
    )


def test_code_default_matches_documentation_governance_registry():
    """The doc-governance registry must agree with the shipped default.

    ``CODE_DEFAULT_TRUE`` is what ADR-0077's generated views resolve the
    production value from. If the two ever disagree, the generated STATUS/
    FEATURES tables state a value the pipeline does not use -- exactly the class
    of defect issue #253 reported.
    """
    from braunschweig.documentation.checks import CODE_DEFAULT_TRUE
    from matsim.simulation import run

    context = _StubContext()
    run.configure(context)

    assert OPTION in CODE_DEFAULT_TRUE, (
        "a flag whose code default is True must be registered in "
        "CODE_DEFAULT_TRUE so the generated views resolve it correctly"
    )
    assert context.recorded[OPTION] is True, (
        "CODE_DEFAULT_TRUE claims this flag defaults True; configure() must match"
    )
