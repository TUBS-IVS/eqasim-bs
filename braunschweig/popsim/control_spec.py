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

from braunschweig.data.mid.status_by_kreis import STATUS_KEYS

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

# The per-cell household-total census column (tier0 backbone HH_TOTAL control). Exposed at
# module scope so the economic_status x Kreis control (issue #109) can sum it per Kreis to
# get the household total its status targets must partition (IPF-consistent).
HH_TOTAL_CENSUS_COLUMN = "Insgesamt_Haushalte_Groesse_des_privaten_Haushalts_100m_Gitter_adj"

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
    """Build the Tier-0 backbone catalog (LOSSLESS-reduced): 20 controls.

    This is the over-controlling fix: redundant/derivable marginals are dropped so
    PopulationSim is constrained only by an independent, non-redundant set, without
    losing any information (every dropped target is an exact sum of kept ones).

    Reduced structure (21 controls):

    - ``ZENSUS100m`` (19): ``HH_TOTAL`` + the 18 age x sex bands.
      * ``POP_TOTAL`` is DROPPED at 100m -- it is the exact sum of the 18 age x sex
        bands, so it is fully derivable (lossless) and adds no independent constraint.
      * ``M_TOTAL`` / ``F_TOTAL`` are DROPPED at 100m -- each is the exact sum of that
        sex's 9 age bands, again fully derivable (lossless).
    - ``ZENSUS1km`` (1): ``HH_TOTAL`` only.
      * The 18 age bands + ``M_TOTAL`` / ``F_TOTAL`` + ``POP_TOTAL`` are ALL DROPPED at
        1km: the 100m age bands aggregate exactly to their 1km parent under nested
        sub_balancing, so every 1km person duplicate is redundant (lossless).
      * ``HH_TOTAL`` is KEPT at BOTH geographies: it is the required household
        expansion / integerizer anchor (also the settings ``total_hh_control``) and
        must not be dropped or renamed. It is the SINGLE 1km control -- a 1km-only
        person control (POP) is avoided (build_control_totals emits one shared 100m
        source set -> a 1km-only source column would be missing -> PopulationSim
        KeyError); persons are enforced via the 100m age x sex bands + aggregation.

    Net: 100m = {HH_TOTAL, 18 bands} = 19 ; 1km = {HH_TOTAL} = 1 ; total 20.

    All controls have both ``mid`` and ``entd`` seed expressions (the MiD and ENTD
    built tables share the column names HP_ALTER / HP_SEX / H_GEW / P_GEW).

    Control-field naming follows the production baseline exactly (byte-identical for
    every kept control):
    - Household total base: ``Insgesamt_Haushalte_Groesse_des_privaten_Haushalts_100m_Gitter_adj``
    - Population total base: ``POP_TOTAL_100m_adj``
    - Male age bands: ``M_AGE_{band_label}_agg``
    - Female age bands: ``F_AGE_{band_label}_agg``

    The base name is the same for both geographies; the geography suffix is appended
    by :func:`render_catalog_csv`.

    Returns
    -------
    list[CatalogControl]
        20 backbone controls (19 at ZENSUS100m, 1 at ZENSUS1km).
    """
    HH_TOTAL_BASE = HH_TOTAL_CENSUS_COLUMN
    POP_TOTAL_BASE = "POP_TOTAL_100m_adj"

    HH_TOTAL_EXPR = "(households.H_GEW > 0) & (households.H_GEW < np.inf)"
    POP_TOTAL_EXPR = "(persons.P_GEW > 0) & (persons.P_GEW < np.inf)"

    catalog: List[CatalogControl] = []

    # --- ZENSUS100m: HH_TOTAL + 18 age x sex bands (POP_TOTAL / M_TOTAL / F_TOTAL
    #     dropped here as exact sums of kept controls -> lossless). ---
    catalog.append(
        CatalogControl(
            name=HH_TOTAL_BASE,
            geography=GEO_100M,
            seed_table=SEED_TABLE_HOUSEHOLDS,
            importance=1000,
            census_source=(HH_TOTAL_BASE,),
            seed_expressions={"mid": HH_TOTAL_EXPR, "entd": HH_TOTAL_EXPR},
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
                    geography=GEO_100M,
                    seed_table=SEED_TABLE_PERSONS,
                    importance=1000,
                    census_source=(name,),
                    seed_expressions={"mid": expr, "entd": expr},
                )
            )

    # --- ZENSUS1km: HH_TOTAL ONLY (the household expansion / integerizer anchor).
    #     POP_TOTAL + the 18 age bands + M/F totals are ALL dropped at 1km: they are
    #     exact 100m->1km aggregates under nested sub_balancing (lossless). A 1km-ONLY
    #     person control (POP) is intentionally avoided: build_control_totals emits one
    #     shared 100m source-column set for both grid geographies, so a 1km-only source
    #     column is NOT written -> PopulationSim setup raises KeyError. HH_TOTAL is the
    #     single 1km control; persons are enforced via the 100m age x sex bands + the
    #     exact 100m->1km aggregation. ---
    catalog.append(
        CatalogControl(
            name=HH_TOTAL_BASE,
            geography=GEO_1KM,
            seed_table=SEED_TABLE_HOUSEHOLDS,
            importance=1000,
            census_source=(HH_TOTAL_BASE,),
            seed_expressions={"mid": HH_TOTAL_EXPR, "entd": HH_TOTAL_EXPR},
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


# ---------------------------------------------------------------------------
# Importance profiles (PopulationSim per-control importance weights)
# ---------------------------------------------------------------------------
# By default every control carries a uniform importance (1000). An importance
# PROFILE overrides that per control GROUP, where a group is matched from the
# rendered ``control_field`` name (the same prefixes the offline optimizer used).
#
# "optimized_2026_06_30": result of the systematic per-group importance search
# (coordinate descent, 3-replicate averaged to beat the integerizer's run-to-run
# noise, count-weighted deviation over all 50 controls, batch_020). It lowered the
# mengengewichtete Gesamtabweichung from 3.365 % (uniform) to 2.868 % (-15 %,
# rep-std 0). The pattern: down-weight the already well-fit controls (age x sex,
# HH-size, HH-type), raise max_expansion_factor (set in settings.yaml, not here),
# and mildly raise the donor-sparse / employment controls. NOTE: tuned on a single
# ZGB tile (batch_020, ~2 % of households); the large-HH (6+) and person totals
# remain donor-bound and are not fully weight-fixable. Provenance: scripts/opt_loop2
# run, opt2_best.json.
IMPORTANCE_PROFILES: dict[str, dict[str, int]] = {
    "uniform": {},
    "optimized_2026_06_30": {
        "anchor": 1_000_000_000,  # household total: hard anchor (forces the count)
        "son": 20_000,            # building_type_sonstiges (donor over-represented)
        "six": 2_000,             # 6+-person households
        "employed": 2_000,        # employment controls
        "age": 200,               # age x sex (well-fit -> down-weighted)
        "size15": 500,            # HH size 1-5
        "hhtype": 200,            # HH type
        # KREIS attribute controls (registry tier -> group kreis_hard / kreis_soft, see
        # importance_group_for_field): HARD entries (economic_status #109, number_of_cars
        # #99) at the level of the other Kreis-scale socio controls ("employed" = 2000);
        # SOFT entries (bikes / ebike / trip_class) carry NO profile entry and keep the
        # uniform 1000, so they yield gracefully in small cells instead of fighting the
        # Zensus backbone. Added 2026-07-08 with the registry wiring; NOT part of the
        # 2026-06-30 offline search (the KREIS attribute controls did not exist then).
        "kreis_hard": 2_000,
    },
}


def importance_group_for_field(control_field: str) -> str:
    """Classify a rendered ``control_field`` into an importance GROUP.

    Matching is by name prefix and mirrors the offline optimizer exactly, so a
    profile applied here reproduces the searched configuration. Returns ``"other"``
    for controls not covered by any profile group (they keep the uniform weight).
    """
    s = str(control_field)
    if s.startswith("Insgesamt_Haushalte"):
        return "anchor"
    if s.startswith("6_Personen_und_mehr"):
        return "six"
    if s.startswith("building_type_sonstiges"):
        return "son"
    if "_AGE_" in s:
        return "age"
    if s.startswith(("Paare_ohneKind", "Paare_mitKind", "Alleinerziehende", "MehrpersHHohneKernfam")):
        return "hhtype"
    if s.startswith(("EigentuemerHH", "MieterHH")):
        return "tenure"
    if s.startswith(("1_Person", "2_Personen", "3_Personen", "4_Personen", "5_Personen")):
        return "size15"
    if s.startswith(("building_type_ein_zweifamilienhaus", "building_type_mehrfamilienhaus")):
        return "bld"
    if s.startswith("EMPLOYED_") or s.startswith("employed"):
        return "employed"
    if s.startswith(("schulabschluss", "beruflabschluss")):
        return "edu"
    # KREIS attribute controls (kreis_attribute_control.REGISTRY): the entry's tier
    # ("hard"/"soft") maps to the group kreis_hard / kreis_soft, so a future registry
    # entry is classified automatically. Matched by the rendered control-column prefix
    # f"{entry.name}_" (e.g. "number_of_cars_3plus_KREIS" -> number_of_cars, hard).
    from braunschweig.popsim.kreis_attribute_control import REGISTRY as _KREIS_REGISTRY
    for _entry in _KREIS_REGISTRY:
        if s.startswith(f"{_entry.name}_"):
            return f"kreis_{_entry.tier}"
    return "other"


def apply_importance_profile(controls_csv: pd.DataFrame, profile: str) -> pd.DataFrame:
    """Return a copy of a rendered ``controls.csv`` frame with profiled importance.

    ``profile`` selects an entry of :data:`IMPORTANCE_PROFILES`. ``"uniform"`` (or an
    empty profile) is a no-op (byte-identical to the input). For any other profile,
    each control's ``importance`` is set from its group's profile value; groups absent
    from the profile keep their existing (uniform) importance. Raises ``KeyError`` on
    an unknown profile name (fail-fast, no silent fallback).
    """
    if profile not in IMPORTANCE_PROFILES:
        raise KeyError(
            f"unknown importance profile {profile!r}; known: {sorted(IMPORTANCE_PROFILES)}"
        )
    weights = IMPORTANCE_PROFILES[profile]
    if not weights:
        return controls_csv
    out = controls_csv.copy()
    groups = out["control_field"].map(importance_group_for_field)
    out["importance"] = [
        weights.get(g, imp) for g, imp in zip(groups, out["importance"])
    ]
    return out


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
# LOSSLESS reduction: the "einpersonen" entry is intentionally OMITTED. The
# single-person household count stays pinned by the Tier-1 household-size control
# H_GR == 1 (einpersonen ~= household_size == 1), so einpersonen becomes the exact
# residual of the household-type partition and adds no independent constraint.
_TIER1_HH_TYPE_ENTRIES: Sequence[tuple] = (
    ("Paare_ohneKind_Typ_priv_HH_Familie_100m_Gitter",         "paar_ohne_kind"),
    ("Paare_mitKind_Typ_priv_HH_Familie_100m_Gitter",          "paar_mit_kind"),
    ("Alleinerziehende_Typ_priv_HH_Familie_100m_Gitter",       "alleinerziehend"),
    ("MehrpersHHohneKernfam_Typ_priv_HH_Familie_100m_Gitter",  "mehrpers_ohne_kernfamilie"),
)


def tier1_controls() -> List[CatalogControl]:
    """Build the Tier-1 catalog (LOSSLESS-reduced): household-size + household-type.

    Reduced to ZENSUS100m only (the 1km duplicates aggregate exactly from the 100m
    controls under nested sub_balancing, so they add no independent constraint):

    Household-size: 6 categories x 1 geography (100m) = 6 controls (MiD + ENTD).
    Household-type: 4 Zensus Familie classes x 1 geography (100m) = 4 controls
    (MiD-only). The "einpersonen" class is dropped (see ``_TIER1_HH_TYPE_ENTRIES``):
    it is the exact residual of the type partition and the single-person count is
    already pinned by the household-size control ``H_GR == 1``.

    The household-size controls use the ``H_GR`` column on the seed households table
    (present in both the MiD and ENTD seeds after the Tier-7 implementation adds it).

    The household-type controls use the ``hh_type5`` column on the MiD seed households
    table, carrying one of the collapsed labels derived from
    ``braunschweig.data.mid.status_by_hhtype.map_households_to_hhtype``. ENTD is
    ``None`` (inexpressible) and is dropped by :func:`controls_for_seed` with a WARNING.

    Returns
    -------
    list[CatalogControl]
        10 Tier-1 controls (6 household-size + 4 household-type), all at ZENSUS100m.
    """
    catalog: List[CatalogControl] = []

    # --- Household-size (Task 7) -- 100m only ---
    for geography in (GEO_100M,):
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

    # --- Household-type / Lebensform Familie (Task 8) -- 100m only, no einpersonen ---
    for geography in (GEO_100M,):
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
    """Build the Tier-2 catalog (LOSSLESS-reduced): tenure + building_type controls.

    Reduced to ZENSUS100m only (the 1km duplicates aggregate exactly from the 100m
    controls under nested sub_balancing, so they add no independent constraint):

    Tenure: 2 categories (owner, renter) x 1 geography (100m) = 2 controls.
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
    1 geography (100m) = 3 controls.  MiD-only (ENTD has no building-type flag).

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
        5 Tier-2 controls: 2 tenure + 3 building_type (all at ZENSUS100m).
    """
    catalog: List[CatalogControl] = []

    # --- Tenure (existing) -- 100m only ---
    for geography in (GEO_100M,):
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

    # --- Building type (multi-column census_source) -- 100m only ---
    for geography in (GEO_100M,):
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


# Tier-3 person-level controls at KREIS geography (MiD-only; ENTD=None -> dropped).
# census_source names the imported Kreis-table columns (the cleancensus kreis_* tables);
# the multi-column classes are materialised via build_aggregation_map at sourcing time.
# Tier-3 expressions use RAW MiD codes (P_TAET / bildung1 / bildung2) evaluated on the
# seed persons -- exactly like tier2's raw households.H_MIETE / haustyp. The seed-build
# (mid.load_mid_seed / project_completed_seed) retains these raw cols via
# select_seed_columns(extra_person_cols=...), so no seed-side derivation/imputation is
# needed. The code groupings + census_source class sums are confirmed vs the MiD 2023
# Codeplan B1 (sheet Personen) and mirror the maps in attributes.py
# (SCHULABS_BY_BILDUNG1 / BERUFABS_BY_BILDUNG2). employed uses P_TAET ∈ {1,2,3,4,6,8}
# = MiD `erwerb` definition (incl. 8 Auszubildende; excl. 5 Elternzeit and 7 FSJ/
# freiwilliger Dienst): 7 is not an ILO-Erwerbstätigkeit (Taschengeld, no wage) so it
# must not count toward census __11 (ILO Erwerbstätige). schulabschluss: bildung1 2->low /
# 3->mid / 4->high; census low = __21+__22 only (POS ≈0 in West-German BS). __3 (ohne
# allgemeinbildenden Abschluss) is intentionally DROPPED from low so both sides measure
# the same completed-qualification universe -- MiD bildung1 cannot cleanly isolate
# "ohne" (code 1 mixes it with all <15 kids + current pupils). beruflabschluss: bildung2
# 1->vocational / {2,3}->tertiary (both carry a Hochschulabschluss) / 5->none; code 4
# (anderer) has no Zensus pendant and is not counted. The derived schulabschluss/
# beruflabschluss attributes are output-only (assembly, validation), not used here.
_TIER3_ENTRIES: Sequence[tuple] = (
    # (name, census_source cols, mid expression over the RAW seed-persons cols)
    ("employed", ("ERWERBSTAT_KURZ_STP__11",), "(persons.P_TAET.isin([1, 2, 3, 4, 6, 8]))"),
    ("schulabschluss_low",
     ("SCHULABS_STP__21", "SCHULABS_STP__22"),
     "(persons.bildung1 == 2)"),
    ("schulabschluss_mid", ("SCHULABS_STP__23",), "(persons.bildung1 == 3)"),
    ("schulabschluss_high", ("SCHULABS_STP__24",), "(persons.bildung1 == 4)"),
    ("beruflabschluss_none", ("BERUFABS_AUSF_STP__2",), "(persons.bildung2 == 5)"),
    ("beruflabschluss_vocational",
     ("BERUFABS_AUSF_STP__11", "BERUFABS_AUSF_STP__12", "BERUFABS_AUSF_STP__13"),
     "(persons.bildung2 == 1)"),
    ("beruflabschluss_tertiary",
     ("BERUFABS_AUSF_STP__14", "BERUFABS_AUSF_STP__15", "BERUFABS_AUSF_STP__16", "BERUFABS_AUSF_STP__17"),
     "(persons.bildung2.isin([2, 3]))"),
)


def tier3_controls() -> List[CatalogControl]:
    """Tier-3: employment + education controls at KREIS geography (MiD-only).

    7 controls (1 employed + 3 schulabschluss + 3 beruflabschluss), each at GEO_KREIS,
    persons table. ENTD cannot express them (entd=None -> dropped by controls_for_seed).
    Multi-column census_source classes are materialised via build_aggregation_map.
    """
    catalog: List[CatalogControl] = []
    for name, source_cols, mid_expr in _TIER3_ENTRIES:
        catalog.append(
            CatalogControl(
                name=name,
                geography=GEO_KREIS,
                seed_table=SEED_TABLE_PERSONS,
                importance=1000,
                census_source=source_cols,
                seed_expressions={"mid": mid_expr, "entd": None},
            )
        )
    return catalog


def employment_grid_controls(importance: int = 1000) -> List[CatalogControl]:
    """Ten 100m age-group×sex employment controls (5 groups × 2 sexes). MiD-only.

    Age groups: 16_29 (16-29), 30_39 (30-39), 40_49 (40-49), 50_59 (50-59), 60plus (60+).
    census_source is the per-cell target column injected by employment_grid into the
    cells frame (EMPLOYED_{M,F}_{16_29,30_39,40_49,50_59,60plus}_agg).
    ENTD cannot express P_TAET -> None.
    Employed definition: P_TAET ∈ {1,2,3,4,6,8} (MiD erwerb; incl. Azubi, excl. Elternzeit/FSJ).
    """
    groups = {
        "16_29": "(persons.HP_ALTER>15)&(persons.HP_ALTER<30)",
        "30_39": "(persons.HP_ALTER>29)&(persons.HP_ALTER<40)",
        "40_49": "(persons.HP_ALTER>39)&(persons.HP_ALTER<50)",
        "50_59": "(persons.HP_ALTER>49)&(persons.HP_ALTER<60)",
        "60plus": "(persons.HP_ALTER>59)",
    }
    out: List[CatalogControl] = []
    for prefix, sex in (("M", 1), ("F", 2)):
        for g, ageclause in groups.items():
            name = f"EMPLOYED_{prefix}_{g}_agg"
            expr = f"(persons.P_TAET.isin([1, 2, 3, 4, 6, 8]))&(persons.HP_SEX=={sex})&{ageclause}"
            out.append(
                CatalogControl(
                    name=name,
                    geography=GEO_100M,
                    seed_table=SEED_TABLE_PERSONS,
                    importance=importance,
                    census_source=(name,),
                    seed_expressions={"mid": expr, "entd": None},
                )
            )
    return out


# Generic per-Kreis attribute controls (S1a, issue #109 follow-up). A registered
# KreisAttributeControl (kreis_attribute_control.REGISTRY) yields one GEO_KREIS control per
# category: expression f"({table}.{seed_column} {predicate})" over the household/person seed
# table; census_source = the derived per-Kreis count column (name_{label}) that stage.py injects
# from the committed target CSV. MiD-only (ENTD cannot express the donor columns -> None, dropped
# by controls_for_seed).
def attribute_kreis_controls(controls, importance: int = 1000) -> List[CatalogControl]:
    """Build GEO_KREIS controls for a list of KreisAttributeControl.

    When an entry carries ``min_age`` (not ``None``), its rendered MiD expression ANDs
    in a person-age clause ``(persons.HP_ALTER >= min_age)`` so the control's universe
    matches the age-restricted base its committed target's shares are reported over
    (e.g. employment_status is MiD P9 / SrV 14+, feature #172 task 4) -- without this,
    the seed attribute assigned to ALL persons (incl. <14) would let those persons
    distort the category counts (the #97 universe trap). Entries with ``min_age=None``
    (every pre-existing REGISTRY entry) are unaffected: their rendered expression is
    byte-identical to before this field existed.
    """
    from braunschweig.popsim.kreis_attribute_control import control_columns as _cols
    table_of = {"household": SEED_TABLE_HOUSEHOLDS, "person": SEED_TABLE_PERSONS}
    out: List[CatalogControl] = []
    for ctl in controls:
        table = table_of[ctl.level]
        for (label, predicate), col in zip(ctl.categories, _cols(ctl)):
            expr = f"({table}.{ctl.seed_column} {predicate})"
            if getattr(ctl, "min_age", None) is not None:
                expr = f"{expr} & ({table}.HP_ALTER >= {ctl.min_age})"
            out.append(
                CatalogControl(
                    name=col,
                    geography=GEO_KREIS,
                    seed_table=table,
                    importance=importance,
                    census_source=(col,),
                    seed_expressions={"mid": expr, "entd": None},
                )
            )
    return out


def status_kreis_controls(importance: int = 1000) -> List[CatalogControl]:
    """Five economic_status x Kreis household controls (very_low..very_high).

    Delegates to the generic registry factory (S1a); output byte-identical to the Phase 2 L1
    controls. oek_status codes 1..5 map to very_low..very_high; census_source == control name,
    so folders.build_kreis_control_totals sums a single identity column that stage.py injects
    from the H4-derived count table.
    """
    from braunschweig.popsim.kreis_attribute_control import REGISTRY
    econ = [c for c in REGISTRY if c.name == "economic_status"]
    return attribute_kreis_controls(econ, importance=importance)


def full_catalog(include_tiers: Sequence[str] = ("tier0",), *, include_employment_grid: bool = False,
                 include_status_kreis: bool = False,
                 kreis_control_names: Sequence[str] = ()) -> List[CatalogControl]:
    """Build the combined catalog for the requested tier set.

    Parameters
    ----------
    include_tiers:
        Tiers to include (LOSSLESS-reduced catalog). ``"tier0"`` is always included
        when present: 21 backbone Zensus controls (100m HH_TOTAL + 18 age x sex bands;
        1km HH_TOTAL + POP_TOTAL). ``"tier1"`` adds the 10 household-size (6) +
        household-type (4) controls at 100m. ``"tier2"`` adds the 5 tenure (2) +
        building_type (3) controls at 100m. ``"tier3"`` adds the 7 employment/education
        controls at KREIS geography (MiD-only; ENTD drops all via controls_for_seed).
        Full ``("tier0","tier1","tier2","tier3")`` = 21 + 10 + 5 + 7 = 43.

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
    if "tier3" in include_tiers:
        catalog.extend(tier3_controls())
    if include_employment_grid:
        catalog.extend(employment_grid_controls())
    # Generic per-Kreis attribute controls (S1c). ``kreis_control_names`` is the generalised
    # knob (a list of REGISTRY entry names to render as GEO_KREIS controls). ``include_status_kreis``
    # is kept as a backward-compat alias for ``kreis_control_names=("economic_status",)`` so
    # existing callers/tests stay byte-identical.
    _kreis_names = list(kreis_control_names)
    if include_status_kreis and "economic_status" not in _kreis_names:
        _kreis_names.append("economic_status")
    if _kreis_names:
        from braunschweig.popsim.kreis_attribute_control import REGISTRY as _KREIS_REGISTRY
        _by_name = {c.name: c for c in _KREIS_REGISTRY}
        _missing = [n for n in _kreis_names if n not in _by_name]
        if _missing:
            raise ValueError(
                f"full_catalog: unknown KREIS attribute control name(s) {_missing}; "
                f"known entries are {sorted(_by_name)}.")
        catalog.extend(attribute_kreis_controls([_by_name[n] for n in _kreis_names]))
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
