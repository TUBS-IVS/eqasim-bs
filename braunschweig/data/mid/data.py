"""
Targets for car / bicycle / PT-subscription availability derived from the
MiD 2023 regional report 'Großraum Braunschweig' (Tabellen-Version 2, sample
7555, infas September 2025).

Values are taken from the 'Zeilen%' rows of Tables A P19, A P22 and A P24.1
at Gesamtregion and Teilgebiete level, and extended with the sex / age
breakdowns reported on the same tables. They replace the Munich-based
defaults from bavaria/data/mid/data.py.

Zones correspond to the eight ZGB Kreise (Landkreise/Kreisfreie Städte) and
match one-to-one the 'Teilgebiete' row group in the MiD tables. 'external'
catches residents outside the ZGB-8 scope; we anchor it on the Gesamtregion
value since the MiD report does not cover the rest of Germany.

Constraint semantics (see bavaria/synthesis/population/enriched.py):
  - 'car_availability_constraints': share of persons (>= minimum_age) with
    'jederzeit' access to a car (P19 column 'jederzeit').
  - 'bicycle_availability_constraints': share of persons owning a non-electric
    bicycle (P22 'ja').
  - 'pt_subscription_constraints': share of persons whose usual ÖPNV ticket
    is a subscription-type (Deutschlandticket + Wochen-/Monatskarte +
    Jobticket/Semesterticket). P24.1 columns 3+4+5+6 summed per row.

Age bins follow the aggregation used in the Bavaria reference file, computed
as person-count-weighted averages of the finer-grained MiD age rows.
"""

import numpy as np


def configure(context):
    pass


def execute(context):
    data = {}

    # ------------------------------------------------------------------
    # CAR AVAILABILITY  (MiD 2023 GroßraumBS, Tabelle A P19, 'jederzeit')
    # ------------------------------------------------------------------
    data["car_availability_constraints"] = [
        # Teilgebiete (ZGB Kreise)
        {"zone": "braunschweig",  "target": 0.67},
        {"zone": "salzgitter",    "target": 0.80},
        {"zone": "wolfsburg",     "target": 0.68},
        {"zone": "gifhorn",       "target": 0.89},
        {"zone": "goslar",        "target": 0.71},
        {"zone": "helmstedt",     "target": 0.77},
        {"zone": "peine",         "target": 0.80},
        {"zone": "wolfenbuettel", "target": 0.76},
        # Rest-of-Germany fallback (Gesamtregion value as proxy).
        {"zone": "external",      "target": 0.76},

        # Sex (region-wide; MiD reports only Gesamt-level crosstab)
        {"sex": "male",   "target": 0.80},
        {"sex": "female", "target": 0.72},

        # Age bins (region-wide)
        {"age": (14, 17),        "target": 0.41},
        {"age": (18, 29),        "target": 0.61},
        {"age": (30, 39),        "target": 0.77},
        {"age": (40, 49),        "target": 0.84},
        {"age": (50, 59),        "target": 0.86},
        {"age": (60, 64),        "target": 0.82},
        {"age": (65, 74),        "target": 0.84},
        {"age": (75, 79),        "target": 0.79},
        {"age": (80, np.inf),    "target": 0.60},
    ]

    # ------------------------------------------------------------------
    # BICYCLE AVAILABILITY  (MiD 2023 GroßraumBS, Tabelle A P22, 'ja')
    # ------------------------------------------------------------------
    data["bicycle_availability_constraints"] = [
        # Teilgebiete
        {"zone": "braunschweig",  "target": 0.73},
        {"zone": "salzgitter",    "target": 0.65},
        {"zone": "wolfsburg",     "target": 0.57},
        {"zone": "gifhorn",       "target": 0.78},
        {"zone": "goslar",        "target": 0.54},
        {"zone": "helmstedt",     "target": 0.64},
        {"zone": "peine",         "target": 0.76},
        {"zone": "wolfenbuettel", "target": 0.63},
        {"zone": "external",      "target": 0.67},

        # Sex
        {"sex": "male",   "target": 0.72},
        {"sex": "female", "target": 0.63},

        # Age bins — native MiD 2023 bins (Tabelle A P22, 'ja')
        #  0- 6: 0.52 | 7-10: 0.95 | 11-13: 0.86 | 14-17: 0.85
        # 18-29: 0.74 | 30-39: 0.74 | 40-49: 0.78 | 50-59: 0.73
        # 60-64: 0.64 | 65-74: 0.55 | 75-79: 0.48 | 80+ : 0.36
        {"age": (-np.inf, 6),    "target": 0.52},
        {"age": (7, 10),         "target": 0.95},
        {"age": (11, 13),        "target": 0.86},
        {"age": (14, 17),        "target": 0.85},
        {"age": (18, 29),        "target": 0.74},
        {"age": (30, 39),        "target": 0.74},
        {"age": (40, 49),        "target": 0.78},
        {"age": (50, 59),        "target": 0.73},
        {"age": (60, 64),        "target": 0.64},
        {"age": (65, 74),        "target": 0.55},
        {"age": (75, 79),        "target": 0.48},
        {"age": (80, np.inf),    "target": 0.36},
    ]

    # ------------------------------------------------------------------
    # PT SUBSCRIPTION  (MiD 2023 GroßraumBS, Tabelle A P24.1)
    # Sum of columns: Deutschlandticket + Wochenkarte/Monatskarte im Abo
    #                 + Jobticket/Semesterticket
    # ------------------------------------------------------------------
    data["pt_subscription_constraints"] = [
        # Teilgebiete
        {"zone": "braunschweig",  "target": 0.26},
        {"zone": "salzgitter",    "target": 0.20},
        {"zone": "wolfsburg",     "target": 0.18},
        {"zone": "gifhorn",       "target": 0.16},
        {"zone": "goslar",        "target": 0.19},
        {"zone": "helmstedt",     "target": 0.19},
        {"zone": "peine",         "target": 0.14},
        {"zone": "wolfenbuettel", "target": 0.14},
        {"zone": "external",      "target": 0.19},

        # Sex
        {"sex": "male",   "target": 0.20},
        {"sex": "female", "target": 0.18},

        # Age bins — native MiD 2023 bins (Tabelle A P24.1, sum of subscription cols)
        # 14-17: 0.65 | 18-29: 0.43 | 30-39: 0.20 | 40-49: 0.21
        # 50-59: 0.12 | 60-64: 0.09 | 65-74: 0.06 | 75-79: 0.07 | 80+ : 0.06
        {"age": (14, 17),        "target": 0.65},
        {"age": (18, 29),        "target": 0.43},
        {"age": (30, 39),        "target": 0.20},
        {"age": (40, 49),        "target": 0.21},
        {"age": (50, 59),        "target": 0.12},
        {"age": (60, 64),        "target": 0.09},
        {"age": (65, 74),        "target": 0.06},
        {"age": (75, 79),        "target": 0.07},
        {"age": (80, np.inf),    "target": 0.06},
    ]

    return data
