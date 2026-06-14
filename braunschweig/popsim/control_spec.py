"""Declarative, typed specification of PopulationSim controls.

PopulationSim drives its synthesis from a hand-edited ``controls.csv`` with the
columns ``target, geography, seed_table, importance, control_field, expression``.
A control's ``expression`` is a pandas-style boolean string evaluated over one
seed table, e.g. ``"(persons.HP_ALTER < 10) & (persons.HP_SEX == 1)"`` selects
males aged 0-9 and ``"(households.H_GEW > 0)"`` selects every household for the
household total.

This module replaces the hand-edited CSV with a typed, reproducible
representation: a frozen :class:`ControlDef` record, a :func:`default_zensus_controls`
factory that builds the standard Zensus control set (household / population
totals, nine ten-year age bands x sex, and male/female totals) per geography, a
:func:`render_controls_csv` renderer to the exact PopulationSim column layout,
and a fail-fast :func:`validate_controls` check that refuses controls with a
blank expression (the notebook rule: only controls that carry an expression are
usable -- they must never be silently dropped).

Assumptions
-----------
- The age column holds integer years and the sex column holds two coded values
  (``male_value`` / ``female_value``); both are configurable so the same factory
  works for the persons seed table whatever the Zensus coding is.
- Age bands are ten-year bands with an open lower edge on the first band
  (``< 10``) and an open upper edge on the last band (``>= 80``); the eight
  interior bands are half-open ranges ``>= lo & < hi``.
- The module is pure: it performs no I/O and reads no real data.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterable, List, Sequence

logger = logging.getLogger(__name__)

import pandas as pd

# The exact PopulationSim controls.csv column order. Renderers must preserve it.
CONTROLS_CSV_COLUMNS: Sequence[str] = (
    "target",
    "geography",
    "seed_table",
    "importance",
    "control_field",
    "expression",
)

# Valid seed tables a control may reference.
SEED_TABLE_HOUSEHOLDS = "households"
SEED_TABLE_PERSONS = "persons"

# Geography levels. The first two are the existing per-cell IPU levels; KREIS and
# GEMEINDE are the multi-geography levels used by the Tier-3 (employment/education)
# controls. Must stay in sync with braunschweig.popsim.folders geography handling.
GEO_100M = "ZENSUS100m"
GEO_1KM = "ZENSUS1km"
GEO_KREIS = "KREIS"
GEO_GEMEINDE = "GEMEINDE"

# Nine ten-year age bands as (label, lower_bound, upper_bound). ``None`` marks an
# open edge: the first band has no lower bound, the last band no upper bound.
AGE_BANDS: Sequence[tuple] = (
    ("0_9", None, 10),
    ("10_19", 10, 20),
    ("20_29", 20, 30),
    ("30_39", 30, 40),
    ("40_49", 40, 50),
    ("50_59", 50, 60),
    ("60_69", 60, 70),
    ("70_79", 70, 80),
    ("80_plus", 80, None),
)


@dataclass(frozen=True)
class ControlDef:
    """A single PopulationSim control marginal.

    Attributes
    ----------
    name:
        Human-readable control identifier; used for both the ``target`` and
        ``control_field`` CSV columns (PopulationSim keys the seed-table
        aggregate column by ``control_field`` and the geography target by
        ``target``; here they are the same stable name).
    geography:
        The control geography level (e.g. ``"ZENSUS100m"`` / ``"ZENSUS1km"``).
    seed_table:
        The seed table the expression is evaluated over: ``"households"`` or
        ``"persons"``.
    importance:
        PopulationSim importance weight (higher = harder constraint).
    control_field:
        The aggregated control column name on the seed table.
    expression:
        Pandas-style boolean expression selecting the rows this control counts.
    """

    name: str
    geography: str
    seed_table: str
    importance: int
    control_field: str
    expression: str


@dataclass(frozen=True)
class CatalogControl:
    """A catalog control with a per-seed expression and its census source column(s).

    ``seed_expressions`` maps a seed name ("mid" / "entd" / "ipf") to the pandas-style
    boolean expression for that seed, or ``None`` when the seed cannot express the
    control. ``census_source`` names the prepared-cell marginal column(s) (the control
    target before geography suffixing).

    The dataclass is frozen for declarative use, but must NOT be used as a dict key
    or set member because the ``seed_expressions`` dict field is unhashable.
    """

    name: str
    geography: str
    seed_table: str
    importance: int
    census_source: tuple[str, ...]
    seed_expressions: dict[str, str | None]

    def expression_for(self, seed: str) -> str | None:
        """Return this control's expression for ``seed``, or ``None`` if inexpressible."""
        return self.seed_expressions.get(seed)


def _age_expression(age_col: str, lower: int | None, upper: int | None) -> str:
    """Build the age-range clause(s) for one band.

    Open lower edge -> ``(persons.<age> < upper)``; open upper edge ->
    ``(persons.<age> >= lower)``; interior band -> both clauses joined by ``&``.
    """
    clauses: List[str] = []
    if lower is not None:
        clauses.append(f"(persons.{age_col} >= {lower})")
    if upper is not None:
        clauses.append(f"(persons.{age_col} < {upper})")
    if not clauses:
        raise ValueError("An age band must have at least one bound (got none).")
    return " & ".join(clauses)


def default_zensus_controls(
    geographies: Sequence[str] = ("ZENSUS100m", "ZENSUS1km"),
    *,
    age_col: str = "HP_ALTER",
    sex_col: str = "HP_SEX",
    male_value: int = 1,
    female_value: int = 2,
    hh_weight_col: str = "H_GEW",
    person_weight_col: str = "P_GEW",
    importance: int = 1000,
) -> List[ControlDef]:
    """Build the standard Zensus control set for each requested geography.

    For every geography this produces 22 controls:

    - 1 household total (``households`` table, ``<hh_weight_col> > 0``),
    - 1 population total (``persons`` table, ``<person_weight_col> > 0``),
    - 18 age x sex controls (9 ten-year age bands x {male, female}),
    - 1 male total and 1 female total (``persons`` table, sex only).

    Parameters
    ----------
    geographies:
        Geography level names to emit controls for (one block each).
    age_col, sex_col:
        Seed-table column names for age (integer years) and sex (coded value).
    male_value, female_value:
        The sex codes used in the persons seed table.
    hh_weight_col, person_weight_col:
        Weight columns whose ``> 0`` selects every household / person for the
        respective total.
    importance:
        PopulationSim importance assigned to every control in the set.

    Returns
    -------
    list[ControlDef]
        ``22 * len(geographies)`` controls (44 for the two default geographies).
    """
    sex_codes = {"male": male_value, "female": female_value}
    controls: List[ControlDef] = []

    for geography in geographies:
        # Household total: one row per household, selected by a positive weight.
        controls.append(
            ControlDef(
                name="total_households",
                geography=geography,
                seed_table=SEED_TABLE_HOUSEHOLDS,
                importance=importance,
                control_field="total_households",
                expression=f"(households.{hh_weight_col} > 0)",
            )
        )
        # Population total: one row per person, selected by a positive weight.
        controls.append(
            ControlDef(
                name="total_population",
                geography=geography,
                seed_table=SEED_TABLE_PERSONS,
                importance=importance,
                control_field="total_population",
                expression=f"(persons.{person_weight_col} > 0)",
            )
        )

        # 18 age x sex controls.
        for band_label, lower, upper in AGE_BANDS:
            age_clause = _age_expression(age_col, lower, upper)
            for sex_label, sex_value in sex_codes.items():
                name = f"age_{band_label}_{sex_label}"
                expression = f"{age_clause} & (persons.{sex_col} == {sex_value})"
                controls.append(
                    ControlDef(
                        name=name,
                        geography=geography,
                        seed_table=SEED_TABLE_PERSONS,
                        importance=importance,
                        control_field=name,
                        expression=expression,
                    )
                )

        # Male and female totals (sex only).
        for sex_label, sex_value in sex_codes.items():
            name = f"total_{sex_label}"
            controls.append(
                ControlDef(
                    name=name,
                    geography=geography,
                    seed_table=SEED_TABLE_PERSONS,
                    importance=importance,
                    control_field=name,
                    expression=f"(persons.{sex_col} == {sex_value})",
                )
            )

    return controls


def render_controls_csv(controls: Iterable[ControlDef]) -> pd.DataFrame:
    """Render controls to the PopulationSim ``controls.csv`` table.

    The control ``name`` is written to BOTH the ``target`` and ``control_field``
    columns (PopulationSim keys the geography target by ``target`` and the
    seed-table aggregate column by ``control_field``; here they share one stable
    name). Columns are emitted in the exact PopulationSim order.

    Parameters
    ----------
    controls:
        Controls to render (any iterable).

    Returns
    -------
    pandas.DataFrame
        One row per control with columns
        ``[target, geography, seed_table, importance, control_field, expression]``.
    """
    rows = [
        {
            "target": control.name,
            "geography": control.geography,
            "seed_table": control.seed_table,
            "importance": control.importance,
            "control_field": control.name,
            "expression": control.expression,
        }
        for control in controls
    ]
    return pd.DataFrame(rows, columns=list(CONTROLS_CSV_COLUMNS))


def validate_controls(controls: Iterable[ControlDef]) -> List[ControlDef]:
    """Validate that every control carries a non-blank expression.

    Only controls with an expression are usable by PopulationSim. A blank or
    whitespace-only expression is a configuration error and must fail loudly
    rather than be silently dropped (no silent fallback).

    Parameters
    ----------
    controls:
        Controls to validate.

    Returns
    -------
    list[ControlDef]
        The validated controls (returned for chaining convenience).

    Raises
    ------
    ValueError
        If any control has an empty or whitespace-only expression. The message
        names the offending control(s) so the source can be fixed.
    """
    materialized = list(controls)
    offenders = [
        control.name
        for control in materialized
        if control.expression is None or not control.expression.strip()
    ]
    if offenders:
        raise ValueError(
            "PopulationSim controls must carry a non-blank expression; the "
            f"following control(s) have a blank expression: {sorted(offenders)}."
        )
    return materialized


def _backbone_age_expression(band_label: str, lower: int | None, upper: int | None, sex_value: int) -> str:
    """Build the age+sex expression for one backbone catalog age-band control.

    Reproduces the exact production fixture expression format:
    - First band (lower=None): ``(persons.HP_ALTER < upper)&(persons.HP_SEX==sex)``
    - Last band (upper=None): ``(persons.HP_ALTER > lower-1)&(persons.HP_SEX==sex)``
    - Interior bands: ``(persons.HP_ALTER > lower-1)&(persons.HP_ALTER < upper)&(persons.HP_SEX==sex)``

    There is one deliberate quirk preserved from the production fixture: the male
    20-29 band uses ``>19`` (no space after ``>``) while every other interior lower
    clause uses ``> N`` (with space). This is reproduced by special-casing
    ``band_label == "20_29"`` and ``sex_value == 1``.
    """
    sex_clause = f"(persons.HP_SEX=={sex_value})"
    if lower is None:
        # First band: only upper bound.
        return f"(persons.HP_ALTER < {upper})&{sex_clause}"
    if upper is None:
        # Last band: only lower bound, using > lower-1.
        return f"(persons.HP_ALTER > {lower - 1})&{sex_clause}"
    # Interior band: > lower-1 and < upper.
    # Special quirk: male 20-29 has no space after ">".
    lower_val = lower - 1
    if band_label == "20_29" and sex_value == 1:
        lower_clause = f"(persons.HP_ALTER >{lower_val})"
    else:
        lower_clause = f"(persons.HP_ALTER > {lower_val})"
    return f"{lower_clause}&(persons.HP_ALTER < {upper})&{sex_clause}"


def tier0_backbone_catalog() -> List[CatalogControl]:
    """Build the Tier-0 backbone catalog: hh/pop totals + 9 age bands x 2 sexes + M/F totals.

    The backbone catalog spans two geographies (ZENSUS100m and ZENSUS1km) and
    produces 22 controls per geography = 44 total. All controls have both ``mid``
    and ``entd`` seed expressions (the MiD and ENTD built tables share the same
    column names HP_ALTER / HP_SEX / H_GEW / P_GEW).

    Control-field naming follows the production baseline exactly:
    - Household total base: ``Insgesamt_Haushalte_Groesse_des_privaten_Haushalts_100m_Gitter_adj``
    - Population total base: ``POP_TOTAL_100m_adj``
    - Male age bands: ``M_AGE_{band_label}_agg``
    - Female age bands: ``F_AGE_{band_label}_agg``
    - Male total: ``M_TOTAL``
    - Female total: ``F_TOTAL``

    The base name is the same for both geographies; the geography suffix is appended
    by :func:`render_catalog_csv`.

    Returns
    -------
    list[CatalogControl]
        44 backbone controls (22 per geography).
    """
    HH_TOTAL_BASE = "Insgesamt_Haushalte_Groesse_des_privaten_Haushalts_100m_Gitter_adj"
    POP_TOTAL_BASE = "POP_TOTAL_100m_adj"

    HH_TOTAL_EXPR = "(households.H_GEW > 0) & (households.H_GEW < np.inf)"
    POP_TOTAL_EXPR = "(persons.P_GEW > 0) & (persons.P_GEW < np.inf)"

    catalog: List[CatalogControl] = []

    for geography in (GEO_100M, GEO_1KM):
        # Household total.
        catalog.append(
            CatalogControl(
                name=HH_TOTAL_BASE,
                geography=geography,
                seed_table=SEED_TABLE_HOUSEHOLDS,
                importance=1000,
                census_source=(HH_TOTAL_BASE,),
                seed_expressions={"mid": HH_TOTAL_EXPR, "entd": HH_TOTAL_EXPR},
            )
        )
        # Population total.
        catalog.append(
            CatalogControl(
                name=POP_TOTAL_BASE,
                geography=geography,
                seed_table=SEED_TABLE_PERSONS,
                importance=1000,
                census_source=(POP_TOTAL_BASE,),
                seed_expressions={"mid": POP_TOTAL_EXPR, "entd": POP_TOTAL_EXPR},
            )
        )
        # 9 male age bands then 9 female age bands.
        for sex_prefix, sex_value in (("M", 1), ("F", 2)):
            for band_label, lower, upper in AGE_BANDS:
                name = f"{sex_prefix}_AGE_{band_label}_agg"
                expr = _backbone_age_expression(band_label, lower, upper, sex_value)
                catalog.append(
                    CatalogControl(
                        name=name,
                        geography=geography,
                        seed_table=SEED_TABLE_PERSONS,
                        importance=1000,
                        census_source=(name,),
                        seed_expressions={"mid": expr, "entd": expr},
                    )
                )
        # Male and female totals.
        for sex_prefix, sex_value in (("M", 1), ("F", 2)):
            name = f"{sex_prefix}_TOTAL"
            expr = f"(persons.HP_SEX=={sex_value})"
            catalog.append(
                CatalogControl(
                    name=name,
                    geography=geography,
                    seed_table=SEED_TABLE_PERSONS,
                    importance=1000,
                    census_source=(name,),
                    seed_expressions={"mid": expr, "entd": expr},
                )
            )

    return catalog


def render_catalog_csv(controls: Iterable[CatalogControl], seed: str) -> pd.DataFrame:
    """Render seed-filtered catalog controls to the PopulationSim ``controls.csv`` layout.

    For each :class:`CatalogControl` the renderer:
    - Sets ``control_field`` = ``f"{control.name}_{control.geography}"``
    - Sets ``target`` = ``f"{control_field}_target"``
    - Resolves ``expression`` via :meth:`CatalogControl.expression_for`

    Parameters
    ----------
    controls:
        Already seed-filtered controls (output of :func:`controls_for_seed`).
    seed:
        The seed name used to resolve each control's expression.

    Returns
    -------
    pandas.DataFrame
        One row per control with columns matching :data:`CONTROLS_CSV_COLUMNS`.
    """
    rows = []
    for control in controls:
        control_field = f"{control.name}_{control.geography}"
        target = f"{control_field}_target"
        rows.append(
            {
                "target": target,
                "geography": control.geography,
                "seed_table": control.seed_table,
                "importance": control.importance,
                "control_field": control_field,
                "expression": control.expression_for(seed),
            }
        )
    return pd.DataFrame(rows, columns=list(CONTROLS_CSV_COLUMNS))


# Tier-1 household-size bases (census column name bases) and their H_GR values.
# Each entry is (census_base, h_gr_value, expression_op) where expression_op
# is either "==" or ">=" for the seed expression.
_TIER1_HH_SIZE_ENTRIES: Sequence[tuple] = (
    ("1_Person_Groesse_des_privaten_Haushalts_100m_Gitter", 1, "=="),
    ("2_Personen_Groesse_des_privaten_Haushalts_100m_Gitter", 2, "=="),
    ("3_Personen_Groesse_des_privaten_Haushalts_100m_Gitter", 3, "=="),
    ("4_Personen_Groesse_des_privaten_Haushalts_100m_Gitter", 4, "=="),
    ("5_Personen_Groesse_des_privaten_Haushalts_100m_Gitter", 5, "=="),
    ("6_Personen_und_mehr_Groesse_des_privaten_Haushalts_100m_Gitter", 6, ">="),
)

# Tier-1 household-type (Zensus 'Typ privater Haushalte nach Familientyp', 5-class).
# Each entry is (census_base, hh_type5_class).
# census_source = (census_base,) -- the prepared-cell marginal column (no _adj suffix
# for this topic; the 5 category columns are used directly as PopulationSim targets).
# seed_expressions: MiD only (entd=None because ENTD composition cannot express the
# German Familie 5-class breakdown; controls_for_seed drops ENTD with a WARNING).
#
# MiD collapse: map_households_to_hhtype (11-class) -> hh_type5 (5-class):
#   single_18_29, single_30_59, single_60_plus       -> einpersonen
#   couple_youngest_18_29 .. couple_youngest_60_plus  -> paar_ohne_kind
#   child_under_6, child_under_14, child_under_18     -> paar_mit_kind
#   single_parent                                     -> alleinerziehend
#   three_plus_adults                                 -> mehrpers_ohne_kernfamilie
#   not_classifiable                                  -> None (seed column is NaN/dropped)
_TIER1_HH_TYPE_ENTRIES: Sequence[tuple] = (
    ("EinpersHH_SingleHH_Typ_priv_HH_Familie_100m_Gitter",    "einpersonen"),
    ("Paare_ohneKind_Typ_priv_HH_Familie_100m_Gitter",         "paar_ohne_kind"),
    ("Paare_mitKind_Typ_priv_HH_Familie_100m_Gitter",          "paar_mit_kind"),
    ("Alleinerziehende_Typ_priv_HH_Familie_100m_Gitter",       "alleinerziehend"),
    ("MehrpersHHohneKernfam_Typ_priv_HH_Familie_100m_Gitter",  "mehrpers_ohne_kernfamilie"),
)


def tier1_controls() -> List[CatalogControl]:
    """Build the Tier-1 catalog: household-size + household-type controls.

    Household-size: 6 categories × 2 geographies = 12 controls (MiD + ENTD).
    Household-type: 5 Zensus Familie classes × 2 geographies = 10 controls (MiD-only).

    The household-size controls use the ``H_GR`` column on the seed households table
    (present in both the MiD and ENTD seeds after the Tier-7 implementation adds it).

    The household-type controls use the ``hh_type5`` column on the MiD seed households
    table, carrying one of five collapsed labels derived from
    ``braunschweig.data.mid.status_by_hhtype.map_households_to_hhtype``. ENTD is
    ``None`` (inexpressible) and is dropped by :func:`controls_for_seed` with a WARNING.

    Returns
    -------
    list[CatalogControl]
        22 Tier-1 controls (12 household-size + 10 household-type).
    """
    catalog: List[CatalogControl] = []

    # --- Household-size (Task 7) ---
    for geography in (GEO_100M, GEO_1KM):
        for census_base, h_gr_value, op in _TIER1_HH_SIZE_ENTRIES:
            expr = f"(households.H_GR {op} {h_gr_value})"
            catalog.append(
                CatalogControl(
                    name=census_base,
                    geography=geography,
                    seed_table=SEED_TABLE_HOUSEHOLDS,
                    importance=1000,
                    census_source=(census_base,),
                    seed_expressions={"mid": expr, "entd": expr},
                )
            )

    # --- Household-type / Lebensform Familie 5-class (Task 8) ---
    for geography in (GEO_100M, GEO_1KM):
        for census_base, hh_type5_class in _TIER1_HH_TYPE_ENTRIES:
            mid_expr = f"(households.hh_type5 == '{hh_type5_class}')"
            catalog.append(
                CatalogControl(
                    name=census_base,
                    geography=geography,
                    seed_table=SEED_TABLE_HOUSEHOLDS,
                    importance=1000,
                    census_source=(census_base,),
                    seed_expressions={"mid": mid_expr, "entd": None},
                )
            )

    return catalog


# Tier-2 tenure census column bases.
# Each entry is (census_base, mid_expression) where mid_expression is the
# pandas-style boolean string for the MiD households seed table.
# H_MIETE coding: 1 = renter (Mieter), 2 = owner (Eigentuemer); 3/9/309 excluded.
_TIER2_TENURE_ENTRIES: Sequence[tuple] = (
    ("EigentuemerHH_Tenure_100m_Gitter", "(households.H_MIETE == 2)"),
    ("MieterHH_Tenure_100m_Gitter",      "(households.H_MIETE == 1)"),
)

# Tier-2 building_type (3-class) census source columns and MiD seed expressions.
#
# Census crosswalk (Zensus 2022 Wohnung Gebaeudetyp, cleaned column names):
#   ein_zweifamilienhaus = SUM of 6 Ein-/Zweifamilienhaus columns
#   mehrfamilienhaus     = SUM of 3 MFH columns
#   sonstiges            = AndererGebaeudetyp column (1 source)
#
# MiD haustyp coding (MiD2023_Haushalte.csv):
#   1 = Ein-/Zweifamilienhaus (EFH/ZFH, freistehend/DHH/Reihenhaus)
#   2 = Mehrfamilienhaus (3-12 Wohnungen)
#   3 = Geschosswohnungsbau (13+ Wohnungen) -> grouped with MFH
#   4 = Sonstiges
#  95 = nicht zutreffend (excluded: controls_for_seed drops entd=None;
#       haustyp==95 households do not match any expression)
#
# MiD-only: ENTD does not carry a building-type flag; entd=None causes
# controls_for_seed to drop all 6 building_type controls for ENTD with WARNING.
_TIER2_BUILDING_TYPE_ENTRIES: Sequence[tuple] = (
    (
        "building_type_ein_zweifamilienhaus",
        (
            "FreiEFH_Wohnung_Gebaeudetyp_Groesse_100m_Gitter",
            "EFH_DHH_Wohnung_Gebaeudetyp_Groesse_100m_Gitter",
            "EFH_Reihenhaus_Wohnung_Gebaeudetyp_Groesse_100m_Gitter",
            "Freist_ZFH_Wohnung_Gebaeudetyp_Groesse_100m_Gitter",
            "ZFH_DHH_Wohnung_Gebaeudetyp_Groesse_100m_Gitter",
            "ZFH_Reihenhaus_Wohnung_Gebaeudetyp_Groesse_100m_Gitter",
        ),
        "(households.haustyp == 1)",
    ),
    (
        "building_type_mehrfamilienhaus",
        (
            "MFH_3bis6Wohnungen_Wohnung_Gebaeudetyp_Groesse_100m_Gitter",
            "MFH_7bis12Wohnungen_Wohnung_Gebaeudetyp_Groesse_100m_Gitter",
            "MFH_13undmehrWohnungen_Wohnung_Gebaeudetyp_Groesse_100m_Gitter",
        ),
        "(households.haustyp.isin([2, 3]))",
    ),
    (
        "building_type_sonstiges",
        (
            "AndererGebaeudetyp_Wohnung_Gebaeudetyp_Groesse_100m_Gitter",
        ),
        "(households.haustyp == 4)",
    ),
)


def tier2_controls() -> List[CatalogControl]:
    """Build the Tier-2 catalog: tenure (owner/renter) + building_type (3-class) controls.

    Tenure: 2 categories (owner, renter) × 2 geographies = 4 controls.
    MiD-only: ENTD does not carry a reliable tenure flag (``entd=None``); the
    control is dropped for ENTD by :func:`controls_for_seed` with a WARNING.

    The census source columns are the Zensus 2022 100m grid tenure counts:

    - ``EigentuemerHH_Tenure_100m_Gitter`` -- owner-occupied household count
    - ``MieterHH_Tenure_100m_Gitter``       -- renter household count

    The MiD mapping uses the H_MIETE flag on the households table:

    - H_MIETE == 2 -> Eigentuemer (owner)
    - H_MIETE == 1 -> Mieter      (renter)
    - H_MIETE in {3, 9, 309}     -> excluded (neither owner nor renter)

    Building type: 3 classes (Ein-/Zweifamilienhaus, Mehrfamilienhaus, Sonstiges) ×
    2 geographies = 6 controls.  MiD-only (ENTD has no building-type flag).

    The building_type controls use MULTI-COLUMN census_source: the derived marginal
    column (e.g. ``building_type_ein_zweifamilienhaus``) is the row-sum of multiple
    Zensus 2022 Gebaeudetyp category columns.  The name is a NEW derived name (not
    a raw parquet column); :func:`braunschweig.popsim.prepared_cells.add_aggregated_controls`
    must be called to materialise the derived column before :func:`build_control_totals`.

    MiD haustyp coding (MiD2023_Haushalte.csv):
    - 1  -> ein_zweifamilienhaus
    - 2,3 -> mehrfamilienhaus
    - 4  -> sonstiges
    - 95 -> n.z. (excluded; does not match any expression)

    Returns
    -------
    list[CatalogControl]
        10 Tier-2 controls: 4 tenure + 6 building_type (both × 2 geographies).
    """
    catalog: List[CatalogControl] = []

    # --- Tenure (existing) ---
    for geography in (GEO_100M, GEO_1KM):
        for census_base, mid_expr in _TIER2_TENURE_ENTRIES:
            catalog.append(
                CatalogControl(
                    name=census_base,
                    geography=geography,
                    seed_table=SEED_TABLE_HOUSEHOLDS,
                    importance=1000,
                    census_source=(census_base,),
                    seed_expressions={"mid": mid_expr, "entd": None},
                )
            )

    # --- Building type (new, multi-column census_source) ---
    for geography in (GEO_100M, GEO_1KM):
        for derived_name, source_cols, mid_expr in _TIER2_BUILDING_TYPE_ENTRIES:
            catalog.append(
                CatalogControl(
                    name=derived_name,
                    geography=geography,
                    seed_table=SEED_TABLE_HOUSEHOLDS,
                    importance=1000,
                    census_source=source_cols,
                    seed_expressions={"mid": mid_expr, "entd": None},
                )
            )

    return catalog


def full_catalog(include_tiers: Sequence[str] = ("tier0",)) -> List[CatalogControl]:
    """Build the combined catalog for the requested tier set.

    Parameters
    ----------
    include_tiers:
        Tiers to include. ``"tier0"`` is always included when present (backbone
        Zensus controls). ``"tier1"`` adds the 22 household-size + household-type
        controls. ``"tier2"`` adds the 4 tenure (owner/renter) controls.
        Default ``("tier0",)`` reproduces the pre-Tier-7 baseline exactly.

    Returns
    -------
    list[CatalogControl]
        All controls from the requested tiers, in tier order.
    """
    catalog: List[CatalogControl] = []
    if "tier0" in include_tiers:
        catalog.extend(tier0_backbone_catalog())
    if "tier1" in include_tiers:
        catalog.extend(tier1_controls())
    if "tier2" in include_tiers:
        catalog.extend(tier2_controls())
    return catalog


def build_aggregation_map(controls: Iterable[CatalogControl]) -> dict[str, tuple[str, ...]]:
    """Build the aggregation map for controls whose name differs from their census_source.

    Returns ``{control.name: control.census_source}`` for controls that need
    multi-column aggregation, i.e. where the derived column name is NOT already
    identical to a single census_source column.

    Single-source controls where ``census_source == (control.name,)`` are the
    identity case (name == raw parquet column) and are excluded from the map;
    they need no aggregation step.

    The returned map is consumed by
    :func:`braunschweig.popsim.prepared_cells.add_aggregated_controls`.

    Parameters
    ----------
    controls:
        Seed-filtered controls (output of :func:`controls_for_seed`).

    Returns
    -------
    dict[str, tuple[str, ...]]
        ``{derived_name: source_cols}`` for controls that require aggregation.
        Empty dict when all controls are single-source identity (tier0-only default).
    """
    agg_map: dict[str, tuple[str, ...]] = {}
    for control in controls:
        is_identity = (
            len(control.census_source) == 1
            and control.census_source[0] == control.name
        )
        if not is_identity:
            agg_map[control.name] = control.census_source
    return agg_map


def source_columns_union(controls: Iterable[CatalogControl]) -> list[str]:
    """Return the union of all census_source columns for the given controls.

    This is the set of RAW parquet columns that must be loaded (the union of each
    control's ``census_source`` tuple).  For single-source identity controls
    (``census_source == (control.name,)``) this equals ``control.name``.
    For multi-source controls the individual source columns are returned.

    Parameters
    ----------
    controls:
        Seed-filtered controls (output of :func:`controls_for_seed`).

    Returns
    -------
    list[str]
        Ordered, deduplicated list of raw census column names to load.
    """
    seen: dict[str, None] = {}
    for control in controls:
        for col in control.census_source:
            seen.setdefault(col, None)
    return list(seen)


def controls_for_seed(catalog: Iterable[CatalogControl], seed: str) -> List[CatalogControl]:
    """Return the catalog controls the given seed can express.

    A control with ``seed_expressions[seed] is None`` is DROPPED for this seed and a
    single WARNING is logged naming the control and seed (no silent fallback).
    """
    kept: List[CatalogControl] = []
    for control in catalog:
        expression = control.expression_for(seed)
        if expression is None:
            logger.warning(
                f"Control {control.name!r} dropped for seed {seed!r}: not expressible by this seed."
            )
            continue
        kept.append(control)
    return kept
