"""Which stage hashes and tokens does a change touch? (read-only; docs/runs/prod100-java-heap-driver-rss-2026-11-04.yml)

Usage (from the repository root, in the pipeline environment):
    python docs/runs/artifacts/prod100-java-heap-driver-rss-2026-11-04/cache_impact.py <base ref> <path> [<path> ...]

For every given module this prints the semantic digest (braunschweig.synpp_tokens.semantic_module_digest, the
digest synpp's stage hash and every validate() token use) at the base ref and in the working tree, and from the
helper-hash audit of the working tree (scripts/audit_synpp_helper_hash.py) the source-hashing stage tokens
that hash the module. A module whose digest is EQUAL costs no recompute. A CHANGED module recomputes the stage
it is the module of and every stage whose token hashes it; the stages downstream of those are spared by early
cutoff only where their output digest comes out unchanged (docs/codebase/notes/synpp-fork.md). ast.dump differs
between Python minors, so the interpreter version is printed with the result.
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO))

from braunschweig import synpp_tokens  # noqa: E402


def module_name(path):
    parts = list(Path(path).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def run_audit():
    with tempfile.TemporaryDirectory() as folder:
        target = Path(folder) / "audit.json"
        subprocess.run([sys.executable, str(REPO / "scripts" / "audit_synpp_helper_hash.py"), str(REPO),
                        "--json", str(target)], check=True, capture_output=True)
        return json.loads(target.read_text(encoding="utf-8"))


def main(base_ref, paths):
    audit = run_audit()
    print("python %d.%d, base ref %s" % (sys.version_info[0], sys.version_info[1], base_ref))
    for path in paths:
        name = module_name(path)
        current = synpp_tokens.semantic_module_digest((REPO / path).read_text(encoding="utf-8"))
        shown = subprocess.run(["git", "show", "%s:%s" % (base_ref, path)], cwd=REPO, capture_output=True,
                               text=True, encoding="utf-8")
        if shown.returncode != 0:
            verdict, before = "NEW", "(not in base ref)"
        else:
            before = synpp_tokens.semantic_module_digest(shown.stdout)
            verdict = "digest EQUAL" if before == current else "digest CHANGED"
        holders = sorted(stage for stage, entry in audit.items() if name in (entry.get("covered") or []))
        print("%-36s %-15s base=%s now=%s" % (path, verdict, before[:12], current[:12]))
        print("    hashed by %d source-hashing stage token(s): %s" % (len(holders), ", ".join(holders) or "-"))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2:])
