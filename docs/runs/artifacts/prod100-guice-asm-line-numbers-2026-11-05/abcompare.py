"""Compare two Melun run directories of the C5f A/B (read-only; run with python -I).

Usage: python -I abcompare.py <dirA> <dirB>
For every output file whose SHA-256 differs: for gzip text files, compare the multiset of lines (is it only an ordering
difference?) and report the first differing line; modules.dot and stopwatch files are shown separately.
"""
import collections
import difflib
import gzip
import hashlib
import os
import sys


def files(root):
    out = {}
    base = os.path.join(root, "melun_test", "output")
    for dirpath, _, names in os.walk(base):
        for n in names:
            if n.startswith("logfile"):
                continue
            p = os.path.join(dirpath, n)
            out[os.path.relpath(p, base)] = p
    return out


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def lines(path):
    if path.endswith(".gz"):
        with gzip.open(path, "rt", encoding="utf-8", errors="replace") as f:
            return f.read().splitlines()
    with open(path, "rt", encoding="utf-8", errors="replace") as f:
        return f.read().splitlines()


def main(a, b):
    fa, fb = files(a), files(b)
    print(f"files: A={len(fa)} B={len(fb)}; only in A: {sorted(set(fa) - set(fb))[:3]}; only in B: {sorted(set(fb) - set(fa))[:3]}")
    diff = [k for k in sorted(fa) if k in fb and sha(fa[k]) != sha(fb[k])]
    print(f"files with different SHA-256: {len(diff)}")
    for k in diff:
        if k.endswith((".png",)) or k == "stopwatch.csv":
            print(f"  {k}: timing/plot output (expected to differ between any two runs)")
            continue
        la, lb = lines(fa[k]), lines(fb[k])
        same_multiset = collections.Counter(la) == collections.Counter(lb)
        first = next((i for i, (x, y) in enumerate(zip(la, lb)) if x != y), None)
        print(f"  {k}: lines A={len(la)} B={len(lb)}; same multiset of lines: {same_multiset}; first differing line index: {first}")
        if k == "modules.dot":
            for d in list(difflib.unified_diff(la, lb, "A", "B", lineterm="", n=0))[:12]:
                print("      " + d[:230])


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
