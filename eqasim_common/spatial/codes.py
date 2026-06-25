"""German Amtlicher Regionalschlüssel (ARS) hierarchy mapping.

Origin: eqasim-bavaria @ b20fbe6, file ``bavaria/data/spatial/codes.py``.
Moved to ``eqasim_common.spatial`` in Phase 2.3 of the eqasim-bs refactor;
inherited unchanged.

The codes (Amtlicher Regionalschlüssel - ARS) are hierarchically structured:

- 2 digits: Bundesland (or city state)
- 1 digit:  Regierungsbezirk / Bezirk
- 2 digits: Landkreis or Kreisfreie Stadt (city without "Landkreis / Kreis")
- 4 digits: Gemeindeverband (municipality association)
- 3 digits: Gemeinde (municipality)

Mapping to the French codes used upstream:

- Bundesland          -> région
- Regierungsbezirk    -> no correspondence
- Landkreis           -> département
- Gemeindeverband     -> no correspondence (theoretical: communauté de communes)
- Gemeinde            -> commune
- French IRIS         -> no correspondence (a fake one is fabricated per Gemeinde)
"""


def configure(context):
    context.stage("eqasim_common.data.population.raw")

def execute(context):
    # Load codes. .copy() makes df_codes an independent DataFrame (not a slice/view of
    # the cached raw population frame), so the subsequent column assignments below are
    # unambiguous and do not raise pandas' SettingWithCopyWarning.
    df_codes = context.stage("eqasim_common.data.population.raw")[["municipality_code"]].copy()

    # Clean up identifiers
    df_codes["region_id"] = df_codes["municipality_code"].str[:2].astype("category")
    df_codes["departement_id"] = df_codes["municipality_code"].str[:5].astype("category")
    df_codes["commune_id"] = df_codes["municipality_code"].astype("category")

    # Fake IRIS
    df_codes["iris_id"] = df_codes["commune_id"].astype(str) + "0000"
    df_codes["iris_id"] = df_codes["iris_id"].astype("category")

    # Track outdated AGS code for conversion
    df_codes["ags"] = df_codes["commune_id"].str[:5] + df_codes["commune_id"].str[9:]
    df_codes["ags"] = df_codes["ags"].astype("category")

    return df_codes[["region_id", "departement_id", "commune_id", "iris_id", "ags"]]
