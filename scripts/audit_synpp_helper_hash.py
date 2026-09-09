"""Re-run the synpp helper-hash coverage audit (docs/codebase/notes/synpp-helper-hash-audit.md).

Implements that note's own four-step method as an executable pass, so the snapshot can be
refreshed instead of hand-patched (issue #327).

Step 1  enumerate stages       -- a module is a stage if ast.parse finds top-level
                                  FunctionDefs named `configure` AND `execute`.
Step 2  collect own imports    -- whole-AST walk, module-level vs lazy (inside a function),
                                  TYPE_CHECKING-guarded excluded, resolved to first-party
                                  on-disk modules.
Step 3  declared stage edges   -- literal context.stage("name") calls, resolved through the
                                  `aliases:` block of configs/base_bs.yml.
                                  required_helpers = first-party imports - declared stages.
Step 4  validate() coverage    -- stages whose validate() hashes source; their
                                  _HELPER_MODULES / _DEFERRED_HELPER_MODULE_NAMES read
                                  STATICALLY, packages enumerated one level deep.

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


def resolve_relative(mod: str | None, package: str, level: int) -> str:
    bits = package.rsplit(".", level - 1)
    base = bits[0]
    return f"{base}.{mod}" if mod else base


def collect_imports(tree, own_module: str, on_disk: set[str]):
    """Return (module_level, lazy) sets of first-party module names this module imports."""
    lazy_ids = lazy_nodes(tree)
    module_level, lazy = set(), set()
    package = own_module if (own_module in on_disk and "." in own_module) else own_module
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


def helper_tuples(tree, own_module: str) -> tuple[set[str], set[str], dict[str, str]]:
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
            base = (resolve_relative(node.module, own_module, node.level)
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


def expand_one_level(name: str, on_disk: set[str], packages: set[str]) -> set[str]:
    """A package contributes its __init__ plus each submodule on disk (one level deep)."""
    out = {name}
    if name in packages:
        prefix = name + "."
        out |= {m for m in on_disk
                if m.startswith(prefix) and "." not in m[len(prefix):]}
    return out


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
    # Which stages hash Python source at all (the note's `grep -rl "inspect.getsource"`).
    source_hashing = set()
    for name, path in stages.items():
        text = path.read_text(encoding="utf-8")
        if "inspect.getsource" in text and "def validate" in text:
            source_hashing.add(name)

    report = {}
    for name, path in sorted(stages.items()):
        tree = trees[name]
        module_level, lazy = collect_imports(tree, name, on_disk)
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
            alias_names, deferred, alias_map = helper_tuples(tree, name)
            resolved = set()
            for alias in alias_names:
                target = alias_map.get(alias, alias)
                resolved |= expand_one_level(target, on_disk, packages)
            for dotted in deferred:
                resolved |= expand_one_level(dotted, on_disk, packages)
            entry["covered"] = sorted(resolved)
            entry["uncovered"] = sorted(required - resolved)
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
              f"uncovered {e.get('uncovered') or 'none'}")
    if args.json_path is not None:
        args.json_path.parent.mkdir(parents=True, exist_ok=True)
        args.json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print("wrote", args.json_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
