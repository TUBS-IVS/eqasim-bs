"""MiD 2023 Tabelle 43 loader: mean school-trip length by RegioStaR-7 + age group.

Source: MiD 2023 Ergebnisbericht (Grossraum Braunschweig), Tabelle 43
"Kita- und Schulweglaengen nach Raumtyp und Altersgruppe" (page 178). Routed
mean trip lengths in km. The committed CSV is produced by
scripts/seed_mid_t43_school_distance.py.

Age-group -> level mapping:
  km_0_6   -> kindergarten  (ages 0-6, Kita)
  km_7_10  -> grundschule   (ages 6-10, primary school)
  km_11_13 -> sekundar_1    (ages 10-15, lower secondary)
  km_14_17 -> oberstufe     (ages 14-17, upper secondary academic track)

Note: the MiD 14-17 column covers all upper-secondary pupils. The BBS
(vocational) level is NOT mapped here; it uses the national Destatis MZ 2024
reference instead (see braunschweig.data.mikrozensus.school_distance). The
oberstufe/bbs split for ages 16-19 pupils is handled in
scripts/calibrate_education_slopes.py via an enrollment-share draw.
"""
from __future__ import annotations

import os

import pandas as pd

# MiD age-group columns -> our school levels. km_0_6 maps to kindergarten,
# which is now routed through the capacity-constrained gravity model on real
# Kita facilities (braunschweig.data.schools.kita_facilities).
AGEGROUP_TO_LEVEL = {
    "km_0_6": "kindergarten",
    "km_7_10": "grundschule",
    "km_11_13": "sekundar_1",
    "km_14_17": "oberstufe",
}


def routed_to_straight_line(routed_km, detour_factor):
    """Convert a routed length to its straight-line equivalent."""
    return float(routed_km) / float(detour_factor)


def build_target_table(raw, detour_factor):
    """Long table [regiostar7, level, routed_km, target_km] from the wide
    RS7 x age-group frame. target_km = routed_km / detour_factor."""
    rows = []
    for _, r in raw.iterrows():
        for col, level in AGEGROUP_TO_LEVEL.items():
            routed = float(r[col])
            rows.append({
                "regiostar7": int(r["regiostar7"]),
                "level": level,
                "routed_km": routed,
                "target_km": routed_to_straight_line(routed, detour_factor),
            })
    return pd.DataFrame(rows, columns=["regiostar7", "level",
                                       "routed_km", "target_km"])


def configure(context):
    context.config("data_path")
    context.config("mid_t43_path",
                   "braunschweig/mid/mid2023_T43_school_distance_by_rs7.csv")


def _resolve_path(context):
    return os.path.join(context.config("data_path"),
                        context.config("mid_t43_path"))


def execute(context):
    return pd.read_csv(_resolve_path(context))
