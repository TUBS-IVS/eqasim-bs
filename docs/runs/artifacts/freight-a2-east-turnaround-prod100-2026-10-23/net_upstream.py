"""#465: shortest upstream driving distance (m) from a target link back to given links (breadth-first over
link lengths, from the end of each upstream link to the start of the target). Usage:
net_upstream.py <run> <target_link> <link,link,...>"""
import collections, gzip, sys, xml.etree.ElementTree as ET
run, target, others = sys.argv[1], sys.argv[2], sys.argv[3].split(",")
links = {}
with gzip.open(f"{run}/output_network.xml.gz") as f:
    for ev, el in ET.iterparse(f, events=("end",)):
        if el.tag == "link": links[el.attrib["id"]] = el.attrib; el.clear()
into = collections.defaultdict(list)
for l in links.values(): into[l["to"]].append(l["id"])
dist = {target: 0.0}; q = collections.deque([target])
while q:
    c = q.popleft()
    for u in into[links[c]["from"]]:
        d = dist[c] + float(links[u]["length"])
        if u not in dist and d < 8000: dist[u] = d; q.append(u)
for o in others: print(o, round(dist[o]) if o in dist else "not within 8 km upstream")
