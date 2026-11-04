"""Read-only census of a synpp pipeline.json (run manifest prod100-expected-message-levels-2026-11-04).

Usage: python measure_pipeline_json_markers.py <pipeline.json or a backup of it> [max legacy entries to list]
Counts entries by module-hash format (synpp 1.6.2 md5, 32 hex digits, versus the fork's digest) and by the fork
markers (output_hash digest, fork key). Reads one file, writes nothing.
"""
import json
import re
import sys

path = sys.argv[1]
max_list = int(sys.argv[2]) if len(sys.argv) > 2 else 10
with open(path, encoding="utf-8") as handle:
    meta = json.load(handle)
print("file:", path)

legacy_hash = re.compile(r"[0-9a-f]{32}")
rows = []
for entry, record in sorted(meta.items()):
    module_hash = record.get("module_hash")
    is_legacy = isinstance(module_hash, str) and legacy_hash.fullmatch(module_hash) is not None
    rows.append((entry, is_legacy, bool((record.get("output_hash") or {}).get("digest")), "fork" in record))

print("entries:", len(rows))
print("legacy md5 module hash:", sum(1 for r in rows if r[1]))
print("with output_hash:", sum(1 for r in rows if r[2]))
print("with fork marker:", sum(1 for r in rows if r[3]))
print("legacy AND output_hash:", sum(1 for r in rows if r[1] and r[2]))
legacy_rows = [row for row in rows if row[1]]
print("legacy entries (first %d of %d):" % (min(max_list, len(legacy_rows)), len(legacy_rows)))
for entry, is_legacy, has_output, has_fork in legacy_rows[:max_list]:
    print("  %s output_hash=%s fork=%s" % (entry, has_output, has_fork))
