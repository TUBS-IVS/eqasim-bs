"""Token behaviour on the real fork (#468 Phase 2 server check); run from the eqasim-bs worktree.

Usage: python token_check_i468.py <fork worktree A> <fork worktree B (same commit)> <java file in B to edit>

Prints the matsim.runtime.eqasim content digest (source_tree_digest) and the
braunschweig.freight.extraction_tool token for: A; B (a second worktree of the same commit at another
path); B after appending a comment line to one non-freight Java file. Expected: A == B for both tokens;
after the edit the eqasim digest changes and the freight tool token does not. The edit is reverted.
"""
import json
import sys

from braunschweig.freight import extraction_tool
from matsim.runtime import eqasim


class _Context:
    def __init__(self, source):
        self.values = {"eqasim_source_path": source, "freight_tool_matsim_version": ""}

    def config(self, key, *args, **kwargs):
        return self.values[key]


def tokens(source):
    return {"eqasim_source_digest": eqasim.source_tree_digest(source),
            "freight_tool_token": extraction_tool.validate(_Context(source))}


a, b, java_file = sys.argv[1:4]
report = {"A": tokens(a), "B_same_commit_other_path": tokens(b)}
original = open(java_file, "rb").read()
try:
    with open(java_file, "ab") as handle:
        handle.write(b"\n// i468 token check: a non-freight Java edit\n")
    report["B_after_java_edit"] = tokens(b)
finally:
    with open(java_file, "wb") as handle:
        handle.write(original)
report["B_after_revert"] = tokens(b)
report["checks"] = {
    "same_commit_other_path_same_eqasim_digest":
        report["A"]["eqasim_source_digest"] == report["B_same_commit_other_path"]["eqasim_source_digest"],
    "same_commit_other_path_same_freight_token":
        report["A"]["freight_tool_token"] == report["B_same_commit_other_path"]["freight_tool_token"],
    "java_edit_changes_eqasim_digest":
        report["B_after_java_edit"]["eqasim_source_digest"] != report["B_same_commit_other_path"]["eqasim_source_digest"],
    "java_edit_keeps_freight_token":
        report["B_after_java_edit"]["freight_tool_token"] == report["B_same_commit_other_path"]["freight_tool_token"],
    "revert_restores_eqasim_digest":
        report["B_after_revert"]["eqasim_source_digest"] == report["B_same_commit_other_path"]["eqasim_source_digest"],
}
print(json.dumps(report, indent=1))
