"""Read-only check of a built standalone jar of eqasim-java-bs (C5f). Run with python -I.

Usage: python -I jarcheck.py <jar>
Prints: size, entry count, the Guice and ASM entries, the highest Java constant of the ASM Opcodes in the jar, and the class
file major versions of the org.eqasim and org.matsim classes.
"""
import collections
import os
import re
import struct
import sys
import zipfile

jar = sys.argv[1]
print(f"jar: {os.path.basename(jar)}, {os.path.getsize(jar):,} bytes")
with zipfile.ZipFile(jar) as z:
    names = z.namelist()
    classes = [n for n in names if n.endswith(".class")]
    print(f"entries: {len(names):,} (classes: {len(classes):,})")
    print("copies of com/google/inject/Guice.class:", sum(1 for n in names if n == "com/google/inject/Guice.class"))
    print("entries below com/google/inject/internal/asm/ (the embedded ASM):", sum(1 for n in names if n.startswith("com/google/inject/internal/asm/")))
    print("classes below com/google/inject/ (without the embedded ASM):", sum(1 for n in classes if n.startswith("com/google/inject/") and not n.startswith("com/google/inject/internal/asm/")))
    asm = [n for n in classes if n.startswith("org/objectweb/asm/") and n.count("/") == 3]
    print("classes of the plain org/objectweb/asm package:", len(asm))
    print("other ASM packages (commons, tree, util, signature):", sorted({"/".join(n.split("/")[:4]) for n in classes if n.startswith("org/objectweb/asm/") and n.count("/") > 3}))
    if "org/objectweb/asm/Opcodes.class" in names:
        data = z.read("org/objectweb/asm/Opcodes.class")
        versions = sorted({int(m) for m in re.findall(rb"V(\d+)(?![\d_])", data)})
        print("highest Java version constant of org/objectweb/asm/Opcodes:", f"V{versions[-1]}")
    else:
        print("no plain org/objectweb/asm/Opcodes in the jar")
    embedded = "com/google/inject/internal/asm/$Opcodes.class"
    if embedded in names:
        data = z.read(embedded)
        versions = sorted({int(m) for m in re.findall(rb"V(\d+)(?![\d_])", data)})
        print("highest Java version constant of the embedded ASM Opcodes:", f"V{versions[-1]}")
    meta = [n for n in names if n.startswith("META-INF/maven/org.ow2.asm/asm/") and n.endswith("pom.properties")]
    for n in meta:
        print("asm pom.properties:", z.read(n).decode().replace("\r", "").replace("\n", " ; "))
    hist = collections.Counter()
    for n in classes:
        if n.startswith(("org/eqasim/", "org/matsim/")):
            hist[struct.unpack(">H", z.read(n)[6:8])[0]] += 1
    print("class file major versions of the org.eqasim and org.matsim classes:", dict(sorted(hist.items())))
