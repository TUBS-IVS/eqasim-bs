"""MIV (car) network link-load 'glow' map for the Braunschweig region.

Uses the PRECOMPUTED MATSim linkStats (25% run, iter 50) -> daily volume per link,
joined to the run network node coordinates -> drawn as a glowing weighted network
(the classic traffic-flow look). No need to parse the 3.7 GB event file.

NOTE / honesty: in this eqasim run public transport is teleported (no TransitDriverStarts
events, no PT vehicles on network links), so link loads are essentially CAR (MIV) only.
A true PT link load is not available from this run; the ÖV counterpart is the demand
(desire-line) map produced elsewhere.
"""
from __future__ import annotations

import glob
import gzip
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import geopandas as gpd
import matplotlib
matplotlib.use("Agg")
import matplotlib.font_manager as fm
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

FONT_DIR = HERE / "fonts"
SPACE_MONO = "DejaVu Sans Mono"
for ttf in sorted(FONT_DIR.glob("SpaceMono-*.ttf")):
    fm.fontManager.addfont(str(ttf))
    SPACE_MONO = fm.FontProperties(fname=str(ttf)).get_name()
plt.rcParams["font.family"] = SPACE_MONO

BG, INK, INK_DIM, ACCENT = "#ffffff", "#14213d", "#52617a", "#0ea5e9"
KREIS_EDGE = "#c2ccd8"
# White traffic map: low volume = pale/thin, high volume = dark red/saturated.
CMAP = LinearSegmentedColormap.from_list("load",
       ["#cfe0f5", "#7aa6e6", "#5b6fe0", "#c026d3", "#dc2626", "#7f1d1d"])


def log(m):
    print(f"[linkload] {m}", flush=True)


def load_nodes():
    nodes = {}
    ctx = ET.iterparse(gzip.open(NETWORK), events=("end",))
    for _, el in ctx:
        if el.tag == "node":
            nodes[el.get("id")] = (float(el.get("x")), float(el.get("y")))
            el.clear()
        elif el.tag == "link":
            el.clear()
    return nodes


def main():
    t0 = time.time()
    OUT.mkdir(exist_ok=True)
    log(f"reading linkstats {LINKSTATS.name} ...")
    df = pd.read_csv(LINKSTATS, sep="\t")
    avg_cols = [c for c in df.columns if c.startswith("HRS") and c.endswith("avg")]
    df["vol"] = df[avg_cols].sum(axis=1)  # daily link volume (vehicles, 25% scale)
    log(f"  {len(df):,} links, {len(avg_cols)} hourly columns, max daily vol {df['vol'].max():.0f}")

    log("reading network nodes ...")
    nodes = load_nodes()
    log(f"  {len(nodes):,} nodes")

    fr = df["FROM"].astype(str).to_numpy()
    to = df["TO"].astype(str).to_numpy()
    vol = df["vol"].to_numpy()
    segs, vols = [], []
    miss = 0
    for a, b, v in zip(fr, to, vol):
        pa, pb = nodes.get(a), nodes.get(b)
        if pa is None or pb is None:
            miss += 1
            continue
        if v <= 0:
            continue
        segs.append([pa, pb])
        vols.append(v)
    segs = np.array(segs)
    vols = np.array(vols)
    cov = 100.0 * (1 - miss / len(df))
    log(f"  node-join coverage {cov:.1f}% ({miss:,} links missing nodes) | drawn {len(segs):,} loaded links")

    kreis = gpd.read_file(KREIS_PATH).to_crs(CRS_METRIC)
    # Tight full-bleed 16:9 window on Braunschweig city -> you see the network fray into
    # finer, less-loaded branches around the loaded arteries (no white margins, no text).
    cx, cy = 603990.0, 5792750.0
    half_w = 23000.0
    half_h = half_w * (10.7 / 19.0)
    ex = (cx - half_w, cx + half_w, cy - half_h, cy + half_h)

    # Order by volume so busy roads draw on top; width + colour by volume.
    order = np.argsort(vols)
    segs, vols = segs[order], vols[order]
    norm = LogNorm(vmin=max(1, np.percentile(vols, 50)), vmax=vols.max())
    colors = CMAP(norm(vols))
    lws = 0.12 + 2.6 * norm(np.clip(vols, norm.vmin, norm.vmax))

    fig = plt.figure(figsize=(19, 10.7), dpi=200)
    fig.patch.set_facecolor(BG)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_facecolor(BG)
    # Kreis boundaries, emphasised (drawn on top so the districts read clearly).
    kreis.plot(ax=ax, facecolor="none", edgecolor="#475569", linewidth=2.0,
               alpha=0.75, zorder=5)
    # White: single crisp, volume-weighted pass (no additive glow on white).
    ax.add_collection(LineCollection(segs, colors=colors, linewidths=lws,
                                     alpha=0.9, zorder=3, capstyle="round"))
    ax.set_xlim(ex[0], ex[1])
    ax.set_ylim(ex[2], ex[3])
    ax.set_aspect("equal")
    ax.set_axis_off()

    out = OUT / "link_load_miv_light.png"
    fig.savefig(out, dpi=200, facecolor=BG)
    plt.close(fig)
    log(f"saved {out.name} in {time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
