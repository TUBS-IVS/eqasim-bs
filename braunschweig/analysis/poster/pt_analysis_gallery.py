"""10 NEW public-transport (OEV) analyses for the Braunschweig region.

White, no glow, no labels, full-bleed. New angles we had not covered:
 1 stop_boardings    - stops sized/coloured by nearby PT trip ends (boardings+alightings)
 2 pt_access_heat    - walking access: distance to the nearest stop (coverage surface)
 3 line_frequency    - service intensity: line segments weighted by daily departures
 4 pt_traveltime     - desire lines coloured by PT travel time
 5 pt_speed          - desire lines coloured by effective door-to-door speed
 6 pt_detour         - desire lines coloured by routed/straight-line detour
 7 pt_modeshare      - choropleth: PT share of trips per Kreis
 8 pt_corridors      - weighted PT OD between Kreise (BS as hub)
 9 pt_am_pm          - morning vs evening PT flows (small multiple)
10 stop_demand_surf  - demand surface: each cell coloured by its nearest stop's boardings
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
from matplotlib.colors import LinearSegmentedColormap, LogNorm, Normalize
from scipy.spatial import cKDTree

HERE = Path(__file__).resolve().parent
ROOT = Path("c:/Users/bienzeisler/Documents/GitHub/eqasim-bs")
for parent in [HERE, *HERE.parents]:
    if (parent / "eqasim-data").is_dir():
        ROOT = parent
        break
DATA = ROOT / "eqasim-data"
TRIPS = DATA / "output_bs_100pct" / "simulation_output" / "eqasim_trips.csv"
SCHEDULE = DATA / "output_bs_100pct" / "braunschweig_100pct_transit_schedule.xml.gz"
KREIS_PATH = DATA / "output_bs_25pct" / "simwrapper" / "kreis_socio.geojson"
OUT = HERE / "poster_maps" / "gallery_pt"
CRS = "EPSG:25832"
FIG_W, FIG_H = 19, 10.7


def log(m):
    print(f"[pt] {m}", flush=True)


def cm(name, cols):
    return LinearSegmentedColormap.from_list(name, cols)


CM_HEAT = cm("heat", ["#fde68a", "#fb923c", "#ef4444", "#991b1b"])
CM_OCEAN = cm("ocean", ["#cffafe", "#22d3ee", "#0891b2", "#0c4a6e"])
CM_TIME = cm("time", ["#0369a1", "#4f46e5", "#c026d3", "#be123c"])
CM_ACCESS = cm("access", ["#15803d", "#84cc16", "#facc15", "#f97316", "#b91c1c"])  # near->far
CM_DETOUR = cm("detour", ["#0ea5e9", "#a3a3a3", "#f59e0b", "#dc2626"])
CM_PTHEAT = cm("ptheat", ["#eaf6fd", "#7dd3fc", "#0ea5e9", "#0c4a6e"])
CM_SHARE = cm("share", ["#fee2e2", "#fca5a5", "#60a5fa", "#1e3a8a"])


def load():
    log("trips ...")
    cols = ["origin_x", "origin_y", "destination_x", "destination_y", "mode",
            "euclidean_distance", "routed_distance", "travel_time", "departure_time"]
    tr = pd.read_csv(TRIPS, sep=";", usecols=cols)
    pt = tr[tr["mode"] == "pt"].copy()
    log(f"  pt {len(pt):,} / all {len(tr):,}")

    log("schedule (stops + routes + departures) ...")
    with gzip.open(SCHEDULE) as f:
        root = ET.parse(f).getroot()
    stop_ids, stop_xy = [], []
    for s in root.iter("stopFacility"):
        stop_ids.append(s.get("id"))
        stop_xy.append((float(s.get("x")), float(s.get("y"))))
    stop_xy = np.array(stop_xy)
    sid_idx = {sid: i for i, sid in enumerate(stop_ids)}
    freq = {}  # (i,j) -> daily departures
    for trn in root.iter("transitRoute"):
        rp = trn.find("routeProfile")
        deps = trn.find("departures")
        nd = len(deps.findall("departure")) if deps is not None else 0
        if rp is None or nd == 0:
            continue
        refs = [s.get("refId") for s in rp.findall("stop")]
        for a, b in zip(refs[:-1], refs[1:]):
            ia, ib = sid_idx.get(a), sid_idx.get(b)
            if ia is None or ib is None or ia == ib:
                continue
            key = (ia, ib) if ia < ib else (ib, ia)
            freq[key] = freq.get(key, 0) + nd

    kreis = gpd.read_file(KREIS_PATH).to_crs(CRS).reset_index(drop=True)
    kreis["cx"] = kreis.geometry.centroid.x
    kreis["cy"] = kreis.geometry.centroid.y

    log("kreis assignment ...")
    allsamp = tr.sample(min(500000, len(tr)), random_state=3)
    k_all = assign_kreis(allsamp[["origin_x", "origin_y"]].to_numpy(), kreis)
    k_pt_o = assign_kreis(pt[["origin_x", "origin_y"]].sample(min(120000, len(pt)), random_state=5).to_numpy()
                          if len(pt) > 120000 else pt[["origin_x", "origin_y"]].to_numpy(), kreis)
    # OD on a pt sample
    pts = pt.sample(min(120000, len(pt)), random_state=9)
    k_o = assign_kreis(pts[["origin_x", "origin_y"]].to_numpy(), kreis)
    k_d = assign_kreis(pts[["destination_x", "destination_y"]].to_numpy(), kreis)

    return dict(pt=pt, tr=tr, stop_xy=stop_xy, freq=freq, kreis=kreis,
                k_all=k_all, k_pt_o=k_pt_o, k_o=k_o, k_d=k_d)


def assign_kreis(xy, kreis):
    g = gpd.GeoDataFrame(geometry=gpd.points_from_xy(xy[:, 0], xy[:, 1]), crs=CRS)
    j = gpd.sjoin(g, kreis[["geometry"]], how="left", predicate="within")
    j = j[~j.index.duplicated(keep="first")]
    idx = j["index_right"].to_numpy()
    return np.where(np.isnan(idx), -1, idx).astype(int)


def region_extent():
    b = D["kreis"].total_bounds
    cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
    hw = max((b[2] - b[0]), (b[3] - b[1]) * FIG_W / FIG_H) / 2 * 1.02
    hh = hw * FIG_H / FIG_W
    return (cx - hw, cx + hw, cy - hh, cy + hh)


def new_fig(ex):
    fig = plt.figure(figsize=(FIG_W, FIG_H), dpi=170)
    fig.patch.set_facecolor("#ffffff")
    ax = fig.add_axes([0, 0, 1, 1]); ax.set_facecolor("#ffffff")
    ax.set_xlim(ex[0], ex[1]); ax.set_ylim(ex[2], ex[3])
    ax.set_aspect("equal"); ax.set_axis_off()
    return fig, ax


def kreis_line(ax, color="#94a3b8", lw=1.4, alpha=0.55):
    D["kreis"].plot(ax=ax, facecolor="none", edgecolor=color, linewidth=lw, alpha=alpha, zorder=6)


def save(fig, name):
    out = OUT / f"{name}.png"
    fig.savefig(out, dpi=170, facecolor="#ffffff")
    plt.close(fig)
    return out


def desire(ax, df, vals, cmap, vmax, vmin=0, alpha=0.09, lw=0.5):
    segs = np.stack([df[["origin_x", "origin_y"]].to_numpy(),
                     df[["destination_x", "destination_y"]].to_numpy()], axis=1)
    norm = Normalize(vmin, vmax)
    order = np.argsort(vals)
    ax.add_collection(LineCollection(segs[order], colors=cmap(norm(vals[order])),
                                     linewidths=lw, alpha=alpha, zorder=3, capstyle="round"))


def main():
    global D
    t0 = time.time()
    OUT.mkdir(parents=True, exist_ok=True)
    D = load()
    ex = region_extent()
    pt = D["pt"]
    stops = D["stop_xy"]
    tree = cKDTree(stops)
    paths = []

    # sample for line panels
    pts = pt.sample(min(28000, len(pt)), random_state=7)
    dur = pts["travel_time"].to_numpy() / 60.0
    eucl = pts["euclidean_distance"].to_numpy()
    routed = pts["routed_distance"].to_numpy()
    speed = np.where(pts["travel_time"] > 0, eucl / pts["travel_time"] * 3.6, 0)
    detour = np.where(eucl > 0, routed / eucl, 1.0)

    # 1 stop boardings (origins+dests -> nearest stop)
    log("1 boardings ...")
    ends = np.vstack([pt[["origin_x", "origin_y"]].to_numpy(),
                      pt[["destination_x", "destination_y"]].to_numpy()])
    _, nidx = tree.query(ends, workers=-1)
    board = np.bincount(nidx, minlength=len(stops)).astype(float)
    fig, ax = new_fig(ex)
    kreis_line(ax)
    o = np.argsort(board)
    sc = ax.scatter(stops[o, 0], stops[o, 1], s=2 + 0.02 * board[o],
                    c=board[o], cmap=CM_HEAT, norm=LogNorm(vmin=1, vmax=board.max()),
                    alpha=0.8, linewidths=0, zorder=4)
    paths.append(save(fig, "01_stop_boardings"))

    # 2 access heat (distance to nearest stop, grid)
    log("2 access ...")
    nx = 320; ny = int(nx * (ex[3] - ex[2]) / (ex[1] - ex[0]))
    gx = np.linspace(ex[0], ex[1], nx); gy = np.linspace(ex[2], ex[3], ny)
    GX, GY = np.meshgrid(gx, gy)
    dist, gidx = tree.query(np.column_stack([GX.ravel(), GY.ravel()]), workers=-1)
    dist = dist.reshape(ny, nx) / 1000.0  # km
    # inside-region mask so the surface is Kreis-shaped, not a rectangle
    gg = gpd.GeoDataFrame(geometry=gpd.points_from_xy(GX.ravel(), GY.ravel()), crs=CRS)
    jj = gpd.sjoin(gg, D["kreis"][["geometry"]], how="left", predicate="within")
    jj = jj[~jj.index.duplicated(keep="first")]
    inside = (~jj["index_right"].isna().to_numpy()).reshape(ny, nx)
    fig, ax = new_fig(ex)
    arr = np.ma.masked_where(~inside, np.clip(dist, 0, 2.0))
    ax.imshow(arr, origin="lower", extent=[ex[0], ex[1], ex[2], ex[3]], cmap=CM_ACCESS,
              vmin=0, vmax=2.0, alpha=0.95, zorder=2, aspect="auto", interpolation="bilinear")
    kreis_line(ax, color="#1f2937", lw=1.2, alpha=0.45)
    paths.append(save(fig, "02_pt_access_heat"))

    # 3 line frequency (service intensity)
    log("3 frequency ...")
    keys = list(D["freq"].keys()); w = np.array([D["freq"][k] for k in keys], float)
    fseg = np.array([[stops[i], stops[j]] for i, j in keys])
    o = np.argsort(w)
    norm = LogNorm(vmin=max(1, np.percentile(w, 50)), vmax=w.max())
    nvf = np.clip(norm(w[o]), 0, 1)
    fig, ax = new_fig(ex)
    kreis_line(ax)
    ax.add_collection(LineCollection(fseg[o], colors=CM_OCEAN(nvf),
                                     linewidths=0.2 + 3.5 * nvf ** 1.3, alpha=0.85,
                                     zorder=3, capstyle="round"))
    paths.append(save(fig, "03_line_frequency"))

    # 4 travel time
    log("4 traveltime ...")
    fig, ax = new_fig(ex); kreis_line(ax, color="#cbd5e1")
    desire(ax, pts, dur, CM_TIME, float(np.percentile(dur, 95)), alpha=0.1, lw=0.5)
    paths.append(save(fig, "04_pt_traveltime"))

    # 5 speed
    log("5 speed ...")
    fig, ax = new_fig(ex); kreis_line(ax, color="#cbd5e1")
    desire(ax, pts, speed, CM_ACCESS, float(np.percentile(speed, 95)), alpha=0.1, lw=0.5)
    paths.append(save(fig, "05_pt_speed"))

    # 6 detour
    log("6 detour ...")
    fig, ax = new_fig(ex); kreis_line(ax, color="#cbd5e1")
    desire(ax, pts, np.clip(detour, 1, 3), CM_DETOUR, 3.0, vmin=1.0, alpha=0.1, lw=0.5)
    paths.append(save(fig, "06_pt_detour"))

    # 7 mode share per kreis
    log("7 modeshare ...")
    nk = len(D["kreis"])
    allc = np.bincount(D["k_all"][D["k_all"] >= 0], minlength=nk).astype(float)
    ptc = np.bincount(D["k_pt_o"][D["k_pt_o"] >= 0], minlength=nk).astype(float)
    # scale pt sample to all sample size
    share = np.divide(ptc / max(1, len(D["k_pt_o"])), allc / max(1, len(D["k_all"])),
                      out=np.zeros(nk), where=allc > 0)
    kk = D["kreis"].copy(); kk["share"] = share
    fig, ax = new_fig(ex)
    kk.plot(ax=ax, column="share", cmap=CM_SHARE, edgecolor="#475569", linewidth=1.2, zorder=2)
    paths.append(save(fig, "07_pt_modeshare_kreis"))

    # 8 corridors (weighted OD between kreise)
    log("8 corridors ...")
    pair = {}
    for a, b in zip(D["k_o"], D["k_d"]):
        if a < 0 or b < 0 or a == b:
            continue
        k = (a, b) if a < b else (b, a)
        pair[k] = pair.get(k, 0) + 1
    cx, cy = D["kreis"]["cx"].to_numpy(), D["kreis"]["cy"].to_numpy()
    mx = max(pair.values()) if pair else 1
    fig, ax = new_fig(ex); kreis_line(ax, color="#cbd5e1")
    for (a, b), c in sorted(pair.items(), key=lambda kv: kv[1]):
        ax.plot([cx[a], cx[b]], [cy[a], cy[b]], color=CM_TIME(c / mx),
                lw=0.6 + 10 * (c / mx), alpha=0.7, solid_capstyle="round", zorder=3)
    ax.scatter(cx, cy, s=90, c="#0c4a6e", zorder=4, edgecolors="white", linewidths=1.4)
    paths.append(save(fig, "08_pt_corridors"))

    # 9 am vs pm (small multiple)
    log("9 am/pm ...")
    h = (pt["departure_time"].to_numpy() / 3600.0) % 24
    am = pt[(h >= 6) & (h < 9)].sample(min(15000, ((h >= 6) & (h < 9)).sum()), random_state=1)
    pm = pt[(h >= 15) & (h < 19)].sample(min(15000, ((h >= 15) & (h < 19)).sum()), random_state=2)
    fig = plt.figure(figsize=(FIG_W, FIG_H), dpi=170); fig.patch.set_facecolor("#fff")
    for col, (sub, c) in enumerate([(am, "#0ea5e9"), (pm, "#e11d48")]):
        ax = fig.add_axes([col * 0.5, 0, 0.5, 1]); ax.set_facecolor("#fff")
        D["kreis"].plot(ax=ax, facecolor="none", edgecolor="#cbd5e1", linewidth=1.0, zorder=2)
        segs = np.stack([sub[["origin_x", "origin_y"]].to_numpy(),
                         sub[["destination_x", "destination_y"]].to_numpy()], axis=1)
        ax.add_collection(LineCollection(segs, colors=c, linewidths=0.5, alpha=0.10,
                                         zorder=3, capstyle="round"))
        ax.set_xlim(ex[0], ex[1]); ax.set_ylim(ex[2], ex[3]); ax.set_aspect("equal"); ax.set_axis_off()
    paths.append(save(fig, "09_pt_am_pm"))

    # 10 demand surface (cell -> nearest stop boardings)
    log("10 demand surface ...")
    surf = board[gidx].reshape(ny, nx)
    fig, ax = new_fig(ex)
    ax.imshow(np.ma.masked_where((~inside) | (surf <= 0), surf), origin="lower",
              extent=[ex[0], ex[1], ex[2], ex[3]], cmap=CM_PTHEAT,
              norm=LogNorm(vmin=1, vmax=board.max()), alpha=0.92, zorder=2,
              aspect="auto", interpolation="nearest")
    kreis_line(ax, color="#1f2937", lw=1.0, alpha=0.4)
    paths.append(save(fig, "10_stop_demand_surface"))

    # contact sheet
    from PIL import Image
    cols = 4; rows = int(np.ceil(len(paths) / cols))
    th = [Image.open(p).resize((480, 270)) for p in paths]
    sheet = Image.new("RGB", (cols * 480, rows * 270), "white")
    for i, t in enumerate(th):
        sheet.paste(t, ((i % cols) * 480, (i // cols) * 270))
    sheet.save(OUT / "_contact_sheet.png")
    log(f"DONE {len(paths)} PT variants in {time.time()-t0:.1f}s -> {OUT}")


if __name__ == "__main__":
    main()
