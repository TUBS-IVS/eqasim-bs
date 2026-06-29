"""White-background map gallery for the Braunschweig region (no labels, no glow, full-bleed).

Generates ~20 'cool' variants of different map types, all on white, with an MIV/OEV
angle where it fits. Purely decorative poster material. Sources: 25% linkStats (road
loads), run network geometry (road mesh + rail), eqasim PT/all trips, homes, transit
stops. A contact sheet is written for quick comparison.
"""
from __future__ import annotations

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
from matplotlib.colors import LinearSegmentedColormap, LogNorm, Normalize

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
TRIPS = DATA / "output_bs_100pct" / "simulation_output" / "eqasim_trips.csv"
HOMES = DATA / "output_bs_100pct" / "braunschweig_100pct_homes.gpkg"
SCHEDULE = DATA / "output_bs_100pct" / "braunschweig_100pct_transit_schedule.xml.gz"
KREIS_PATH = DATA / "output_bs_25pct" / "simwrapper" / "kreis_socio.geojson"
OUT = HERE / "poster_maps" / "gallery"
CRS = "EPSG:25832"
RNG = np.random.default_rng(7)

FIG_W, FIG_H = 19, 10.7
BS = (603990.0, 5792750.0)


def log(m):
    print(f"[gallery] {m}", flush=True)


# ---- colormaps (white-friendly: low = light but visible, high = dark/saturated) ----
C = {
    "heat":    ["#fde68a", "#fb923c", "#ef4444", "#991b1b"],
    "web":     ["#1d4ed8", "#7c3aed", "#db2777", "#dc2626"],
    "blues":   ["#bfdbfe", "#3b82f6", "#1e3a8a"],
    "thermal": ["#dbeafe", "#60a5fa", "#a855f7", "#ec4899", "#ef4444", "#7f1d1d"],
    "ocean":   ["#cffafe", "#22d3ee", "#0891b2", "#0c4a6e"],
    "magenta": ["#fbcfe8", "#f472b6", "#db2777", "#831843"],
    "sunset":  ["#fde68a", "#fb923c", "#f43f5e", "#7c3aed"],
    "forest":  ["#bbf7d0", "#4ade80", "#16a34a", "#14532d"],
    "ink":     ["#cbd5e1", "#64748b", "#1e293b", "#020617"],
    "dist":    ["#0369a1", "#4f46e5", "#c026d3", "#be123c"],
    "ptheat":  ["#eaf6fd", "#7dd3fc", "#0ea5e9", "#0c4a6e"],
}
CM = {k: LinearSegmentedColormap.from_list(k, v) for k, v in C.items()}
CMAP_RG = LinearSegmentedColormap.from_list("rg", ["#15803d", "#84cc16", "#facc15", "#f97316", "#dc2626"])
PURPOSE_COLOR = {"work": "#0284c7", "education": "#7c3aed", "leisure": "#e11d48",
                 "shop": "#d97706", "other": "#059669", "home": "#64748b"}


def load_all():
    log("network (nodes + mesh + rail) ...")
    nodes, fr, to, modes = {}, [], [], []
    ctx = ET.iterparse(gzip.open(NETWORK), events=("end",))
    for _, el in ctx:
        if el.tag == "node":
            nodes[el.get("id")] = (float(el.get("x")), float(el.get("y")))
            el.clear()
        elif el.tag == "link":
            fr.append(el.get("from")); to.append(el.get("to")); modes.append(el.get("modes", ""))
            el.clear()
    all_segs, rail_segs = [], []
    for a, b, m in zip(fr, to, modes):
        pa, pb = nodes.get(a), nodes.get(b)
        if pa is None or pb is None:
            continue
        all_segs.append([pa, pb])
        if "rail" in m or "train" in m:
            rail_segs.append([pa, pb])
    all_segs = np.array(all_segs); rail_segs = np.array(rail_segs)

    log("linkstats ...")
    df = pd.read_csv(LINKSTATS, sep="\t", low_memory=False)
    avg = [c for c in df.columns if c.startswith("HRS") and c.endswith("avg")]
    df["vol"] = df[avg].sum(axis=1)
    df["sat"] = df[avg].max(axis=1) / df["CAPACITY"].replace(0, np.nan)
    fa = df["FROM"].astype(str).to_numpy(); ta = df["TO"].astype(str).to_numpy()
    vol = df["vol"].to_numpy(); fs = (df["FREESPEED"].to_numpy() * 3.6); sat = df["sat"].to_numpy()
    rsegs, rvol, rfs, rsat = [], [], [], []
    for a, b, v, f, s in zip(fa, ta, vol, fs, sat):
        pa, pb = nodes.get(a), nodes.get(b)
        if pa is None or pb is None or v <= 0:
            continue
        rsegs.append([pa, pb]); rvol.append(v); rfs.append(f); rsat.append(0.0 if np.isnan(s) else s)
    rsegs = np.array(rsegs); rvol = np.array(rvol); rfs = np.array(rfs); rsat = np.array(rsat)
    order = np.argsort(rvol)
    rsegs, rvol, rfs, rsat = rsegs[order], rvol[order], rfs[order], rsat[order]
    nv = np.clip(LogNorm(vmin=max(1, np.percentile(rvol, 40)), vmax=rvol.max())(rvol), 0, 1)

    log("trips ...")
    cols = ["origin_x", "origin_y", "destination_x", "destination_y", "mode",
            "euclidean_distance", "following_purpose"]
    tr = pd.read_csv(TRIPS, sep=";", usecols=cols)
    pt = tr[tr["mode"] == "pt"]
    pt_pts = (np.concatenate([pt["origin_x"], pt["destination_x"]]),
              np.concatenate([pt["origin_y"], pt["destination_y"]]))
    pts = pt.sample(min(30000, len(pt)), random_state=7)
    pt_segs = np.stack([pts[["origin_x", "origin_y"]].to_numpy(),
                        pts[["destination_x", "destination_y"]].to_numpy()], axis=1)
    pt_dist = pts["euclidean_distance"].to_numpy() / 1000.0
    alls = tr.sample(min(60000, len(tr)), random_state=3)
    act_xy = alls[["destination_x", "destination_y"]].to_numpy()
    act_pp = alls["following_purpose"].to_numpy()
    all_segs_d = np.stack([alls[["origin_x", "origin_y"]].to_numpy(),
                           alls[["destination_x", "destination_y"]].to_numpy()], axis=1)

    log("homes + stops + kreis ...")
    hm = gpd.read_file(HOMES); hm = hm.set_crs(CRS) if hm.crs is None else hm.to_crs(CRS)
    hx, hy = hm.geometry.x.to_numpy(), hm.geometry.y.to_numpy()
    s = RNG.choice(len(hx), min(160000, len(hx)), replace=False)
    hx, hy = hx[s], hy[s]
    with gzip.open(SCHEDULE) as f:
        root = ET.parse(f).getroot()
    stops = np.array([(float(s.get("x")), float(s.get("y"))) for s in root.iter("stopFacility")])
    kreis = gpd.read_file(KREIS_PATH).to_crs(CRS)
    return dict(all_segs=all_segs, rail_segs=rail_segs, rsegs=rsegs, rvol=rvol, rfs=rfs,
                rsat=rsat, nv=nv, pt_segs=pt_segs, pt_dist=pt_dist, pt_pts=pt_pts,
                act_xy=act_xy, act_pp=act_pp, all_segs_d=all_segs_d,
                hx=hx, hy=hy, stops=stops, kreis=kreis)


def extent(which):
    if which == "bs":
        hw = 23000.0
    elif which == "core":
        hw = 13000.0
    else:  # region
        b = D["kreis"].total_bounds
        cx = (b[0] + b[2]) / 2
        hw = max((b[2] - b[0]), (b[3] - b[1]) * FIG_W / FIG_H) / 2 * 1.02
        hh = hw * FIG_H / FIG_W
        return (cx - hw, cx + hw, (b[1] + b[3]) / 2 - hh, (b[1] + b[3]) / 2 + hh)
    hh = hw * FIG_H / FIG_W
    return (BS[0] - hw, BS[0] + hw, BS[1] - hh, BS[1] + hh)


def lws_from(nv, wbase, wscale, wpow):
    return wbase + wscale * (nv ** wpow)


def kreis_outline(ax, color="#475569", lw=1.6, alpha=0.5):
    D["kreis"].plot(ax=ax, facecolor="none", edgecolor=color, linewidth=lw, alpha=alpha, zorder=6)


def mesh(ax, color="#dfe4ea", lw=0.28, alpha=0.8, segs=None):
    ax.add_collection(LineCollection(D["all_segs"] if segs is None else segs, colors=color,
                                     linewidths=lw, alpha=alpha, zorder=1, capstyle="round"))


def loaded(ax, cmap, wbase=0.12, wscale=4.2, wpow=1.35, alpha=0.92, by="vol"):
    if by == "freespeed":
        vals = np.clip(Normalize(20, 120)(D["rfs"]), 0, 1)
        col = cmap(vals)
        lw = lws_from(np.clip(Normalize(20, 120)(D["rfs"]), 0, 1), wbase, wscale * 0.7, 1.0)
    elif by == "sat":
        vals = np.clip(Normalize(0, 1)(D["rsat"]), 0, 1)
        col = CMAP_RG(vals)
        lw = lws_from(D["nv"], wbase, wscale, wpow)
    else:
        col = cmap(D["nv"]); lw = lws_from(D["nv"], wbase, wscale, wpow)
    ax.add_collection(LineCollection(D["rsegs"], colors=col, linewidths=lw, alpha=alpha,
                                     zorder=3, capstyle="round"))


def heatmap(ax, x, y, cmap, ex):
    nx = 260; ny = int(nx * (ex[3] - ex[2]) / (ex[1] - ex[0]))
    H, _, _ = np.histogram2d(x, y, bins=[nx, ny], range=[[ex[0], ex[1]], [ex[2], ex[3]]])
    H = H.T; Hm = np.ma.masked_where(H <= 0, H)
    cm = cmap.copy(); cm.set_bad("#ffffff")
    ax.imshow(Hm, origin="lower", extent=[ex[0], ex[1], ex[2], ex[3]], cmap=cm,
              norm=LogNorm(vmin=1, vmax=H.max()), interpolation="bilinear", zorder=2, aspect="auto")


def desire(ax, segs, vals, cmap, vmax, alpha=0.09, lw=0.5):
    norm = Normalize(0, vmax)
    ax.add_collection(LineCollection(segs, colors=cmap(norm(vals)), linewidths=lw,
                                     alpha=alpha, zorder=3, capstyle="round"))


def render(name, kind, ex_key, **kw):
    ex = extent(ex_key)
    fig = plt.figure(figsize=(FIG_W, FIG_H), dpi=170)
    fig.patch.set_facecolor("#ffffff")
    ax = fig.add_axes([0, 0, 1, 1]); ax.set_facecolor("#ffffff")

    if kind == "road":
        mesh(ax, alpha=0.7)
        loaded(ax, CM[kw["cmap"]], **{k: kw[k] for k in ("wbase", "wscale", "wpow") if k in kw})
        kreis_outline(ax)
    elif kind == "road_min":  # minimal: no mesh, ink width-driven
        loaded(ax, CM["ink"], wbase=0.1, wscale=4.6, wpow=1.5)
        kreis_outline(ax, alpha=0.35)
    elif kind == "hierarchy":
        mesh(ax, alpha=0.5)
        loaded(ax, CM["sunset"], by="freespeed", wbase=0.12, wscale=3.4)
        kreis_outline(ax)
    elif kind == "congestion":
        mesh(ax, alpha=0.6)
        loaded(ax, CMAP_RG, by="sat", wbase=0.2, wscale=3.6, wpow=1.1)
        kreis_outline(ax)
    elif kind == "rail":
        mesh(ax, color="#e5e9ee", alpha=0.8)
        ax.add_collection(LineCollection(D["rail_segs"], colors="#0d9488", linewidths=2.2,
                                         alpha=0.95, zorder=4, capstyle="round"))
        ax.scatter(D["stops"][:, 0], D["stops"][:, 1], s=3, c="#0f766e", alpha=0.5,
                   linewidths=0, marker=".", zorder=3)
        kreis_outline(ax)
    elif kind == "overlay":  # MIV load + OEV rail
        mesh(ax, color="#eceef2", alpha=0.7)
        loaded(ax, CM["heat"], wbase=0.1, wscale=3.6, wpow=1.4, alpha=0.85)
        ax.add_collection(LineCollection(D["rail_segs"], colors="#1d4ed8", linewidths=1.8,
                                         alpha=0.9, zorder=4, capstyle="round"))
        kreis_outline(ax)
    elif kind == "pt_desire":
        mesh(ax, color="#eef1f5", alpha=0.6)
        desire(ax, D["pt_segs"], D["pt_dist"], CM["dist"],
               float(np.percentile(D["pt_dist"], 95)), alpha=0.1, lw=0.5)
        kreis_outline(ax)
    elif kind == "pt_heat":
        heatmap(ax, D["pt_pts"][0], D["pt_pts"][1], CM["ptheat"], ex)
        kreis_outline(ax)
    elif kind == "pop_heat":
        heatmap(ax, D["hx"], D["hy"], plt.get_cmap("magma_r"), ex)
        kreis_outline(ax)
    elif kind == "activities":
        mesh(ax, color="#eef1f5", alpha=0.5)
        cols = np.array([PURPOSE_COLOR.get(p, "#94a3b8") for p in D["act_pp"]])
        ax.scatter(D["act_xy"][:, 0], D["act_xy"][:, 1], s=5, c=cols, alpha=0.5,
                   linewidths=0, zorder=3)
        kreis_outline(ax)
    elif kind == "homes":
        ax.scatter(D["hx"], D["hy"], s=1.3, c="#1e3a8a", alpha=0.16, linewidths=0,
                   marker=".", zorder=2, rasterized=True)
        kreis_outline(ax, alpha=0.4)
    elif kind == "all_desire":
        desire(ax, D["all_segs_d"], np.hypot(D["all_segs_d"][:, 1, 0] - D["all_segs_d"][:, 0, 0],
               D["all_segs_d"][:, 1, 1] - D["all_segs_d"][:, 0, 1]) / 1000.0,
               CM["thermal"], 30, alpha=0.06, lw=0.4)
        kreis_outline(ax)
    elif kind == "sim":
        mesh(ax, color="#b4bdc9", lw=0.55, alpha=0.9)
        # agent dots weighted by volume, coloured by congestion
        p = D["rvol"] / D["rvol"].sum()
        idx = RNG.choice(len(D["rsegs"]), 8000, p=p)
        t = RNG.random(8000)[:, None]
        ag = D["rsegs"][idx, 0] * (1 - t) + D["rsegs"][idx, 1] * t
        col = CMAP_RG(np.clip(Normalize(0, 1)(D["rsat"][idx]), 0, 1))
        sz = RNG.choice([10, 16, 26, 42], 8000, p=[0.5, 0.3, 0.15, 0.05])
        ax.scatter(D["stops"][:, 0], D["stops"][:, 1], s=3, c="#3f4754", alpha=0.5,
                   linewidths=0, marker=".", zorder=3)
        ax.scatter(ag[:, 0], ag[:, 1], s=sz, c=col, alpha=0.85, linewidths=0, zorder=4)
        kreis_outline(ax, alpha=0.5)
    elif kind == "constellation":
        loaded(ax, CM["blues"], wbase=0.1, wscale=3.0, wpow=1.4, alpha=0.8)
        # node glow points sized by local volume (intersections)
        ax.scatter(D["rsegs"][:, 0, 0], D["rsegs"][:, 0, 1], s=2 + 30 * D["nv"] ** 2,
                   c="#be123c", alpha=0.25, linewidths=0, zorder=4)
        kreis_outline(ax)

    ax.set_xlim(ex[0], ex[1]); ax.set_ylim(ex[2], ex[3])
    ax.set_aspect("equal"); ax.set_axis_off()
    out = OUT / f"{name}.png"
    fig.savefig(out, dpi=170, facecolor="#ffffff")
    plt.close(fig)
    return out


VARIANTS = [
    ("01_road_heat", "road", "bs", dict(cmap="heat")),
    ("02_road_web", "road", "bs", dict(cmap="web")),
    ("03_road_blues", "road", "bs", dict(cmap="blues")),
    ("04_road_thermal", "road", "bs", dict(cmap="thermal")),
    ("05_road_ocean", "road", "bs", dict(cmap="ocean")),
    ("06_road_magenta", "road", "bs", dict(cmap="magenta")),
    ("07_road_sunset", "road", "bs", dict(cmap="sunset")),
    ("08_road_forest", "road", "bs", dict(cmap="forest")),
    ("09_road_ink_minimal", "road_min", "bs", {}),
    ("10_road_hierarchy", "hierarchy", "bs", {}),
    ("11_congestion_rg", "congestion", "bs", {}),
    ("12_oev_rail_supply", "rail", "bs", {}),
    ("13_miv_oev_overlay", "overlay", "bs", {}),
    ("14_oev_desire_lines", "pt_desire", "region", {}),
    ("15_oev_demand_heat", "pt_heat", "region", {}),
    ("16_population_heat", "pop_heat", "region", {}),
    ("17_activities_purpose", "activities", "region", {}),
    ("18_homes_dotcloud", "homes", "region", {}),
    ("19_sim_snapshot", "sim", "bs", {}),
    ("20_node_constellation", "constellation", "bs", {}),
    ("21_road_heat_region", "road", "region", dict(cmap="heat", wscale=3.0)),
    ("22_all_desire_thermal", "all_desire", "region", {}),
    ("23_road_core_zoom", "road", "core", dict(cmap="thermal", wscale=5.0)),
]


def main():
    global D
    t0 = time.time()
    OUT.mkdir(parents=True, exist_ok=True)
    D = load_all()
    log(f"data loaded in {time.time()-t0:.1f}s; rendering {len(VARIANTS)} variants ...")
    paths = []
    for name, kind, exk, kw in VARIANTS:
        t1 = time.time()
        paths.append(render(name, kind, exk, **kw))
        log(f"  {name} ({time.time()-t1:.1f}s)")

    # contact sheet
    from PIL import Image
    cols = 4
    rows = int(np.ceil(len(paths) / cols))
    thumbs = [Image.open(p).resize((480, 270)) for p in paths]
    sheet = Image.new("RGB", (cols * 480, rows * 270), "white")
    for i, th in enumerate(thumbs):
        sheet.paste(th, ((i % cols) * 480, (i // cols) * 270))
    sheet.save(OUT / "_contact_sheet.png")
    log(f"DONE {len(paths)} variants + contact sheet in {time.time()-t0:.1f}s -> {OUT}")


if __name__ == "__main__":
    main()
