"""Static contract test: ``context.config(key, default)`` is valid only in ``configure()``.

synpp exposes two different context objects whose ``config()`` methods have
different signatures:

* ``ConfigurationContext.config(name, default=None)`` -- the ``configure()`` phase,
  where a stage *declares* its config options together with a default value.
* ``ExecuteContext.config(name)``                     -- the ``execute()`` phase,
  which only *reads* an already-declared option and takes the key alone.

Passing a default to ``context.config()`` outside ``configure()`` therefore
raises at *runtime* -- ``ExecuteContext.config() takes 2 positional arguments
but 3 were given`` -- and aborts the entire pipeline. This bit
``braunschweig.gravity.model`` once: the ``sector_aware_enabled`` flag was read
as ``context.config(key, False)`` inside ``_execute_gravity_base`` (an execute
helper), and because that call runs unconditionally it crashed every run that
reached the gravity stage, regardless of the flag.

Conventional unit tests cannot catch this: a stub context that accepts a default
argument (as the existing stubs do) silently allows the two-argument form, and
the stage's ``execute()`` path is rarely exercised end-to-end. We therefore guard
the contract *statically*: no ``context.config(...)`` call with two or more
arguments may live outside a function named ``configure``.

The check parses the source with :mod:`ast` (no imports of the stage modules, so
it is immune to the environment's broken LAPACK) and scans the whole
``braunschweig`` package.
"""
from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PACKAGE = REPO / "braunschweig"

# The synpp stage convention always names the context parameter ``context``;
# restricting the check to ``context.config(...)`` avoids false positives from
# unrelated ``.config(...)`` methods on other objects.
CONTEXT_NAME = "context"
CONFIG_ATTR = "config"


def _is_configure_context(function_name: str) -> bool:
    """Whether a function runs in synpp's configure phase.

    The stage entry point is ``configure``; stages that grow large factor the
    declarations into helpers that are still only ever called from ``configure``
    (e.g. ``braunschweig.synthesis.population.enriched._configure_base``). By
    convention such helpers carry ``configure`` in their name, so the
    two-argument ``context.config(name, default)`` form is legitimate there.
    Execute-path helpers (``_execute_*``, ``_read_*``, ...) do not.
    """
    return "configure" in function_name.lower()


class _ConfigCallChecker(ast.NodeVisitor):
    """Collect ``context.config(name, default, ...)`` calls outside ``configure``.

    A function-name stack tracks the nearest enclosing ``def``; a call is a
    violation when it carries two or more positional arguments and its nearest
    enclosing function is not ``configure`` (or it sits at module level).
    """

    def __init__(self, module_path: Path) -> None:
        self._module_path = module_path
        self._function_stack: list[str] = []
        self.violations: list[tuple[str, int, str]] = []

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self._function_stack.append(node.name)
        self.generic_visit(node)
        self._function_stack.pop()

    visit_FunctionDef = _visit_function
    visit_AsyncFunctionDef = _visit_function

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        is_context_config = (
            isinstance(func, ast.Attribute)
            and func.attr == CONFIG_ATTR
            and isinstance(func.value, ast.Name)
            and func.value.id == CONTEXT_NAME
        )
        # Two or more positional args means a default was passed (the second
        # positional is the default value); keyword-only forms are not used here.
        if is_context_config and len(node.args) >= 2:
            enclosing = self._function_stack[-1] if self._function_stack else "<module>"
            if not _is_configure_context(enclosing):
                key = ast.literal_eval(node.args[0]) if isinstance(
                    node.args[0], ast.Constant
                ) else "<dynamic>"
                rel = self._module_path.relative_to(REPO).as_posix()
                self.violations.append(
                    (f"{rel}:{node.lineno}", node.lineno, f"context.config({key!r}, ...) in {enclosing}()")
                )
        self.generic_visit(node)


def _scan(path: Path) -> list[tuple[str, int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    checker = _ConfigCallChecker(path)
    checker.visit(tree)
    return checker.violations


def test_no_two_argument_context_config_outside_configure():
    """Across the whole braunschweig package, ``context.config(key, default)``
    must appear only inside ``configure()``; anywhere else it crashes at execute.
    """
    violations: list[str] = []
    for module_path in sorted(PACKAGE.rglob("*.py")):
        for location, _line, detail in _scan(module_path):
            violations.append(f"{location}  ->  {detail}")

    assert not violations, (
        "context.config(key, default) is only valid in configure(); the execute "
        "context's config() takes the key alone and these calls would crash the "
        "pipeline at runtime:\n  " + "\n  ".join(violations)
    )


def test_checker_flags_the_known_regression_pattern():
    """Self-check: the AST checker must flag the exact pattern that escaped the
    stub-based tests (a two-argument context.config inside an execute helper)."""
    source = (
        "def _execute_gravity_base(context):\n"
        "    if context.config('braunschweig.gravity.sector_aware_enabled', False):\n"
        "        pass\n"
    )
    tree = ast.parse(source)
    checker = _ConfigCallChecker(REPO / "synthetic_regression_sample.py")
    checker.visit(tree)
    assert len(checker.violations) == 1, "the checker must flag the execute-path two-arg call"


def test_checker_allows_two_argument_calls_inside_configure():
    """The same two-argument form must be accepted inside configure()."""
    source = (
        "def configure(context):\n"
        "    context.config('braunschweig.gravity.sector_aware_enabled', False)\n"
    )
    tree = ast.parse(source)
    checker = _ConfigCallChecker(REPO / "synthetic_configure_sample.py")
    checker.visit(tree)
    assert checker.violations == [], "two-arg context.config in configure() must be allowed"
