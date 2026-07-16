"""ÖPNV-focused presentation maps of the eqasim-bs Braunschweig model (dark style).

Design (per design-for-ai): clear type hierarchy (title >> panel title >> note >> source),
restrained purposeful colour (dark ground, PT identity in cyan/magenta, data colours encode
real quantities), even spacing, the data glow is the hero. Space Mono throughout.

All panels use REAL model data (no schematic network):
  pop_heat       - population density heatmap (where PT demand lives)
  lines_dist     - PT trips as origin->destination desire lines, coloured by distance
  pt_heat        - PT demand heatmap (density of PT trip origins + destinations)
  lines_purpose  - PT trips coloured by trip purpose (work/education/leisure/...)

Outputs three layout variants to compare:
  variant_a_dark.png  BEVÖLKERUNG | ÖV-WEGE | ÖV-NACHFRAGE
  variant_b_dark.png  ÖV-WEGE | ÖV-NACHFRAGE | ÖV NACH ZWECK
  variant_c_hero_dark.png  one large ÖV-WEGE glow
"""
from __future__ import annotations

import time
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
KREIS_PATH = ROOT / "eqasim-data" / "output_bs_25pct" / "simwrapper" / "kreis_socio.geojson"
OUT = HERE / "poster_maps"
OUT.mkdir(exist_ok=True)

CRS_METRIC = "EPSG:25832"
PT_LINE_SAMPLE = 32000

# --- fonts -----------------------------------------------------------------
FONT_DIR = HERE / "fonts"
SPACE_MONO = "DejaVu Sans Mono"
for ttf in sorted(FONT_DIR.glob("SpaceMono-*.ttf")):
    fm.fontManager.addfont(str(ttf))
    SPACE_MONO = fm.FontProperties(fname=str(ttf)).get_name()
plt.rcParams["font.family"] = SPACE_MONO

# Keys are the zero-padded 5-digit Kreis ARS, matching the ``ars5`` property
# written into kreis_socio.geojson by the SimWrapper spatial export.
KREIS_LABEL = {"03101": "Braunschweig", "03102": "Salzgitter", "03103": "Wolfsburg",
               "03151": "Gifhorn", "03157": "Peine", "03158": "Wolfenbüttel"}
LABEL_THESE = set(KREIS_LABEL)

# --- design tokens (style-selected) ----------------------------------------
STYLE = "light"  # "light" (white poster) or "dark"

if STYLE == "dark":
    BG, INK, INK_DIM = "#06090f", "#eef3fb", "#9fb2cc"
    ACCENT, KREIS_EDGE, CITY = "#22d3ee", "#2c3e57", "#dceaff"
    POP_CMAP = "magma"
    CMAP_DIST = LinearSegmentedColormap.from_list("dist",
                ["#22d3ee", "#6366f1", "#d946ef", "#fb7185"])
    CMAP_PTHEAT = LinearSegmentedColormap.from_list("ptheat",
                ["#0b1b2b", "#0e7490", "#22d3ee", "#a5f3fc", "#ffffff"])
    CMAP_TIME = LinearSegmentedColormap.from_list("time", [
        "#27306b", "#3b82f6", "#22d3ee", "#fde047", "#fb923c", "#f43f5e", "#312e6b"])
    PURPOSE_COLOR = {"work": "#38bdf8", "education": "#a78bfa", "leisure": "#fb7185",
                     "shop": "#fbbf24", "other": "#34d399", "home": "#94a3b8"}
    LINE_DIST = (0.012, 2.3, 0.18, 0.55)      # glow_a, glow_lw, sharp_a, sharp_lw
    LINE_PURPOSE = (0.012, 2.3, 0.20, 0.55)
    LINE_TIME = (0.012, 2.3, 0.18, 0.55)
else:  # light
    BG, INK, INK_DIM = "#ffffff", "#14213d", "#52617a"
    ACCENT, KREIS_EDGE, CITY = "#0ea5e9", "#aab6c6", "#14213d"
    POP_CMAP = "magma_r"
    # On white: start saturated/dark so lines stay visible (no additive glow).
    CMAP_DIST = LinearSegmentedColormap.from_list("dist",
                ["#0369a1", "#4f46e5", "#c026d3", "#be123c"])
    CMAP_PTHEAT = LinearSegmentedColormap.from_list("ptheat",
                ["#eaf6fd", "#7dd3fc", "#0ea5e9", "#0c4a6e"])
    # Vivid day-cycle for WHITE: highly saturated, distinct hues per daypart (no dull browns).
    CMAP_TIME = LinearSegmentedColormap.from_list("time", [
        "#4f46e5", "#0ea5e9", "#06b6d4", "#ec4899", "#f43f5e", "#fb923c", "#7c3aed"])
    PURPOSE_COLOR = {"work": "#0284c7", "education": "#7c3aed", "leisure": "#e11d48",
                     "shop": "#d97706", "other": "#059669", "home": "#94a3b8"}
    # White needs no glow; draw a single denser, saturated pass (sharp). Purpose extra-intense.
    LINE_DIST = (0.0, 0.0, 0.10, 0.55)
    LINE_PURPOSE = (0.0, 0.0, 0.30, 0.7)
    LINE_TIME = (0.0, 0.0, 0.14, 0.6)

# Type scale (single source) -- bumped up for poster legibility.
FS_TITLE, FS_SUB, FS_PANEL, FS_NOTE, FS_CITY, FS_CBAR, FS_SRC = 58, 26, 31, 18, 15, 17, 16
PURPOSE_LABEL = {"work": "Arbeit", "education": "Bildung", "leisure": "Freizeit",
                 "shop": "Einkauf", "other": "Sonstiges", "home": "Zuhause"}
PURPOSE_ORDER = ["work", "education", "leisure", "shop", "other", "home"]


def log(m):
    print(f"[oepnv] {m}", flush=True)


def _segments(df):
    return np.stack([
        np.column_stack([df["origin_x"].to_numpy(), df["origin_y"].to_numpy()]),
        np.column_stack([df["destination_x"].to_numpy(), df["destination_y"].to_numpy()]),
    ], axis=1)


def load():
    log("homes ...")
    homes = gpd.read_file(HOMES_PATH)
    homes = homes.set_crs(CRS_METRIC) if homes.crs is None else homes.to_crs(CRS_METRIC)
    hx, hy = homes.geometry.x.to_numpy(), homes.geometry.y.to_numpy()

    log("trips ...")
    cols = ["origin_x", "origin_y", "destination_x", "destination_y",
            "mode", "euclidean_distance", "departure_time", "following_purpose"]
    trips = pd.read_csv(TRIPS_PATH, sep=";", usecols=cols)
    pt = trips[trips["mode"] == "pt"].copy()
    log(f"  pt trips total {len(pt):,}")

    # PT demand point cloud (all origins + destinations) for the hotspot heatmap.
    pt_pts = (np.concatenate([pt["origin_x"].to_numpy(), pt["destination_x"].to_numpy()]),
              np.concatenate([pt["origin_y"].to_numpy(), pt["destination_y"].to_numpy()]))

    # Full distributions (chart panels).
    dist_all = pt["euclidean_distance"].to_numpy() / 1000.0
    hours_all = (pt["departure_time"].to_numpy() / 3600.0) % 24.0

    # Sample for the line panels.
    pt_s = pt.sample(PT_LINE_SAMPLE, random_state=7) if len(pt) > PT_LINE_SAMPLE else pt
    segs = _segments(pt_s)
    dist = pt_s["euclidean_distance"].to_numpy() / 1000.0
    purpose = pt_s["following_purpose"].to_numpy()
    hour = (pt_s["departure_time"].to_numpy() / 3600.0) % 24.0

    log("kreis ...")
    kreis = gpd.read_file(KREIS_PATH).to_crs(CRS_METRIC).reset_index(drop=True)
    # Normalise the Kreis key to the zero-padded 5-char ARS so the KREIS_LABEL
    # lookups match regardless of string/numeric coercion in the GeoJSON.
    kreis["ars5"] = kreis["ars5"].astype(str).str.zfill(5)
    kreis["cx"] = kreis.geometry.centroid.x
    kreis["cy"] = kreis.geometry.centroid.y

    log("kreis assignment (OD) ...")
    k_o = _assign_kreis(segs[:, 0, :], kreis)
    k_d = _assign_kreis(segs[:, 1, :], kreis)

    return dict(homes=(hx, hy), pt_pts=pt_pts, segs=segs, dist=dist, purpose=purpose,
                hour=hour, dist_all=dist_all, hours_all=hours_all, k_o=k_o, k_d=k_d,
                kreis=kreis, n_pt=len(pt))


def _assign_kreis(xy, kreis):
    """Positional Kreis index (0..n-1) for each point; -1 if outside all Kreise."""
    pts = gpd.GeoDataFrame(geometry=gpd.points_from_xy(xy[:, 0], xy[:, 1]), crs=CRS_METRIC)
    j = gpd.sjoin(pts, kreis[["geometry"]], how="left", predicate="within")
    j = j[~j.index.duplicated(keep="first")]
    idx = j["index_right"].to_numpy()
    return np.where(np.isnan(idx), -1, idx).astype(int)


def slim_colorbar(ax, mappable, label, ticks=None, ticklabels=None):
    cax = ax.inset_axes([0.12, 0.05, 0.76, 0.018])
    cb = ax.figure.colorbar(mappable, cax=cax, orientation="horizontal")
    cb.outline.set_visible(False)
    cax.tick_params(length=0, labelsize=FS_CBAR, colors=INK_DIM, pad=2)
    if ticks is not None:
        cb.set_ticks(ticks)
    if ticklabels is not None:
        cax.set_xticklabels(ticklabels, fontfamily=SPACE_MONO)
    cax.set_title(label, fontsize=FS_CBAR, color=INK_DIM, fontfamily=SPACE_MONO, pad=4)


def _heatmap(ax, hx, hy, extent, cmap):
    xmin, xmax, ymin, ymax = extent
    nx = 240
    ny = int(nx * (ymax - ymin) / (xmax - xmin))
    H, _, _ = np.histogram2d(hx, hy, bins=[nx, ny], range=[[xmin, xmax], [ymin, ymax]])
    H = H.T
    Hm = np.ma.masked_where(H <= 0, H)
    cm = (cmap if isinstance(cmap, LinearSegmentedColormap) else plt.get_cmap(cmap)).copy()
    cm.set_bad(BG)
    norm = LogNorm(vmin=1, vmax=H.max())
    ax.imshow(Hm, origin="lower", extent=[xmin, xmax, ymin, ymax], cmap=cm,
              norm=norm, interpolation="bilinear", zorder=2, aspect="auto")
    return ScalarMappable(norm=norm, cmap=cm)


def _lines(ax, segs, colors, params):
    """params = (glow_alpha, glow_lw, sharp_alpha, sharp_lw). A wide low-alpha glow pass
    (additive, only useful on dark) is skipped when glow_alpha == 0 (white style)."""
    ga, glw, sa, slw = params
    if ga > 0:
        ax.add_collection(LineCollection(segs, colors=colors, linewidths=glw,
                                         alpha=ga, zorder=3, capstyle="round"))
    ax.add_collection(LineCollection(segs, colors=colors, linewidths=slw,
                                     alpha=sa, zorder=4, capstyle="round"))


def draw_panel(ax, kind, D, extent, hero=False):
    xmin, xmax, ymin, ymax = extent
    ax.set_facecolor(BG)
    kreis = D["kreis"]

    if kind == "pop_heat":
        kreis.plot(ax=ax, facecolor="none", edgecolor=KREIS_EDGE, linewidth=0.8, zorder=3)
        sm = _heatmap(ax, *D["homes"], extent, "magma")
        if not hero:
            slim_colorbar(ax, sm, "Wohnorte je Zelle (log)")
        for _, r in kreis.iterrows():
            a = str(r["ars5"])
            if a in LABEL_THESE:
                ax.text(r["cx"], r["cy"], KREIS_LABEL[a], fontsize=FS_CITY, color=CITY,
                        ha="center", va="center", alpha=0.92, zorder=6, fontfamily=SPACE_MONO)

    elif kind == "pt_heat":
        kreis.plot(ax=ax, facecolor="none", edgecolor=KREIS_EDGE, linewidth=0.8, zorder=3)
        sm = _heatmap(ax, D["pt_pts"][0], D["pt_pts"][1], extent, CMAP_PTHEAT)
        if not hero:
            slim_colorbar(ax, sm, "Start + Ziel je Zelle (log)")

    elif kind == "lines_dist":
        kreis.plot(ax=ax, facecolor="none", edgecolor=KREIS_EDGE, linewidth=0.8, zorder=1)
        # Colour spread tuned to the bulk (95th pct ~37 km); longer trips clamp to the
        # end colour, flagged honestly as "<vmax> km+" in the legend.
        vmax = float(np.percentile(D["dist"], 95))
        norm = Normalize(0, vmax)
        _lines(ax, D["segs"], CMAP_DIST(norm(D["dist"])), LINE_DIST)
        if not hero:
            vmi = int(round(vmax))
            slim_colorbar(ax, ScalarMappable(norm=norm, cmap=CMAP_DIST), "Reiseweite",
                          ticks=[0, vmi // 2, vmi], ticklabels=["0", f"{vmi//2}", f"{vmi} km+"])

    elif kind == "lines_purpose":
        kreis.plot(ax=ax, facecolor="none", edgecolor=KREIS_EDGE, linewidth=0.8, zorder=1)
        cols = np.array([PURPOSE_COLOR.get(p, "#64748b") for p in D["purpose"]])
        _lines(ax, D["segs"], cols, LINE_PURPOSE)
        present = [p for p in PURPOSE_ORDER if p in set(D["purpose"])]
        handles = [plt.Line2D([0], [0], color=PURPOSE_COLOR[p], lw=2.6) for p in present]
        leg = ax.legend(handles, [PURPOSE_LABEL[p] for p in present], loc="lower center",
                        bbox_to_anchor=(0.5, 0.015), ncol=3, frameon=False, fontsize=FS_CBAR,
                        handlelength=1.5, columnspacing=1.2, labelcolor=INK_DIM)
        for t in leg.get_texts():
            t.set_fontfamily(SPACE_MONO)

    elif kind == "lines_time":
        kreis.plot(ax=ax, facecolor="none", edgecolor=KREIS_EDGE, linewidth=0.8, zorder=1)
        norm = Normalize(0, 24)
        _lines(ax, D["segs"], CMAP_TIME(norm(D["hour"])), LINE_TIME)
        if not hero:
            slim_colorbar(ax, ScalarMappable(norm=norm, cmap=CMAP_TIME), "Abfahrtszeit",
                          ticks=[0, 6, 12, 18, 24], ticklabels=["0", "6", "12", "18", "24 h"])

    elif kind == "od":
        kreis.plot(ax=ax, facecolor="none", edgecolor=KREIS_EDGE, linewidth=0.8, zorder=1)
        ko, kd = D["k_o"], D["k_d"]
        pairs = {}
        for a, b in zip(ko, kd):
            if a < 0 or b < 0 or a == b:
                continue
            key = (a, b) if a < b else (b, a)
            pairs[key] = pairs.get(key, 0) + 1
        cx, cy = D["kreis"]["cx"].to_numpy(), D["kreis"]["cy"].to_numpy()
        mx = max(pairs.values()) if pairs else 1
        for (a, b), c in sorted(pairs.items(), key=lambda kv: kv[1]):
            ax.plot([cx[a], cx[b]], [cy[a], cy[b]], color=ACCENT,
                    lw=0.6 + 9.0 * (c / mx), alpha=0.55, solid_capstyle="round", zorder=3)
        ax.scatter(cx, cy, s=70, c=INK, zorder=4, edgecolors=BG, linewidths=1.2)
        for _, r in D["kreis"].iterrows():
            a = str(r["ars5"])
            if a in LABEL_THESE:
                ax.text(r["cx"], r["cy"] + 2500, KREIS_LABEL[a], fontsize=FS_CITY,
                        color=INK, ha="center", va="bottom", zorder=5, fontfamily=SPACE_MONO)

    elif kind == "choro":
        counts = np.zeros(len(D["kreis"]))
        for a in D["k_o"]:
            if a >= 0:
                counts[a] += 1
        kk = D["kreis"].copy()
        kk["cnt"] = counts
        norm = Normalize(0, counts.max() if counts.max() > 0 else 1)
        kk.plot(ax=ax, column="cnt", cmap=CMAP_PTHEAT, norm=norm,
                edgecolor=KREIS_EDGE, linewidth=0.8, zorder=2)
        if not hero:
            slim_colorbar(ax, ScalarMappable(norm=norm, cmap=CMAP_PTHEAT),
                          "ÖV-Wege je Kreis (Stichprobe)")
        for _, r in kk.iterrows():
            a = str(r["ars5"])
            if a in LABEL_THESE:
                ax.text(r["cx"], r["cy"], KREIS_LABEL[a], fontsize=FS_CITY, color=INK,
                        ha="center", va="center", zorder=5, fontfamily=SPACE_MONO)

    elif kind in ("dist_hist", "hourly"):
        _chart_panel(ax, kind, D)
        return

    ax.set_xlim(xmin, xmax)
    ax.set_ylim(ymin, ymax)
    ax.set_aspect("equal")
    ax.set_axis_off()


def _chart_panel(ax, kind, D):
    """Distribution charts in a portrait cell (horizontal bars read well vertically)."""
    ax.set_facecolor(BG)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(INK_DIM)
        ax.spines[s].set_linewidth(0.8)
    ax.tick_params(colors=INK_DIM, labelsize=FS_CBAR, length=3)

    if kind == "dist_hist":
        bins = np.arange(0, 63, 3)
        counts, _ = np.histogram(D["dist_all"], bins=bins)
        centers = (bins[:-1] + bins[1:]) / 2
        norm = Normalize(0, float(np.percentile(D["dist_all"], 95)))
        colors = CMAP_DIST(norm(np.clip(centers, 0, norm.vmax)))
        ax.barh(centers, counts, height=2.6, color=colors)
        ax.set_ylim(0, 60)
        ax.set_yticks([0, 15, 30, 45, 60])
        ax.set_yticklabels(["0", "15", "30", "45", "60 km"], fontfamily=SPACE_MONO)
        ax.set_xticks([])
    else:  # hourly
        counts, _ = np.histogram(D["hours_all"], bins=np.arange(0, 25, 1))
        colors = CMAP_TIME(np.linspace(0, 1, 24))
        ax.barh(np.arange(24) + 0.5, counts, height=0.85, color=colors)
        ax.set_ylim(0, 24)
        ax.invert_yaxis()  # 0 h at top, reads downward like a day
        ax.set_yticks([0, 6, 12, 18, 24])
        ax.set_yticklabels(["0", "6", "12", "18", "24 h"], fontfamily=SPACE_MONO)
        ax.set_xticks([])
    for lbl in ax.get_yticklabels():
        lbl.set_color(INK_DIM)


def _extent(kreis):
    xmin, ymin, xmax, ymax = kreis.total_bounds
    pad = 0.02 * (ymax - ymin)
    return (xmin - pad, xmax + pad, ymin - pad, ymax + pad)


def compose_triptych(name, panels, D):
    extent = _extent(D["kreis"])
    fig = plt.figure(figsize=(18, 12.4), dpi=200)
    fig.patch.set_facecolor(BG)
    gs = fig.add_gridspec(1, 3, left=0.018, right=0.992, top=0.795, bottom=0.10, wspace=0.012)
    for col, (kind, ttl, note) in enumerate(panels):
        ax = fig.add_subplot(gs[0, col])
        draw_panel(ax, kind, D, extent)
        ax.text(0.5, 1.060, ttl, transform=ax.transAxes, ha="center", va="bottom",
                fontsize=FS_PANEL, color=INK, fontfamily=SPACE_MONO, fontweight="bold")
        ax.text(0.5, 1.022, note, transform=ax.transAxes, ha="center", va="bottom",
                fontsize=FS_NOTE, color=INK_DIM, fontfamily=SPACE_MONO)

    fig.text(0.018, 0.982, "VERKEHRSMODELL REGION BRAUNSCHWEIG", fontsize=52,
             fontweight="bold", color=INK, fontfamily=SPACE_MONO, ha="left", va="top")
    fig.text(0.020, 0.928, "Öffentlicher Verkehr (ÖPNV) · eqasim-bs Modell",
             fontsize=FS_SUB, color=INK_DIM, fontfamily=SPACE_MONO, ha="left", va="top")
    fig.add_artist(plt.Line2D([0.018, 0.992], [0.898, 0.898], color=ACCENT, linewidth=1.8, alpha=0.85))
    fig.text(0.018, 0.030,
             "Quelle: eqasim-bs Verkehrsmodell · 100%-Synthese (Region Braunschweig) · EPSG:25832",
             fontsize=FS_SRC, color=INK_DIM, fontfamily=SPACE_MONO, ha="left", va="center")

    out = OUT / f"{name}.png"
    fig.savefig(out, dpi=200, facecolor=BG, bbox_inches="tight", pad_inches=0.08)
    plt.close(fig)
    log(f"saved {out.name}")


def compose_hero(D):
    extent = _extent(D["kreis"])
    # Wide canvas; map centred, generous dark margins for the editorial text block.
    xmin, xmax, ymin, ymax = extent
    w, h = xmax - xmin, ymax - ymin
    cx = (xmin + xmax) / 2
    target_ratio = 16 / 9
    half_w = max(w, h * target_ratio) / 2 * 1.05
    ex = (cx - half_w, cx + half_w, ymin - 0.02 * h, ymax + 0.02 * h)

    fig = plt.figure(figsize=(19, 10.7), dpi=200)
    fig.patch.set_facecolor(BG)
    ax = fig.add_axes([0, 0, 1, 1])
    draw_panel(ax, "lines_dist", D, ex, hero=True)
    ax.set_xlim(ex[0], ex[1]); ax.set_ylim(ex[2], ex[3]); ax.set_axis_off()

    fig.text(0.035, 0.135, "ÖPNV-WEGE", fontsize=64, fontweight="bold", color=INK,
             fontfamily=SPACE_MONO, ha="left", va="bottom")
    fig.text(0.035, 0.092, "REGION BRAUNSCHWEIG", fontsize=30, color=ACCENT,
             fontfamily=SPACE_MONO, ha="left", va="bottom")
    fig.text(0.035, 0.045,
             "Öffentlicher Verkehr im eqasim-bs Verkehrsmodell · Farbe = Reiseweite",
             fontsize=17, color=INK_DIM, fontfamily=SPACE_MONO, ha="left", va="bottom")
    fig.text(0.035, 0.022,
             f"Quelle: eqasim-bs · 100%-Synthese · EPSG:25832 · {D['n_pt']/1e6:.2f} Mio. ÖV-Wege (Stichprobe)",
             fontsize=12, color=INK_DIM, fontfamily=SPACE_MONO, ha="left", va="bottom", alpha=0.8)

    out = OUT / "variant_c_hero_dark.png"
    fig.savefig(out, dpi=200, facecolor=BG)
    plt.close(fig)
    log(f"saved {out.name}")


def main():
    t0 = time.time()
    D = load()
    log(f"loaded in {time.time()-t0:.1f}s")
    # Fixed first two panels; the third panel is what we are A/B testing.
    base = [("lines_dist", "ÖV-WEGE", "Farbe = Reiseweite"),
            ("pt_heat", "ÖV-NACHFRAGE", "Start- & Zielorte der Wege")]
    options = {
        "dist_hist": ("REISEWEITEN", "Verteilung der Reiseweiten"),
        "hourly": ("TAGESGANG", "Wege je Stunde (0–24 Uhr)"),
        "od": ("ÖV-KORRIDORE", "Linienbreite = Anzahl Wege"),
        "choro": ("ÖV-NACHFRAGE / KREIS", "ÖV-Wege je Kreis"),
        "lines_time": ("ÖV NACH TAGESZEIT", "Farbe = Abfahrtszeit"),
    }
    for kind, (ttl, note) in options.items():
        compose_triptych(f"variant_{kind}_{STYLE}", base + [(kind, ttl, note)], D)
    log(f"DONE in {time.time()-t0:.1f}s -> {OUT}")


if __name__ == "__main__":
    main()
