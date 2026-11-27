"""#678 B9 step 2 diagnosis: write the first <n> persons that carry a subpopulation attribute (freight in
prod100_20261031) from a population file into a small population, header kept, persons copied as text unchanged.

Usage: python i678_cut_subpopulation.py <population.xml.gz> <out.xml.gz> <n>
"""
import gzip
import sys

source, target, limit = sys.argv[1], sys.argv[2], int(sys.argv[3])
written, block, header_done = 0, [], False
with gzip.open(source, "rt", encoding="utf-8") as handle, gzip.open(target, "wt", encoding="utf-8") as out:
    for line in handle:
        if not header_done:
            if "<person " in line:
                header_done = True
            else:
                out.write(line)
                continue
        if "<person " in line:
            block = [line]
            continue
        if block:
            block.append(line)
            if "</person>" in line:
                text = "".join(block)
                block = []
                if 'name="subpopulation"' in text:
                    out.write(text)
                    written += 1
                    if written >= limit:
                        break
    out.write("</population>\n")
print(f"written {written} persons with a subpopulation attribute to {target}")
