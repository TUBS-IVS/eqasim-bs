"""Vehicle departures per hour of a MATSim transit schedule (departure times, not offsets). Read-only.
Usage: python schedule_hours_i465.py <transitSchedule.xml.gz> [...]"""
import gzip, sys, re
from collections import Counter
pat = re.compile(rb"<departure [^>]*departureTime=\"(\d+):(\d+):(\d+)\"")
for path in sys.argv[1:]:
    c = Counter()
    with gzip.open(path, "rb") as h:
        for line in h:
            for m in pat.finditer(line):
                c[int(m.group(1))] += 1
    print(path); print(" total", sum(c.values()), " per hour", dict(sorted(c.items())))
