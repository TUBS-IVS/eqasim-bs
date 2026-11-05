"""Details of the C5f A/B runs (read-only; python -I abdetails.py <ab-dir>)."""
import csv
import gzip
import hashlib
import io
import os
import re
import sys

ab = sys.argv[1]
runs = ["melun-before", "melun-before2", "melun-after", "melun-after2"]


def out(run, name):
    return os.path.join(ab, run, "melun_test", "output", name)


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        h.update(f.read())
    return h.hexdigest()[:12]


print("== byte-identical key results in all four runs?")
for name in ["output_plans.xml.gz", "scorestats.csv", "modestats.csv", "traveldistancestats.csv", "pHrsStats.csv",
             "output_network.xml.gz", "output_config.xml", "output_households.xml.gz", "output_facilities.xml.gz"]:
    if os.path.exists(out(runs[0], name)):
        hashes = [sha(out(r, name)) for r in runs]
        print(f"  {name:28s} {hashes}  all equal: {len(set(hashes)) == 1}")

print("== output_links.csv.gz: after vs after2")
def rows(path):
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return list(csv.reader(f, delimiter=";"))
la, lb = rows(out("melun-after", "output_links.csv.gz")), rows(out("melun-after2", "output_links.csv.gz"))
print("  header:", la[0][:8], "... rows:", len(la), len(lb))
diff_rows = [(i, a, b) for i, (a, b) in enumerate(zip(la, lb)) if a != b]
print("  differing rows:", len(diff_rows))
for i, a, b in diff_rows[:3]:
    cols = [(la[0][k], a[k], b[k]) for k in range(len(a)) if a[k] != b[k]]
    print("   row", i, "differing columns:", cols[:4])
print("  before == before2 == after for this file:",
      sha(out("melun-before", "output_links.csv.gz")), sha(out("melun-before2", "output_links.csv.gz")),
      sha(out("melun-after", "output_links.csv.gz")), sha(out("melun-after2", "output_links.csv.gz")))

print("== modules.dot (Guice grapher output): labels without a source file, 'null:1', and labels with a real file:line")
for r in runs:
    text = open(out(r, "modules.dot"), encoding="utf-8").read()
    null_labels = len(re.findall(r">null:1<", text))
    real = len(re.findall(r">[A-Za-z0-9_$]+\.java:\d+<", text))
    print(f"  {r:14s} 'null:1' labels = {null_labels:4d}   'File.java:NN' labels = {real:4d}")
