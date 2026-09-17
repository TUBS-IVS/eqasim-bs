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
it is immune to the environment's broken LAPACK) and scans the ``braunschweig``
package plus the top-level ``synthesis`` package. The latter carries the
eqasim-vendored primary/secondary location stages that the TAZ work-location
feature modifies (``synthesis.population.spatial.primary.locations`` /
``.candidates`` read ``taz_work_location_choice`` in their execute paths), so the
same execute-time ``config()`` contract must hold there. The two braunschweig
alias wrappers (``braunschweig.locations.synthesis.replacement_*``) already live
under ``braunschweig`` and are covered by that scan.
"""
from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
# Stage packages whose execute() paths must obey the single-argument config()
# contract. ``braunschweig`` is our own code; ``synthesis`` is the vendored
# eqasim location/population tree that the TAZ feature edits.
PACKAGES = (REPO / "braunschweig", REPO / "synthesis")

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
    """Across the braunschweig and synthesis packages, ``context.config(key,
    default)`` must appear only inside ``configure()``; anywhere else it crashes
    at execute.
    """
    violations: list[str] = []
    for package in PACKAGES:
        for module_path in sorted(package.rglob("*.py")):
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


# --------------------------------------------------------------------------- #
# The fleet stage's stub must declare every key execute() reads (issue #317)
# --------------------------------------------------------------------------- #
FLEET_STAGE = REPO / "braunschweig" / "synthesis" / "vehicles" / "cars" / "household.py"
FLEET_STAGE_TEST = REPO / "tests" / "test_run_fleet_stage.py"


def _single_argument_config_keys(module_path: Path) -> set[str]:
    """Literal keys of every one-argument ``context.config("...")`` call in a module.

    One argument means an execute-phase READ (see the contract above), which the
    stub context must be able to resolve by key alone.
    """
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    keys: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "config"):
            continue
        if not (isinstance(func.value, ast.Name) and func.value.id == "context"):
            continue
        if len(node.args) != 1 or node.keywords:
            continue
        argument = node.args[0]
        if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
            keys.add(argument.value)
    return keys


#: A dict literal in tests/ is treated as a stub for the fleet stage when it carries
#: BOTH of these keys. Two markers rather than one: a single flag name also appears in
#: assertion LISTS (tests/test_configs_composed.py), which are not stubs.
_FLEET_STUB_MARKERS = ("fleet_model_enabled", "fleet_consistency_v2")


def _fleet_stub_dicts(tests_dir: Path) -> list[tuple[Path, int, set[str]]]:
    """Every dict literal under ``tests/`` that configures the fleet stage.

    Deliberately NOT limited to one known file or one syntactic shape. The three
    stubs that exist today are written three different ways -- a module-level
    ``config = {...}``, a ``defaults = {...}`` inside a ``config()`` method, and a
    dict returned inline from ``config()`` -- and scoping an earlier version of this
    guard to the first of them let the other two ship a KeyError to CI. Matching on
    CONTENT rather than on file name or assignment shape covers any new stub too.

    Returns:
        ``(path, lineno, keys)`` per matching dict literal.
    """
    found: list[tuple[Path, int, set[str]]] = []
    for module_path in sorted(tests_dir.glob("test_*.py")):
        try:
            tree = ast.parse(module_path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover - a broken test file fails elsewhere
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            keys = {
                key.value for key in node.keys
                if isinstance(key, ast.Constant) and isinstance(key.value, str)
            }
            if all(marker in keys for marker in _FLEET_STUB_MARKERS):
                found.append((module_path, node.lineno, keys))
    return found


def test_every_fleet_stage_stub_covers_every_execute_config_key():
    """Every fleet-stage stub in tests/ must declare every key execute() reads.

    Those stubs mimic a synpp context AFTER ``configure()``, but they hand-maintain
    the defaults instead of replaying ``configure()``, so a newly added key raises
    ``KeyError`` in each of them while the rest of the suite stays green. That has
    now happened for six flags in a row, and for the sixth
    (``fleet_gemeinde_bev_composition_tilt``) it reached CI in two stubs a
    single-file version of this guard did not look at.

    The check is static (``ast`` only, no imports), so it runs in the environments
    where the stage tests themselves cannot even be COLLECTED -- locally the
    installed ``matsim-tools`` shadows the repository's ``matsim`` namespace package,
    which is why this class of defect keeps reaching CI instead of the desk.
    """
    read_keys = _single_argument_config_keys(FLEET_STAGE)
    assert read_keys, "no execute-time context.config() reads found -- parser broken?"

    stubs = _fleet_stub_dicts(REPO / "tests")
    assert stubs, "no fleet-stage stub found -- the markers or the scan are broken"

    problems = []
    for path, lineno, keys in stubs:
        missing = sorted(read_keys - keys)
        if missing:
            problems.append(f"{path.name}:{lineno} is missing {missing}")
    assert not problems, (
        "fleet-stage stub(s) do not declare every key "
        f"{FLEET_STAGE.name}'s execute() reads without a default, so execute() "
        "would raise KeyError there:\n  " + "\n  ".join(problems)
    )
