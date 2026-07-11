"""Static guard: ``context.config(key, default)`` is configure()-only.

synpp's ``ExecuteContext.config()`` takes a single key; the two-argument
(key, default) form exists only on the configure-time context. Calling the
two-argument form inside ``execute()`` raises
``TypeError: ExecuteContext.config() takes 2 positional arguments but 3 were
given`` -- which killed the 2026-07-11 kreis5 run in its LAST stage
(analysis_suite) after everything expensive had already computed.

This test walks every braunschweig module's ``execute`` function (including
nested closures) and fails on any ``*.config(...)`` call with more than one
argument, naming file and line.
"""

from __future__ import annotations

import ast
from pathlib import Path

BRAUNSCHWEIG_ROOT = Path(__file__).resolve().parent.parent / "braunschweig"


def _two_arg_config_calls_in_execute(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "execute":
            for call in ast.walk(node):
                if not isinstance(call, ast.Call):
                    continue
                func = call.func
                if isinstance(func, ast.Attribute) and func.attr == "config":
                    if len(call.args) + len(call.keywords) > 1:
                        offenders.append((path, call.lineno))
    return offenders


def test_no_two_argument_config_calls_inside_execute():
    offenders = []
    for path in sorted(BRAUNSCHWEIG_ROOT.rglob("*.py")):
        offenders.extend(_two_arg_config_calls_in_execute(path))
    formatted = "\n".join(f"{p.relative_to(BRAUNSCHWEIG_ROOT.parent)}:{line}"
                          for p, line in offenders)
    assert not offenders, (
        "context.config(key, default) inside execute() raises TypeError at "
        "runtime (synpp ExecuteContext.config takes one key only; declare the "
        "default in configure() instead):\n" + formatted
    )
