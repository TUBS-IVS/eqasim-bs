"""Repo-wide gate for the synpp stage own-package helper-hash invariant.

synpp's ``get_stage_hash`` hashes only a stage module's OWN source. If a stage
imports a sibling module from its OWN package (the pattern the #267 module-split
programme created: a stage file or package that pulls in extracted helper
submodules) and does not fold that sibling's source into a ``validate()``
token, then editing the sibling silently reuses the stage's STALE cached output
on a partial rerun -- the pipeline runs, the tests pass, the result is wrong.
See ``docs/codebase/notes/synpp-helper-hash-audit.md`` (issue #290) for the
full, one-time inventory this gate is seeded from, and
``docs/codebase/notes/gravity-model-split.md`` /
``docs/codebase/notes/popsim-stage-split.md`` for the two cases that motivated
closing the gap in the first place.

Why this gate is scoped to OWN-PACKAGE siblings only (not every first-party
import, issue #291): the #290 audit counted every first-party helper a stage
reaches, wherever it lives in the repo, and found 86 stages with an
incomplete-or-absent token (category (c)). Gating on that full surface today
would be unenforceable noise: most of those gaps sit across unrelated package
boundaries (a cordon-data stage importing a schools helper, for instance) and
reflect a much broader, pre-existing first-party coupling problem, not the
sibling-split pattern this programme created. Own-package siblings are where
the #267 split concretely put behaviour outside a stage's own file while
leaving the import inside the stage's own directory -- the sharpest, most
common, and cheapest-to-close instance of the trap -- so this gate enforces
exactly that subset and defers the rest to the audit note as inventoried,
un-gated debt.

Method (static, not dynamic): stage discovery, import resolution and
``_HELPER_MODULES``/``_DEFERRED_HELPER_MODULE_NAMES`` tuple reading are all done
by parsing each stage module's AST -- no stage module is imported (some of the
230 stages carry real import-time cost or side effects; importing all of them
would make this gate slow enough that nobody would run it, which is worse than
a static gate that runs in well under a second). ``braunschweig.gravity.model``
and ``braunschweig.popsim.stage`` already have dedicated dynamic suites
(``tests/test_gravity_validate_token.py``, ``tests/test_popsim_stage_validate_token.py``)
that import them and hash-check the real ``validate()`` output; this gate only
needs to see what the two coverage tuples literally declare, which AST gives
directly.

Import resolution mirrors the #290 audit's documented rules exactly (see that
file's "Method" section for the reasoning): imports are collected at ANY AST
depth (module-level and function-body/lazy -- three of the four gravity gaps
and the popsim ``enriched`` gap were function-level, so a module-level-only
scan would miss them and call stages clean that are not); ``if TYPE_CHECKING:``
guarded branches are pruned (they never execute at runtime); ``from X import Y``
is tried both as an attribute of ``X`` and as the submodule ``X.Y``, keeping
whichever exists on disk; relative imports are resolved with the same
``bits = package.rsplit('.', level - 1)`` rule ``importlib`` itself uses. A
first-party import that is itself a synpp stage the importer's own
``configure()`` DECLARES via a literal ``context.stage("name")`` call (resolved
through the ``aliases:`` block of ``configs/base_bs.yml``) is excluded from the
required set: synpp's own DAG hashes that stage and propagates it through the
declared edge, so no extra token is needed for it (this is exactly why
``braunschweig.freight.trips`` -- which imports its own-package sibling
``braunschweig.freight.extraction`` at module level AND declares it -- is not a
gate violation).

"Own package" is the package the stage's own file lives inside: for a plain
module ``pkg.sub.mod``, that is ``pkg.sub``; for a package-style stage
(``pkg.sub.stage/__init__.py``), that is ``pkg.sub.stage`` itself, so its
required siblings are its own submodules. This falls out of one rule applied
uniformly (drop the stage's own trailing path component only if it is not
itself a package), not two different rules for the two shapes.

The #291 sizing probe this gate was built from mis-reported
``braunschweig.popsim.stage`` (this repo's only category-(b) "fully covered"
stage per the #290 audit) as VIOLATING. Both bugs were found and fixed during
development of this gate, and are recorded here so they are not reintroduced:

1. ``_HELPER_MODULES``/``_DEFERRED_HELPER_MODULE_NAMES`` can be declared as an
   ``ast.AnnAssign`` (``_HELPER_MODULES: Tuple[Any, ...] = (...)``), not only a
   plain ``ast.Assign`` -- ``braunschweig/synthesis/locations/secondary_chainsolvers/__init__.py``
   uses the annotated form. A reader that only matched ``ast.Assign`` would see
   no ``_HELPER_MODULES`` at all for that stage and mark every one of its 13
   submodules uncovered.
2. A bare ``from . import name`` (``ast.ImportFrom`` with ``module=None``,
   ``level>=1``) is exactly how a package stage binds its own submodules as
   MODULE OBJECTS (``braunschweig/popsim/stage/__init__.py`` line ~211:
   ``from . import batch_cache``; ``secondary_chainsolvers/__init__.py`` line 79
   does the same for its 13 siblings in one statement). ``_HELPER_MODULES``
   then references those bindings by bare ``Name`` (e.g. ``batch_cache``). A
   resolver that only handles ``from .sub import attribute`` (``module`` set)
   still detects the required siblings correctly (via the SEPARATE
   ``from .batch_cache import (...)`` attribute-import lines every one of these
   stages also has, for its re-exports) but then cannot resolve what the bare
   name ``batch_cache`` in ``_HELPER_MODULES`` refers to, and reports the
   sibling as uncovered despite it being explicitly listed. Both
   ``_resolve_relative_container`` (below) and the corresponding module-level
   binding map are exercised, per stage, against this exact shape; the
   reference-case tests near the bottom of this file assert both
   ``braunschweig.gravity.model`` and ``braunschweig.popsim.stage`` come back
   fully covered specifically so a regression here fails loudly instead of
   silently re-opening the false-negative the probe hit.

The allow-list (``ALLOWED_VIOLATIONS`` below) is a SHRINKING debt register, not
a mute button: for every stage the gate currently finds violating, the exact
set of uncovered sibling names is pinned literally, and the parametrised gate
test fails if that set changes AT ALL -- shrinks (someone fixed it: remove the
entry), grows (a new gap opened up), or the stage stops needing an entry at all
(also: remove it). Closing any of these violations is separate follow-up work
with cache-devalidation consequences of its own and is deliberately NOT done in
this change (see the issue's hard constraint: no ``_HELPER_MODULES`` tuple is
touched here).
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]

# The five first-party roots audited by #290; a synpp stage outside these is
# not currently known to exist in this repo (see that audit's "Method" step 1).
FIRST_PARTY_ROOTS = ("braunschweig", "data", "eqasim_common", "matsim", "synthesis")


# ---------------------------------------------------------------------------
# stage discovery
# ---------------------------------------------------------------------------

def _dotted_name_for(path: Path) -> str:
    """Dotted module/package name for a ``.py`` file under ``REPO_ROOT``."""
    parts = list(path.relative_to(REPO_ROOT).parts)
    if parts[-1] == "__init__.py":
        parts = parts[:-1]
    else:
        parts[-1] = parts[-1][:-len(".py")]
    return ".".join(parts)


def _iter_first_party_python_files():
    for root_name in FIRST_PARTY_ROOTS:
        root = REPO_ROOT / root_name
        if root.exists():
            yield from root.rglob("*.py")


def _is_stage_tree(tree: ast.Module) -> bool:
    """A stage defines module-level (not nested) ``configure`` and ``execute``.

    Matches the #290 audit's stage-detection rule exactly (``ast.FunctionDef``
    nodes directly in ``ast.Module.body``): that audit also checked there is no
    factory-assignment stage pattern (``configure = ...`` at module level)
    anywhere in the five roots, so this ``def``-only rule is not a guess.
    """
    names = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}
    return "configure" in names and "execute" in names


class StageModule:
    """A discovered synpp stage: its dotted name, file, package-ness and AST."""

    __slots__ = ("dotted_name", "path", "is_package", "tree")

    def __init__(self, dotted_name: str, path: Path, is_package: bool, tree: ast.Module):
        self.dotted_name = dotted_name
        self.path = path
        self.is_package = is_package
        self.tree = tree

    @property
    def own_package(self) -> str:
        """The dotted package this stage's own siblings would live in.

        A package stage (``__init__.py``) IS its own package -- its siblings
        are its own submodules. A plain module's own package is its parent
        directory. One rule, not two: only drop the trailing path component
        when the stage is not itself a package.
        """
        if self.is_package:
            return self.dotted_name
        if "." not in self.dotted_name:
            return ""
        return self.dotted_name.rsplit(".", 1)[0]


def _discover_stage_modules() -> list[StageModule]:
    stages = []
    for path in _iter_first_party_python_files():
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        if _is_stage_tree(tree):
            dotted_name = _dotted_name_for(path)
            is_package = path.name == "__init__.py"
            stages.append(StageModule(dotted_name, path, is_package, tree))
    return stages


# ---------------------------------------------------------------------------
# first-party module existence (static; nothing is imported)
# ---------------------------------------------------------------------------

def _module_exists_on_disk(dotted_name: str) -> bool:
    if not dotted_name:
        return False
    relative = Path(*dotted_name.split("."))
    return (REPO_ROOT / f"{relative}.py").is_file() or (REPO_ROOT / relative / "__init__.py").is_file()


# ---------------------------------------------------------------------------
# import resolution: any AST depth, TYPE_CHECKING pruned, relative imports
# resolved with importlib's own rule
# ---------------------------------------------------------------------------

def _is_type_checking_guard(test: ast.expr) -> bool:
    if isinstance(test, ast.Name) and test.id == "TYPE_CHECKING":
        return True
    if isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING":
        return True
    return False


def _walk_imports(tree: ast.Module):
    """Yield every ``Import``/``ImportFrom`` node in ``tree``, anywhere in it.

    Prunes the true-branch of ``if TYPE_CHECKING:`` guards (never executes at
    runtime, per the #290 audit's checked-not-assumed finding that none of the
    230 stage modules rely on it -- pruned anyway so a future one is handled
    correctly rather than by accident).
    """
    def walk(node):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            yield node
            return
        if isinstance(node, ast.If) and _is_type_checking_guard(node.test):
            for child in node.orelse:
                yield from walk(child)
            return
        for child in ast.iter_child_nodes(node):
            yield from walk(child)

    yield from walk(tree)


def _in_function_body(tree: ast.Module, target: ast.AST) -> bool:
    """Whether ``target`` sits inside a ``(Async)FunctionDef`` at any depth."""
    def walk(node, in_function):
        if node is target:
            return in_function
        next_in_function = in_function or isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        for child in ast.iter_child_nodes(node):
            found = walk(child, next_in_function)
            if found is not None:
                return found
        return None

    result = walk(tree, False)
    return bool(result)


def _resolve_relative_container(own_package: str, level: int, module: str | None) -> str:
    """Mirror importlib's own relative-import resolution rule.

    For ``from . import X`` (module is ``None``): the container is
    ``own_package`` itself. For ``from .. import X``: one level up, etc. --
    ``bits = package.rsplit('.', level - 1)``, exactly as documented in
    ``docs/codebase/notes/synpp-helper-hash-audit.md``'s "Method" section.
    """
    bits = own_package.rsplit(".", level - 1)
    base = bits[0]
    if module:
        return f"{base}.{module}" if base else module
    return base


class ImportResolution:
    """Everything a stage's own AST tells us about its first-party imports."""

    __slots__ = ("first_party_names", "module_level_bindings")

    def __init__(self):
        self.first_party_names: set[str] = set()
        # local binding name -> resolved dotted module name, MODULE-LEVEL only
        # (this is what _HELPER_MODULES / _DEFERRED_HELPER_MODULE_NAMES's bare
        # Name references must resolve through, since those tuples are
        # themselves module-level assignments).
        self.module_level_bindings: dict[str, str] = {}


def _resolve_imports(stage: StageModule) -> ImportResolution:
    resolution = ImportResolution()
    own_package = stage.own_package

    for node in _walk_imports(stage.tree):
        in_function = _in_function_body(stage.tree, node)

        if isinstance(node, ast.Import):
            for alias in node.names:
                dotted = alias.name
                if _module_exists_on_disk(dotted):
                    resolution.first_party_names.add(dotted)
                    if not in_function:
                        binding = alias.asname or dotted.split(".")[0]
                        resolution.module_level_bindings[binding] = dotted
            continue

        # ast.ImportFrom
        if node.level >= 1:
            container = _resolve_relative_container(own_package, node.level, node.module)
        else:
            container = node.module
        if not container:
            continue

        for alias in node.names:
            name = alias.name
            if name == "*":
                if _module_exists_on_disk(container):
                    resolution.first_party_names.add(container)
                continue
            submodule_candidate = f"{container}.{name}"
            if _module_exists_on_disk(submodule_candidate):
                resolved = submodule_candidate
            elif _module_exists_on_disk(container):
                resolved = container
            else:
                continue  # not a first-party import (stdlib/third-party)
            resolution.first_party_names.add(resolved)
            if not in_function:
                binding = alias.asname or name
                resolution.module_level_bindings[binding] = resolved

    return resolution


# ---------------------------------------------------------------------------
# declared stage dependencies: context.stage("literal"), alias-resolved
# ---------------------------------------------------------------------------

def _load_aliases() -> dict[str, str]:
    config = yaml.safe_load((REPO_ROOT / "configs" / "base_bs.yml").read_text(encoding="utf-8"))
    return dict(config.get("aliases") or {})


def _declared_stage_names(tree: ast.Module, aliases: dict[str, str]) -> set[str]:
    declared = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "stage"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            literal = node.args[0].value
            declared.add(aliases.get(literal, literal))
    return declared


# ---------------------------------------------------------------------------
# reading _HELPER_MODULES / _DEFERRED_HELPER_MODULE_NAMES via AST
# ---------------------------------------------------------------------------

UndecidedElement = tuple[str, str, str]  # (stage dotted name, tuple name, ast.dump of the element)


def _find_module_level_assignment(tree: ast.Module, target_name: str) -> ast.expr | None:
    """Find ``target_name = <value>`` (plain or annotated) at module level."""
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name) and target.id == target_name:
                return node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            if isinstance(node.target, ast.Name) and node.target.id == target_name:
                return node.value
    return None


def _resolve_coverage_tuple(
    value: ast.expr | None,
    stage: StageModule,
    module_level_bindings: dict[str, str],
    tuple_name: str,
    undecided: list[UndecidedElement],
) -> set[str]:
    """Resolve a ``_HELPER_MODULES``/``_DEFERRED_HELPER_MODULE_NAMES`` value.

    Elements are either string literals (dotted names, as
    ``_DEFERRED_HELPER_MODULE_NAMES`` uses) or bare ``Name`` references to a
    module-level import binding (as ``_HELPER_MODULES`` uses, since it holds
    module OBJECTS). A ``+`` of two such tuples (seen in this codebase's own
    tests, not yet in production code, but handled defensively) is flattened by
    resolving both sides; a Name on the right of ``+`` is looked up as another
    module-level assignment in the same file. Anything else is recorded as
    UNDECIDED rather than silently dropped or silently counted as covered.
    """
    if value is None:
        return set()

    if isinstance(value, (ast.Tuple, ast.List)):
        resolved = set()
        for element in value.elts:
            if isinstance(element, ast.Constant) and isinstance(element.value, str):
                resolved.add(element.value)
            elif isinstance(element, ast.Name):
                bound = module_level_bindings.get(element.id)
                if bound is None:
                    undecided.append((stage.dotted_name, tuple_name, ast.dump(element)))
                else:
                    resolved.add(bound)
            else:
                undecided.append((stage.dotted_name, tuple_name, ast.dump(element)))
        return resolved

    if isinstance(value, ast.BinOp) and isinstance(value.op, ast.Add):
        left = _resolve_coverage_tuple(value.left, stage, module_level_bindings, tuple_name, undecided)
        right_value = value.right
        if isinstance(right_value, ast.Name):
            right_value = _find_module_level_assignment(stage.tree, right_value.id)
        right = _resolve_coverage_tuple(right_value, stage, module_level_bindings, tuple_name, undecided)
        return left | right

    undecided.append((stage.dotted_name, tuple_name, "top-level:" + ast.dump(value)))
    return set()


def _covered_names(
    stage: StageModule, module_level_bindings: dict[str, str], undecided: list[UndecidedElement]
) -> set[str]:
    helper_modules_value = _find_module_level_assignment(stage.tree, "_HELPER_MODULES")
    deferred_value = _find_module_level_assignment(stage.tree, "_DEFERRED_HELPER_MODULE_NAMES")
    covered = _resolve_coverage_tuple(
        helper_modules_value, stage, module_level_bindings, "_HELPER_MODULES", undecided
    )
    covered |= _resolve_coverage_tuple(
        deferred_value, stage, module_level_bindings, "_DEFERRED_HELPER_MODULE_NAMES", undecided
    )
    return covered


# ---------------------------------------------------------------------------
# putting it together: one GateResult per stage
# ---------------------------------------------------------------------------

class GateResult:
    __slots__ = ("stage", "required", "covered", "uncovered")

    def __init__(self, stage: StageModule, required: set[str], covered: set[str]):
        self.stage = stage
        self.required = required
        self.covered = covered
        self.uncovered = required - covered


def _compute_gate_results() -> tuple[dict[str, GateResult], list[UndecidedElement]]:
    aliases = _load_aliases()
    undecided: list[UndecidedElement] = []
    results: dict[str, GateResult] = {}

    for stage in _discover_stage_modules():
        resolution = _resolve_imports(stage)
        own_package = stage.own_package
        required = {
            name for name in resolution.first_party_names
            if name != stage.dotted_name and own_package and name.startswith(own_package + ".")
        }
        declared = _declared_stage_names(stage.tree, aliases)
        required -= declared

        covered = _covered_names(stage, resolution.module_level_bindings, undecided)
        results[stage.dotted_name] = GateResult(stage, required, covered)

    return results, undecided


_GATE_RESULTS, _UNDECIDED = _compute_gate_results()


# ---------------------------------------------------------------------------
# the shrinking allow-list
#
# One entry per currently-known-violating stage, holding the EXACT set of its
# own-package siblings not covered by _HELPER_MODULES or
# _DEFERRED_HELPER_MODULE_NAMES today. Hand-verified against the source for at
# least three entries (see the PR description / task report for which); the
# rest were cross-checked against the independently-produced #290 audit's
# per-stage uncovered-module lists (docs/codebase/notes/synpp-helper-hash-audit.md),
# restricted to the own-package subset -- every overlapping row matched
# exactly. Fixing any one of these is separate, deliberate follow-up work (see
# this file's module docstring); shrinking this dict is how that work is
# reflected here.
# ---------------------------------------------------------------------------

ALLOWED_VIOLATIONS: dict[str, tuple[str, ...]] = {
    "braunschweig.analysis.analysis_suite": (
        "braunschweig.analysis.dashboard.build_dashboard",
        "braunschweig.analysis.popsim_validation.run_popsim_control_validation",
        "braunschweig.analysis.population_validation.controls",
        "braunschweig.analysis.population_validation.population_source",
        "braunschweig.analysis.population_validation.run_population_validation",
        "braunschweig.analysis.run_education_validation",
        "braunschweig.analysis.run_household_composition",
        "braunschweig.analysis.run_integerizer_quality",
        "braunschweig.analysis.run_mid_validation",
    ),
    "braunschweig.analysis.simwrapper_export": (
        "braunschweig.analysis.simwrapper.export",
    ),
    "braunschweig.data.bosserhof_location_category": (
        "braunschweig.data.bosserhof_purpose",
    ),
    "braunschweig.data.census.households_type": (
        "braunschweig.data.census.households_size_age",
    ),
    "braunschweig.data.cordon_gemeinden": (
        "braunschweig.data.cordon.network",
    ),
    "braunschweig.data.cordon_network": (
        "braunschweig.data.cordon.network",
    ),
    "braunschweig.data.cordon_pt_gates": (
        "braunschweig.data.cordon.network",
        "braunschweig.data.cordon.pt_reachability",
    ),
    "braunschweig.data.external_secondary_points": (
        "braunschweig.data.external_workplaces",
    ),
    "braunschweig.data.external_workplaces": (
        "braunschweig.data.cordon.external_points",
    ),
    "braunschweig.data.mid.data": (
        "braunschweig.data.mid.reference_tables",
    ),
    "braunschweig.data.schools.facilities": (
        "braunschweig.data.schools.typing",
    ),
    "braunschweig.ipf.attributed": (
        "braunschweig.ipf.config_validation",
        "braunschweig.ipf.household_composition",
    ),
    "braunschweig.ipf.model": (
        "braunschweig.ipf.config_validation",
        "braunschweig.ipf.joint_age_size",
    ),
    "braunschweig.ipf.prepare": (
        "braunschweig.ipf.joint_age_size",
    ),
    "braunschweig.popsim.completed_donor": (
        "braunschweig.popsim.mid",
        "braunschweig.popsim.seed",
        "braunschweig.popsim.stage",
        "braunschweig.popsim.weekend_plan_match",
    ),
    "braunschweig.popsim.distance_distributions": (
        "braunschweig.popsim.mid",
        "braunschweig.popsim.purpose_subtype",
        "braunschweig.popsim.shop_subtype",
        "braunschweig.popsim.time_imputation",
        "braunschweig.popsim.trips",
    ),
    "braunschweig.popsim.trips_stage": (
        "braunschweig.popsim.plan_validation",
        "braunschweig.popsim.sources",
        "braunschweig.popsim.trips",
    ),
    "braunschweig.synthesis.incommuters": (
        "braunschweig.synthesis.vehicles.fleet_sampling_de",
    ),
    "braunschweig.synthesis.locations.education_gravity": (
        "braunschweig.synthesis.locations.education_gravity_model",
    ),
    "braunschweig.synthesis.locations.home_cell": (
        "braunschweig.synthesis.locations.building_typing",
        "braunschweig.synthesis.locations.cell_building_signals",
        "braunschweig.synthesis.locations.home_matcher",
    ),
    "braunschweig.synthesis.locations.secondary_candidates": (
        "braunschweig.synthesis.locations.landuse_candidates",
        "braunschweig.synthesis.locations.secondary_chainsolvers",
    ),
    "braunschweig.synthesis.student_incommuters": (
        "braunschweig.synthesis.incommuters",
    ),
    "data.gtfs.cleaned": (
        "data.gtfs.utils",
    ),
    "data.hts.edgt_44.raw": (
        "data.hts.edgt_44.format",
    ),
    "eqasim_common.analysis.synthesis.statistics.monte_carlo": (
        "eqasim_common.analysis.synthesis.statistics.marginal",
    ),
    "synthesis.population.income.bhepop2": (
        "synthesis.population.income.utils",
    ),
    "synthesis.population.income.uniform": (
        "synthesis.population.income.utils",
    ),
    "synthesis.population.spatial.secondary.locations": (
        "synthesis.population.spatial.secondary.components",
        "synthesis.population.spatial.secondary.problems",
        "synthesis.population.spatial.secondary.rda",
    ),
}


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------

def test_discovery_finds_a_plausible_number_of_stage_modules():
    """Sanity bound on the file walk, not a brittle exact pin.

    The #290 audit snapshot found 230; legitimate, unrelated stage additions or
    removals elsewhere in the repo will move this slightly over time, so this
    only guards against a badly broken walk (e.g. a root silently not found),
    not against normal repo growth.
    """
    assert 200 <= len(_GATE_RESULTS) <= 320, (
        f"discovered {len(_GATE_RESULTS)} stage modules -- outside the plausible "
        "range, check FIRST_PARTY_ROOTS and the file walk"
    )
    for known_stage in (
        "braunschweig.gravity.model",
        "braunschweig.popsim.stage",
        "braunschweig.freight.trips",
        "data.census.raw",
    ):
        assert known_stage in _GATE_RESULTS, f"expected stage not discovered: {known_stage}"


def test_no_undecided_helper_tuple_elements():
    """The resolver must never silently drop an element it cannot interpret.

    An element of ``_HELPER_MODULES``/``_DEFERRED_HELPER_MODULE_NAMES`` this
    gate cannot statically resolve to a dotted name is recorded here rather
    than being silently treated as either covered or uncovered -- per
    CLAUDE.md's "never invent" rule, a case the resolver cannot decide must be
    visible, not guessed away.
    """
    assert not _UNDECIDED, (
        "found _HELPER_MODULES/_DEFERRED_HELPER_MODULE_NAMES elements this "
        f"gate could not statically resolve: {_UNDECIDED}"
    )


@pytest.mark.parametrize("stage_name", (
    "braunschweig.gravity.model",
    "braunschweig.popsim.stage",
))
def test_the_two_known_good_reference_stages_are_fully_covered(stage_name):
    """``gravity.model`` and ``popsim.stage`` MUST come back fully covered.

    These are, respectively, the #289 fix and this repo's only #290-audited
    category-(b) stage. If either comes back violating, the resolver itself is
    broken (see the module docstring for the two concrete bugs a rough probe
    hit here) -- fix the resolver before trusting anything else this gate
    reports, rather than allow-listing a false positive on either of them.
    """
    result = _GATE_RESULTS[stage_name]
    assert not result.uncovered, (
        f"{stage_name} should be fully covered but the resolver found uncovered "
        f"own-package siblings: {sorted(result.uncovered)} -- this indicates a "
        "bug in the import/coverage resolver, not a real regression"
    )


_ALL_RELEVANT_STAGE_NAMES = sorted(
    {name for name, result in _GATE_RESULTS.items() if result.required}
    | set(ALLOWED_VIOLATIONS)
)


@pytest.mark.parametrize("stage_name", _ALL_RELEVANT_STAGE_NAMES)
def test_own_package_helper_coverage_matches_the_allow_list(stage_name):
    """The core gate: every own-package-sibling importer must match its entry.

    Three failure shapes, all deliberate:

    - a stage NOT in ``ALLOWED_VIOLATIONS`` with any uncovered own-package
      sibling is a NEW violation -- fail, naming what is uncovered. This is
      the case that matters most going forward: it is exactly how the ENTD
      siblings escaped the popsim token until issue #296.
    - a stage IN ``ALLOWED_VIOLATIONS`` whose uncovered set no longer matches
      the recorded one -- whether it shrank (partially or fully fixed) or grew
      (a new gap opened) -- fails, so the allow-list cannot go stale in either
      direction; a stage that is fully fixed must have its entry REMOVED, not
      merely emptied.
    """
    result = _GATE_RESULTS.get(stage_name)
    if result is None:
        pytest.fail(
            f"{stage_name} is in ALLOWED_VIOLATIONS but is no longer a discovered "
            "stage module -- remove the stale entry"
        )

    expected = set(ALLOWED_VIOLATIONS.get(stage_name, ()))
    actual = result.uncovered

    if stage_name not in ALLOWED_VIOLATIONS:
        assert not actual, (
            f"NEW own-package helper-hash gap on {stage_name}: imports "
            f"{sorted(actual)} without covering it in _HELPER_MODULES or "
            "_DEFERRED_HELPER_MODULE_NAMES. Either close the gap or add an "
            "entry to ALLOWED_VIOLATIONS in this file with the exact uncovered "
            "set and a reason."
        )
        return

    if actual == expected:
        return

    if not actual:
        pytest.fail(
            f"{stage_name} is fully covered now (no uncovered own-package "
            f"siblings) but still has an ALLOWED_VIOLATIONS entry {sorted(expected)} "
            "-- remove the entry, the allow-list must shrink when a violation "
            "is fixed."
        )

    fixed = expected - actual
    grown = actual - expected
    details = []
    if fixed:
        details.append(f"no longer uncovered (update/remove from the entry): {sorted(fixed)}")
    if grown:
        details.append(f"newly uncovered (not previously allow-listed): {sorted(grown)}")
    pytest.fail(
        f"{stage_name}'s uncovered own-package siblings changed since the "
        f"allow-list was written -- {'; '.join(details)}. Update the "
        "ALLOWED_VIOLATIONS entry to match the current, verified state."
    )


def test_allowed_violations_has_no_duplicate_uncovered_names_per_entry():
    """A guard against copy-paste drift in the allow-list itself."""
    for stage_name, uncovered in ALLOWED_VIOLATIONS.items():
        assert len(uncovered) == len(set(uncovered)), (
            f"duplicate uncovered-module entries for {stage_name}: {uncovered}"
        )
