"""'Simulation snapshot' look for the Braunschweig region (white, horizontal).

Recreates the MATSim-running aesthetic: a light-grey road network, faint home
locations, dark PT stops, and coloured 'agent' dots placed ON the links. The agent
dots are sampled along links with probability proportional to link volume (from the
linkStats), so they cluster in the city and along corridors like a real run.

This is a schematic visual (the dots are sampled positions, not a true second-by-second
snapshot). Inputs: committed run network, homes, transit schedule, linkStats.
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
from matplotlib.colors import LinearSegmentedColormap

HERE = Path(__file__).resolve().parent
ROOT = Path("c:/Users/bienzeisler/Documents/GitHub/eqasim-bs")
for parent in [HERE, *HERE.parents]:
    if (parent / "eqasim-data").is_dir():
        ROOT = parent
        break
DATA = ROOT / "eqasim-data"
CACHE = DATA / "cache_bs_25pct" / "matsim.simulation.run__c0346dec5de3c5b765e82ed35f3433e9.cache" / "simulation_output"
NETWORK = CACHE / "output_network.xml.gz"
LINKSTATS = CACHE / "ITERS" / "it.50" / "50.linkstats.txt.gz"
HOMES_PATH = DATA / "output_bs_100pct" / "braunschweig_100pct_homes.gpkg"
SCHEDULE = DATA / "output_bs_100pct" / "braunschweig_100pct_transit_schedule.xml.gz"
KREIS_PATH = DATA / "output_bs_25pct" / "simwrapper" / "kreis_socio.geojson"
OUT = HERE / "poster_maps"
CRS_METRIC = "EPSG:25832"
RNG = np.random.default_rng(7)

FONT_DIR = HERE / "fonts"
SPACE_MONO = "DejaVu Sans Mono"
for ttf in sorted(FONT_DIR.glob("SpaceMono-*.ttf")):
    fm.fontManager.addfont(str(ttf))
    SPACE_MONO = fm.FontProperties(fname=str(ttf)).get_name()
plt.rcParams["font.family"] = SPACE_MONO

BG, INK, INK_DIM, ACCENT = "#ffffff", "#14213d", "#52617a", "#0ea5e9"
NET_COLOR = "#b4bdc9"
STOP_COLOR = "#3f4754"
KREIS_EDGE = "#8b97a8"
# Congestion colour: green (free) -> yellow -> red (saturated/slow).
CMAP_SPEED = LinearSegmentedColormap.from_list(
    "speed", ["#15803d", "#84cc16", "#facc15", "#f97316", "#dc2626"])

N_AGENTS = 7000
N_HOME_SAMPLE = 120000


def log(m):
    print(f"[snapshot] {m}", flush=True)


def load_network():
    nodes = {}
    fr, to = [], []
    ctx = ET.iterparse(gzip.open(NETWORK), events=("end",))
    for _, el in ctx:
        if el.tag == "node":
            nodes[el.get("id")] = (float(el.get("x")), float(el.get("y")))
            el.clear()
        elif el.tag == "link":
            fr.append(el.get("from"))
            to.append(el.get("to"))
            el.clear()
    segs = np.array([[nodes[a], nodes[b]] for a, b in zip(fr, to)
                     if a in nodes and b in nodes])
    return nodes, segs


def load_stops():
    with gzip.open(SCHEDULE) as f:
        root = ET.parse(f).getroot()
    xy = [(float(s.get("x")), float(s.get("y"))) for s in root.iter("stopFacility")]
    return np.array(xy)


def main():
    t0 = time.time()
    OUT.mkdir(exist_ok=True)

    log("network ...")
    nodes, net_segs = load_network()
    log(f"  {len(nodes):,} nodes, {len(net_segs):,} link segments")

    log("linkstats (agent weighting + congestion) ...")
    df = pd.read_csv(LINKSTATS, sep="\t", low_memory=False)
    avg_cols = [c for c in df.columns if c.startswith("HRS") and c.endswith("avg")]
    df["vol"] = df[avg_cols].sum(axis=1)
    # Congestion proxy: peak-hour volume / capacity (high -> slow/red, low -> fast/green).
    df["sat"] = df[avg_cols].max(axis=1) / df["CAPACITY"].replace(0, np.nan)
    fr = df["FROM"].astype(str).to_numpy()
    to = df["TO"].astype(str).to_numpy()
    vol = df["vol"].to_numpy()
    sat = df["sat"].to_numpy()
    a_xy, b_xy, w, s_lst = [], [], [], []
    for a, b, v, sv in zip(fr, to, vol, sat):
        pa, pb = nodes.get(a), nodes.get(b)
        if pa is None or pb is None or v <= 0:
            continue
        a_xy.append(pa)
        b_xy.append(pb)
        w.append(v)
        s_lst.append(0.0 if np.isnan(sv) else sv)
    a_xy = np.array(a_xy)
    b_xy = np.array(b_xy)
    w = np.array(w, dtype=float)
    s_arr = np.array(s_lst, dtype=float)
    p = w / w.sum()
    # Sample links by volume (busy roads get more agents), then a random point along each.
    idx = RNG.choice(len(a_xy), size=N_AGENTS, p=p)
    t = RNG.random(N_AGENTS)[:, None]
    agents = a_xy[idx] * (1 - t) + b_xy[idx] * t
    agent_sat = s_arr[idx]
    log(f"  placed {N_AGENTS:,} agents on links")

    log("homes ...")
    homes = gpd.read_file(HOMES_PATH)
    homes = homes.set_crs(CRS_METRIC) if homes.crs is None else homes.to_crs(CRS_METRIC)
    hx, hy = homes.geometry.x.to_numpy(), homes.geometry.y.to_numpy()
    if len(hx) > N_HOME_SAMPLE:
        s = RNG.choice(len(hx), N_HOME_SAMPLE, replace=False)
        hx, hy = hx[s], hy[s]

    log("stops ...")
    stops = load_stops()

    kreis = gpd.read_file(KREIS_PATH).to_crs(CRS_METRIC)

    # Full-bleed 16:9 window centred on Braunschweig, sized to the network's width so
    # the road mesh fills the whole frame (no white margins). Network spans ~90 km in x.
    cx = 604500.0   # Braunschweig
    cy = 5792750.0
    half_w = 44000.0
    half_h = half_w * (10.7 / 19.0)
    ex = (cx - half_w, cx + half_w, cy - half_h, cy + half_h)

    # Agent colour by congestion: green = free-flowing, red = saturated/slow.
    norm = plt.Normalize(0.0, 1.0)
    colors = CMAP_SPEED(norm(np.clip(agent_sat, 0, 1.0)))
    sizes = RNG.choice([12, 18, 28, 46], size=N_AGENTS, p=[0.5, 0.3, 0.15, 0.05])

    fig = plt.figure(figsize=(19, 10.7), dpi=200)
    fig.patch.set_facecolor(BG)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_facecolor(BG)
    # 0) Kreis boundaries (the 8 districts) as faint lines, no labels
    kreis.plot(ax=ax, facecolor="none", edgecolor=KREIS_EDGE, linewidth=1.0,
               alpha=0.55, zorder=1)
    # 1) light grey road network
    ax.add_collection(LineCollection(net_segs, colors=NET_COLOR, linewidths=0.6,
                                     alpha=0.95, zorder=2, capstyle="round"))
    # 2) faint homes
    ax.scatter(hx, hy, s=1.0, c="#c2cdda", alpha=0.18, linewidths=0, marker=".", zorder=2,
               rasterized=True)
    # 3) dark PT stops
    ax.scatter(stops[:, 0], stops[:, 1], s=4.0, c=STOP_COLOR, alpha=0.55, linewidths=0,
               marker=".", zorder=3, rasterized=True)
    # 4) agent dots on the links, coloured by congestion (no labels: just the map)
    ax.scatter(agents[:, 0], agents[:, 1], s=sizes, c=colors, alpha=0.85, linewidths=0,
               zorder=4)

    ax.set_xlim(ex[0], ex[1])
    ax.set_ylim(ex[2], ex[3])
    ax.set_aspect("equal")
    ax.set_axis_off()

    out = OUT / "sim_snapshot_light.png"
    fig.savefig(out, dpi=200, facecolor=BG)
    plt.close(fig)
    log(f"saved {out.name} in {time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
