"""Read-only jar inspection for the C5f investigation (class file major versions, ASM level).

Usage: python -I jarinfo.py <jar> [<jar> ...]
For every jar: histogram of class-file major versions, the first example class per version, and for
any (shaded or plain) ASM `Opcodes` class the highest `V<n>` Java-version constant it defines.
"""
import collections
import re
import struct
import sys
import zipfile

JAVA = {52: 8, 53: 9, 54: 10, 55: 11, 56: 12, 57: 13, 58: 14, 59: 15, 60: 16, 61: 17, 62: 18, 63: 19,
        64: 20, 65: 21, 66: 22, 67: 23, 68: 24, 69: 25, 70: 26}


def main(paths):
    for path in paths:
        print("=" * 100)
        print(path)
        hist = collections.Counter()
        example = {}
        opcodes = []
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            for n in names:
                if not n.endswith(".class"):
                    continue
                data = z.read(n)
                major = struct.unpack(">H", data[6:8])[0]
                hist[major] += 1
                example.setdefault(major, n)
                base = n.rsplit("/", 1)[-1]
                if base in ("Opcodes.class", "$Opcodes.class"):
                    opcodes.append((n, data))
        for major in sorted(hist):
            print(f"  class file major {major} (Java {JAVA.get(major, '?')}): {hist[major]} classes, e.g. {example[major]}")
        for n, data in opcodes:
            versions = sorted({int(m) for m in re.findall(rb"V(\d+)(?![\d_])", data)})
            print(f"  ASM Opcodes: {n}: highest V<n> constant = V{versions[-1] if versions else '?'}")
        meta = [n for n in names if n.startswith("META-INF/maven/") and n.endswith("pom.properties")]
        for n in meta[:10]:
            print("  " + n, "|", z.read(n).decode("utf-8", "replace").replace("\n", " ; ")[:160]) if False else None


if __name__ == "__main__":
    main(sys.argv[1:])
