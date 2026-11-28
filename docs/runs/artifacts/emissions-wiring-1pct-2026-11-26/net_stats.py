import sys, re, collections, gzip, math
c = collections.Counter(); km = collections.Counter(); sp = collections.Counter(); links = 0
cur = False
for line in gzip.open(sys.argv[1], "rt", encoding="utf-8"):
    if "<link " in line:
        links += 1
        fs = float(re.search(r"freespeed=\"([^\"]+)\"", line).group(1))
        ln = float(re.search(r" length=\"([^\"]+)\"", line).group(1))
        modes = re.search(r"modes=\"([^\"]*)\"", line).group(1).split(",")
        hw = None; cur = True
    elif cur and "osm:way:highway" in line:
        hw = re.search(r">([^<]*)<", line).group(1)
    elif cur and "</link>" in line:
        drv = ("car" in modes) or ("truck" in modes)
        key = (hw or "<none>", drv); c[key] += 1; km[key] += ln / 1000
        if drv:
            sp[(hw or "<none>", "inf" if math.isinf(fs) else round(fs * 3.6))] += 1
        cur = False
print("links", links)
for k, v in sorted(c.items(), key=lambda x: -x[1]): print(k, v, round(km[k], 1))
print("speeds (drivable only)")
for k, v in sorted(sp.items(), key=lambda x: (x[0][0], str(x[0][1]))): print(k, v)
