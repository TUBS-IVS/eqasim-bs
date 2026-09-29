"""Re-run the synpp helper-hash coverage audit (docs/codebase/notes/synpp-helper-hash-audit.md).

Implements that note's own four-step method as an executable pass, so the snapshot can be
refreshed instead of hand-patched (issue #327).

Step 1  enumerate stages       -- a module is a stage if ast.parse finds top-level
                                  FunctionDefs named `configure` AND `execute`.
Step 2  collect own imports    -- whole-AST walk, module-level vs lazy (inside a function),
                                  TYPE_CHECKING-guarded excluded, resolved to first-party
                                  on-disk modules; a relative import resolves against the
                                  importing module's package.
Step 3  declared stage edges   -- literal context.stage("name") calls, resolved through the
                                  `aliases:` block of configs/base_bs.yml.
                                  required_helpers = first-party imports - declared stages.
Step 4  validate() coverage    -- stages whose validate() hashes source; their
                                  _HELPER_MODULES / _DEFERRED_HELPER_MODULE_NAMES read
                                  STATICALLY, each credited only when validate() reads it;
                                  a package entry covers its own __init__ only.
Step 5  transitive closure     -- the same imports followed through every first-party
                                  module a source-hashing stage reaches, stopping at its
                                  declared stage edges; what the token misses of that closure
                                  is ``transitive_uncovered`` (ADR-0136). Two declarations
                                  end the walk early, each with its reason: a module whose
                                  ``_SYNPP_TOKEN_EXEMPTION`` says it never shapes a stage
                                  result, and a stage module the walking stage lists in its
                                  ``_TOKEN_CLOSURE_BOUNDARIES`` because it never runs it.

Usage: python scripts/audit_synpp_helper_hash.py [<repo_root>] [--json <out.json>]
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import sys
from pathlib import Path

ROOTS = ("braunschweig", "data", "eqasim_common", "matsim", "synthesis")

#: Module-level string a first-party module sets to state why no stage token needs to hash it:
#: the module never changes a value a stage returns (progress output, terminal colours, process
#: monitoring). Read statically; an empty reason is an error, not an exemption.
EXEMPTION_NAME = "_SYNPP_TOKEN_EXEMPTION"

#: Module-level dict a source-hashing stage sets, {dotted stage module: reason}: stage modules
#: its imports reach but whose code it never runs. The closure walk of THAT stage stops there.
BOUNDARIES_NAME = "_TOKEN_CLOSURE_BOUNDARIES"


def module_name(path: Path, repo: Path) -> str:
    rel = path.relative_to(repo).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def iter_py(repo: Path):
    for root in ROOTS:
        base = repo / root
        if not base.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d != "__pycache__"]
            for name in filenames:
                if name.endswith(".py"):
                    yield Path(dirpath) / name


def top_level_funcs(tree: ast.Module) -> set[str]:
    return {n.name for n in tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def in_type_checking(node, tree) -> bool:
    """True when the node sits inside an `if TYPE_CHECKING:` block."""
    for parent in ast.walk(tree):
        if isinstance(parent, ast.If):
            test = parent.test
            name = (test.id if isinstance(test, ast.Name)
                    else test.attr if isinstance(test, ast.Attribute) else None)
            if name == "TYPE_CHECKING":
                for child in ast.walk(parent):
                    if child is node:
                        return True
    return False


def lazy_nodes(tree) -> set[int]:
    """ids of import nodes nested inside a function body (executed on call, not import)."""
    lazy = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for child in ast.walk(node):
                if isinstance(child, (ast.Import, ast.ImportFrom)):
                    lazy.add(id(child))
    return lazy


def _top_level_value(tree: ast.Module, name: str):
    """The value node of a top-level ``name = ...`` (or annotated) assignment, else None."""
    for node in tree.body:
        if isinstance(node, ast.Assign):
            if any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
                return node.value
        elif (isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
                and node.target.id == name):
            return node.value
    return None


def exemption_reason(tree: ast.Module, module: str) -> str | None:
    """The module's ``_SYNPP_TOKEN_EXEMPTION`` reason, or None when it declares none.

    Raises ValueError for a marker that is not a non-empty string literal: an exemption
    nobody can read the reason of must not quietly take a module out of every token.
    """
    value = _top_level_value(tree, EXEMPTION_NAME)
    if value is None:
        return None
    if not (isinstance(value, ast.Constant) and isinstance(value.value, str)
            and value.value.strip()):
        raise ValueError(f"{module}: {EXEMPTION_NAME} must be a non-empty string literal "
                         "stating why no stage result depends on this module")
    return value.value


def closure_boundaries(tree: ast.Module, module: str) -> dict[str, str]:
    """A stage's ``_TOKEN_CLOSURE_BOUNDARIES`` as {dotted module: reason}; empty when unset.

    Raises ValueError unless it is a dict literal of non-empty string keys and reasons.
    """
    value = _top_level_value(tree, BOUNDARIES_NAME)
    if value is None:
        return {}
    pairs = list(zip(value.keys, value.values)) if isinstance(value, ast.Dict) else None
    if pairs is None or not all(
            isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.strip()
            for pair in pairs for node in pair):
        raise ValueError(f"{module}: {BOUNDARIES_NAME} must be a dict literal mapping each "
                         "dotted stage module to a non-empty reason string")
    return {key.value: reason.value for key, reason in pairs}


def relative_anchor(own_module: str, is_package: bool) -> str:
    """The package a relative import of ``own_module`` counts its dots from.

    A package ``__init__`` is its own anchor; a plain module ``a.b.c`` is anchored at ``a.b``,
    exactly as Python resolves ``from .x import y`` there.
    """
    return own_module if is_package else own_module.rsplit(".", 1)[0]


def resolve_relative(mod: str | None, package: str, level: int) -> str:
    bits = package.rsplit(".", level - 1)
    base = bits[0]
    return f"{base}.{mod}" if mod else base


def collect_imports(tree, own_module: str, on_disk: set[str], is_package: bool = True):
    """Return (module_level, lazy) sets of first-party module names this module imports.

    ``is_package`` says whether ``own_module`` is a package ``__init__``; relative imports are
    resolved against :func:`relative_anchor`.
    """
    lazy_ids = lazy_nodes(tree)
    module_level, lazy = set(), set()
    package = relative_anchor(own_module, is_package)
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        if in_type_checking(node, tree):
            continue
        targets = set()
        if isinstance(node, ast.Import):
            for alias in node.names:
                targets.add(alias.name)
        else:
            if node.level:
                base = resolve_relative(node.module, package, node.level)
            else:
                base = node.module or ""
            # ONE reading per imported name, per the note's method: `X.Y` when that is a
            # module on disk (`from braunschweig.popsim import assembly`), otherwise `X`
            # because the name is an attribute of it (`from ...income import CONST`).
            # Adding the base as WELL would list every parent package as a helper -- which
            # is what made a fully covered stage look uncovered on the first pass here.
            for alias in node.names:
                candidate = f"{base}.{alias.name}" if base else alias.name
                if candidate in on_disk:
                    targets.add(candidate)
                elif base:
                    targets.add(base)
        for target in targets:
            if target.split(".")[0] not in ROOTS:
                continue
            if target == own_module:
                continue
            if target not in on_disk:
                continue
            (lazy if id(node) in lazy_ids else module_level).add(target)
    return module_level, lazy


def declared_stages(tree) -> set[str]:
    """Literal context.stage("name") first arguments."""
    out = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "stage" and node.args):
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                out.add(first.value)
    return out


def read_aliases(repo: Path) -> dict[str, str]:
    import yaml
    with open(repo / "configs" / "base_bs.yml", encoding="utf-8") as handle:
        cfg = yaml.safe_load(handle)
    return dict(cfg.get("aliases") or {})


def helper_tuples(tree, own_module: str,
                  is_package: bool = True) -> tuple[set[str], set[str], dict[str, str]]:
    """Statically read _HELPER_MODULES (alias names) and _DEFERRED_HELPER_MODULE_NAMES.

    Returns (module_object_aliases, deferred_dotted_names, alias -> module map).

    Handles both the ast.Assign and the ast.AnnAssign declaration form, and BOTH import
    binding forms -- with and without `as` -- for absolute and relative imports. The note
    records these as the two resolver bugs a rough sizing probe hit; a bare
    `from . import batch_cache` binds the local name `batch_cache`, and missing that makes
    a fully covered stage look uncovered.
    """
    aliases, deferred, alias_map = set(), set(), {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            base = (resolve_relative(node.module, relative_anchor(own_module, is_package),
                                     node.level)
                    if node.level else (node.module or ""))
            for alias in node.names:
                bound = alias.asname or alias.name
                alias_map[bound] = f"{base}.{alias.name}" if base else alias.name
        elif isinstance(node, ast.Import):
            for alias in node.names:
                # `import a.b.c` binds `a`; `import a.b.c as x` binds `x` -> a.b.c.
                bound = alias.asname or alias.name.split(".")[0]
                alias_map[bound] = alias.name
        targets = []
        if isinstance(node, ast.Assign):
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
            value = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            targets = [node.target.id]
            value = node.value
        else:
            continue
        if not value or not isinstance(value, (ast.Tuple, ast.List)):
            continue
        for name in targets:
            if name == "_HELPER_MODULES":
                for element in value.elts:
                    if isinstance(element, ast.Name):
                        aliases.add(element.id)
                    elif isinstance(element, ast.Attribute):
                        aliases.add(ast.unparse(element))
            elif name == "_DEFERRED_HELPER_MODULE_NAMES":
                for element in value.elts:
                    if isinstance(element, ast.Constant) and isinstance(element.value, str):
                        deferred.add(element.value)
    return aliases, deferred, alias_map


def names_read_by_validate(tree: ast.Module) -> set[str]:
    """Every name the stage's top-level ``validate()`` reads.

    A helper tuple is credited only when ``validate()`` reads it: a tuple that is declared but
    never iterated hashes nothing, and crediting it would report a stage as covered whose
    token ignores the very modules the tuple lists.
    """
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "validate":
            return {child.id for child in ast.walk(node)
                    if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load)}
    return set()


def resolve_module_binding(dotted: str, trees: dict, on_disk: set[str],
                           packages: set[str]) -> str:
    """The on-disk module a helper-tuple name stands for, following re-exports.

    ``from M import name`` reads as ``M.name``. When that is no module but ``M`` is, ``name`` is
    an attribute of ``M``; if ``M`` binds it by importing a module, the tuple entry hashes that
    module. The popsim stage, for example, listed the ``sources`` package it took from
    ``source_resolution``. Any other name comes back unchanged and covers no module.
    """
    seen = set()
    while dotted not in on_disk and "." in dotted and dotted not in seen:
        seen.add(dotted)
        owner, attribute = dotted.rsplit(".", 1)
        if owner not in trees:
            break
        _aliases, _deferred, owner_bindings = helper_tuples(
            trees[owner], owner, is_package=owner in packages)
        if attribute not in owner_bindings:
            break
        dotted = owner_bindings[attribute]
    return dotted


def tuple_entries(tree: ast.Module) -> tuple[list[str], list[str]]:
    """The raw entries of ``_HELPER_MODULES`` (bound names) and ``_DEFERRED_HELPER_MODULE_NAMES``
    (dotted strings), in order and with repetitions, for the hashed-twice check."""
    helper_value = _top_level_value(tree, "_HELPER_MODULES")
    deferred_value = _top_level_value(tree, "_DEFERRED_HELPER_MODULE_NAMES")
    helpers = [ast.unparse(element) for element in getattr(helper_value, "elts", [])
               if isinstance(element, (ast.Name, ast.Attribute))]
    deferred = [element.value for element in getattr(deferred_value, "elts", [])
                if isinstance(element, ast.Constant) and isinstance(element.value, str)]
    return helpers, deferred


def covered_by_entry(name: str) -> set[str]:
    """The modules ONE helper-tuple entry actually causes to be hashed.

    Exactly one: the entry's own file. ``validate()`` digests
    ``inspect.getsource(module)``, and for a PACKAGE that returns only its ``__init__.py``
    -- verified against the interpreter, not assumed. An earlier version of this pass
    credited a package entry with its submodules too ("enumerated one level deep", the
    phrasing the audit note uses for what it COUNTS as covered). That over-credits: listing
    a package does not hash its submodules, and a stage that listed only the package would
    have been reported as fully covered while every submodule went unhashed. The stages that
    ARE fully covered list each submodule explicitly for exactly this reason.
    """
    return {name}


def import_closure(stage: str, imports: dict[str, set[str]], stages, declared_modules,
                   exempt=frozenset(), boundaries=frozenset()) -> set[str]:
    """Every first-party module ``stage`` reaches through imports, the stage module excluded.

    Follows module-level and lazy imports from the stage module through every module it
    reaches and stops at the stage's DECLARED synpp dependencies, whose code the DAG covers.
    Static, so it over-approximates the code a run executes: it can ask a token to hash a
    module the stage never calls, never the other way round. Step 2 stops after the stage
    module's own imports, the one-level boundary the audit note describes (ADR-0136).

    The walk also ends, without reporting the module, at an ``exempt`` module (one that never
    shapes a stage result, so neither it nor what it imports for its own purpose needs
    hashing) and at one of the stage's own ``boundaries``.
    """
    reached, frontier = set(), [stage]
    while frontier:
        module = frontier.pop()
        for target in imports.get(module, ()):
            if target == stage or target in reached:
                continue
            if target in stages and target in declared_modules:
                continue
            if target in exempt or target in boundaries:
                continue
            reached.add(target)
            frontier.append(target)
    return reached


def exemption_reasons(repo: Path) -> dict[str, str]:
    """{module: reason} of every first-party module that sets ``_SYNPP_TOKEN_EXEMPTION``."""
    reasons = {}
    for path in iter_py(repo):
        text = path.read_text(encoding="utf-8")
        if EXEMPTION_NAME not in text:
            continue
        name = module_name(path, repo)
        reason = exemption_reason(ast.parse(text), name)
        if reason is not None:
            reasons[name] = reason
    return reasons


def report_meta(repo: Path) -> dict:
    """Counts that describe the SCAN rather than any single stage."""
    return {"files": len(list(iter_py(repo)))}


def build_report(repo: Path) -> dict:
    """Run the whole four-step pass over ``repo`` and return the per-stage report.

    One entry per stage module, keyed by dotted name, carrying its module-level and lazy
    first-party imports, its declared ``context.stage`` edges, the resulting
    ``required_helpers``, whether its ``validate()`` hashes source, and -- for those that
    do -- the ``covered`` and ``uncovered`` sets. Separated from :func:`main` so the pass
    itself is testable without the CLI (tests/test_audit_synpp_helper_hash.py).
    """
    files = list(iter_py(repo))
    on_disk = {module_name(p, repo) for p in files}
    packages = {module_name(p, repo) for p in files if p.name == "__init__.py"}

    trees, stages = {}, {}
    for path in files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as error:
            print(f"SYNTAX ERROR {path}: {error}", file=sys.stderr)
            continue
        name = module_name(path, repo)
        trees[name] = tree
        funcs = top_level_funcs(tree)
        if {"configure", "execute"} <= funcs:
            stages[name] = path

    aliases = read_aliases(repo)
    # Step 5 needs the first-party imports of every module, not only of the stages.
    imports = {}
    exempt = set()
    for module, module_tree in trees.items():
        module_level_imports, lazy_imports = collect_imports(
            module_tree, module, on_disk, is_package=module in packages)
        imports[module] = module_level_imports | lazy_imports
        if exemption_reason(module_tree, module) is not None:
            exempt.add(module)
    # Which stages hash Python source at all (the note's `grep -rl "inspect.getsource"`).
    source_hashing = set()
    for name, path in stages.items():
        text = path.read_text(encoding="utf-8")
        if "inspect.getsource" in text and "def validate" in text:
            source_hashing.add(name)

    report = {}
    for name, path in sorted(stages.items()):
        tree = trees[name]
        module_level, lazy = collect_imports(tree, name, on_disk, is_package=name in packages)
        first_party = module_level | lazy
        declared = declared_stages(tree)
        declared_modules = {aliases.get(d, d) for d in declared}
        # A first-party import that IS a stage AND is declared is covered by synpp's DAG.
        covered_by_dag = {m for m in first_party if m in stages and m in declared_modules}
        required = first_party - covered_by_dag
        entry = {
            "module_level": sorted(module_level),
            "lazy": sorted(lazy),
            "declared": sorted(declared),
            "required_helpers": sorted(required),
            "hashes_source": name in source_hashing,
        }
        if name in source_hashing:
            alias_names, deferred, alias_map = helper_tuples(tree, name,
                                                             is_package=name in packages)
            read = names_read_by_validate(tree)
            if "_HELPER_MODULES" not in read:
                alias_names = set()
            if "_DEFERRED_HELPER_MODULE_NAMES" not in read:
                deferred = set()
            resolved = set()
            for alias in alias_names:
                target = resolve_module_binding(alias_map.get(alias, alias), trees, on_disk,
                                                packages)
                resolved |= covered_by_entry(target)
            for dotted in deferred:
                resolved |= covered_by_entry(dotted)
            boundaries = closure_boundaries(tree, name)
            closure = import_closure(name, imports, stages, declared_modules,
                                     exempt=exempt, boundaries=set(boundaries))
            walked = closure | {name}
            helper_entries, deferred_entries = tuple_entries(tree)
            hashed = [resolve_module_binding(alias_map.get(alias, alias), trees, on_disk, packages)
                      for alias in (helper_entries if "_HELPER_MODULES" in read else [])]
            hashed += deferred_entries if "_DEFERRED_HELPER_MODULE_NAMES" in read else []
            entry["hashed_twice"] = sorted({m for m in hashed if hashed.count(m) > 1})
            entry["covered"] = sorted(resolved)
            entry["uncovered"] = sorted(required - resolved - exempt)
            entry["transitive_uncovered"] = sorted(closure - resolved)
            entry["transitive_exempt"] = sorted(
                m for m in exempt if any(m in imports.get(w, ()) for w in walked))
            entry["closure_boundaries"] = sorted(boundaries)
            entry["unreached_boundaries"] = sorted(
                b for b in boundaries if not any(b in imports.get(w, ()) for w in walked))
        report[name] = entry

    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("repo_root", nargs="?", default=".",
                        help="repository root to audit (default: the current directory)")
    parser.add_argument("--json", dest="json_path", type=Path, default=None,
                        help="write the full per-stage report to this JSON file")
    args = parser.parse_args(argv)
    repo = Path(args.repo_root).resolve()
    report = build_report(repo)

    stages = {n for n, e in report.items()}
    cat_a = [n for n, e in report.items() if not e["required_helpers"]]
    cat_b = [n for n, e in report.items()
             if e["required_helpers"] and e["hashes_source"] and not e.get("uncovered")]
    cat_c = [n for n, e in report.items()
             if e["required_helpers"] and (not e["hashes_source"] or e.get("uncovered"))]

    source_hashing = sorted(n for n, e in report.items() if e["hashes_source"])
    per_root: dict[str, int] = {}
    for n in stages:
        root = n.split(".")[0]
        per_root[root] = per_root.get(root, 0) + 1

    print(f"py files scanned: {report_meta(repo)['files']}")
    print(f"stages: {len(stages)}")
    print("stages per root:", per_root)
    print(f"category (a) no first-party helpers: {len(cat_a)}")
    print(f"category (b) helpers, fully covered: {len(cat_b)} -> {sorted(cat_b)}")
    print(f"category (c) helpers, not/partly covered: {len(cat_c)}")
    print(f"stages whose validate() hashes source: {len(source_hashing)}")
    for n in source_hashing:
        e = report[n]
        print(f"  {n}: required {len(e['required_helpers'])}, "
              f"covered {len(e.get('covered', []))}, "
              f"uncovered {e.get('uncovered') or 'none'}, "
              f"transitive gap {len(e.get('transitive_uncovered', []))}")
    for module, reason in sorted(exemption_reasons(repo).items()):
        print(f"exempt from every token: {module} -- {reason}")
    for n in source_hashing:
        for boundary in report[n].get("closure_boundaries", []):
            print(f"closure boundary of {n}: {boundary}")
    if args.json_path is not None:
        args.json_path.parent.mkdir(parents=True, exist_ok=True)
        args.json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print("wrote", args.json_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
