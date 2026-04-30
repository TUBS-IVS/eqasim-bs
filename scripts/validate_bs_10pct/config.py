"""Static configuration for the Braunschweig 10 % validation."""
from __future__ import annotations

from pathlib import Path

# --- Paths -----------------------------------------------------------------
REPO = Path(__file__).resolve().parents[2]
OUTPUT_DIR = REPO / "eqasim-data" / "output_bs_10pct"
DATA_DIR = REPO / "eqasim-data" / "data" / "braunschweig"
SPATIAL_DIR = REPO / "eqasim-data" / "data" / "spatial"
CACHE_DIR = REPO / "eqasim-data" / "cache_bs_10pct"

PREFIX = "braunschweig_10pct_"
SAMPLING_RATE = 0.1
EPSG = 25832

# --- Region scope ----------------------------------------------------------
# ZGB-8 Kreise (AGS-5).
ZGB8: dict[str, str] = {
    "03101": "SK Braunschweig",
    "03102": "SK Salzgitter",
    "03103": "SK Wolfsburg",
    "03151": "LK Gifhorn",
    "03153": "LK Goslar",
    "03154": "LK Helmstedt",
    "03157": "LK Peine",
    "03158": "LK Wolfenbüttel",
}

# --- MiD 2023 Großraum Braunschweig — region baseline ---------------------
# Region totals across all purposes, working day. Source: infas
# Ergebnistabellen Großraum Braunschweig 2023, sample 7555.
MID_BASELINE = {
    "trips_per_person": 3.1,
    "mean_trip_distance_km": 12.6,
    "mean_trip_duration_min": 22.0,
    "daily_distance_km": 39.0,
    "mode_share": {
        "miv": 0.59,
        "oev": 0.10,
        "rad": 0.13,
        "fuss": 0.18,
    },
    "purpose_mix": {
        "work": 0.16,
        "education": 0.07,
        "shop": 0.18,
        "other": 0.12,
        "leisure": 0.27,
        "escort": 0.05,
        "home": 0.15,
    },
    # MiD 2023 W1 (Großraum BS, p231): Hauptwegezweck "analog MiD 2008".
    # Heimwege werden auf den Zweck des Hinwegs zurück-gemapped, daher
    # gibt es keine eigene "home"-Kategorie. Quelle:
    # eqasim-data/data/braunschweig/mid/mid2023_W1.csv (Gesamt = 03ZGB).
    # Das ist die in der Literatur übliche Darstellung; wir vergleichen
    # synthetische Wege ohne "home" gegen diese Verteilung in
    # purpose_mix_no_home(). Mapping eqasim → W1:
    #   work     ← Arbeit + Dienst   (13 + 16 = 29 %)
    #   education← Ausbildung        ( 6 %)
    #   shop     ← Einkauf           (16 %)
    #   other    ← Erledigung        (11 %)
    #   leisure  ← Freizeit          (29 %)
    #   escort   ← Begleitung        ( 8 %)
    # Summe ohne "keine Angabe" (1 %), normiert auf 100 %.
    # Hinweis: Der eqasim-Pipeline-Output kennt keine eigene "escort"-
    # Kategorie (Begleitwege werden auf "other" abgebildet). Damit der
    # Vergleich apples-to-apples bleibt, fassen wir Erledigung +
    # Begleitung MiD-seitig ebenfalls zu "other" zusammen.
    "purpose_mix_w1": {
        "work":      (13 + 16) / 99,        # 0.293  (Arbeit + Dienst)
        "education":  6 / 99,               # 0.061  (Ausbildung)
        "shop":      16 / 99,               # 0.162  (Einkauf)
        "other":     (11 + 8) / 99,         # 0.192  (Erledigung + Begleitung)
        "leisure":   29 / 99,               # 0.293  (Freizeit)
    },
    # MiD 2023 P36.1 (p195): Mobilität am Stichtag (Mobilitätsquote).
    # Anteil Personen mit mindestens einem Weg am Stichtag, Basis = alle
    # Personen (inkl. Kinder). Quelle: mid2023_P36_1.csv (Gesamt 03ZGB).
    # Die "unbekannt"-Spalte (1 %) wird ignoriert.
    "mobility_quote": 0.80,
    "mobility_quote_per_kreis": {
        "03101": 0.81,   # Braunschweig
        "03102": 0.80,   # Salzgitter
        "03103": 0.76,   # Wolfsburg
        "03151": 0.83,   # Gifhorn
        "03153": 0.75,   # Goslar
        "03154": 0.83,   # Helmstedt
        "03157": 0.79,   # Peine
        "03158": 0.84,   # Wolfenbüttel
    },
    "car_occupancy": 1.42,
}

# Distance bins matching MiD P13 (km).
DISTANCE_BINS = [0, 1, 2, 5, 10, 20, 50, 100, float("inf")]
DISTANCE_LABELS = ["<1", "1-2", "2-5", "5-10", "10-20", "20-50", "50-100", ">=100"]

# Duration bins (minutes).
DURATION_BINS = [0, 10, 20, 30, 60, 90, float("inf")]
DURATION_LABELS = ["<10", "10-20", "20-30", "30-60", "60-90", ">=90"]

# --- KPI thresholds (deviation that flips the traffic light) --------------
THRESHOLDS = {
    "population_ratio_pct": (2.0, 5.0),  # green if |dev| <= 2 %, amber <= 5 %
    "mode_share_pp": (3.0, 8.0),
    "license_rate_pp": (2.0, 5.0),
    "commute_distance_km": (2.0, 5.0),
    "trips_per_person_pct": (10.0, 20.0),
    "trip_distance_km": (2.0, 5.0),
    "trip_duration_min": (3.0, 8.0),
    "purpose_mix_l1": (0.10, 0.20),
    "mobility_quote_pp": (3.0, 8.0),
}
