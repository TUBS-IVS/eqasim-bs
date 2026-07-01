"""Fancy link-load network variants for the Braunschweig region (no labels, full-bleed).

Draws the FULL road network as fine hairlines (maximum branching), then overlays the
loaded links coloured + width-scaled by daily volume so arteries fan out dramatically.
Renders several styles (white / dark glow / turbo / heat) to compare. Purely decorative;
volumes from the 25% linkStats.
"""
from __future__ import annotations

import gzip
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import geopandas as gpd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.collections import LineCollection
from matplotlib.colors import LinearSegmentedColormap, LogNorm

HERE = Path(__file__).resolve().parent
ROOT = Path("c:/Users/bienzeisler/Documents/GitHub/eqasim-bs")
for parent in [HERE, *HERE.parents]:
    if (parent / "eqasim-data").is_dir():
        ROOT = parent
        break
DATA = ROOT / "eqasim-data"
CACHE = DATA / "cache_bs_25pct" / "matsim.simulation.run__c0346dec5de3c5b765e82ed35f3433e9.cache" / "simulation_output"
LINKSTATS = CACHE / "ITERS" / "it.50" / "50.linkstats.txt.gz"
NETWORK = CACHE / "output_network.xml.gz"
KREIS_PATH = DATA / "output_bs_25pct" / "simwrapper" / "kreis_socio.geojson"
OUT = HERE / "poster_maps"
CRS_METRIC = "EPSG:25832"


def log(m):
    print(f"[fancy] {m}", flush=True)


def load_network():
    nodes, fr, to = {}, [], []
    ctx = ET.iterparse(gzip.open(NETWORK), events=("end",))
    for _, el in ctx:
        if el.tag == "node":
            nodes[el.get("id")] = (float(el.get("x")), float(el.get("y")))
            el.clear()
        elif el.tag == "link":
            fr.append(el.get("from"))
            to.append(el.get("to"))
            el.clear()
    all_segs = np.array([[nodes[a], nodes[b]] for a, b in zip(fr, to)
                         if a in nodes and b in nodes])
    return nodes, all_segs


CMAP_WHITE_WEB = LinearSegmentedColormap.from_list("ww", ["#2563eb", "#7c3aed", "#db2777", "#dc2626"])
CMAP_NEON = LinearSegmentedColormap.from_list("neon",
            ["#15124a", "#4f46e5", "#c026d3", "#fb7185", "#fde68a", "#ffffff"])
CMAP_HEAT = LinearSegmentedColormap.from_list("heat",
            ["#fde68a", "#fb923c", "#ef4444", "#991b1b"])

STYLES = {
    "white_web": dict(bg="#ffffff", mesh="#dfe4ea", mesh_a=0.8, mesh_lw=0.28,
                      cmap=CMAP_WHITE_WEB, glow=False, sharp_a=0.92,
                      wbase=0.12, wscale=4.2, wpow=1.35, kreis="#475569", kreis_a=0.5),
    "dark_neon": dict(bg="#05070d", mesh="#101a2e", mesh_a=0.95, mesh_lw=0.3,
                      cmap=CMAP_NEON, glow=True, sharp_a=0.95,
                      wbase=0.12, wscale=4.6, wpow=1.25, kreis="#2c3e57", kreis_a=0.7),
    "dark_turbo": dict(bg="#05070d", mesh="#101a2e", mesh_a=0.95, mesh_lw=0.3,
                       cmap=plt.get_cmap("turbo"), glow=True, sharp_a=0.95,
                       wbase=0.12, wscale=4.6, wpow=1.25, kreis="#2c3e57", kreis_a=0.7),
    "white_heat": dict(bg="#ffffff", mesh="#e6e9ee", mesh_a=0.8, mesh_lw=0.28,
                       cmap=CMAP_HEAT, glow=False, sharp_a=0.95,
                       wbase=0.12, wscale=4.4, wpow=1.35, kreis="#334155", kreis_a=0.5),
}


def main():
    t0 = time.time()
    OUT.mkdir(exist_ok=True)
    log("network ...")
    nodes, all_segs = load_network()
    log(f"  {len(nodes):,} nodes, {len(all_segs):,} links (full mesh)")

    log("linkstats ...")
    df = pd.read_csv(LINKSTATS, sep="\t", low_memory=False)
    avg = [c for c in df.columns if c.startswith("HRS") and c.endswith("avg")]
    df["vol"] = df[avg].sum(axis=1)
    fr = df["FROM"].astype(str).to_numpy()
    to = df["TO"].astype(str).to_numpy()
    vol = df["vol"].to_numpy()
    segs, vols = [], []
    for a, b, v in zip(fr, to, vol):
        pa, pb = nodes.get(a), nodes.get(b)
        if pa is None or pb is None or v <= 0:
            continue
        segs.append([pa, pb]); vols.append(v)
    segs = np.array(segs); vols = np.array(vols)
    order = np.argsort(vols)
    segs, vols = segs[order], vols[order]
    norm = LogNorm(vmin=max(1, np.percentile(vols, 40)), vmax=vols.max())
    nv = np.clip(norm(vols), 0, 1)

    kreis = gpd.read_file(KREIS_PATH).to_crs(CRS_METRIC)
    cx, cy = 603990.0, 5792750.0
    half_w = 23000.0
    half_h = half_w * (10.7 / 19.0)
    ex = (cx - half_w, cx + half_w, cy - half_h, cy + half_h)

    for name, st in STYLES.items():
        t1 = time.time()
        colors = st["cmap"](nv)
        lws = st["wbase"] + st["wscale"] * (nv ** st["wpow"])
        fig = plt.figure(figsize=(19, 10.7), dpi=200)
        fig.patch.set_facecolor(st["bg"])
        ax = fig.add_axes([0, 0, 1, 1]); ax.set_facecolor(st["bg"])
        # full mesh hairlines (max branching)
        ax.add_collection(LineCollection(all_segs, colors=st["mesh"], linewidths=st["mesh_lw"],
                                         alpha=st["mesh_a"], zorder=1, capstyle="round"))
        # loaded links: optional additive glow (dark) + crisp width-scaled pass
        if st["glow"]:
            ax.add_collection(LineCollection(segs, colors=colors, linewidths=lws * 3.2,
                                             alpha=0.05, zorder=2, capstyle="round"))
        ax.add_collection(LineCollection(segs, colors=colors, linewidths=lws,
                                         alpha=st["sharp_a"], zorder=3, capstyle="round"))
        kreis.plot(ax=ax, facecolor="none", edgecolor=st["kreis"], linewidth=1.6,
                   alpha=st["kreis_a"], zorder=5)
        ax.set_xlim(ex[0], ex[1]); ax.set_ylim(ex[2], ex[3])
        ax.set_aspect("equal"); ax.set_axis_off()
        out = OUT / f"fancy_{name}.png"
        fig.savefig(out, dpi=200, facecolor=st["bg"])
        plt.close(fig)
        log(f"  saved {out.name} in {time.time()-t1:.1f}s")
    log(f"DONE in {time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
