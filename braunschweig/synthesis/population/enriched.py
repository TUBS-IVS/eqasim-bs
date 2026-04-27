"""Enriched population stage - Braunschweig overrides + Bavaria base.

This module is the merged successor of:
- ``bavaria/synthesis/population/enriched.py`` (Origin: eqasim-bavaria @ b20fbe6).
- ``braunschweig/synthesis/population/enriched.py`` (BS-specific MiD-2023
  vehicle counts, INKAR income scaling, BS-resident flag).

Phase 2.8 of the eqasim-bs refactor merged both into a single module so the
BS pipeline no longer delegates through ``bavaria.synthesis.population.enriched``.

Per Decision D-5 the merge is behaviour-preserving: the wrapper-around-wrapper
structure is intentionally preserved (BUG-001 stays untouched; cleanup is
deferred to a separate post-refactor pass).

Pipeline order: ``synthesis.population.enriched`` (eqasim core) -> base
augmentation (car/bike/PT IPF, household size + income sampling) -> BS overlay
(MiD-2023 vehicle counts, INKAR income, BS resident flag).
"""

import synthesis.population.enriched as delegate

import pandas as pd
import geopandas as gpd
import numpy as np

from braunschweig.data.census.household_income import CLASS_MIDPOINT_EUR


# --- Inherited from eqasim-bavaria -----------------------------------------
# Helper: map IPF hh_size values onto the bins present in df_income.

def _build_income_size_map(income_bins):
    """Map IPF hh_size values onto the bins present in df_income.

    Returns (mapping_dict, scheme_name). ``scheme_name`` is "6-bin" if the
    reference table separates 5 from 6+, else "5-bin" (collapses 5/6/5+/6+
    onto "5+"). Raises ValueError if neither scheme is recognised.
    """
    bins = set(income_bins)
    if {"1", "2", "3", "4", "5", "6+"}.issubset(bins):
        return (
            {"1": "1", "2": "2", "3": "3", "4": "4",
             "5": "5", "6": "6+", "5+": "5", "6+": "6+"},
            "6-bin",
        )
    if {"1", "2", "3", "4", "5+"}.issubset(bins):
        return (
            {"1": "1", "2": "2", "3": "3", "4": "4",
             "5": "5+", "6": "5+", "5+": "5+", "6+": "5+"},
            "5-bin",
        )
    raise ValueError(
        f"household_income reference has unrecognised hh_size bins: {sorted(bins)}"
    )


def _configure_base(context):
    """Inherited configure() from bavaria.synthesis.population.enriched."""
    delegate.configure(context)

    context.stage("synthesis.population.spatial.home.locations")

    context.stage("braunschweig.data.mid.data")
    context.stage("braunschweig.data.mid.zones")

    context.stage("braunschweig.data.census.household_size")
    context.stage("braunschweig.data.census.household_income")

    context.config("random_seed")

    context.config("braunschweig.minimum_age.car_availability", 0)
    context.config("braunschweig.minimum_age.bicycle_availability", 0)
    context.config("braunschweig.minimum_age.pt_subscription", 0)

    context.config("braunschweig.minimum_age.one_person_household", 16)
    context.config("braunschweig.ipf.use_household_size_margin", False)


def _execute_base(context):
    """Inherited execute() from bavaria.synthesis.population.enriched.

    Overrides car availability, bike availability and transit subscription
    based on MiD data, then samples household_size and household_income from
    the German census/MiD reference tables.
    """
    df_persons = delegate.execute(context)

    df_homes = context.stage("synthesis.population.spatial.home.locations")[["household_id", "geometry"]].copy()

    df_zones = context.stage("braunschweig.data.mid.zones")
    mid = context.stage("braunschweig.data.mid.data")

    f_covered = np.zeros(len(df_homes), dtype=bool)
    for zone in df_zones["name"].unique():
        df_query = gpd.sjoin(df_homes, df_zones[df_zones["name"] == zone], predicate="within")
        df_homes["inside_{}".format(zone)] = df_homes["household_id"].isin(df_query["household_id"])
        f_covered |= df_homes["inside_{}".format(zone)]

    df_homes["inside_external"] = ~f_covered

    df_persons = gpd.GeoDataFrame(
        pd.merge(df_persons, df_homes, on="household_id"),
        crs=df_homes.crs,
    )

    iterations = 1000

    # CAR AVAILABILITY
    df_persons["car_availability"] = 1.0
    constraints = mid["car_availability_constraints"]
    constraints.append({
        "age": (-np.inf, context.config("braunschweig.minimum_age.car_availability") - 1),
        "target": 0.0,
    })
    filters = []
    targets = []
    for constraint in constraints:
        f = np.ones((len(df_persons),), dtype=bool)
        if "zone" in constraint:
            f &= df_persons["inside_{}".format(constraint["zone"])]
        if "sex" in constraint:
            f &= df_persons["sex"] == constraint["sex"]
        if "age" in constraint:
            f &= df_persons["age"].between(*constraint["age"])
        targets.append(constraint["target"] * np.count_nonzero(f))
        filters.append(f)
    for iteration in context.progress(range(iterations), label="imputing car availability"):
        factors = []
        for f, target in zip(filters, targets):
            current = df_persons.loc[f, "car_availability"].sum()
            factor = target / current if current > 0 else 1.0
            df_persons.loc[f, "car_availability"] *= factor
            factors.append(factor)
    print("Factors", "min:", min(factors), "max:", max(factors), "mean:", np.mean(factors))
    print(df_persons["car_availability"].min(), df_persons["car_availability"].max())

    # BIKE AVAILABILITY
    df_persons["bicycle_availability"] = 1.0
    constraints = mid["bicycle_availability_constraints"]
    constraints.append({
        "age": (-np.inf, context.config("braunschweig.minimum_age.bicycle_availability") - 1),
        "target": 0.0,
    })
    filters = []
    targets = []
    for constraint in constraints:
        f = np.ones((len(df_persons),), dtype=bool)
        if "zone" in constraint:
            if constraint["zone"].startswith("!"):
                f &= ~df_persons["inside_{}".format(constraint["zone"][1:])]
            else:
                f &= df_persons["inside_{}".format(constraint["zone"])]
        if "sex" in constraint:
            f &= df_persons["sex"] == constraint["sex"]
        if "age" in constraint:
            f &= df_persons["age"].between(*constraint["age"])
        targets.append(constraint["target"] * np.count_nonzero(f))
        filters.append(f)
    for iteration in context.progress(range(iterations), label="imputing bike availability"):
        factors = []
        for f, target in zip(filters, targets):
            current = df_persons.loc[f, "bicycle_availability"].sum()
            factor = target / current if current > 0 else 1.0
            df_persons.loc[f, "bicycle_availability"] *= factor
            factors.append(factor)
    print("Factors", "min:", min(factors), "max:", max(factors), "mean:", np.mean(factors))

    # PT SUBSCRIPTION
    df_persons["has_pt_subscription"] = 1.0
    constraints = mid["pt_subscription_constraints"]
    constraints.append({
        "age": (-np.inf, context.config("braunschweig.minimum_age.pt_subscription") - 1),
        "target": 0.0,
    })
    filters = []
    targets = []
    for constraint in constraints:
        f = np.ones((len(df_persons),), dtype=bool)
        if "zone" in constraint:
            if constraint["zone"].startswith("!"):
                f &= ~df_persons["inside_{}".format(constraint["zone"][1:])]
            else:
                f &= df_persons["inside_{}".format(constraint["zone"])]
        if "sex" in constraint:
            f &= df_persons["sex"] == constraint["sex"]
        if "age" in constraint:
            f &= df_persons["age"].between(*constraint["age"])
        targets.append(constraint["target"] * np.count_nonzero(f))
        filters.append(f)
    for iteration in context.progress(range(iterations), label="imputing pt subscription"):
        factors = []
        for f, target in zip(filters, targets):
            current = df_persons.loc[f, "has_pt_subscription"].sum()
            factor = target / current if current > 0 else 1.0
            df_persons.loc[f, "has_pt_subscription"] *= factor
            factors.append(factor)
    print("Factors", "min:", min(factors), "max:", max(factors), "mean:", np.mean(factors))

    # Sample categorical values from the IPF probabilities
    random = np.random.RandomState(context.config("random_seed") + 8572)

    u = random.random_sample(len(df_persons))
    selection = u < df_persons["car_availability"]
    df_persons["car_availability"] = "none"
    df_persons.loc[selection, "car_availability"] = "all"
    df_persons["car_availability"] = df_persons["car_availability"].astype("category")

    u = random.random_sample(len(df_persons))
    selection = u < df_persons["bicycle_availability"]
    df_persons["bicycle_availability"] = "none"
    df_persons.loc[selection, "bicycle_availability"] = "all"
    df_persons["bicycle_availability"] = df_persons["bicycle_availability"].astype("category")

    u = random.random_sample(len(df_persons))
    selection = u < df_persons["has_pt_subscription"]
    df_persons["has_pt_subscription"] = selection

    # Household size: keep IPF-balanced values when the margin was active,
    # otherwise sample from the German census reference table.
    if context.config("braunschweig.ipf.use_household_size_margin"):
        df_persons["household_size"] = df_persons["household_size"].astype("category")
        print(
            "[braunschweig.enriched] using IPF-balanced hh_size; share by bin = "
            + ", ".join(
                f"{k}={v:.1%}"
                for k, v in df_persons["household_size"]
                .value_counts(normalize=True)
                .sort_index()
                .items()
            )
        )
    else:
        df_household_size = context.stage("braunschweig.data.census.household_size")

        minimum_age = context.config("braunschweig.minimum_age.one_person_household")
        df_household_size["lower_age"] = df_household_size["lower_age"].replace({0: minimum_age})

        df_young = df_household_size[df_household_size["lower_age"] == minimum_age].copy()
        df_young["lower_age"] = 0
        df_young["upper_age"] = minimum_age
        df_young.loc[df_young["household_size"] == "1", "weight"] = 0

        df_household_size = pd.concat([df_household_size, df_young])

        for (lower_age, upper_age, sex), df in df_household_size.groupby(["lower_age", "upper_age", "sex"]):
            f = df_persons["age"].between(lower_age, upper_age, inclusive="left")
            f &= df_persons["sex"] == sex  # TODO

            df = df.copy()
            df["weight"] /= df["weight"].sum()
            df = df.sample(n=np.count_nonzero(f), weights="weight", replace=True)
            df_persons.loc[f, "household_size"] = df["household_size"].values

        df_persons["household_size"] = df_persons["household_size"].astype("category")

    # Household income (overwrite). The reference table can be 5-bin
    # (Bavaria GENESIS) or 6-bin (Braunschweig MiD H4); pick adaptively.
    df_income = context.stage("braunschweig.data.census.household_income")
    income_bins = set(df_income["household_size"].astype(str).unique())
    income_size_map, scheme = _build_income_size_map(income_bins)
    income_lookup_size = df_persons["household_size"].astype(str).map(
        lambda s: income_size_map.get(s, s)
    )
    unresolved = set(income_lookup_size.unique()) - income_bins
    if unresolved:
        raise RuntimeError(
            "income_size_map produced bins not present in df_income: "
            f"{sorted(unresolved)} (reference has {sorted(income_bins)}, "
            f"scheme={scheme})"
        )

    for household_size, df in df_income.groupby("household_size"):
        f = (income_lookup_size == household_size).values
        df = df.copy()
        df["weight"] /= df["weight"].sum()
        df = df.sample(n=np.count_nonzero(f), weights="weight", replace=True)
        df_persons.loc[f, "household_income"] = df["income_class"].values

    n_missing_income = int(df_persons["household_income"].isna().sum())
    if n_missing_income > 0:
        raise RuntimeError(
            f"{n_missing_income} persons have no household_income after "
            f"reference sampling (scheme={scheme}, reference bins "
            f"{sorted(income_bins)}, lookup bins "
            f"{sorted(income_lookup_size.unique())})"
        )

    df_persons["high_income"] = df_persons["household_income"] == "5000+"

    # Munich residents (only defined when using the Munich MiD zones;
    # falls back to False for forks that use a different zoning system).
    df_persons["is_munich_resident"] = (
        df_persons["inside_munich"]
        if "inside_munich" in df_persons.columns
        else False
    )

    return df_persons


# --- Braunschweig-specific -------------------------------------------------
# MiD 2023 Tabelle H7 / H12.3 vehicle counts + INKAR-scaled income.

# MiD 2023 Tabelle H7 'Anzahl Autos im Haushalt'
CARS_BY_KREIS = {
    "03101": (0.25, 0.53, 0.20, 0.02),   # Braunschweig
    "03102": (0.10, 0.62, 0.22, 0.06),   # Salzgitter
    "03103": (0.17, 0.57, 0.22, 0.04),   # Wolfsburg
    "03151": (0.06, 0.50, 0.35, 0.08),   # Gifhorn
    "03153": (0.22, 0.53, 0.21, 0.04),   # Goslar
    "03154": (0.14, 0.52, 0.27, 0.07),   # Helmstedt
    "03157": (0.07, 0.48, 0.37, 0.08),   # Peine
    "03158": (0.13, 0.56, 0.22, 0.09),   # Wolfenbuettel
}
CARS_VALUES = np.array([0, 1, 2, 3])

# MiD 2023 Tabelle H12.3 'Anzahl Fahrraeder/Pedelecs/E-Bikes im Haushalt'
BIKES_BY_KREIS = {
    "03101": (0.17, 0.25, 0.26, 0.12, 0.21),
    "03102": (0.23, 0.24, 0.25, 0.11, 0.17),
    "03103": (0.36, 0.22, 0.21, 0.08, 0.14),
    "03151": (0.12, 0.22, 0.25, 0.14, 0.26),
    "03153": (0.36, 0.23, 0.17, 0.09, 0.15),
    "03154": (0.28, 0.16, 0.29, 0.13, 0.15),
    "03157": (0.18, 0.23, 0.22, 0.16, 0.22),
    "03158": (0.23, 0.27, 0.17, 0.13, 0.20),
}
BIKES_VALUES = np.array([0, 1, 2, 3, 4])

# Region-wide fallback for external residents (H7/H12.3 'Gesamt' row).
CARS_REGION = (0.15, 0.53, 0.26, 0.06)
BIKES_REGION = (0.23, 0.23, 0.23, 0.12, 0.20)


# Map inside_<kreis> boolean flags to the 5-digit Kreis ARS code (AGS-5).
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
    """Return a per-person AGS-5 Series derived from inside_<kreis> flags."""
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


def _apply_inkar_income_scale(df_persons, df_inkar):
    """Add ``household_income_eur`` = class_midpoint * INKAR-scale[home_kreis]."""
    midpoint = df_persons["household_income"].astype(str).map(CLASS_MIDPOINT_EUR)
    if midpoint.isna().any():
        n_na = int(midpoint.isna().sum())
        print(
            f"[braunschweig.enriched] {n_na} persons with unknown income_class; "
            f"using median midpoint 2800 EUR."
        )
        midpoint = midpoint.fillna(2800.0)

    kreis = _derive_kreis_ars5(df_persons)
    scale_lookup = dict(zip(df_inkar["ars5"], df_inkar["scale"]))
    scale = kreis.map(scale_lookup).fillna(1.0).astype(float)

    df_persons["household_income_eur"] = (midpoint.astype(float) * scale).round(0)
    return df_persons


def configure(context):
    _configure_base(context)
    context.stage("braunschweig.data.inkar.household_income")
    context.config("random_seed")


def execute(context):
    df_persons = _execute_base(context)

    # Re-sample vehicle counts from MiD H7 / H12.3 instead of the hardcoded 1s.
    random = np.random.RandomState(context.config("random_seed") + 91731)
    _sample_counts(df_persons, "number_of_cars", CARS_VALUES,
                   CARS_REGION, CARS_BY_KREIS, random)
    _sample_counts(df_persons, "number_of_bicycles", BIKES_VALUES,
                   BIKES_REGION, BIKES_BY_KREIS, random)

    # INKAR-based EUR income (Kreis-specific shift on top of the MiD H4
    # regionless quintile distribution).
    df_inkar = context.stage("braunschweig.data.inkar.household_income")
    df_persons = _apply_inkar_income_scale(df_persons, df_inkar)

    # BS-specific residency flag (aligns with is_munich_resident semantics).
    if "inside_braunschweig" in df_persons.columns:
        df_persons["is_bs_resident"] = df_persons["inside_braunschweig"]

    # ------------------------------------------------------------------
    # Post-enriched control variables - hard asserts that ensure no silent
    # NaN propagation, no impossible categorical, and (when the IPF
    # hh_size margin is enabled) that the per-cell shares survive the
    # household-formation/sampling round-trip with low deviation against
    # the IPF input target.
    # ------------------------------------------------------------------
    n = len(df_persons)
    for col in [
        "household_size", "household_income", "high_income",
        "car_availability", "bicycle_availability", "has_pt_subscription",
        "number_of_cars", "number_of_bicycles",
    ]:
        if col not in df_persons.columns:
            raise RuntimeError(
                f"[braunschweig.enriched] expected column '{col}' missing after enrichment"
            )
        n_na = int(df_persons[col].isna().sum())
        if n_na > 0:
            raise RuntimeError(
                f"[braunschweig.enriched] column '{col}' has {n_na}/{n} NaN values"
            )

    if context.config("braunschweig.ipf.use_household_size_margin"):
        achieved = (
            df_persons["household_size"].astype(str).value_counts(normalize=True)
            .sort_index()
        )
        df_size_ref = context.stage("braunschweig.data.census.household_size").copy()
        if "household_size" in df_size_ref.columns and "weight" in df_size_ref.columns:
            target = (
                df_size_ref.groupby("household_size", observed=True)["weight"].sum()
            )
            target = (target / target.sum()).sort_index()
            common = sorted(set(achieved.index) & set(target.index))
            if common:
                deltas = {
                    k: float(achieved.get(k, 0.0)) - float(target.get(k, 0.0))
                    for k in common
                }
                max_dev = max(abs(v) for v in deltas.values())
                print(
                    "[braunschweig.enriched] hh_size deviation vs reference (pp): "
                    + ", ".join(f"{k}={v*100:+.2f}" for k, v in deltas.items())
                    + f" - max |delta|={max_dev*100:.2f}pp"
                )
                if max_dev > 0.05:
                    raise RuntimeError(
                        "[braunschweig.enriched] hh_size shares drift more than "
                        f"5pp from reference: {deltas}"
                    )

    # Sanity ranges (catch arithmetic blow-ups, e.g. INKAR scale gone wild).
    if "household_income_eur" in df_persons.columns:
        eur = df_persons["household_income_eur"]
        n_eur_na = int(eur.isna().sum())
        if n_eur_na > 0:
            raise RuntimeError(
                f"[braunschweig.enriched] household_income_eur has {n_eur_na} NaN values"
            )
        if (eur < 100).any() or (eur > 20000).any():
            raise RuntimeError(
                f"[braunschweig.enriched] household_income_eur outside plausible "
                f"range [100, 20000]: min={eur.min():.0f}, max={eur.max():.0f}"
            )

    print(
        "[braunschweig.enriched] post-enrichment OK: "
        f"n={n:,}, high_income={df_persons['high_income'].mean():.2%}, "
        f"car_avail={(df_persons['car_availability']=='all').mean():.2%}, "
        f"bike_avail={(df_persons['bicycle_availability']=='all').mean():.2%}, "
        f"pt_sub={df_persons['has_pt_subscription'].mean():.2%}."
    )

    return df_persons
