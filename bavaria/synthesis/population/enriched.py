import synthesis.population.enriched as delegate

import pandas as pd
import geopandas as gpd
import numpy as np


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


def configure(context):
    delegate.configure(context)

    context.stage("synthesis.population.spatial.home.locations")

    context.stage("bavaria.data.mid.data")
    context.stage("bavaria.data.mid.zones")

    context.stage("bavaria.data.census.household_size")
    context.stage("bavaria.data.census.household_income")

    context.config("random_seed")

    context.config("bavaria.minimum_age.car_availability", 0)
    context.config("bavaria.minimum_age.bicycle_availability", 0)
    context.config("bavaria.minimum_age.pt_subscription", 0)

    context.config("bavaria.minimum_age.one_person_household", 16)
    context.config("braunschweig.ipf.use_household_size_margin", False)

"""
This stage overrides car availability, bike availability and transit subscription based on MiD data
"""

def execute(context):
    # delegate population
    df_persons = delegate.execute(context)

    # require home locations
    df_homes = context.stage("synthesis.population.spatial.home.locations")[["household_id", "geometry"]].copy()

    # load MiD
    df_zones = context.stage("bavaria.data.mid.zones")
    mid = context.stage("bavaria.data.mid.data")

    # assign zone membership to each person
    f_covered = np.zeros(len(df_homes), dtype = bool)
    for zone in df_zones["name"].unique():
        df_query = gpd.sjoin(df_homes, df_zones[df_zones["name"] == zone], predicate = "within")
        df_homes["inside_{}".format(zone)] = df_homes["household_id"].isin(df_query["household_id"])
        f_covered |= df_homes["inside_{}".format(zone)]

    df_homes["inside_external"] = ~f_covered

    df_persons = gpd.GeoDataFrame(
        pd.merge(df_persons, df_homes, on = "household_id"),
        crs = df_homes.crs
    )

    # Run IPFs to impute availabilities
    iterations = 1000

    # CAR AVAILABILITY
    df_persons["car_availability"] = 1.0
    constraints = mid["car_availability_constraints"]

    constraints.append({ 
        "age": (-np.inf, context.config("bavaria.minimum_age.car_availability") - 1), 
        "target": 0.0 
    })

    filters = []
    targets = []

    for constraint in constraints:
        f = np.ones((len(df_persons),), dtype = bool)

        if "zone" in constraint:
            f &= df_persons["inside_{}".format(constraint["zone"])]
        
        if "sex" in constraint:
            f &= df_persons["sex"] == constraint["sex"]

        if "age" in constraint:
            f &= df_persons["age"].between(*constraint["age"])

        targets.append(constraint["target"] * np.count_nonzero(f))
        filters.append(f)

    for iteration in context.progress(range(iterations), label = "imputing car availability"):
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
        "age": (-np.inf, context.config("bavaria.minimum_age.bicycle_availability") - 1), 
        "target": 0.0 
    })

    filters = []
    targets = []

    for constraint in constraints:
        f = np.ones((len(df_persons),), dtype = bool)

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

    for iteration in context.progress(range(iterations), label = "imputing bike availability"):
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
        "age": (-np.inf, context.config("bavaria.minimum_age.pt_subscription") - 1), 
        "target": 0.0 
    })

    filters = []
    targets = []

    for constraint in constraints:
        f = np.ones((len(df_persons),), dtype = bool)

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

    for iteration in context.progress(range(iterations), label = "imputing pt subscription"):
        factors = []

        for f, target in zip(filters, targets):
            current = df_persons.loc[f, "has_pt_subscription"].sum()
            factor = target / current if current > 0 else 1.0
            df_persons.loc[f, "has_pt_subscription"] *= factor
            factors.append(factor)

    print("Factors", "min:", min(factors), "max:", max(factors), "mean:", np.mean(factors))

    # Sample values
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

    # Household size (overwrite)
    # When the IPF already balanced a per-commune hh_size margin (Zensus 2022
    # 1000A-2081), df_persons["household_size"] arrives as a meaningful
    # categorical with values "1".. "6+" — we keep that and skip the
    # regions-aggregated post-hoc draw.
    if context.config("braunschweig.ipf.use_household_size_margin"):
        df_persons["household_size"] = df_persons["household_size"].astype("category")
        print(
            "[bavaria.synthesis.population.enriched] using IPF-balanced hh_size; "
            "share by bin = "
            + ", ".join(
                f"{k}={v:.1%}"
                for k, v in df_persons["household_size"]
                .value_counts(normalize=True)
                .sort_index()
                .items()
            )
        )
    else:
        df_household_size = context.stage("bavaria.data.census.household_size")

        # Make sure that persons <16 are not in 1-person households
        minimum_age = context.config("bavaria.minimum_age.one_person_household")
        df_household_size["lower_age"] = df_household_size["lower_age"].replace({ 0: minimum_age })

        df_young = df_household_size[df_household_size["lower_age"] == minimum_age].copy()
        df_young["lower_age"] = 0
        df_young["upper_age"] = minimum_age
        df_young.loc[df_young["household_size"] == "1", "weight"] = 0

        df_household_size = pd.concat([df_household_size, df_young])

        for (lower_age, upper_age, sex), df in df_household_size.groupby(["lower_age", "upper_age", "sex"]):
            f = df_persons["age"].between(lower_age, upper_age, inclusive = "left")
            f &= df_persons["sex"] == sex ## TODO

            df = df.copy()
            df["weight"] /= df["weight"].sum()
            df = df.sample(n = np.count_nonzero(f), weights = "weight", replace = True)
            df_persons.loc[f, "household_size"] = df["household_size"].values

        df_persons["household_size"] = df_persons["household_size"].astype("category")

    # Household income (overwrite)
    df_income = context.stage("bavaria.data.census.household_income")

    # The income reference table can use either a 5-bin scheme (Bavaria
    # GENESIS: "1","2","3","4","5+") or a 6-bin scheme (Braunschweig MiD H4:
    # "1","2","3","4","5","6+"). Build an adaptive map from the IPF's
    # hh_size space (which can include "5","6" as integers post-IPF) onto
    # whichever bins the reference table actually carries.
    income_bins = set(df_income["household_size"].astype(str).unique())
    income_size_map, scheme = _build_income_size_map(income_bins)
    income_lookup_size = df_persons["household_size"].astype(str).map(
        lambda s: income_size_map.get(s, s)
    )

    # Sanity check: every person must map to a bin that exists in the
    # reference table, otherwise their household_income silently stays NaN.
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
        df = df.sample(n = np.count_nonzero(f), weights = "weight", replace = True)
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
    df_persons["is_munich_resident"] = df_persons["inside_munich"] \
        if "inside_munich" in df_persons.columns else False

    # ------------------------------------------------------------------
    # Post-enriched control variables — hard asserts that ensure no
    # silent NaN propagation, no impossible categorical, and (when the
    # IPF hh_size margin is enabled) that the per-cell shares survive
    # the household-formation/sampling round-trip with low deviation
    # against the IPF input target.
    # ------------------------------------------------------------------
    n = len(df_persons)
    for col in [
        "household_size", "household_income", "high_income",
        "car_availability", "bicycle_availability", "has_pt_subscription",
        "number_of_cars", "number_of_bicycles",
    ]:
        if col not in df_persons.columns:
            raise RuntimeError(
                f"[bavaria.enriched] expected column '{col}' missing after enrichment"
            )
        n_na = int(df_persons[col].isna().sum())
        if n_na > 0:
            raise RuntimeError(
                f"[bavaria.enriched] column '{col}' has {n_na}/{n} NaN values"
            )

    if context.config("braunschweig.ipf.use_household_size_margin"):
        achieved = (
            df_persons["household_size"].astype(str).value_counts(normalize=True)
            .sort_index()
        )
        # The bavaria.data.census.household_size reference (potentially
        # alias-swapped to braunschweig.data.census.household_size) is
        # the canonical pre-IPF target. Re-load and aggregate to
        # per-person shares for the comparison.
        df_size_ref = context.stage("bavaria.data.census.household_size").copy()
        # Both reference schemas expose at least household_size + weight.
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
                    "[bavaria.enriched] hh_size deviation vs reference (pp): "
                    + ", ".join(f"{k}={v*100:+.2f}" for k, v in deltas.items())
                    + f" — max |Δ|={max_dev*100:.2f}pp"
                )
                # 5pp is the soft tolerance — household formation drops
                # the trailing partial chunk per (commune, hh_size)
                # bucket so a small bias is expected, but anything
                # larger means the formation logic drifted.
                if max_dev > 0.05:
                    raise RuntimeError(
                        "[bavaria.enriched] hh_size shares drift more than "
                        f"5pp from reference: {deltas}"
                    )

    # Sanity ranges (catch arithmetic blow-ups, e.g. INKAR scale gone wild).
    if "household_income_eur" in df_persons.columns:
        eur = df_persons["household_income_eur"]
        n_eur_na = int(eur.isna().sum())
        if n_eur_na > 0:
            raise RuntimeError(
                f"[bavaria.enriched] household_income_eur has {n_eur_na} NaN values"
            )
        if (eur < 100).any() or (eur > 20000).any():
            raise RuntimeError(
                f"[bavaria.enriched] household_income_eur outside plausible "
                f"range [100, 20000]: min={eur.min():.0f}, max={eur.max():.0f}"
            )

    print(
        "[bavaria.enriched] post-enrichment OK: "
        f"n={n:,}, high_income={df_persons['high_income'].mean():.2%}, "
        f"car_avail={(df_persons['car_availability']=='all').mean():.2%}, "
        f"bike_avail={(df_persons['bicycle_availability']=='all').mean():.2%}, "
        f"pt_sub={df_persons['has_pt_subscription'].mean():.2%}."
    )

    return df_persons
