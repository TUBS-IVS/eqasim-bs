# -*- coding: utf-8 -*-
"""Two-panel management-deck figure: (left) BEV share of the attributed synthetic
fleet per Gemeinde (choropleth), (right) vehicle-segment mix by household economic
status (normalized stacked horizontal bars).

Data (100% all-features PopulationSim run, exported 2026-06-30):
  * population_explorer.gpkg layer 'gemeinde_aggregat' (EPSG:25832):
      powertrain_share_bev + n_vehicles per Gemeinde. The underlying vehicles
      layer holds exactly the 579,501 attributed cars (mode=='car', brand set),
      so powertrain_share_bev is the BEV share OF THE ATTRIBUTED FLEET.
  * braunschweig_100pct_allfeat_popsim_vehicles.csv (semicolon-separated):
      attributed fleet rows (mode=='car' AND brand notna) for segment x status.
  * kreis_socio.geojson (EPSG:4326 -> 25832) for Kreis boundaries.
  * kba_gemeinde_private_bev.csv (committed KBA reference) only for the
    ars5 -> Kreis name mapping (traceable label source).

Assumption: Gemeinden with fewer than MIN_VEHICLES_FOR_SHARE attributed vehicles
(3 gemeindefreie Gebiete with 4/6/46 vehicles) are masked as no-data because a
share from <50 vehicles is not meaningful. Stated in the map footnote.
"""
import numpy as np
import pandas as pd
import geopandas as gpd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.colors import Normalize, to_rgb
from matplotlib.patches import Patch
import matplotlib.patheffects as pe

# ---------------------------------------------------------------- paths
BASE = "C:/Users/bienzeisler/Downloads/popsim_100pct_results/output_bs_100pct_allfeat_popsim"
GPKG = BASE + "/analysis/population_validation/population_explorer.gpkg"
VEHICLES_CSV = BASE + "/braunschweig_100pct_allfeat_popsim_vehicles.csv"
KREIS_GEOJSON = BASE + "/simwrapper/kreis_socio.geojson"
KBA_GEM_BEV = ("c:/Users/bienzeisler/Documents/GitHub/eqasim-bs/eqasim-data/data/"
               "braunschweig/kba/derived/kba_gemeinde_private_bev.csv")
FONT_DIR = "c:/Users/bienzeisler/Documents/GitHub/eqasim-bs/braunschweig/analysis/poster/fonts/"
OUT_PNG = ("C:/Users/BIENZE~1/AppData/Local/Temp/claude/"
           "c--Users-bienzeisler-Documents-GitHub-eqasim-bs/"
           "b066c272-e6db-4fdb-ad5f-98cd5497db59/scratchpad/figs/fig_fleet_space.png")

MIN_VEHICLES_FOR_SHARE = 50  # mask Gemeinden below this attributed-fleet size

# ---------------------------------------------------------------- style
BG = "#0a0e14"
INK = "#eef3fb"
SUB = "#8b95a7"
MUT = "#5a6577"
GRID = "#1d2633"
KREIS_EDGE = "#33465f"
NODATA = "#11161f"

for fname in ("SpaceMono-Regular.ttf", "SpaceMono-Bold.ttf"):
    try:
        font_manager.fontManager.addfont(FONT_DIR + fname)
    except Exception:
        pass
family = "Space Mono" if any("Space Mono" in f.name for f in font_manager.fontManager.ttflist) \
    else "DejaVu Sans Mono"
plt.rcParams.update({"font.family": family, "axes.unicode_minus": False,
                     "text.color": INK})


def de(x, dec=1):
    """Format a number German-style (decimal comma, dot thousands)."""
    s = f"{x:,.{dec}f}"
    return s.replace(",", "@").replace(".", ",").replace("@", ".")


# ---------------------------------------------------------------- data: left map
gem = gpd.read_file(GPKG, layer="gemeinde_aggregat",
                    columns=["commune_id", "n_vehicles", "powertrain_share_bev"])
assert str(gem.crs).endswith("25832"), f"unexpected CRS {gem.crs}"
gem["bev_pct"] = gem["powertrain_share_bev"] * 100.0
tiny = gem["n_vehicles"].fillna(0) < MIN_VEHICLES_FOR_SHARE
n_masked_tiny = int((tiny & gem["bev_pct"].notna()).sum())
gem.loc[tiny, "bev_pct"] = np.nan
print(f"gemeinden: {len(gem)} total, {gem['bev_pct'].notna().sum()} with data, "
      f"{n_masked_tiny} masked (<{MIN_VEHICLES_FOR_SHARE} vehicles)")

kreis = gpd.read_file(KREIS_GEOJSON).to_crs(25832)
kba_names = pd.read_csv(KBA_GEM_BEV, dtype={"kreis_ags5": str})
name_map = (kba_names.drop_duplicates("kreis_ags5")
            .set_index("kreis_ags5")["kreis_name"]
            .str.replace(", Stadt", "", regex=False).to_dict())
kreis["name"] = kreis["ars5"].map(name_map)
# Cosmetic display fix only (source file stores the ASCII transliteration).
kreis["name"] = kreis["name"].replace({"Wolfenbuettel": "Wolfenbüttel"})

vmax = float(np.ceil(np.nanmax(gem["bev_pct"])))
print("map vmax:", vmax)

# ---------------------------------------------------------------- data: right bars
veh = pd.read_csv(VEHICLES_CSV, sep=";", low_memory=False,
                  usecols=["mode", "brand", "segment", "powertrain", "economic_status"])
att = veh[(veh["mode"] == "car") & veh["brand"].notna()].copy()
n_fleet = len(att)
n_hh = 427625  # from task spec; not re-derived here (household_id not loaded)
bev_total_pct = (att["powertrain"] == "bev").mean() * 100.0
print(f"attributed fleet: {n_fleet} cars, BEV total {bev_total_pct:.2f}%")

STATUS_ORDER = ["very_low", "low", "medium", "high", "very_high"]
STATUS_DE = {"very_low": "sehr niedrig", "low": "niedrig", "medium": "mittel",
             "high": "hoch", "very_high": "sehr hoch"}

# (raw column(s), German label, color) -- stack order = car size ladder, then
# SUV family, then the grouped remainder.
SEGMENTS = [
    (["minis"], "Minis", "#7dd3fc"),
    (["kleinwagen"], "Kleinwagen", "#38bdf8"),
    (["kompaktklasse"], "Kompaktklasse", "#6366f1"),
    (["mittelklasse"], "Mittelklasse", "#8b7cf6"),
    (["obere_mittelklasse"], "Obere Mittelklasse", "#d946ef"),
    (["oberklasse"], "Oberklasse", "#fb7185"),
    (["suv"], "SUV", "#fbbf24"),
    (["gelaendewagen"], "Geländewagen", "#f97316"),
    (["mini_vans", "grossraum_vans", "utilities", "wohnmobile", "sportwagen"],
     "Übrige (Vans, Utilities, Wohnmobile, Sportwagen)", "#55607a"),
]

ct = pd.crosstab(att["economic_status"], att["segment"], normalize="index") * 100.0
ct = ct.loc[STATUS_ORDER]
n_by_status = att["economic_status"].value_counts()

stack = pd.DataFrame(index=ct.index)
for cols, label, _ in SEGMENTS:
    stack[label] = ct[[c for c in cols if c in ct.columns]].sum(axis=1)
assert np.allclose(stack.sum(axis=1), 100.0, atol=1e-6), "segment shares do not sum to 100"

# ---------------------------------------------------------------- figure
fig = plt.figure(figsize=(18, 8.5), facecolor=BG)
gs = fig.add_gridspec(1, 2, left=0.035, right=0.965, top=0.775, bottom=0.175,
                      wspace=0.14, width_ratios=[0.84, 1.30])

# ================================================================ LEFT: map
ax_map = fig.add_subplot(gs[0, 0], facecolor=BG)
ax_map.set_axis_off()
ax_map.set_anchor("W")  # align map under the left-aligned panel title

gem.plot(column="bev_pct", cmap="viridis", vmin=0, vmax=vmax, ax=ax_map,
         edgecolor=BG, linewidth=0.35,
         missing_kwds={"color": NODATA, "edgecolor": BG, "linewidth": 0.35})
kreis.boundary.plot(ax=ax_map, color=KREIS_EDGE, linewidth=0.9)

for _, row in kreis.iterrows():
    pt = row.geometry.representative_point()
    ax_map.annotate(row["name"], (pt.x, pt.y), ha="center", va="center",
                    fontsize=7.5, color=SUB,
                    path_effects=[pe.withStroke(linewidth=2.2, foreground=BG)])

# Value callout for the standout city (Wolfsburg, Kreis 03103 == Gemeinde 03103000),
# stacked directly below the Kreis label on the polygon.
wob_val = float(gem.loc[gem["commune_id"] == "03103000", "bev_pct"].iloc[0])
wob_pt = kreis.loc[kreis["ars5"] == "03103", "geometry"].iloc[0].representative_point()
ax_map.annotate(f"{de(wob_val)} %", (wob_pt.x, wob_pt.y - 5200), ha="center", va="top",
                fontsize=8, color="#34d399", fontweight="bold",
                path_effects=[pe.withStroke(linewidth=2.4, foreground=BG)])

sm = plt.cm.ScalarMappable(cmap="viridis", norm=Normalize(0, vmax))
cb = fig.colorbar(sm, ax=ax_map, fraction=0.030, pad=0.012, shrink=0.70)
cb.set_label("BEV-Anteil [%]", color="#c7d0e0", fontsize=9)
cb.ax.tick_params(colors=SUB, labelsize=8, length=3)
cb.outline.set_edgecolor(KREIS_EDGE)
cb.outline.set_linewidth(0.8)

ax_map.set_title("E-Auto-Anteil je Gemeinde", loc="left", fontsize=12.5,
                 color=INK, fontweight="bold", pad=44)
ax_map.text(0.0, 1.03, "BEV-Anteil an der attributierten Pkw-Flotte [%]\n"
            "kalibriert gegen KBA-Zulassungen (FZ 27.15/27.17)",
            transform=ax_map.transAxes, fontsize=8.5, color=SUB, va="bottom")
ax_map.text(0.0, 0.035, f"Region gesamt: {de(bev_total_pct)} % BEV",
            transform=ax_map.transAxes, fontsize=9, color="#34d399", fontweight="bold")
ax_map.text(0.0, -0.005, f"grau: keine Daten bzw. < {MIN_VEHICLES_FOR_SHARE} Fahrzeuge "
            "(gemeindefreie Gebiete)",
            transform=ax_map.transAxes, fontsize=7, color=MUT)

# ================================================================ RIGHT: bars
ax_bar = fig.add_subplot(gs[0, 1], facecolor=BG)

for x in range(0, 101, 20):
    ax_bar.axvline(x, color=GRID, linewidth=0.8, zorder=0)

BARH = 0.60
ypos = {s: len(STATUS_ORDER) - 1 - i for i, s in enumerate(STATUS_ORDER)}

for status in STATUS_ORDER:
    y = ypos[status]
    left = 0.0
    for cols, label, color in SEGMENTS:
        v = float(stack.loc[status, label])
        # glow pass (wider, translucent) then sharp block with background gap edge
        ax_bar.barh(y, v, left=left, height=BARH * 1.24, color=color, alpha=0.17,
                    edgecolor="none", zorder=2)
        ax_bar.barh(y, v, left=left, height=BARH, color=color, alpha=1.0,
                    edgecolor=BG, linewidth=1.2, zorder=3)
        if v >= 8.0:
            r, g, b = to_rgb(color)
            lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
            txt = "#0a0e14" if lum >= 0.60 else "#eef3fb"
            ax_bar.text(left + v / 2.0, y, f"{v:.0f} %", ha="center", va="center",
                        fontsize=8, color=txt, fontweight="bold", zorder=4)
        left += v
    ax_bar.text(101.2, y, f"n={de(n_by_status[status], 0)}", ha="left", va="center",
                fontsize=7, color=MUT, clip_on=False)

ax_bar.set_xlim(0, 100)
ax_bar.set_ylim(-0.62, len(STATUS_ORDER) - 1 + 0.62)
ax_bar.set_yticks([ypos[s] for s in STATUS_ORDER])
ax_bar.set_yticklabels([STATUS_DE[s] for s in STATUS_ORDER], fontsize=9.5, color="#c7d0e0")
ax_bar.set_xticks(range(0, 101, 20))
ax_bar.set_xticklabels([str(v) for v in range(0, 101, 20)], fontsize=8, color=SUB)
ax_bar.set_xlabel("Anteil an der Pkw-Flotte der Statusgruppe [%]", fontsize=8.5, color=SUB)
ax_bar.tick_params(colors=SUB, length=0)
for spine in ax_bar.spines.values():
    spine.set_visible(False)

ax_bar.set_title("Segment-Mix nach ökonomischem Status", loc="left", fontsize=12.5,
                 color=INK, fontweight="bold", pad=44)
ax_bar.text(0.0, 1.03, "Anteile der Fahrzeugsegmente je Statusgruppe [%]\n"
            "Segmentwahl im Modell konditioniert auf Status × Raumtyp",
            transform=ax_bar.transAxes, fontsize=8.5, color=SUB, va="bottom")

handles = [Patch(facecolor=c, edgecolor="none", label=l) for _, l, c in SEGMENTS]
leg = ax_bar.legend(handles=handles, loc="upper left", bbox_to_anchor=(-0.02, -0.115),
                    ncol=3, frameon=False, fontsize=7.6, labelcolor="#c7d0e0",
                    handlelength=1.1, handleheight=1.1, columnspacing=1.4,
                    borderaxespad=0.0)

# ================================================================ titles + credit
fig.text(0.035, 0.945, "Die Flotte im Raum und im Sozialprofil", fontsize=16.5,
         color=INK, fontweight="bold", ha="left")
fig.text(0.035, 0.895, f"Synthetische Pkw-Flotte des 100-%-Modells · "
         f"{de(n_fleet, 0)} attributierte Pkw in {de(n_hh, 0)} Haushalten "
         f"(Region Braunschweig / ZGB)",
         fontsize=10, color=SUB, ha="left")
fig.text(0.035, 0.022, "Daten: population_explorer.gpkg (gemeinde_aggregat) · "
         "braunschweig_100pct_allfeat_popsim_vehicles.csv (mode=car, attributierte Flotte) · "
         "kreis_socio.geojson · KBA-Referenz: kba_gemeinde_private_bev.csv · "
         "100%-PopulationSim-Lauf, Export 2026-06-30",
         fontsize=7.5, color=MUT, ha="left")

fig.savefig(OUT_PNG, dpi=170, facecolor=BG, bbox_inches="tight", pad_inches=0.15)
print("wrote", OUT_PNG)
