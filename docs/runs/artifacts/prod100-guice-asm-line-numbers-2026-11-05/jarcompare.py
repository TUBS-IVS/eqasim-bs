"""Inspect the unshaded Guice `classes` jar against the default Guice jar (read-only).

Usage: python -I jarcompare.py <guice.jar> <guice-classes.jar>
Prints: entry counts, class file major versions, which ASM packages the classes reference, and whether the
classes of the unshaded jar still name the shaded ASM package.
"""
import collections
import re
import struct
import sys
import zipfile


def main(default_jar, classes_jar):
    with zipfile.ZipFile(default_jar) as za, zipfile.ZipFile(classes_jar) as zb:
        a_cls = {n for n in za.namelist() if n.endswith(".class")}
        b_cls = {n for n in zb.namelist() if n.endswith(".class")}
        shaded = "com/google/inject/internal/asm/"
        print(f"default jar : {len(a_cls)} classes, of which shaded ASM: {sum(1 for n in a_cls if n.startswith(shaded))}")
        print(f"classes jar : {len(b_cls)} classes, of which shaded ASM: {sum(1 for n in b_cls if n.startswith(shaded))}")
        print("classes only in the classes jar:", sorted(b_cls - a_cls)[:5])
        print("classes only in the default jar that are NOT shaded ASM:",
              sorted(n for n in a_cls - b_cls if not n.startswith(shaded))[:5])

        for label, z, names in (("default", za, a_cls), ("classes", zb, b_cls)):
            hist = collections.Counter(struct.unpack(">H", z.read(n)[6:8])[0] for n in names)
            print(f"{label} jar class file major versions:", dict(sorted(hist.items())))

        refs = collections.defaultdict(set)
        for n in sorted(b_cls):
            data = zb.read(n)
            for m in set(re.findall(rb"org/objectweb/asm/[A-Za-z/]*", data)):
                refs[m.decode().rsplit("/", 1)[0]].add(n)
        print("ASM packages referenced by the classes jar:")
        for pkg, users in sorted(refs.items()):
            print(f"  {pkg}: {len(users)} classes, e.g. {sorted(users)[:4]}")
        leftovers = [n for n in b_cls if b"com/google/inject/internal/asm" in zb.read(n)]
        print("classes of the classes jar naming the shaded ASM package:", len(leftovers), leftovers[:3])

        # does the default jar's LineNumbers name the shaded package, and the classes jar's the plain one?
        ln = "com/google/inject/internal/util/LineNumbers.class"
        print("default LineNumbers names shaded ASM:", b"com/google/inject/internal/asm/$ClassReader" in za.read(ln))
        print("classes LineNumbers names plain ASM :", b"org/objectweb/asm/ClassReader" in zb.read(ln))

        meta = [n for n in zb.namelist() if n.startswith("META-INF/") and not n.endswith("/")]
        print("classes jar META-INF entries:", meta[:12])


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
