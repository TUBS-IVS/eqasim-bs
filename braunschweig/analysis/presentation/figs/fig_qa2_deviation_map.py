# Figure: "Wo weicht die Synthese ab?" -- spatial control-fit deviation maps.
#
# SCOPE (real-data constraint): the requested Gemeinde-level composites are NOT
# computable from real references. In this run's validation output the demographic
# controls (age_group, sex) and the mobility controls (driving_license_type,
# pt_ticket_type, cars_per_hh) are defined and validated at KREIS level only
# (controls_long.csv; see braunschweig/analysis/population_validation/controls.py).
# The only Gemeinde-level control is household_size (Zensus); it is excluded here
# because these maps are Kreis-level (household_size binds at Gemeinde level), as
# is employment (P9 noise). The maps
# therefore show Kreis-level composites -- the level at which the targets bind.
# No Gemeinde-level reference values are invented.
#
# delta_pp = synthetic_pct - target_pct (control_validation.py).
# Composite = mean of |delta_pp| over all category cells of the panel's controls.

import numpy as np
import pandas as pd
import geopandas as gpd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
from matplotlib.colors import Normalize
from matplotlib.cm import ScalarMappable
import matplotlib.patheffects as pe

# ---------------------------------------------------------------- fonts
FONT_DIR = "c:/Users/bienzeisler/Documents/GitHub/eqasim-bs/braunschweig/analysis/poster/fonts/"
for f in ["SpaceMono-Regular.ttf", "SpaceMono-Bold.ttf"]:
    try:
        fm.fontManager.addfont(FONT_DIR + f)
    except Exception:
        pass
if any("Space Mono" in f.name for f in fm.fontManager.ttflist):
    plt.rcParams["font.family"] = "Space Mono"
else:
    plt.rcParams["font.family"] = "DejaVu Sans Mono"

BG = "#0a0e14"
KREIS_EDGE = "#33465f"
TXT_MAIN = "#eef3fb"
TXT_SUB = "#8b95a7"
TXT_CREDIT = "#5a6577"

BASE = "C:/Users/bienzeisler/Downloads/popsim_100pct_results/output_bs_100pct_allfeat_popsim/"
VAL = BASE + "analysis/population_validation/"
KBA = "c:/Users/bienzeisler/Documents/GitHub/eqasim-bs/eqasim-data/data/braunschweig/kba/derived/kba_kreis_powertrain.csv"
OUT = ("C:/Users/BIENZE~1/AppData/Local/Temp/claude/"
       "c--Users-bienzeisler-Documents-GitHub-eqasim-bs/"
       "b066c272-e6db-4fdb-ad5f-98cd5497db59/scratchpad/figs/fig_qa2_deviation_map.png")

# ---------------------------------------------------------------- data
cl = pd.read_csv(VAL + "controls_long.csv", dtype={"geo_id": str})
cl = cl[cl["geography"] == "kreis"].copy()

DEMO_CONTROLS = ["age_group", "sex"]
MOB_CONTROLS = ["driving_license_type", "pt_ticket_type", "cars_per_hh"]

def composite(controls):
    sub = cl[cl["control"].isin(controls)]
    return sub.assign(a=sub["delta_pp"].abs()).groupby("geo_id")["a"].mean()

demo = composite(DEMO_CONTROLS).rename("dev_demo")
mob = composite(MOB_CONTROLS).rename("dev_mob")
n_demo_cells = int(cl[cl["control"].isin(DEMO_CONTROLS)].groupby("geo_id").size().iloc[0])
n_mob_cells = int(cl[cl["control"].isin(MOB_CONTROLS)].groupby("geo_id").size().iloc[0])

kreis = gpd.read_file(BASE + "simwrapper/kreis_socio.geojson").to_crs("EPSG:25832")
kreis = kreis.merge(demo, left_on="ars5", right_index=True, how="left")
kreis = kreis.merge(mob, left_on="ars5", right_index=True, how="left")

# Kreis names from the committed KBA reference table (real names, not invented).
names = pd.read_csv(KBA)[["kreis_ags5", "kreis_name"]]
names["ars5"] = names["kreis_ags5"].astype(str).str.zfill(5)
names["kreis_name"] = (names["kreis_name"]
                       .str.replace(", Stadt", "", regex=False)
                       .str.replace("Wolfenbuettel", "Wolfenbüttel"))
kreis = kreis.merge(names[["ars5", "kreis_name"]], on="ars5", how="left")
assert kreis["kreis_name"].notna().all(), "missing Kreis name"
assert kreis["dev_demo"].notna().all() and kreis["dev_mob"].notna().all()

# ---------------------------------------------------------------- worst cells
CONTROL_DE = {
    "driving_license_type": "Führerschein",
    "pt_ticket_type": "ÖV-Ticket",
    "cars_per_hh": "Pkw je HH",
    "age_group": "Alter",
    "sex": "Geschlecht",
}
# PT ticket category display labels (English category values, issue #329 --
# only the PT-ticket taxonomy is anglicized; the surrounding *_DE maps below
# stay German, out of scope for this rename).
PT_CAT_DE = {
    "never_pt": "never uses PT", "deutschlandticket": "Deutschlandticket",
    "single_ticket": "single ticket", "multi_ride_ticket": "multi-ride ticket",
    "monthly_or_annual_subscription": "monthly/annual subscription",
    "weekly_monthly_no_subscription": "weekly/monthly (no subscription)",
    "job_or_semester_ticket": "job/semester ticket", "other_ticket": "other",
}
CARS_CAT_DE = {"0": "HH ohne Pkw", "1": "HH mit 1 Pkw",
               "2": "HH mit 2 Pkw", "3": "HH mit 3+ Pkw"}  # top bucket = ">= 3"
SEX_DE = {"female": "weiblich", "male": "männlich"}

def de(x, nd=1):
    return f"{x:.{nd}f}".replace(".", ",")

def de_signed(x, nd=1):
    return f"{x:+.{nd}f}".replace(".", ",").replace("-", "−")

def worst_cell_label(controls, geo_id):
    sub = cl[(cl["control"].isin(controls)) & (cl["geo_id"] == geo_id)]
    row = sub.loc[sub["delta_pp"].abs().idxmax()]
    ctrl, cat = row["control"], str(row["category"])
    if ctrl == "cars_per_hh":
        label = CARS_CAT_DE.get(cat, cat)
    elif ctrl == "pt_ticket_type":
        label = f"ÖV '{PT_CAT_DE.get(cat, cat)}'"
    elif ctrl == "driving_license_type":
        label = f"Führerschein '{cat.replace('keine_angabe', 'k. A.')}'"
    elif ctrl == "age_group":
        label = f"Alter {cat}"
    elif ctrl == "sex":
        label = SEX_DE.get(cat, cat)
    else:
        label = f"{ctrl} {cat}"
    return f"{label}: {de_signed(row['delta_pp'])} pp", row["delta_pp"]

# ---------------------------------------------------------------- shared scale
vals = np.concatenate([kreis["dev_demo"].to_numpy(), kreis["dev_mob"].to_numpy()])
vmax = float(np.percentile(vals, 95))
norm = Normalize(vmin=0.0, vmax=vmax)
CMAP = plt.get_cmap("magma")
print(f"shared scale: 0 .. {vmax:.2f} pp (95th pct of {len(vals)} values)")

# ---------------------------------------------------------------- figure
fig = plt.figure(figsize=(16, 8.5), dpi=170, facecolor=BG)
gs = fig.add_gridspec(1, 2, left=0.02, right=0.895, top=0.77, bottom=0.175, wspace=0.05)
ax_l = fig.add_subplot(gs[0, 0])
ax_r = fig.add_subplot(gs[0, 1])
for ax in (ax_l, ax_r):
    ax.set_facecolor(BG)
    ax.set_axis_off()
    ax.set_aspect("equal")

panels = [
    (ax_l, "dev_demo", "Demografie (Alter + Geschlecht)",
     f"Referenz: Zensus · {n_demo_cells} Kontrollzellen je Kreis", DEMO_CONTROLS),
    (ax_r, "dev_mob", "Mobilität (Führerschein, ÖV-Ticket, Pkw)",
     f"Referenz: MiD 2023 · {n_mob_cells} Kontrollzellen je Kreis", MOB_CONTROLS),
]

for ax, col, t1, t2, controls in panels:
    kreis.plot(ax=ax, column=col, cmap=CMAP, norm=norm,
               edgecolor="#1a2230", linewidth=0.4)
    kreis.plot(ax=ax, facecolor="none", edgecolor=KREIS_EDGE, linewidth=0.9)

    worst_ids = kreis.nlargest(3, col)["ars5"].tolist()
    # small label shifts in meters so the enlarged labels of small/adjacent Kreise do not collide
    LABEL_SHIFT = {
        "03103": (-2200, 0),      # Wolfsburg: keep label on the bright fill
        "03157": (-6000, -1800),  # Peine: away from the Braunschweig label
        "03101": (1200, 4200),    # Braunschweig: up into the city polygon
        "03102": (-1000, 3000),   # Salzgitter: up, clear of Wolfenbuettel
        "03158": (7000, -5500),   # Wolfenbuettel: down-right, clear of Salzgitter
        "03154": (2500, -1500),   # Helmstedt: away from the Braunschweig label
    }
    for _, row in kreis.iterrows():
        p = row.geometry.representative_point()
        dx, dy = LABEL_SHIFT.get(row["ars5"], (0, 0))
        val = row[col]
        lab = f"{row['kreis_name']}\n{de(val)} pp"
        # Light bold text with a subtle dark halo reads on bright and dark fills alike.
        ax.annotate(lab, (p.x + dx, p.y + dy), ha="center", va="center",
                    fontsize=11, color=TXT_MAIN, fontweight="bold",
                    path_effects=[pe.withStroke(linewidth=2.8, foreground=BG)])

    pos = ax.get_position()
    fig.text(pos.x0, pos.y1 + 0.045, t1, fontsize=14, color=TXT_MAIN, va="bottom")
    fig.text(pos.x0, pos.y1 + 0.013, t2, fontsize=11.5, color=TXT_SUB, va="bottom")

    entries = []
    for gid in worst_ids:
        name = kreis.loc[kreis["ars5"] == gid, "kreis_name"].iloc[0]
        lab, _ = worst_cell_label(controls, gid)
        entries.append(f"  {name} · {lab}")
    block = ("Größte Einzelabweichungen (synthetisch − Referenz):\n"
             + "\n".join(entries))
    fig.text(pos.x0, 0.148, block, fontsize=10.5, color=TXT_SUB,
             va="top", linespacing=1.7)

# ---------------------------------------------------------------- colorbar
cax = fig.add_axes([0.915, 0.13, 0.011, 0.58])
sm = ScalarMappable(norm=norm, cmap=CMAP)
cb = fig.colorbar(sm, cax=cax)
cb.outline.set_edgecolor("#1d2633")
cb.ax.tick_params(colors=TXT_SUB, labelsize=10.5, length=2.5)
cb.set_label("mittlere absolute Abweichung [Prozentpunkte]", color=TXT_SUB, fontsize=10.5)
ticks = list(np.arange(0, np.floor(vmax) + 0.1, 1.0))
cb.set_ticks(ticks)
cb.set_ticklabels([de(t, 0) for t in ticks])
cb.ax.text(0.5, 1.03, f"≥ {de(vmax)}", transform=cb.ax.transAxes,
           ha="center", va="bottom", fontsize=10, color=TXT_SUB)

# ---------------------------------------------------------------- headline
fig.text(0.02, 0.982, "Wo weicht die Synthese ab?", fontsize=19,
         color=TXT_MAIN, fontweight="bold", va="top")
sub = ("Mittlere absolute Abweichung der synthetischen Anteile von den Kontrollwerten je Kreis\n"
       "100-%-Lauf, 1.100.608 Personen · geteilte Farbskala, Kappung am 95. Perzentil\n"
       "Referenzanteile liegen nur je Kreis vor (Zensus bzw. MiD 2023) · gemeindescharf nur Haushaltsgröße (hier ausgeschlossen)")
fig.text(0.02, 0.938, sub, fontsize=11.5, color=TXT_SUB, va="top", linespacing=1.45)
fig.text(0.02, 0.008,
         "Daten: controls_long.csv (population_validation) · kreis_socio.geojson · Kreisnamen: kba_kreis_powertrain.csv — "
         "Lauf output_bs_100pct_allfeat_popsim (100 %, PopulationSim, Export 2026-06-30)",
         fontsize=8, color=TXT_CREDIT, va="bottom")

fig.savefig(OUT, facecolor=BG, bbox_inches="tight", pad_inches=0.15)
print("saved:", OUT)
print(kreis[["ars5", "kreis_name", "dev_demo", "dev_mob"]].round(2).to_string())
