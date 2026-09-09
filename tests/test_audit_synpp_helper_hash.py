"""Regression tests for the helper-hash audit script's AST resolver.

``docs/codebase/notes/synpp-helper-hash-audit.md`` records two resolver bugs that a
sizing probe for ``tests/test_synpp_helper_hash_invariant.py`` hit, "so they are not
reintroduced". Turning the note's prose method into
``scripts/audit_synpp_helper_hash.py`` (issue #327) reintroduced one of them
immediately -- the bare ``from . import name`` binding -- which made a fully covered
stage report as uncovered. These tests are what makes "not reintroduced" checkable
rather than a hope in a document.
"""
from __future__ import annotations

import ast

from scripts import audit_synpp_helper_hash as audit


def _tuples(source: str, own_module: str):
    return audit.helper_tuples(ast.parse(source), own_module)


def test_a_bare_relative_import_binds_the_local_name():
    """``from . import batch_cache`` binds ``batch_cache`` -> the package submodule.

    Resolver bug 1. Reading only ``alias.asname`` misses this form entirely, so every
    own-package sibling listed in ``_HELPER_MODULES`` by its bare name resolves to
    nothing and the stage's coverage looks empty.
    """
    _aliases, _deferred, alias_map = _tuples(
        "from . import batch_cache\n_HELPER_MODULES = (batch_cache,)\n",
        "braunschweig.popsim.stage")
    assert alias_map["batch_cache"] == "braunschweig.popsim.stage.batch_cache"


def test_an_aliased_absolute_import_binds_the_alias():
    """``from braunschweig.popsim import income as _income`` binds ``_income``."""
    _aliases, _deferred, alias_map = _tuples(
        "from braunschweig.popsim import income as _income\n_HELPER_MODULES = (_income,)\n",
        "braunschweig.popsim.stage")
    assert alias_map["_income"] == "braunschweig.popsim.income"


def test_an_unaliased_absolute_import_binds_the_imported_name():
    """``from braunschweig.popsim import assembly`` binds ``assembly``.

    The same shape as bug 1 without the relative level; both were missed by an
    asname-only reading.
    """
    _aliases, _deferred, alias_map = _tuples(
        "from braunschweig.popsim import assembly\n_HELPER_MODULES = (assembly,)\n",
        "braunschweig.popsim.stage")
    assert alias_map["assembly"] == "braunschweig.popsim.assembly"


def test_an_annassign_declared_helper_tuple_is_read():
    """``_DEFERRED_HELPER_MODULE_NAMES: tuple[str, ...] = (...)`` must be read.

    Resolver bug 2. An annotated assignment is an ``ast.AnnAssign``, not an
    ``ast.Assign``, so a resolver matching only the latter silently reads an EMPTY
    deferred list -- and reports every deferred module as uncovered.
    """
    _aliases, deferred, _alias_map = _tuples(
        "_DEFERRED_HELPER_MODULE_NAMES: tuple[str, ...] = (\n"
        '    "braunschweig.popsim.attributes",\n'
        '    "braunschweig.popsim.trips",\n'
        ")\n",
        "braunschweig.popsim.stage")
    assert deferred == {"braunschweig.popsim.attributes", "braunschweig.popsim.trips"}


def test_from_x_import_name_prefers_the_submodule_reading():
    """``X.Y`` when that is a module on disk, otherwise ``X`` -- never both.

    Adding the parent package as WELL lists every parent as a required helper, which is
    what first made ``braunschweig.popsim.stage`` look uncovered on four parent packages
    it merely imports names through.
    """
    on_disk = {"braunschweig.popsim", "braunschweig.popsim.assembly",
               "braunschweig.popsim.income"}
    tree = ast.parse(
        "from braunschweig.popsim import assembly\n"
        "from braunschweig.popsim.income import HIGH_INCOME_THRESHOLD_EUR\n")
    module_level, lazy = audit.collect_imports(tree, "braunschweig.popsim.stage", on_disk)
    assert lazy == set()
    # `assembly` is a module -> that is the import; `braunschweig.popsim` is NOT listed.
    # HIGH_INCOME_THRESHOLD_EUR is an attribute -> its module `...income` is the import.
    assert module_level == {"braunschweig.popsim.assembly", "braunschweig.popsim.income"}


def test_a_function_body_import_is_classified_lazy():
    """Module-level vs lazy is the note's own distinction and decides which tuple a
    helper belongs in (module objects vs dotted names)."""
    on_disk = {"braunschweig.popsim.cells"}
    tree = ast.parse(
        "def execute(context):\n"
        "    from braunschweig.popsim import cells\n"
        "    return cells\n")
    module_level, lazy = audit.collect_imports(tree, "braunschweig.popsim.stage", on_disk)
    assert module_level == set()
    assert lazy == {"braunschweig.popsim.cells"}


def test_the_real_repository_reports_popsim_stage_as_fully_covered():
    """End-to-end sanity on the actual tree: the one stage the project has pinned as
    fully covered (tests/test_popsim_stage_validate_token.py) must come out covered.

    Without this, every unit above could pass while the assembled pass still misreports
    the repository -- which is exactly what happened on the first two runs.
    """
    from pathlib import Path

    repo = Path(__file__).resolve().parents[1]
    report = audit.build_report(repo)
    entry = report["braunschweig.popsim.stage"]
    assert entry["hashes_source"] is True
    assert entry["uncovered"] == []
    assert entry["required_helpers"], "an empty required set would pass vacuously"


def test_a_package_entry_does_not_credit_its_submodules():
    """One helper-tuple entry hashes exactly one file -- its own.

    ``validate()`` digests ``inspect.getsource(module)``, which for a PACKAGE returns only
    its ``__init__.py``. An earlier version of this pass credited a package entry with its
    submodules as well, so a stage listing only ``braunschweig.popsim.mid`` would have been
    reported fully covered while all nine of its submodules went unhashed. Verified against
    the interpreter here rather than assumed, because the whole gate below rests on it.
    """
    import importlib
    import inspect as inspect_module

    assert audit.covered_by_entry("braunschweig.popsim.mid") == {"braunschweig.popsim.mid"}
    package = importlib.import_module("braunschweig.popsim.mid")
    assert inspect_module.getsource(package) == open(
        package.__file__, encoding="utf-8").read()
    assert package.__file__.endswith("__init__.py")


# --- The full-surface coverage gate -----------------------------------------------------
#
# tests/test_synpp_helper_hash_invariant.py gates the NARROW own-package-siblings slice.
# This gate covers the WIDER first-party surface the audit note inventories: for every stage
# whose validate() hashes source at all, EVERY first-party module it imports and does not
# declare as a synpp stage dependency must feed that token. Under-hashing means a warm cache
# silently serves output built by code that has since changed.
#
# EXPECTED_UNCOVERED is a shrinking register of deliberate exceptions, empty by design. An
# entry needs a reason in the comment above it, not just a name -- the only defensible reason
# is that COVERING the module would be worse than not covering it (a downstream stage hashing
# a large upstream package would be re-run by every unrelated edit to it), and in that case
# the right fix is usually to narrow the IMPORT instead.
EXPECTED_UNCOVERED: dict[str, tuple[str, ...]] = {}


def test_every_source_hashing_stage_covers_its_required_helpers():
    """No stage with a source-hashing validate() may leave a first-party import unhashed.

    Discovered by the #327 re-audit: eight of the sixteen source-hashing stages did. This
    gate is what keeps the audit note's inventory from drifting back into debt -- a new
    unhashed import fails here instead of quietly joining a list in a dated document.
    """
    from pathlib import Path

    repo = Path(__file__).resolve().parents[1]
    report = audit.build_report(repo)
    offenders = {name: tuple(entry["uncovered"])
                 for name, entry in sorted(report.items())
                 if entry["hashes_source"] and entry["uncovered"]}
    expected = {name: tuple(mods) for name, mods in EXPECTED_UNCOVERED.items()}

    assert offenders == expected, (
        "helper-hash coverage changed.\n"
        f"unhashed now: {offenders}\n"
        f"allow-listed: {expected}\n"
        "Add the module to the stage's _HELPER_MODULES (module-level import) or "
        "_DEFERRED_HELPER_MODULE_NAMES (function-level import), or narrow the import so the "
        "module is no longer part of this stage's surface. Only add an EXPECTED_UNCOVERED "
        "entry if covering it would be actively worse, with the reason written down.")

    # Guard the guard: the gate must be looking at a non-trivial set of stages.
    hashing = [n for n, e in report.items() if e["hashes_source"]]
    assert len(hashing) >= 15, hashing
