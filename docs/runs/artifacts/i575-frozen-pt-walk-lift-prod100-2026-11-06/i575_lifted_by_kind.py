"""#575 ADR-0189: split the lifted trips of the replay by person kind (in-commuter plan or resident).

prod100_20261031 predates #566: its in-commuters carry no ``subpopulation`` attribute, so the lift's resident filter
cannot tell them apart there. This script counts, in the lifted population, the lifted legs (``liftedFrom``) of persons
whose plan uses an in-commuter facility (``ic_`` prefix, the B3 probe's rule) and of all other persons.

Usage: python i575_lifted_by_kind.py <lifted_population.xml.gz>
"""
import collections
import gzip
import sys
import xml.etree.ElementTree as ET

counts = collections.Counter()
root = None
for event, element in ET.iterparse(gzip.open(sys.argv[1], "rb"), events=("start", "end")):
    if event == "start":
        root = root if root is not None else element
        continue
    if element.tag != "person":
        continue
    lifted = sum(1 for a in element.iter("attribute") if a.get("name") == "liftedFrom")
    if lifted:
        kind = "incommuter" if any((a.get("facility") or "").startswith("ic_") for a in element.iter("activity")) \
            else "resident"
        counts[kind + "_persons"] += 1
        counts[kind + "_legs"] += lifted
    element.clear()
    root.clear()
print(dict(counts))
