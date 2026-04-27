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
}
