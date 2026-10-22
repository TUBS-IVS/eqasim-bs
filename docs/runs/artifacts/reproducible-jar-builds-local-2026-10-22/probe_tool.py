"""Probe for issue #474: build the freight tool jar twice in two working directories and diff.

Usage: python probe_tool.py <root> [--baseline] [extra maven arguments...]

Mirrors braunschweig.freight.extraction_tool.execute (rendered pom, wrapper copy, Maven
package with -Djava.io.tmpdir) in two directories whose path lengths differ by 16
characters ("aaa" vs "bbbbbbbbbbbb_longer"), then lists every file whose size or sha256
differs and, for the jar, every entry whose CRC, DOS timestamp or extra field differs.
--baseline removes the project.build.outputTimestamp property from the rendered pom, which
reproduces the build before the fix. Maven runs offline (-o) against the local repository.
"""
import hashlib, os, shutil, subprocess, sys, zipfile

WT = os.environ.get("I474_WORKTREE", r"C:\Users\bienzeisler\Documents\GitHub\eqasim-bs\.claude\worktrees\i474-reproducible-jars")
sys.path.insert(0, WT)
import braunschweig.freight.extraction_tool as tool

MVN = os.environ.get("I474_MVN", r"C:\Users\bienzeisler\tools\apache-maven-3.9.9\bin\mvn.cmd")
FORK_POM = os.environ.get("I474_FORK_POM", r"C:\Users\bienzeisler\Documents\GitHub\eqasim-java-bs\pom.xml")
ROOT = os.path.realpath(sys.argv[1])
BASELINE = "--baseline" in sys.argv[2:]
EXTRA = [a for a in sys.argv[2:] if a != "--baseline"]

mv = tool.read_pom_property(FORK_POM, "matsim.version")
jr = tool.read_pom_property(FORK_POM, "maven.compiler.source")


def render():
    pom = tool.render_pom(mv, jr)
    if BASELINE:
        lines = [l for l in pom.splitlines(True) if "project.build.outputTimestamp" not in l]
        assert len(lines) == len(pom.splitlines(True)) - 1
        pom = "".join(lines)
    return pom


def build(name):
    base = os.path.join(ROOT, name)
    if os.path.exists(base):
        shutil.rmtree(base)
    project = os.path.join(base, "freight-tool")
    os.makedirs(os.path.dirname(os.path.join(project, tool.WRAPPER_TARGET)))
    with open(os.path.join(project, "pom.xml"), "w", encoding="utf-8") as h:
        h.write(render())
    shutil.copy(tool.WRAPPER_SOURCE, os.path.join(project, tool.WRAPPER_TARGET))
    tmp = os.path.join(base, "__java_temp"); os.mkdir(tmp)
    subprocess.check_call([MVN, "-q", "-o", "-Djava.io.tmpdir=%s" % tmp, "package", "-DskipTests=true"] + EXTRA, cwd=project)
    return base


def listing(base):
    out = {}
    for r, d, f in os.walk(base):
        for n in f:
            p = os.path.join(r, n)
            out[os.path.relpath(p, base).replace(os.sep, "/")] = (os.path.getsize(p), hashlib.sha256(open(p, "rb").read()).hexdigest()[:12])
    return out


print("baseline (no outputTimestamp in pom):", BASELINE, "extra maven arguments:", EXTRA)
a, b = build("aaa"), build("bbbbbbbbbbbb_longer")
la, lb = listing(a), listing(b)
diffs = [k for k in sorted(set(la) | set(lb)) if la.get(k) != lb.get(k)]
print("files", len(la), len(lb), "differing", len(diffs))
for k in diffs:
    print("DIFF", k, la.get(k), lb.get(k))
jar = tool.JAR_RELATIVE % mv
za, zb = zipfile.ZipFile(os.path.join(a, jar)), zipfile.ZipFile(os.path.join(b, jar))
ia = {i.filename: i for i in za.infolist()}; ib = {i.filename: i for i in zb.infolist()}
print("JAR", jar, "size", os.path.getsize(os.path.join(a, jar)), os.path.getsize(os.path.join(b, jar)),
      "entries", len(ia), len(ib), "order same:", [i.filename for i in za.infolist()] == [i.filename for i in zb.infolist()])
nd = 0
for k in ia:
    x, y = ia[k], ib.get(k)
    if y is None or x.CRC != y.CRC or x.date_time != y.date_time or x.extra != y.extra:
        nd += 1
        print(" ENTRY", k, x.date_time, y and y.date_time, "crc same", x.CRC == (y and y.CRC), "extra same", x.extra == (y and y.extra))
print(" entry diffs", nd)
