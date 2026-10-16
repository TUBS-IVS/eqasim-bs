"""Compare the ramp path the stuck trucks use with the best alternative between the same two nodes. Read-only.

Usage: python ramp_paths_i444.py <output_network.xml.gz> <split_node> <merge_node> <ramp_link,ramp_link,...>

For three cost functions -- length, car free-flow time (freespeed), truck free-flow time
(min(freespeed, 80 km/h), the freight_truck_max_velocity_kmh base value) -- prints the cost of the given
ramp path and of the cheapest path between split and merge node (Dijkstra restricted to links that allow
truck), with its link list and highway classes. Also prints the cheapest path that avoids every link of
the ramp path, so the main-carriageway alternative is visible even when the ramp path is cheapest.
"""
import gzip
import heapq
import json
import sys
import xml.etree.ElementTree as ET

net, split, merge, ramp = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4].split(",")
TRUCK_MAX_MS = 80.0 / 3.6

links, out_of = {}, {}
for _, el in ET.iterparse(gzip.open(net, "rb"), events=("end",)):
    if el.tag == "link":
        if "truck" in el.get("modes", "").split(","):
            hw = None
            attrs = el.find("attributes")
            if attrs is not None:
                hw = next((a.text for a in attrs if a.get("name") == "osm:way:highway"), None)
            l = {"from": el.get("from"), "to": el.get("to"), "len": float(el.get("length")),
                 "v": float(el.get("freespeed")), "cap": float(el.get("capacity")), "hw": hw}
            links[el.get("id")] = l
            out_of.setdefault(l["from"], []).append(el.get("id"))
        el.clear()
    elif el.tag == "node":
        el.clear()

COSTS = {"length_m": lambda l: l["len"], "car_time_s": lambda l: l["len"] / l["v"],
         "truck_time_s": lambda l: l["len"] / min(l["v"], TRUCK_MAX_MS)}


def dijkstra(cost, banned=frozenset()):
    best, prev, heap = {split: 0.0}, {}, [(0.0, split)]
    while heap:
        c, n = heapq.heappop(heap)
        if n == merge:
            break
        if c > best.get(n, 1e18) or c > 5000:
            continue
        for lid in out_of.get(n, []):
            if lid in banned:
                continue
            l = links[lid]
            nc = c + cost(l)
            if nc < best.get(l["to"], 1e18):
                best[l["to"]], prev[l["to"]] = nc, lid
                heapq.heappush(heap, (nc, l["to"]))
    if merge not in best:
        return None
    path, n = [], merge
    while n != split:
        path.append(prev[n])
        n = links[prev[n]]["from"]
    return round(best[merge], 1), path[::-1]


assert links[ramp[0]]["from"] == split and links[ramp[-1]]["to"] == merge, "ramp path does not span split -> merge"
res = {}
for name, cost in COSTS.items():
    best = dijkstra(cost)
    avoid = dijkstra(cost, frozenset(ramp))
    res[name] = {
        "ramp_path": round(sum(cost(links[x]) for x in ramp), 1),
        "cheapest": best and {"cost": best[0], "links": [(x, links[x]["hw"], links[x]["cap"]) for x in best[1]]},
        "cheapest_avoiding_ramp": avoid and {"cost": avoid[0], "links": [(x, links[x]["hw"], links[x]["cap"],
                                                                         round(links[x]["v"] * 3.6)) for x in avoid[1]]}}
print(json.dumps(res, indent=1))
