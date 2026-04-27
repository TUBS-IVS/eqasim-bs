"""
Braunschweig-specific wrapper around bavaria.synthesis.population.enriched.

Adds three refinements that the Bavaria delegate either hardcodes or
references against Munich:

1. number_of_cars per person is sampled from MiD 2023 Tabelle H7
   (Anzahl Autos im Haushalt) cross-tabulated by Kreis × household_size.
   Bavaria sets number_of_cars = 1 unconditionally.

2. number_of_bicycles per person is sampled from MiD 2023 Tabelle H12.3
   (Anzahl Elektrofahrräder, Pedelecs und Fahrräder im Haushalt) by
   Kreis × household_size. Bavaria sets number_of_bicycles = 1.

3. is_bs_resident replaces is_munich_resident (the delegate already
   falls back to False when inside_munich is absent).

Everything else — the IPF for car/bike/PT availability, household size
and income sampling — is delegated unchanged, so upstream logic remains
the single source of truth.
"""

import bavaria.synthesis.population.enriched as delegate
import numpy as np
import pandas as pd

from braunschweig.data.census.household_income import CLASS_MIDPOINT_EUR


# MiD 2023 Tabelle H7 'Anzahl Autos im Haushalt' — Zeilengruppe Teilgebiete.
# Shares (Zeilen%): kein Auto, 1 Auto, 2 Autos, 3+ Autos  (keine Angabe ~0)
CARS_BY_KREIS = {
    "03101": (0.25, 0.53, 0.20, 0.02),   # Braunschweig
    "03102": (0.10, 0.62, 0.22, 0.06),   # Salzgitter
    "03103": (0.17, 0.57, 0.22, 0.04),   # Wolfsburg
    "03151": (0.06, 0.50, 0.35, 0.08),   # Gifhorn
    "03153": (0.22, 0.53, 0.21, 0.04),   # Goslar
    "03154": (0.14, 0.52, 0.27, 0.07),   # Helmstedt
    "03157": (0.07, 0.48, 0.37, 0.08),   # Peine
    "03158": (0.13, 0.56, 0.22, 0.09),   # Wolfenbüttel
}
CARS_VALUES = np.array([0, 1, 2, 3])

# MiD 2023 Tabelle H12.3 'Anzahl Fahrräder/Pedelecs/E-Bikes im Haushalt'
# Shares (Zeilen%): keins, 1, 2, 3, 4+
BIKES_BY_KREIS = {
    "03101": (0.17, 0.25, 0.26, 0.12, 0.21),   # Braunschweig
    "03102": (0.23, 0.24, 0.25, 0.11, 0.17),   # Salzgitter
    "03103": (0.36, 0.22, 0.21, 0.08, 0.14),   # Wolfsburg
    "03151": (0.12, 0.22, 0.25, 0.14, 0.26),   # Gifhorn
    "03153": (0.36, 0.23, 0.17, 0.09, 0.15),   # Goslar
    "03154": (0.28, 0.16, 0.29, 0.13, 0.15),   # Helmstedt
    "03157": (0.18, 0.23, 0.22, 0.16, 0.22),   # Peine
    "03158": (0.23, 0.27, 0.17, 0.13, 0.20),   # Wolfenbüttel
}
BIKES_VALUES = np.array([0, 1, 2, 3, 4])

# Region-wide fallback for external residents (H7/H12.3 'Gesamt' row).
CARS_REGION   = (0.15, 0.53, 0.26, 0.06)
BIKES_REGION  = (0.23, 0.23, 0.23, 0.12, 0.20)


# Map inside_<kreis> boolean flags (set by the bavaria delegate via sjoin
# with the MiD zones) to the 5-digit Kreis ARS code (AGS-5).
INSIDE_FLAG_TO_ARS5 = {
    "inside_braunschweig":  "03101",
    "inside_salzgitter":    "03102",
    "inside_wolfsburg":     "03103",
    "inside_gifhorn":       "03151",
    "inside_goslar":        "03153",
    "inside_helmstedt":     "03154",
    "inside_peine":         "03157",
    "inside_wolfenbuettel": "03158",
}


def _derive_kreis_ars5(df_persons):
    """Return a per-person AGS-5 Series derived from inside_<kreis> flags.

    The Bavaria delegate attaches one boolean flag per MiD zone (plus
    inside_external). Each zone corresponds exactly to one ZGB Kreis, so the
    first matching flag uniquely identifies the home Kreis. Persons whose
    home is outside the 8 ZGB Kreise fall back to an empty string, which the
    samplers handle via the region-wide (Gesamt) shares.
    """
    ars5 = np.full(len(df_persons), "", dtype=object)
    for flag, code in INSIDE_FLAG_TO_ARS5.items():
        if flag not in df_persons.columns:
            continue
        flag_mask = df_persons[flag].fillna(False).astype(bool).values
        ars5 = np.where((ars5 == "") & flag_mask, code, ars5)
    return pd.Series(ars5, index=df_persons.index)


def _sample_counts(df_persons, column, values, region_shares, kreis_shares, random):
    """Sample an integer count per person given a Kreis-indexed share table."""
    kreis = _derive_kreis_ars5(df_persons)
    result = np.zeros(len(df_persons), dtype=int)

    for ars in set(kreis.unique()):
        shares = kreis_shares.get(ars, region_shares)
        shares = np.asarray(shares, dtype=float)
        shares /= shares.sum()
        mask = (kreis == ars).values
        n = int(mask.sum())
        if n == 0:
            continue
        result[mask] = random.choice(values, size=n, p=shares)

    df_persons[column] = result


def configure(context):
    delegate.configure(context)
    context.stage("braunschweig.data.inkar.household_income")
    context.config("random_seed")


def _apply_inkar_income_scale(df_persons, df_inkar):
    """Add ``household_income_eur`` = class_midpoint × INKAR-scale[home_kreis].

    The coarse MiD H4 quintile (already sampled by the Bavaria delegate as
    ``household_income``) is translated to a continuous € value using the
    class midpoint, then rescaled by the Kreis-specific INKAR factor so the
    Wolfsburg/Goslar spread propagates into the synthetic population. Persons
    whose ``commune_id`` falls outside the 8 ZGB Kreise (pendler/external
    attached by matching) keep scale = 1.0 = national mean.
    """
    midpoint = df_persons["household_income"].astype(str).map(CLASS_MIDPOINT_EUR)
    # Flag rows for which the mapping failed (unknown category); fall back
    # to the median midpoint to stay finite.
    if midpoint.isna().any():
        n_na = int(midpoint.isna().sum())
        print(
            f"[braunschweig.enriched] {n_na} persons with unknown income_class; "
            f"using median midpoint 2800 €."
        )
        midpoint = midpoint.fillna(2800.0)

    kreis = _derive_kreis_ars5(df_persons)
    scale_lookup = dict(zip(df_inkar["ars5"], df_inkar["scale"]))
    scale = kreis.map(scale_lookup).fillna(1.0).astype(float)

    df_persons["household_income_eur"] = (midpoint.astype(float) * scale).round(0)
    return df_persons


def execute(context):
    df_persons = delegate.execute(context)

    # Re-sample vehicle counts from MiD H7 / H12.3 instead of the hardcoded 1s.
    random = np.random.RandomState(context.config("random_seed") + 91731)
    _sample_counts(df_persons, "number_of_cars", CARS_VALUES,
                   CARS_REGION, CARS_BY_KREIS, random)
    _sample_counts(df_persons, "number_of_bicycles", BIKES_VALUES,
                   BIKES_REGION, BIKES_BY_KREIS, random)

    # INKAR-based €-income (Kreis-specific shift on top of the MiD H4
    # regionless quintile distribution).
    df_inkar = context.stage("braunschweig.data.inkar.household_income")
    df_persons = _apply_inkar_income_scale(df_persons, df_inkar)

    # BS-specific residency flag (aligns with is_munich_resident semantics).
    if "inside_braunschweig" in df_persons.columns:
        df_persons["is_bs_resident"] = df_persons["inside_braunschweig"]

    return df_persons
