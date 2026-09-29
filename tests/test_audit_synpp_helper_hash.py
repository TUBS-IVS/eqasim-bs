"""Regression tests for the helper-hash audit script's AST resolver.

``docs/codebase/notes/synpp-helper-hash-audit.md`` records the resolver bugs found so far;
the first two a sizing probe for ``tests/test_synpp_helper_hash_invariant.py`` hit, "so they
are not reintroduced". Turning the note's prose method into
``scripts/audit_synpp_helper_hash.py`` (issue #327) reintroduced one of them
immediately -- the bare ``from . import name`` binding -- which made a fully covered
stage report as uncovered. These tests are what makes "not reintroduced" checkable
rather than a hope in a document.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from scripts import audit_synpp_helper_hash as audit


def _tuples(source: str, own_module: str):
    return audit.helper_tuples(ast.parse(source), own_module)


@pytest.mark.parametrize("import_line, bound_name, module", [
    # Resolver bug 1: reading only ``alias.asname`` misses this form entirely, so every
    # own-package sibling listed in ``_HELPER_MODULES`` by its bare name resolved to
    # nothing and the stage's coverage looked empty.
    pytest.param("from . import batch_cache", "batch_cache",
                 "braunschweig.popsim.stage.batch_cache", id="bare-relative-import"),
    pytest.param("from braunschweig.popsim import income as _income", "_income",
                 "braunschweig.popsim.income", id="aliased-absolute-import"),
    # The same shape as bug 1 without the relative level; both were missed by an
    # asname-only reading.
    pytest.param("from braunschweig.popsim import assembly", "assembly",
                 "braunschweig.popsim.assembly", id="unaliased-absolute-import"),
])
def test_every_import_form_binds_the_name_the_helper_tuple_lists(import_line, bound_name, module):
    source = f"{import_line}\n_HELPER_MODULES = ({bound_name},)\n"
    _aliases, _deferred, alias_map = _tuples(source, "braunschweig.popsim.stage")
    assert alias_map[bound_name] == module


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


def test_a_relative_import_in_a_plain_module_resolves_against_its_package():
    """``from .sibling import X`` in ``pkg/mod.py`` names ``pkg.sibling``, not ``pkg.mod.sibling``.

    Resolver bug 3. Anchoring a relative import at the importing module is right only for a
    package ``__init__``. For a plain module it produced a name that is not on disk, so the
    import was dropped without a trace: the closure never reached the chainsolver's
    ``activity_types``, which ``candidates`` imports relatively and the candidates stage runs.
    """
    on_disk = {"braunschweig.pkg", "braunschweig.pkg.mod", "braunschweig.pkg.sibling",
               "braunschweig.pkg.other", "braunschweig.parent_sibling"}
    tree = ast.parse(
        "from .sibling import VALUE\n"
        "from . import other\n"
        "from ..parent_sibling import helper\n")
    module_level, lazy = audit.collect_imports(
        tree, "braunschweig.pkg.mod", on_disk, is_package=False)
    assert lazy == set()
    assert module_level == {"braunschweig.pkg.sibling", "braunschweig.pkg.other",
                            "braunschweig.parent_sibling"}
    _aliases, _deferred, alias_map = audit.helper_tuples(
        tree, "braunschweig.pkg.mod", is_package=False)
    assert alias_map["other"] == "braunschweig.pkg.other"


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


@pytest.fixture(scope="module")
def real_repository_report():
    """The audit report of the actual tree, built once: it walks every stage module
    (about 14 s), and the tests below only read it."""
    return audit.build_report(Path(__file__).resolve().parents[1])


def test_the_real_repository_reports_popsim_stage_as_fully_covered(real_repository_report):
    """End-to-end sanity on the actual tree: the one stage the project has pinned as
    fully covered (tests/test_popsim_stage_validate_token.py) must come out covered.

    Without this, every unit above could pass while the assembled pass still misreports
    the repository -- which is exactly what happened on the first two runs.
    """
    entry = real_repository_report["braunschweig.popsim.stage"]
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
EXPECTED_UNCOVERED: dict[str, tuple[str, ...]] = {
    # The one deliberate exception, and it is the case this register exists for: covering it
    # WOULD be worse. braunschweig.analysis.json_output decides only how the INFO_KEY
    # diagnostics of the reporting-day trips are RENDERED -- never one value of the returned
    # frame -- so folding it into the token would devalidate this stage, and at 100 % scale
    # hours of everything downstream of it, on a pure formatting change. The reasoning is
    # stated at the module's own _HELPER_MODULES; narrowing the import cannot help, because
    # the stage genuinely calls json_safe() on the diagnostics it reports.
    "braunschweig.synthesis.commute_day.trips_day_stage": (
        "braunschweig.analysis.json_output",
    ),
}


def test_every_source_hashing_stage_covers_its_required_helpers(real_repository_report):
    """No stage with a source-hashing validate() may leave a first-party import unhashed.

    Discovered by the #327 re-audit: eight of the sixteen source-hashing stages did. This
    gate is what keeps the audit note's inventory from drifting back into debt -- a new
    unhashed import fails here instead of quietly joining a list in a dated document.
    """
    report = real_repository_report
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


# --- The transitive closure (step 5, ADR-0136) ---------------------------------------------
#
# The gate above stops at a stage module's OWN imports. A helper's own imports run inside the
# stage just the same: the enriched stage's car-ownership draw read its tilt and base table from
# braunschweig.data.mid.cars_by_status through vehicle_ownership, and no token hashed that module.

def test_the_closure_follows_helpers_and_stops_at_declared_stages(tmp_path):
    """A module the stage reaches only through its hashed helper is reported; a declared stage
    dependency ends the walk, because the DAG covers its code and its own imports."""
    package = tmp_path / "braunschweig"
    package.mkdir()
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs" / "base_bs.yml").write_text("aliases: {}\n", encoding="utf-8")
    files = {
        "__init__.py": "",
        "stage_a.py": (
            "import hashlib\nimport inspect\n"
            "from braunschweig import helper_one\n"
            "_HELPER_MODULES = (helper_one,)\n"
            "def configure(context):\n    context.stage('braunschweig.stage_b')\n"
            "def execute(context):\n    return helper_one.run()\n"
            "def validate(context):\n"
            "    sources = [inspect.getsource(module) for module in _HELPER_MODULES]\n"
            "    return hashlib.md5(''.join(sources).encode()).hexdigest()\n"),
        "helper_one.py": (
            "def run():\n    from braunschweig import helper_two, stage_b\n"
            "    return helper_two.value() + stage_b.VALUE\n"),
        "helper_two.py": "def value():\n    return 1\n",
        "stage_b.py": (
            "from braunschweig import behind_stage_b\n"
            "VALUE = behind_stage_b.VALUE\n"
            "def configure(context):\n    pass\n"
            "def execute(context):\n    return VALUE\n"),
        "behind_stage_b.py": "VALUE = 2\n",
    }
    for name, source in files.items():
        (package / name).write_text(source, encoding="utf-8")

    entry = audit.build_report(tmp_path)["braunschweig.stage_a"]
    assert entry["uncovered"] == []  # the one-level gate sees nothing missing
    assert entry["transitive_uncovered"] == ["braunschweig.helper_two"]


def test_a_module_object_re_exported_by_another_module_counts_as_that_module(tmp_path):
    """``from braunschweig.resolution import pkg`` binds the package that ``resolution`` imports.

    Resolver bug 4. The name read as ``braunschweig.resolution.pkg``, which is no module, so a
    helper tuple listing the re-exported package hashed it at run time while the audit called it
    unhashed. The popsim stage took its ``sources`` package this way from ``source_resolution``.
    """
    package = tmp_path / "braunschweig"
    (package / "pkg").mkdir(parents=True)
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs" / "base_bs.yml").write_text("aliases: {}\n", encoding="utf-8")
    files = {
        "__init__.py": "",
        "pkg/__init__.py": "VALUE = 1\n",
        "resolution.py": "from braunschweig import pkg\n",
        "stage_a.py": (
            "import hashlib\nimport inspect\n"
            "from braunschweig import resolution\n"
            "from braunschweig.resolution import pkg\n"
            "_HELPER_MODULES = (resolution, pkg)\n"
            "def configure(context):\n    pass\n"
            "def execute(context):\n    return pkg.VALUE\n"
            "def validate(context):\n"
            "    sources = [inspect.getsource(module) for module in _HELPER_MODULES]\n"
            "    return hashlib.md5(''.join(sources).encode()).hexdigest()\n"),
    }
    for name, source in files.items():
        (package / name).write_text(source, encoding="utf-8")

    entry = audit.build_report(tmp_path)["braunschweig.stage_a"]
    assert {"braunschweig.pkg", "braunschweig.resolution"} <= set(entry["covered"])
    assert entry["uncovered"] == []
    assert entry["transitive_uncovered"] == []


def test_exemptions_boundaries_and_unread_tuples_shape_the_closure(tmp_path):
    """The walk skips an exempt module together with what only it imports, stops at a boundary
    the stage declares, and credits a helper tuple only when validate() reads it. An exemption
    without a reason is an error rather than an exemption."""
    package = tmp_path / "braunschweig"
    package.mkdir()
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs" / "base_bs.yml").write_text("aliases: {}\n", encoding="utf-8")
    files = {
        "__init__.py": "",
        "stage_a.py": (
            "import hashlib\nimport inspect\n"
            "from braunschweig import helper_one\n"
            "_HELPER_MODULES = (helper_one,)\n"
            "_DEFERRED_HELPER_MODULE_NAMES = ('braunschweig.helper_two',)\n"
            "_TOKEN_CLOSURE_BOUNDARIES = {'braunschweig.stage_c': 'reached, never run'}\n"
            "def configure(context):\n    pass\n"
            "def execute(context):\n    return helper_one.run()\n"
            "def validate(context):\n"
            "    sources = [inspect.getsource(module) for module in _HELPER_MODULES]\n"
            "    return hashlib.md5(''.join(sources).encode()).hexdigest()\n"),
        "helper_one.py": (
            "from braunschweig import helper_two, quiet\n"
            "def run():\n    from braunschweig import stage_c\n"
            "    return quiet.shown(helper_two.value())\n"),
        "helper_two.py": "def value():\n    return 1\n",
        "quiet.py": (
            "from braunschweig import behind_quiet\n"
            "_SYNPP_TOKEN_EXEMPTION = 'prints only'\n"
            "def shown(value):\n    behind_quiet.draw()\n    return value\n"),
        "behind_quiet.py": "def draw():\n    pass\n",
        "stage_c.py": (
            "from braunschweig import behind_stage_c\n"
            "def configure(context):\n    pass\n"
            "def execute(context):\n    return behind_stage_c.VALUE\n"),
        "behind_stage_c.py": "VALUE = 2\n",
    }
    for name, source in files.items():
        (package / name).write_text(source, encoding="utf-8")

    entry = audit.build_report(tmp_path)["braunschweig.stage_a"]
    # validate() never reads the deferred tuple, so its helper_two entry hashes nothing.
    assert entry["transitive_uncovered"] == ["braunschweig.helper_two"]
    assert entry["transitive_exempt"] == ["braunschweig.quiet"]
    assert entry["closure_boundaries"] == ["braunschweig.stage_c"]
    assert entry["unreached_boundaries"] == []

    (package / "quiet.py").write_text("_SYNPP_TOKEN_EXEMPTION = ''\n", encoding="utf-8")
    with pytest.raises(ValueError, match="_SYNPP_TOKEN_EXEMPTION"):
        audit.build_report(tmp_path)


#: Transitive gaps left open on purpose, per stage. A shrinking register like
#: EXPECTED_UNCOVERED, empty by design: every entry needs its reason, and the gate fails when an
#: entry closes. Two kinds of module are not gaps and are declared where they live instead: a
#: module that never shapes a stage result carries a ``_SYNPP_TOKEN_EXEMPTION`` reason, and a
#: stage module that a stage reaches through its imports but never runs is listed, with the
#: reason, in that stage's ``_TOKEN_CLOSURE_BOUNDARIES``.
DEFERRED_TRANSITIVE_GAPS: dict[str, tuple[str, ...]] = {}


def test_every_source_hashing_stage_hashes_its_whole_import_closure(real_repository_report):
    """No source-hashing stage may leave a module of its import closure unhashed (ADR-0136).

    The one-level gate above stops at a stage module's own imports. This gate follows them
    through every first-party module reached: an unhashed module here is code the stage runs
    whose edit a warm cache would not notice.
    """
    report = real_repository_report
    offenders = {}
    for name, entry in sorted(report.items()):
        if not entry["hashes_source"]:
            continue
        # A one-level exception holds for the closure too: its reason is about the module and
        # the stage, not about where the walk found the module.
        missing = tuple(module for module in entry["transitive_uncovered"]
                        if module not in EXPECTED_UNCOVERED.get(name, ()))
        if missing:
            offenders[name] = missing
    assert offenders == DEFERRED_TRANSITIVE_GAPS, (
        "transitive helper-hash coverage changed.\n"
        + "".join(f"  {stage}: {', '.join(modules)}\n" for stage, modules in offenders.items())
        + f"deferred on purpose: {DEFERRED_TRANSITIVE_GAPS}\n"
        "Add each module to the stage's _DEFERRED_HELPER_MODULE_NAMES, or narrow the import "
        "that reaches it. A module that never shapes a stage result gets a "
        "_SYNPP_TOKEN_EXEMPTION; a stage module the stage reaches but never runs goes into that "
        "stage's _TOKEN_CLOSURE_BOUNDARIES. Remove a register entry once its gap closes.")


def test_no_stage_token_hashes_a_module_twice(real_repository_report):
    """A module belongs in one of the two tuples, once: its import site decides which.

    Hashing it twice changes nothing a stale cache could hide, but it doubles that module's
    hashing work and hides which mechanism covers it. The chainsolver stage listed
    escort_links in both tuples after a module-level import was added next to the
    function-level one.
    """
    offenders = {name: entry["hashed_twice"]
                 for name, entry in sorted(real_repository_report.items())
                 if entry.get("hashed_twice")}
    assert offenders == {}


def test_closure_boundaries_are_stages_the_stage_reaches_without_importing_them(
        real_repository_report):
    """A boundary may only end the walk at a synpp stage that the stage reaches through its
    helpers but does not import itself: that stage's own token covers the code for the output it
    builds. A boundary the walk no longer reaches is stale."""
    report = real_repository_report
    problems = []
    for name, entry in sorted(report.items()):
        for boundary in entry.get("closure_boundaries", []):
            if boundary not in report:
                problems.append(f"{name}: {boundary} is not a synpp stage")
            if boundary in entry["required_helpers"]:
                problems.append(f"{name}: imports {boundary} itself, so it runs its code")
            if boundary in entry["unreached_boundaries"]:
                problems.append(f"{name}: no longer reaches {boundary}")
    assert problems == []
    # Guard the guard: the boundaries this repository declares are actually read.
    assert any(entry.get("closure_boundaries") for entry in report.values())


def test_only_the_trips_stage_builds_trips_through_a_donor_source():
    """The trips-stage boundary of the popsim and MiD donor stages rests on this fact.

    Their donor-source adapters import the trips stage for PopsimSource.build_trips, and only the
    trips stage calls build_trips. A second caller could run trip code inside one of those stages
    while neither token hashes it, so a new caller has to revisit their _TOKEN_CLOSURE_BOUNDARIES.
    """
    repo = Path(__file__).resolve().parents[1]
    callers = set()
    for path in audit.iter_py(repo):
        text = path.read_text(encoding="utf-8")
        if "build_trips" not in text:
            continue
        if any(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
               and node.func.attr == "build_trips" for node in ast.walk(ast.parse(text))):
            callers.add(audit.module_name(path, repo))
    assert callers == {"braunschweig.popsim.trips_stage"}
