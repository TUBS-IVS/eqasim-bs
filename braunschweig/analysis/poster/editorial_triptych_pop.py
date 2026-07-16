"""Wide editorial 'small multiples' poster figure of the eqasim-bs Braunschweig model.

Inspired by python-graph-gallery 'web-multiple-maps': a shared title band over a row
of clearly-labelled map panels, each with its own slim colour scale, plus a source
footer -> the viewer instantly understands WHAT is shown. Fills a landscape format.

Panels (same region extent, EPSG:25832), each with a DIFFERENT encoding so they do
not read as duplicates:
  1  BEVOELKERUNG - density heatmap of home locations (log-scaled)
  2  OEV-WEGE     - PT trips as desire lines, coloured by DEPARTURE TIME (commute waves)
  3  PKW-WEGE     - car trips as desire lines, coloured by TRIP DISTANCE

One-off visual asset. Inputs are committed 100% run outputs. No network/basemap needed.
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
from matplotlib.cm import ScalarMappable
from matplotlib.collections import LineCollection
from matplotlib.colors import LinearSegmentedColormap, LogNorm, Normalize

# --- paths -----------------------------------------------------------------
HERE = Path(__file__).resolve().parent
for parent in [HERE, *HERE.parents]:
    if (parent / "eqasim-data").is_dir():
        ROOT = parent
        break
else:
    ROOT = Path("c:/Users/bienzeisler/Documents/GitHub/eqasim-bs")
DATA = ROOT / "eqasim-data" / "output_bs_100pct"
HOMES_PATH = DATA / "braunschweig_100pct_homes.gpkg"
TRIPS_PATH = DATA / "simulation_output" / "eqasim_trips.csv"
NETWORK_PATH = DATA / "braunschweig_100pct_network.xml.gz"
KREIS_PATH = ROOT / "eqasim-data" / "output_bs_25pct" / "simwrapper" / "kreis_socio.geojson"
OUT = HERE / "poster_maps"
OUT.mkdir(exist_ok=True)

CRS_METRIC = "EPSG:25832"
RNG = np.random.default_rng(42)
PT_LINE_SAMPLE = 30000
CAR_LINE_SAMPLE = 30000

# --- fonts -----------------------------------------------------------------
FONT_DIR = HERE / "fonts"
SPACE_MONO = "DejaVu Sans Mono"
for ttf in sorted(FONT_DIR.glob("SpaceMono-*.ttf")):
    fm.fontManager.addfont(str(ttf))
    SPACE_MONO = fm.FontProperties(fname=str(ttf)).get_name()
plt.rcParams["font.family"] = SPACE_MONO

# Keys are the zero-padded 5-digit Kreis ARS, matching the ``ars5`` property
# written into kreis_socio.geojson by the SimWrapper spatial export.
KREIS_NAME = {
    "03101": "Braunschweig", "03102": "Salzgitter", "03103": "Wolfsburg",
    "03151": "Gifhorn", "03153": "Goslar", "03154": "Helmstedt",
    "03157": "Peine", "03158": "Wolfenbuettel",
}
KREIS_LABEL = {**KREIS_NAME, "03158": "Wolfenbüttel"}
LABEL_THESE = {"03101", "03103", "03102", "03158", "03157", "03151"}

# Day-cycle colormap for departure time (0..24 h): night -> morning -> noon -> evening.
CMAP_TIME = LinearSegmentedColormap.from_list("time", [
    (0.00, "#27306b"), (0.25, "#3b82f6"), (0.34, "#22d3ee"), (0.50, "#fde047"),
    (0.67, "#fb923c"), (0.75, "#f43f5e"), (1.00, "#312e6b"),
])
CMAP_DIST = LinearSegmentedColormap.from_list("dist", ["#fde68a", "#fb923c", "#ef4444", "#7f1d1d"])


def log(m):
    print(f"[editorial] {m}", flush=True)


def _segments(df):
    return np.stack([
        np.column_stack([df["origin_x"].to_numpy(), df["origin_y"].to_numpy()]),
        np.column_stack([df["destination_x"].to_numpy(), df["destination_y"].to_numpy()]),
    ], axis=1)


def load_roadnet():
    """Parse the real MATSim network geometry (the actual links, not a schematic).

    Returns (road_segs (N,2,2), road_speed_kmh (N,), rail_segs (M,2,2)).
    'artificial' helper links (stop-facility connectors etc.) are dropped so only the
    real road + rail infrastructure is drawn. Road links are coloured by free-flow speed
    (motorways bright); rail links are returned separately for a distinct overlay.
    """
    nodes = {}
    road_seg, road_spd, rail_seg = [], [], []
    ctx = ET.iterparse(gzip.open(NETWORK_PATH), events=("end",))
    for _, el in ctx:
        if el.tag == "node":
            nodes[el.get("id")] = (float(el.get("x")), float(el.get("y")))
        elif el.tag == "link":
            modes = el.get("modes", "")
            f, t = el.get("from"), el.get("to")
            if "artificial" in modes or "stopFacilityLink" in modes or f not in nodes or t not in nodes:
                el.clear()
                continue
            seg = [nodes[f], nodes[t]]
            if "rail" in modes or "train" in modes:
                rail_seg.append(seg)
            else:
                try:
                    spd = float(el.get("freespeed"))
                except (TypeError, ValueError):
                    spd = 8.0
                if spd == float("inf"):
                    spd = 36.0
                road_seg.append(seg)
                road_spd.append(spd * 3.6)  # m/s -> km/h
            el.clear()
    return (np.array(road_seg), np.array(road_spd), np.array(rail_seg))


def load():
    log("homes ...")
    homes = gpd.read_file(HOMES_PATH)
    homes = homes.set_crs(CRS_METRIC) if homes.crs is None else homes.to_crs(CRS_METRIC)
    hx, hy = homes.geometry.x.to_numpy(), homes.geometry.y.to_numpy()  # all homes for density

    log("trips ...")
    cols = ["origin_x", "origin_y", "destination_x", "destination_y",
            "mode", "euclidean_distance", "departure_time"]
    trips = pd.read_csv(TRIPS_PATH, sep=";", usecols=cols)

    pt = trips[trips["mode"] == "pt"]
    if len(pt) > PT_LINE_SAMPLE:
        pt = pt.sample(PT_LINE_SAMPLE, random_state=7)
    pt_layer = (_segments(pt), pt["euclidean_distance"].to_numpy() / 1000.0)

    log("road network ...")
    road_segs, road_spd, rail_segs = load_roadnet()
    log(f"  road links {len(road_segs):,} | rail links {len(rail_segs):,}")

    log("kreis ...")
    kreis = gpd.read_file(KREIS_PATH).to_crs(CRS_METRIC)
    # Normalise the Kreis key to the zero-padded 5-char ARS so the KREIS_LABEL
    # lookups match regardless of string/numeric coercion in the GeoJSON.
    kreis["ars5"] = kreis["ars5"].astype(str).str.zfill(5)
    kreis["cx"] = kreis.geometry.centroid.x
    kreis["cy"] = kreis.geometry.centroid.y
    return (hx, hy), pt_layer, (road_segs, road_spd, rail_segs), kreis


STYLES = {
    "dark": {
        "bg": "#070b14", "panel_bg": "#070b14",
        "heat_cmap": "magma",
        "kreis": "#33465f", "kreis_lw": 0.8,
        "glow_a": 0.012, "glow_lw": 2.2, "sharp_a": 0.18, "sharp_lw": 0.55,
        "ink": "#eef3fb", "ink_dim": "#9fb2cc", "accent": "#22d3ee", "city": "#e6eefb",
        "net": {
            "bus": ("#2f5a86", 0.45, 0.5), "tram": ("#34d399", 0.95, 1.2),
            "rail": ("#fbbf24", 0.95, 1.6), "stop": ("#aebfd6", 0.30, 1.4),
        },
    },
    "light": {
        "bg": "#ffffff", "panel_bg": "#ffffff",
        "heat_cmap": "magma_r",
        "kreis": "#aab6c6", "kreis_lw": 0.9,
        "glow_a": 0.014, "glow_lw": 1.9, "sharp_a": 0.17, "sharp_lw": 0.5,
        "ink": "#14213d", "ink_dim": "#52617a", "accent": "#0ea5e9", "city": "#14213d",
        "net": {
            "bus": ("#9bb0cc", 0.7, 0.5), "tram": ("#0d9488", 0.95, 1.1),
            "rail": ("#c2410c", 0.95, 1.5), "stop": ("#475569", 0.45, 1.2),
        },
    },
}
MODE_LABEL = {"bus": "Bus", "rail": "Bahn", "tram": "Tram"}

# Font sizes (single place to scale everything up).
FS_TITLE = 46
FS_SUB = 21
FS_PANEL = 23
FS_NOTE = 15
FS_CITY = 12
FS_CBAR = 12
FS_LEG = 16
FS_SRC = 13


def slim_colorbar(ax, st, mappable, label, ticks=None, ticklabels=None):
    """Thin horizontal colour scale tucked into the panel's lower padding (saves space)."""
    cax = ax.inset_axes([0.12, 0.045, 0.76, 0.018])
    cb = ax.figure.colorbar(mappable, cax=cax, orientation="horizontal")
    cb.outline.set_visible(False)
    cax.tick_params(length=0, labelsize=FS_CBAR, colors=st["ink_dim"], pad=2)
    if ticks is not None:
        cb.set_ticks(ticks)
    if ticklabels is not None:
        cax.set_xticklabels(ticklabels, fontfamily=SPACE_MONO)
    cax.set_title(label, fontsize=FS_CBAR, color=st["ink_dim"], fontfamily=SPACE_MONO, pad=4)


def draw_panel(ax, st, kind, homes, pt, net, kreis, extent):
    xmin, xmax, ymin, ymax = extent
    ax.set_facecolor(st["panel_bg"])

    if kind == "pop":
        hx, hy = homes
        nx = 240
        ny = int(nx * (ymax - ymin) / (xmax - xmin))
        H, _, _ = np.histogram2d(hx, hy, bins=[nx, ny],
                                 range=[[xmin, xmax], [ymin, ymax]])
        H = H.T
        Hm = np.ma.masked_where(H <= 0, H)
        cmap = plt.get_cmap(st["heat_cmap"]).copy()
        cmap.set_bad(st["panel_bg"])
        norm = LogNorm(vmin=1, vmax=H.max())
        ax.imshow(Hm, origin="lower", extent=[xmin, xmax, ymin, ymax], cmap=cmap,
                  norm=norm, interpolation="bilinear", zorder=2, aspect="auto")
        kreis.plot(ax=ax, facecolor="none", edgecolor=st["kreis"],
                   linewidth=st["kreis_lw"], zorder=3)
        sm = ScalarMappable(norm=norm, cmap=cmap)
        slim_colorbar(ax, st, sm, "Wohnorte je Zelle (log)")

    elif kind == "net":
        kreis.plot(ax=ax, facecolor="none", edgecolor=st["kreis"],
                   linewidth=st["kreis_lw"], zorder=1)
        seg_by_mode, stop_xy = net
        ns = st["net"]
        # stops first (faint), then bus mesh, then tram, then rail on top
        ax.scatter(stop_xy[:, 0], stop_xy[:, 1], s=ns["stop"][2], c=ns["stop"][0],
                   alpha=ns["stop"][1], linewidths=0, marker=".", zorder=2, rasterized=True)
        for z, mode in enumerate(["bus", "tram", "rail"], start=3):
            if mode not in seg_by_mode:
                continue
            col, a, lw = ns[mode]
            ax.add_collection(LineCollection(seg_by_mode[mode], colors=col, linewidths=lw,
                                             alpha=a, zorder=z, capstyle="round"))
        handles = [plt.Line2D([0], [0], color=ns[m][0], lw=2.4, alpha=0.95)
                   for m in ["rail", "tram", "bus"] if m in seg_by_mode]
        labels = [MODE_LABEL[m] for m in ["rail", "tram", "bus"] if m in seg_by_mode]
        leg = ax.legend(handles, labels, loc="lower center", bbox_to_anchor=(0.5, 0.02),
                        ncol=3, frameon=False, fontsize=FS_CBAR, handlelength=1.6,
                        columnspacing=1.3, labelcolor=st["ink_dim"])
        for txt in leg.get_texts():
            txt.set_fontfamily(SPACE_MONO)

    else:  # pt usage lines coloured by departure time
        kreis.plot(ax=ax, facecolor="none", edgecolor=st["kreis"],
                   linewidth=st["kreis_lw"], zorder=1)
        segs, vals = pt
        cmap, norm = CMAP_TIME, Normalize(0, 24)
        colors = cmap(norm(vals))
        ax.add_collection(LineCollection(segs, colors=colors, linewidths=st["glow_lw"],
                                         alpha=st["glow_a"], zorder=3, capstyle="round"))
        ax.add_collection(LineCollection(segs, colors=colors, linewidths=st["sharp_lw"],
                                         alpha=st["sharp_a"], zorder=4, capstyle="round"))
        sm = ScalarMappable(norm=norm, cmap=cmap)
        slim_colorbar(ax, st, sm, "Abfahrtszeit", ticks=[0, 6, 12, 18, 24],
                      ticklabels=["0", "6", "12", "18", "24 h"])

    if kind == "pop":
        for _, r in kreis.iterrows():
            ars = str(r["ars5"])
            if ars in LABEL_THESE:
                ax.text(r["cx"], r["cy"], KREIS_LABEL.get(ars, ""), fontsize=FS_CITY,
                        color=st["city"], ha="center", va="center", alpha=0.92,
                        zorder=6, fontfamily=SPACE_MONO)

    ax.set_xlim(xmin, xmax)
    ax.set_ylim(ymin, ymax)
    ax.set_aspect("equal")
    ax.set_axis_off()


def compose(style, homes, pt, net, kreis):
    st = STYLES[style]
    xmin, ymin, xmax, ymax = kreis.total_bounds
    pad = 0.02 * (ymax - ymin)
    extent = (xmin - pad, xmax + pad, ymin - pad, ymax + pad)

    fig = plt.figure(figsize=(18, 12.4), dpi=200)
    fig.patch.set_facecolor(st["bg"])
    # Tight margins -> minimal white space. Panels start lower to leave a clean band
    # for the shared title + per-panel headers (no overlap).
    gs = fig.add_gridspec(1, 3, left=0.018, right=0.992, top=0.795, bottom=0.10, wspace=0.012)

    panels = [("pop", "BEVÖLKERUNG", "Dichte der Wohnorte"),
              ("net", "ÖV-NETZ", "Haltestellen & Linien nach Modus"),
              ("pt", "ÖV-WEGE", "Genutzte Wege · Farbe = Tageszeit")]
    for col, (kind, ttl, note) in enumerate(panels):
        ax = fig.add_subplot(gs[0, col])
        draw_panel(ax, st, kind, homes, pt, net, kreis, extent)
        ax.text(0.5, 1.060, ttl, transform=ax.transAxes, ha="center", va="bottom",
                fontsize=FS_PANEL, color=st["ink"], fontfamily=SPACE_MONO, fontweight="bold")
        ax.text(0.5, 1.022, note, transform=ax.transAxes, ha="center", va="bottom",
                fontsize=FS_NOTE, color=st["ink_dim"], fontfamily=SPACE_MONO)

    # Shared title band (compact)
    fig.text(0.018, 0.985, "REGION BRAUNSCHWEIG", fontsize=FS_TITLE, fontweight="bold",
             color=st["ink"], fontfamily=SPACE_MONO, ha="left", va="top")
    fig.text(0.020, 0.928,
             "Synthetische Bevölkerung und Wege aus dem eqasim-bs Verkehrsmodell",
             fontsize=FS_SUB, color=st["ink_dim"], fontfamily=SPACE_MONO, ha="left", va="top")
    fig.add_artist(plt.Line2D([0.018, 0.992], [0.895, 0.895], color=st["accent"],
                              linewidth=1.8, alpha=0.8))

    # Footer: source (traceability)
    fig.text(0.018, 0.045,
             "Quelle: eqasim-bs · 100%-Synthese (Region Braunschweig) · EPSG:25832",
             fontsize=FS_LEG, color=st["ink_dim"], fontfamily=SPACE_MONO, ha="left", va="center")
    fig.text(0.018, 0.018,
             "3,95 Mio. modellierte Wege · je Modus eine Stichprobe dargestellt · Darstellung schematisch",
             fontsize=FS_SRC, color=st["ink_dim"], fontfamily=SPACE_MONO, ha="left", va="center",
             alpha=0.85)

    out = OUT / f"editorial_{style}.png"
    fig.savefig(out, dpi=200, facecolor=st["bg"], bbox_inches="tight", pad_inches=0.08)
    plt.close(fig)
    log(f"saved {out.name}")
    return out


def main():
    t0 = time.time()
    homes, pt, net, kreis = load()
    log(f"loaded in {time.time()-t0:.1f}s")
    for style in ("dark", "light"):
        t1 = time.time()
        compose(style, homes, pt, net, kreis)
        log(f"  composed {style} in {time.time()-t1:.1f}s")
    log(f"DONE in {time.time()-t0:.1f}s -> {OUT}")


if __name__ == "__main__":
    main()
