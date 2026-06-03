"""
Targets for car / bicycle / PT-subscription availability derived from the
MiD 2023 regional sample (Tabellen-Version 2).

Values are taken from the 'Zeilen%' rows of Tables A P19, A P22 and A P24.1
at Gesamtregion and Teilgebiete level, and extended with the sex / age
breakdowns reported on the same tables.

Storage
-------
The reference percentages live in three CSV files produced by
``scripts/seed_mid_constraint_tables.py`` and read by
``braunschweig.data.mid.reference_tables``:

  * ``mid2023_P19_car_constraints.csv``           — car availability
  * ``mid2023_P22_bicycle_constraints.csv``       — bicycle ownership
  * ``mid2023_P24_1_pt_subscription_constraints.csv`` — PT subscriptions

This stage glues the three CSVs together into the structure expected by
``bavaria/synthesis/population/enriched.py``.

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

from braunschweig.data.mid.reference_tables import load_constraint_table


CSV_FILES = {
    "car_availability_constraints":   "mid2023_P19_car_constraints.csv",
    "bicycle_availability_constraints": "mid2023_P22_bicycle_constraints.csv",
    "pt_subscription_constraints":    "mid2023_P24_1_pt_subscription_constraints.csv",
}


def configure(context):
    context.config("data_path")


def execute(context):
    data_path = context.config("data_path")
    data = {key: load_constraint_table(data_path, fname)
            for key, fname in CSV_FILES.items()}
    return data


def validate(context):
    import os
    data_path = context.config("data_path")
    total = 0
    for fname in CSV_FILES.values():
        path = os.path.join(data_path, "braunschweig", "mid", fname)
        if not os.path.exists(path):
            raise RuntimeError(
                f"MiD constraint CSV missing at {path}. Re-run "
                "scripts/seed_mid_constraint_tables.py."
            )
        total += os.path.getsize(path)
    return total


# Legacy module-level globals removed in 2026-04 refactor; values now live
# in eqasim-data/data/braunschweig/mid/mid2023_P{19,22,24_1}_*.csv.

