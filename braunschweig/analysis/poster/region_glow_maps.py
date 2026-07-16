"""Generate 'fancy' presentation maps of the eqasim-bs Braunschweig model.

Motif: synthetic population as a fine point cloud + public-transport (PT) trips as
glowing origin->destination desire lines, with Kreis boundaries for context.

Produces 4 variants (2 styles x 2 extents) plus a contact sheet for picking:
  - style:  dark glow  /  light clean
  - extent: full region (ZGB)  /  Braunschweig city zoom

This is a one-off visual asset for a poster. Inputs are the committed 100% run
outputs (EPSG:25832). No network access / basemap tiles required.
"""
from __future__ import annotations

import time
from pathlib import Path

import geopandas as gpd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import matplotlib.font_manager as fm
from matplotlib.collections import LineCollection
from matplotlib.colors import LinearSegmentedColormap, Normalize

# Register Space Mono (poster typeface, OFL). Falls back gracefully if absent.
FONT_DIR = Path(__file__).resolve().parent / "fonts"
SPACE_MONO = "DejaVu Sans Mono"  # fallback
for ttf in sorted(FONT_DIR.glob("SpaceMono-*.ttf")):
    fm.fontManager.addfont(str(ttf))
    SPACE_MONO = fm.FontProperties(fname=str(ttf)).get_name()
plt.rcParams["font.family"] = SPACE_MONO

REPO = Path(__file__).resolve()
# Walk up to repo root (folder containing eqasim-data)
for parent in REPO.parents:
    if (parent / "eqasim-data").is_dir():
        ROOT = parent
        break
else:
    ROOT = Path("c:/Users/bienzeisler/Documents/GitHub/eqasim-bs")

DATA = ROOT / "eqasim-data" / "output_bs_100pct"
HOMES_PATH = DATA / "braunschweig_100pct_homes.gpkg"
TRIPS_PATH = DATA / "simulation_output" / "eqasim_trips.csv"
KREIS_PATH = ROOT / "eqasim-data" / "output_bs_25pct" / "simwrapper" / "kreis_socio.geojson"

OUT = Path(__file__).resolve().parent / "poster_maps"
OUT.mkdir(exist_ok=True)

CRS_METRIC = "EPSG:25832"
RNG = np.random.default_rng(42)  # reproducible sampling

# How many PT desire lines to draw (global sample, clipped per extent by axes limits).
PT_LINE_SAMPLE = 28000
# How many population points to draw (homes are ~hundreds of thousands; subsample for speed).
HOME_POINT_SAMPLE = 400000


def log(msg: str) -> None:
    print(f"[poster] {msg}", flush=True)


def load_data():
    log("loading homes ...")
    homes = gpd.read_file(HOMES_PATH)
    if homes.crs is None:
        homes = homes.set_crs(CRS_METRIC)
    homes = homes.to_crs(CRS_METRIC)
    hx = homes.geometry.x.to_numpy()
    hy = homes.geometry.y.to_numpy()
    if len(hx) > HOME_POINT_SAMPLE:
        idx = RNG.choice(len(hx), HOME_POINT_SAMPLE, replace=False)
        hx, hy = hx[idx], hy[idx]
    log(f"  homes points: {len(hx):,}")

    log("loading PT trips ...")
    cols = ["origin_x", "origin_y", "destination_x", "destination_y", "mode", "euclidean_distance"]
    trips = pd.read_csv(TRIPS_PATH, sep=";", usecols=cols)
    pt = trips[trips["mode"] == "pt"].copy()
    log(f"  PT trips total: {len(pt):,}")
    if len(pt) > PT_LINE_SAMPLE:
        pt = pt.sample(PT_LINE_SAMPLE, random_state=7)
    # Build segments array for LineCollection: shape (N, 2, 2)
    segs = np.stack(
        [
            np.column_stack([pt["origin_x"].to_numpy(), pt["origin_y"].to_numpy()]),
            np.column_stack([pt["destination_x"].to_numpy(), pt["destination_y"].to_numpy()]),
        ],
        axis=1,
    )
    dist_km = pt["euclidean_distance"].to_numpy() / 1000.0
    log(f"  PT lines drawn: {len(segs):,}")

    log("loading kreis boundaries ...")
    kreis = gpd.read_file(KREIS_PATH).to_crs(CRS_METRIC)

    return (hx, hy), (segs, dist_km), kreis


def extent_for(kreis: gpd.GeoDataFrame, which: str):
    """Return (xmin, xmax, ymin, ymax) for the requested extent."""
    if which == "region":
        xmin, ymin, xmax, ymax = kreis.total_bounds
        return xmin, xmax, ymin, ymax
    # city: Braunschweig kreisfreie Stadt = ars5 '03101'. Zero-pad before
    # comparing so the lookup works regardless of whether the GeoJSON property
    # survived as a padded string ("03101") or was coerced numeric (3101);
    # the previous un-padded equality + str.contains fallback silently
    # depended on the fallback for the padded (correct) export.
    city = kreis[kreis["ars5"].astype(str).str.zfill(5) == "03101"]
    if city.empty:
        raise RuntimeError(
            "kreis_socio.geojson: no feature with ars5 == '03101' "
            f"(Braunschweig); got ars5 values {sorted(set(kreis['ars5'].astype(str)))}"
        )
    xmin, ymin, xmax, ymax = city.total_bounds
    pad = 0.06 * max(xmax - xmin, ymax - ymin)
    return xmin - pad, xmax + pad, ymin - pad, ymax + pad


# Custom colormaps for the PT desire lines (short=cool, long=hot).
CMAP_DARK = LinearSegmentedColormap.from_list("pt_dark", ["#22d3ee", "#6366f1", "#d946ef", "#fb7185"])
CMAP_LIGHT = LinearSegmentedColormap.from_list("pt_light", ["#0ea5e9", "#6d28d9", "#be123c"])

STYLES = {
    "dark": {
        "bg": "#070b14",
        "home_color": "#cfe8ff",
        "home_alpha": 0.16,
        "home_size": 0.8,
        "home_bloom": True,        # extra soft halo so settlements read as glowing nodes
        "home_bloom_alpha": 0.03,
        "home_bloom_size": 6.0,
        "kreis_edge": "#33465f",
        "kreis_lw": 0.7,
        "kreis_face": "none",
        "cmap": CMAP_DARK,
        "glow_alpha": 0.011,
        "glow_lw": 2.1,
        "sharp_alpha": 0.16,
        "sharp_lw": 0.5,
        "title_color": "#eef3fb",
    },
    "light": {
        "bg": "#ffffff",
        "home_color": "#243b6b",
        "home_alpha": 0.11,
        "home_size": 0.7,
        "home_bloom": True,
        "home_bloom_alpha": 0.025,
        "home_bloom_size": 6.0,
        "kreis_edge": "#bcc6d2",
        "kreis_lw": 0.8,
        "kreis_face": "#f6f8fb",
        "cmap": CMAP_LIGHT,
        "glow_alpha": 0.014,
        "glow_lw": 1.8,
        "sharp_alpha": 0.15,
        "sharp_lw": 0.45,
        "title_color": "#1b2a4a",
    },
}


def render(style_name, extent_name, homes, pt, kreis, ax=None, standalone=True):
    st = STYLES[style_name]
    hx, hy = homes
    segs, dist_km = pt
    xmin, xmax, ymin, ymax = extent_for(kreis, extent_name)

    created = False
    if ax is None:
        created = True
        # aspect from extent
        w, h = xmax - xmin, ymax - ymin
        fig_w = 12.0
        fig_h = fig_w * h / w
        fig, ax = plt.subplots(figsize=(fig_w, fig_h), dpi=200)
    fig = ax.figure
    ax.set_facecolor(st["bg"])
    if created:
        fig.patch.set_facecolor(st["bg"])

    # Layer 1: kreis context
    kreis.plot(ax=ax, facecolor=st["kreis_face"], edgecolor=st["kreis_edge"],
               linewidth=st["kreis_lw"], zorder=1)

    # Layer 2: population point cloud. Optional soft "bloom" halo underneath makes
    # settlement cores (BS, Wolfsburg, Salzgitter, Wolfenbuettel, Gifhorn) read as glowing nodes.
    if st.get("home_bloom"):
        ax.scatter(hx, hy, s=st["home_bloom_size"], c=st["home_color"],
                   alpha=st["home_bloom_alpha"], linewidths=0, marker=".",
                   zorder=2, rasterized=True)
    ax.scatter(hx, hy, s=st["home_size"], c=st["home_color"], alpha=st["home_alpha"],
               linewidths=0, marker=".", zorder=2, rasterized=True)

    # Layer 3: PT desire lines, colored by trip length.
    norm = Normalize(vmin=0, vmax=np.percentile(dist_km, 95) if len(dist_km) else 1)
    colors = st["cmap"](norm(dist_km))
    # wide blurry glow pass
    lc_glow = LineCollection(segs, colors=colors, linewidths=st["glow_lw"],
                             alpha=st["glow_alpha"], zorder=3, capstyle="round")
    ax.add_collection(lc_glow)
    # sharp pass
    lc_sharp = LineCollection(segs, colors=colors, linewidths=st["sharp_lw"],
                              alpha=st["sharp_alpha"], zorder=4, capstyle="round")
    ax.add_collection(lc_sharp)

    ax.set_xlim(xmin, xmax)
    ax.set_ylim(ymin, ymax)
    ax.set_aspect("equal")
    ax.set_axis_off()

    # Space Mono caption (only on standalone exports; contact sheet uses titles).
    if standalone:
        headline = "REGION BRAUNSCHWEIG" if extent_name == "region" else "STADT BRAUNSCHWEIG"
        sub = "Synthetische Bevölkerung  +  ÖV-Wege   //   eqasim-bs"
        ax.text(0.035, 0.075, headline, transform=ax.transAxes, ha="left", va="bottom",
                fontfamily=SPACE_MONO, fontweight="bold", fontsize=15,
                color=st["title_color"], zorder=10)
        ax.text(0.035, 0.045, sub, transform=ax.transAxes, ha="left", va="bottom",
                fontfamily=SPACE_MONO, fontweight="normal", fontsize=8.5,
                color=st["title_color"], alpha=0.75, zorder=10)

    if standalone:
        name = f"map_{style_name}_{extent_name}"
        path = OUT / f"{name}.png"
        fig.savefig(path, dpi=200, bbox_inches="tight", pad_inches=0.05,
                    facecolor=st["bg"])
        plt.close(fig)
        log(f"saved {path.name}")
        return path
    return ax


def main():
    t0 = time.time()
    homes, pt, kreis = load_data()
    log(f"data loaded in {time.time()-t0:.1f}s")

    combos = [("dark", "region"), ("dark", "city"), ("light", "region"), ("light", "city")]
    paths = []
    for style, extent in combos:
        t1 = time.time()
        paths.append(render(style, extent, homes, pt, kreis))
        log(f"  rendered {style}/{extent} in {time.time()-t1:.1f}s")

    # Contact sheet 2x2 for quick comparison (neutral grey background).
    log("building contact sheet ...")
    fig, axes = plt.subplots(2, 2, figsize=(20, 16))
    fig.patch.set_facecolor("#dfe3e8")
    titles = {
        ("dark", "region"): "A  dark glow / Region",
        ("dark", "city"): "B  dark glow / Stadt BS",
        ("light", "region"): "C  hell clean / Region",
        ("light", "city"): "D  hell clean / Stadt BS",
    }
    for ax, (style, extent) in zip(axes.ravel(), combos):
        render(style, extent, homes, pt, kreis, ax=ax, standalone=False)
        ax.set_title(titles[(style, extent)], fontsize=16, color="#222",
                     fontfamily=SPACE_MONO, pad=8)
    fig.tight_layout()
    sheet = OUT / "_contact_sheet.png"
    fig.savefig(sheet, dpi=110, bbox_inches="tight", facecolor="#dfe3e8")
    plt.close(fig)
    log(f"saved {sheet.name}")
    log(f"DONE in {time.time()-t0:.1f}s -> {OUT}")


if __name__ == "__main__":
    main()
