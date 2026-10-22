"""Probe for issue #474: build the eqasim jar twice in two working directories and diff.

Usage: python probe_eqasim.py <root> [extra maven arguments...]

Mirrors the source-build path of matsim.runtime.eqasim.execute (copytree of the fork work
tree without target/.git/.claude, then Maven -Pstandalone --projects braunschweig
--also-make package -DskipTests=true with -Djava.io.tmpdir) in two directories whose path
lengths differ by 16 characters ("aaa" vs "bbbbbbbbbbbb_longer"), then lists every file
whose size or sha256 differs and, for each differing jar, the entries whose CRC, DOS
timestamp or extra field differs (the first 8 per jar, plus every CRC difference).
Without extra arguments this is the build before the fix; the fix adds
-Dproject.build.outputTimestamp=... . Maven runs offline (-o) against the local repository.
"""
import hashlib, os, shutil, subprocess, sys, zipfile

MVN = os.environ.get("I474_MVN", r"C:\Users\bienzeisler\tools\apache-maven-3.9.9\bin\mvn.cmd")
SOURCE = os.environ.get("I474_FORK", r"C:\Users\bienzeisler\Documents\GitHub\eqasim-java-bs")
ROOT = os.path.realpath(sys.argv[1])
EXTRA = sys.argv[2:]


def build(name):
    base = os.path.join(ROOT, name)
    if os.path.exists(base):
        shutil.rmtree(base)
    dest = os.path.join(base, "eqasim-java")
    shutil.copytree(SOURCE, dest, ignore=shutil.ignore_patterns("target", ".git", ".claude"))
    tmp = os.path.join(base, "__java_temp"); os.mkdir(tmp)
    subprocess.check_call([MVN, "-q", "-o", "-Djava.io.tmpdir=%s" % tmp, "-Pstandalone", "--projects", "braunschweig",
                           "--also-make", "package", "-DskipTests=true"] + EXTRA, cwd=dest)
    return base


def listing(base):
    out = {}
    for r, d, f in os.walk(base):
        for n in f:
            p = os.path.join(r, n)
            out[os.path.relpath(p, base).replace(os.sep, "/")] = (os.path.getsize(p), hashlib.sha256(open(p, "rb").read()).hexdigest()[:12])
    return out


a, b = build("aaa"), build("bbbbbbbbbbbb_longer")
la, lb = listing(a), listing(b)
diffs = [k for k in sorted(set(la) | set(lb)) if la.get(k) != lb.get(k)]
print("files", len(la), len(lb), "differing", len(diffs))
for k in diffs:
    print("DIFF", k, la.get(k), lb.get(k))
for k in diffs:
    if k.endswith(".jar") and k in la and k in lb:
        za, zb = zipfile.ZipFile(os.path.join(a, k)), zipfile.ZipFile(os.path.join(b, k))
        ia = {i.filename: i for i in za.infolist()}; ib = {i.filename: i for i in zb.infolist()}
        print(" JAR", k, "entries", len(ia), len(ib), "order same:", list(ia) == list(ib))
        nd = 0
        for e in ia:
            x, y = ia[e], ib.get(e)
            if y is None or x.CRC != y.CRC or x.date_time != y.date_time or x.extra != y.extra:
                nd += 1
                if nd <= 8 or x.CRC != (y and y.CRC):
                    print("  ENTRY", e, x.date_time, y and y.date_time, "crc same", x.CRC == (y and y.CRC), "extra same", x.extra == (y and y.extra))
        print("  entry diffs", nd)
