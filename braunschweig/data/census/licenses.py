import pandas as pd
import os
import numpy as np

"""
Driving license ownership information for the Braunschweig region (Niedersachsen, ARS prefix 031).

Fork of bavaria/data/census/licenses.py:
- FE4.3 is filtered to Bundesland "Niedersachsen" instead of "Bayern".
- kreis_mapping is empty: in Lower Saxony every Kreis (incl. kreisfreie Städte
  Braunschweig, Salzgitter, Wolfsburg) reports its own licenses in FE4.4,
  so no redistribution from an umbrella city to a Landkreis is required.
"""

def configure(context):
    context.config("data_path")
    context.config("bavaria.licenses_path", "germany/fe4_2024.xlsx")

    context.stage("eqasim_common.spatial.codes")
    context.stage("braunschweig.data.census.population")

COUNT_COLUMN = "Fahrerlaubnisse bzw. Führerscheine"

def execute(context):
    # Load country-wide data
    df_country = pd.read_excel("{}/{}".format(context.config("data_path"), context.config("bavaria.licenses_path")),
        sheet_name = "FE4.2", skiprows = 8)

    df_country = df_country[["Geschlecht und\nLebensalter (in Jahren)", COUNT_COLUMN]]
    df_country.columns = ["age_class", "relative_weight"]

    f_sex = df_country["age_class"].str.contains("Männer")
    f_sex |= df_country["age_class"].str.contains("Frauen")
    f_sex |= df_country["age_class"].str.contains("Zusammen")

    df_country.loc[f_sex, "sex"] = df_country.loc[f_sex, "age_class"]
    df_country["sex"] = df_country["sex"].ffill()

    df_country = df_country[~df_country["sex"].str.contains("Zusammen") & ~f_sex]

    df_country.loc[df_country["sex"].str.contains("Männer"), "sex"] = "male"
    df_country.loc[df_country["sex"].str.contains("Frauen"), "sex"] = "female"
    df_country["sex"] = df_country["sex"].astype("category")

    df_country["relative_weight"] = df_country["relative_weight"] / df_country["relative_weight"].sum()
    df_country["age_class"] = df_country["age_class"].apply(clean_age_class).astype(int)

    # Load Bundesland-specific data (Niedersachsen)
    df_land = pd.read_excel("{}/{}".format(context.config("data_path"), context.config("bavaria.licenses_path")),
        sheet_name = "FE4.3", skiprows = 8)

    df_land = df_land[["Geschlecht und Land", COUNT_COLUMN]]
    df_land.columns = ["land", "relative_weight"]

    f_sex = df_land["land"].str.contains("Männer")
    f_sex |= df_land["land"].str.contains("Frauen")
    f_sex |= df_land["land"].str.contains("Zusammen")

    df_land.loc[f_sex, "sex"] = df_land.loc[f_sex, "land"]
    df_land["sex"] = df_land["sex"].ffill()

    df_land = df_land[~df_land["sex"].str.contains("Zusammen") & ~f_sex]

    df_land.loc[df_land["sex"].str.contains("Männer"), "sex"] = "male"
    df_land.loc[df_land["sex"].str.contains("Frauen"), "sex"] = "female"
    df_land["sex"] = df_land["sex"].astype("category")

    df_land = df_land[df_land["land"] == "Niedersachsen"]
    df_land["relative_weight"] = df_land["relative_weight"] / df_land["relative_weight"].sum()

    # Load Kreis-specific data
    df_kreis = pd.read_excel("{}/{}".format(context.config("data_path"), context.config("bavaria.licenses_path")),
        sheet_name = "FE4.4", skiprows = 7)

    assert df_kreis.columns[1].startswith("Amtlicher")
    assert df_kreis.columns[5].startswith("Pkw")

    df_kreis = df_kreis[["Amtlicher Gemeindeschlüssel", COUNT_COLUMN]]
    df_kreis.columns = ["kreis_code", "weight"]

    df_kreis = df_kreis[df_kreis["kreis_code"].str.len() == 5]
    df_kreis["departement_id"] = df_kreis["kreis_code"].astype("str").astype("category")
    df_kreis = df_kreis[["departement_id", "weight"]]

    df_codes = context.stage("eqasim_common.spatial.codes")
    df_kreis = df_kreis[df_kreis["departement_id"].isin(df_codes["departement_id"])]

    df_population = context.stage("braunschweig.data.census.population")

    required_kreis = set(df_population["commune_id"].str[:5].unique())
    available_kreis = set(df_kreis["departement_id"].unique())

    # In Lower Saxony there is no known kreisfrei/umliegender-Kreis split of driving-license
    # records similar to Bavaria (where Rosenheim, Landshut, Bamberg etc. aggregate their
    # surrounding Landkreis). All 9 Kreise in ARS prefix 031 appear individually in FE4.4.
    kreis_mapping: dict[str, str] = {}

    missing_kreis = required_kreis - available_kreis
    for kreis in set(missing_kreis):
        assert kreis in kreis_mapping, (
            "Kreis {} missing from KBA FE4.4 and no redistribution mapping is defined".format(kreis)
        )
        city = kreis_mapping[kreis]

        city_population = df_population.loc[df_population["commune_id"].str[:5] == city, "weight"].sum()
        kreis_population = df_population.loc[df_population["commune_id"].str[:5] == kreis, "weight"].sum()

        total_licenses = df_kreis.loc[df_kreis["departement_id"] == city, "weight"].sum()
        city_ratio = city_population / (city_population + kreis_population)

        df_kreis.loc[df_kreis["departement_id"] == city, "weight"] = total_licenses * city_ratio

        df_kreis = pd.concat([df_kreis, pd.DataFrame({
            "departement_id": [kreis], "weight": [total_licenses * (1 - city_ratio)]
        })])

        missing_kreis.remove(kreis)

    assert len(missing_kreis) == 0

    return df_country, df_land, df_kreis

def clean_age_class(age_class):
    if age_class.startswith("Bis"):
        return 0
    elif age_class.endswith("mehr"):
        return int(age_class.split(" ")[0])
    else:
        return int(age_class.split(" ")[0])

def validate(context):
    if not os.path.exists("{}/{}".format(context.config("data_path"), context.config("bavaria.licenses_path"))):
        raise RuntimeError("KBA FE4 licenses data is not available")

    return os.path.getsize("{}/{}".format(context.config("data_path"), context.config("bavaria.licenses_path")))
