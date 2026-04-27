"""IPF input preparation - assemble Kreis / Gemeinde marginals.

Origin: eqasim-bavaria @ b20fbe6, file ``bavaria/ipf/prepare.py``.
Adapted for Braunschweig:
- Config keys renamed from ``bavaria.ipf.*`` to ``braunschweig.ipf.*``.
- No behavioural change otherwise (Phase 2.6 is relocation-only per D-5).
"""
import pandas as pd
import numpy as np

"""
This stage updates the formatting of the population and employment census data sets such
that they can be procesed by the IPF algorithm.

When ``braunschweig.ipf.use_household_size_margin`` is ``True``, an additional
DataFrame ``df_household_size`` is emitted carrying ``commune × hh_size``
targets (in persons). It is consumed by ``braunschweig.ipf.model`` as the
fifth IPF margin. When the flag is ``False``, the DataFrame is empty and
the IPF behaves identically to the legacy four-margin formulation
(bit-identical for Bavaria configs).
"""

HH_SIZE_BINS = ("1", "2", "3", "4", "5", "6+")


def configure(context):
    context.stage("bavaria.data.census.population")
    context.stage("bavaria.data.census.employment")
    context.stage("bavaria.data.census.licenses")

    context.config("braunschweig.ipf.use_household_size_margin", False)
    if context.config("braunschweig.ipf.use_household_size_margin"):
        context.stage("braunschweig.data.census.households_type")


def _build_household_size_margin(
    df_household_type: pd.DataFrame,
    df_population: pd.DataFrame,
) -> pd.DataFrame:
    """Aggregate persons-per-(commune, hh_size) and align to the IPF spatial scope.

    Source ``df_household_type`` is Zensus 2022 1000A-2081 (commune × size ×
    type, in persons). We sum over hh_type to obtain a (commune, hh_size)
    margin. The result is then *rescaled per commune* to match the total
    population already balanced by the population margin — this guarantees
    the IPF cannot be infeasible even if Zensus 2022 (Stichtag 15.05.2022)
    and the population source (DESTATIS 12411-0018, 31.12.2024) disagree on
    the commune total by a few percent.
    """
    df = (
        df_household_type
        .groupby(["commune_id", "hh_size"], observed=True, as_index=False)["weight"]
        .sum()
    )

    # Filter to the spatial scope already used by the population margin.
    scope = set(df_population["commune_id"].astype(str).unique())
    df = df[df["commune_id"].astype(str).isin(scope)].copy()

    # Rescale per commune to match the population total. This makes the
    # 5th margin internally consistent with the 1st — IPF feasibility is
    # then guaranteed up to floating-point precision.
    df_pop_totals = (
        df_population.groupby("commune_id", observed=True, as_index=False)["weight"]
        .sum()
        .rename(columns={"weight": "pop_total"})
    )
    df = df.merge(df_pop_totals, on="commune_id", how="left")
    df = df[df["pop_total"] > 0].copy()

    df_size_totals = (
        df.groupby("commune_id", observed=True)["weight"].sum()
        .rename("size_total")
    )
    df = df.merge(df_size_totals, on="commune_id", how="left")
    if not (df["size_total"] > 0).all():
        bad = df.loc[df["size_total"] <= 0, "commune_id"].unique()
        raise RuntimeError(
            "[braunschweig.ipf.prepare] zero size_total in HH-size margin for "
            f"{len(bad)} commune(s); first 5: {list(bad[:5])}"
        )
    df["weight"] = df["weight"] * df["pop_total"] / df["size_total"]

    return df[["commune_id", "hh_size", "weight"]]


def execute(context):
    # Load data
    df_population = context.stage("bavaria.data.census.population")
    df_employment = context.stage("bavaria.data.census.employment")

    df_licenses_country = context.stage("bavaria.data.census.licenses")[0]
    df_licenses_kreis = context.stage("bavaria.data.census.licenses")[2]

    use_hh_size = context.config("braunschweig.ipf.use_household_size_margin")

    # Generate numeric sex
    df_population["sex"] = df_population["sex"].replace({ "male": 1, "female": 2 })
    df_employment["sex"] = df_employment["sex"].replace({ "male": 1, "female": 2 })
    df_licenses_country["sex"] = df_licenses_country["sex"].replace({ "male": 1, "female": 2 })

    # Validation
    unique_population_kreis = set(df_population["commune_id"].str[:5].unique())
    unique_employment_kreis = set(df_employment["departement_id"].unique())
    unique_licenses_kreis = set(df_licenses_kreis["departement_id"].unique())
    assert unique_population_kreis == unique_employment_kreis
    assert unique_population_kreis == unique_licenses_kreis

    # Generate numeric department index
    df_population["departement_id"] = df_population["commune_id"].str[:5].astype("category")

    unique_communes = np.sort(df_population["commune_id"].unique())
    unique_departements = np.sort(list(
        set(df_employment["departement_id"].unique()) | 
        set(df_population["departement_id"].unique())))

    commune_mapping = { c: k for k, c in enumerate(unique_communes) }
    departement_mapping = { c: k for k, c in enumerate(unique_departements) }

    df_population["commune_index"] = df_population["commune_id"].replace(commune_mapping)
    df_population["departement_index"] = df_population["departement_id"].replace(departement_mapping)
    df_employment["departement_index"] = df_employment["departement_id"].replace(departement_mapping)
    df_licenses_kreis["departement_index"] = df_licenses_kreis["departement_id"].replace(departement_mapping)

    ## Licenses

    # Consolidate municipalities
    for department_id in df_licenses_kreis["departement_id"].unique():
        population = df_population.loc[df_population["commune_id"].str[:5] == department_id, "weight"].sum()
        licenses = df_licenses_kreis.loc[df_licenses_kreis["departement_id"] == department_id, "weight"].sum()

        if licenses > population:
            factor = population / licenses
            df_licenses_kreis.loc[df_licenses_kreis["departement_id"] == department_id, "weight"] *= factor
            print("Adapting licenses for {} by factor {}".format(department_id, factor))

    # Scale up the sociodemographics for the study area
    df_licenses_country["weight"] = df_licenses_country["relative_weight"] * df_licenses_kreis["weight"].sum()

    # Consolidate sex and age
    population_age_classes = np.sort(df_population["age_class"].unique())
    license_age_classes = np.sort(df_licenses_country["age_class"].unique())
    
    joint_age_classes = np.sort(list(set(population_age_classes) & set(license_age_classes)))
    joint_age_upper = list(joint_age_classes[1:]) + [9999]

    for sex in [1, 2]:
        for lower, upper in zip(joint_age_classes, joint_age_upper):
            f_population = df_population["sex"] == sex
            f_population &= df_population["age_class"] >= lower 
            f_population &= df_population["age_class"] < upper

            f_license = df_licenses_country["sex"] == sex
            f_license &= df_licenses_country["age_class"] >= lower
            f_license &= df_licenses_country["age_class"] < upper

            population = df_population.loc[f_population, "weight"].sum()
            licenses = df_licenses_country.loc[f_license, "weight"].sum()

            if population < licenses:
                factor = population / licenses

                print("Adapting sex:{} age:({}, {}) by factor {}".format(
                    ["", "m", "f"][sex], lower, upper, factor
                ))

                df_licenses_country.loc[f_license, "weight"] *= factor
    
    # Take into account updated total
    licenses_kreis_total = df_licenses_kreis["weight"].sum()
    if licenses_kreis_total <= 0:
        raise RuntimeError(
            "[braunschweig.ipf.prepare] df_licenses_kreis weight sum is non-positive "
            f"({licenses_kreis_total!r}); cannot rescale licenses."
        )
    factor = df_licenses_country["weight"].sum() / licenses_kreis_total
    print("Adapting total with correction factor {}".format(factor))
    df_licenses_kreis["weight"] *= factor

    # Optional fifth margin: per-commune household-size distribution.
    if use_hh_size:
        df_household_type = context.stage("braunschweig.data.census.households_type")
        df_household_size = _build_household_size_margin(df_household_type, df_population)
        print(
            "[braunschweig.ipf.prepare] HH-size margin: {:,} cells across {:,} communes, "
            "total persons = {:,.0f}".format(
                len(df_household_size),
                df_household_size["commune_id"].nunique(),
                df_household_size["weight"].sum(),
            )
        )
    else:
        df_household_size = pd.DataFrame(
            {"commune_id": pd.Series(dtype=str),
             "hh_size": pd.Series(dtype=str),
             "weight": pd.Series(dtype=float)}
        )

    return (df_population, df_employment, df_licenses_country, df_licenses_kreis,
            df_household_size)
