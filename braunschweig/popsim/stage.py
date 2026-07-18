"""synpp stage for the popsim_mid population producer.

Runs the validated popsim_mid chain (prepared cells -> control totals ->
complete-household MiD seed -> PopulationSim per 1 km batch -> merge) and returns
the merged expanded-household table (one row per synthetic household, located by
100 m cell). PopulationSim runs in its own uv environment as a subprocess
(``uv run populationsim``), so the heavy synthesizer stays out of the eqasim
process.

This stage is the popsim_mid *producer*; the selector
(``braunschweig.population.selector``) routes ``population.method == popsim_mid``
here. After the merge it expands the donor households into the full eqasim persons
frame (``braunschweig.popsim.assembly.build_persons``: join the MiD donor persons,
map demographics + attributes, validate against ``braunschweig.population.schema``)
and returns that. The home-location placement (``braunschweig.popsim.handoff``) and
the activity-chain construction (``braunschweig.popsim.trips``) are layered on top
when feeding the spatial / trip stages.

Config keys (all under ``braunschweig.population.popsim.*``); defaults point at the
canonical local-only layout (docs/population/DATA_LAYOUT.md) and the committed
popsimprep PopulationSim config.

The active donor source is controlled by ``braunschweig.population.popsim.source``
(default ``"mid"``). The default ``"mid"`` path is byte-identical to the pre-source
implementation. Switching to ``"entd"`` (Phase 2) will route the seed build, donor
loading, and attribute mapping through the ENTD adapter without changing the
structural PopulationSim orchestration.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
from pathlib import Path

import numpy as np
import pandas as pd

from braunschweig.data.census.household_size import kreis_household_stats
from braunschweig.data.mid.income_by_size import (
    load_income_by_size_bundesland,
    load_income_by_size_raumtyp,
)
from braunschweig.data.mid.income_by_status import (
    load_income_by_status_bundesland,
    load_income_by_status_raumtyp,
)
from braunschweig.popsim import assembly
from braunschweig.popsim import batch
from braunschweig.popsim import income as _income
from braunschweig.popsim import income_kreis_control as _kic
from braunschweig.popsim import income_spatial_tilt as _ist
from braunschweig.popsim import plausibility as _plausibility
from braunschweig.popsim import mid
from braunschweig.popsim import prepared_cells
from braunschweig.popsim import sources
from braunschweig.popsim.income import HIGH_INCOME_THRESHOLD_EUR

logger = logging.getLogger(__name__)

# Config keys.
KEY_CELLS = "braunschweig.population.popsim.cells_100m_path"
KEY_MID = "braunschweig.population.popsim.mid_raw_path"
KEY_CONTROLS = "braunschweig.population.popsim.controls_path"
KEY_SETTINGS = "braunschweig.population.popsim.settings_path"
KEY_LOGGING = "braunschweig.population.popsim.logging_path"
KEY_POPSIMPREP = "braunschweig.population.popsim.popsimprep_dir"
KEY_UV = "braunschweig.population.popsim.uv_path"
KEY_MAX_CELLS = "braunschweig.population.popsim.max_cells"
KEY_WORKERS = "braunschweig.population.popsim.num_workers"
KEY_WORK_DIR = "braunschweig.population.popsim.work_dir"
# Hard per-batch PopulationSim wall-clock limit (seconds). A batch exceeding this is
# killed and flagged "failed (timeout)". Heavy control sets (tier1/2 + stratify) make
# big batches slow; raise this so they finish + converge cleanly instead of being killed.
KEY_BATCH_TIMEOUT = "braunschweig.population.popsim.batch_timeout_s"
# Delete each batch's dead PopulationSim checkpoint store (output/pipeline.h5) once the
# batch is VERIFIED complete (issue #153: ~15 GB/batch at full donor pool would overflow
# the run server's disk mid-campaign). Default ON (project rule: new features default on).
# Set False to keep the stores, e.g. for balancer forensics on a small run.
KEY_CLEANUP_H5 = "braunschweig.population.popsim.cleanup_batch_pipeline"
KEY_KREISE = "braunschweig.political_prefix"
# Donor source identifier: "mid" (default) or a future registered source name.
KEY_SOURCE = "braunschweig.population.popsim.source"
# RegioStaR donor stratification (Phase 4B). Default ON (project rule: new features
# default on); set False for the byte-identical pre-4B path (full seed per batch,
# still supported + unit-tested).
KEY_STRATIFY = "braunschweig.population.popsim.stratify_regiostar"
# Member completion (decision D3, mid source only): fill member-incomplete MiD
# donor households by mirror-household sampling, in ONE pass on the attribute
# donor tables that feeds BOTH the PopulationSim seed and the expansion.
# Default ON (project rule: new features default on); False reproduces the
# legacy load_mid_seed + load_donor path byte-identically.
KEY_COMPLETE_MEMBERS = "braunschweig.population.popsim.complete_members"
# Controls source: "csv" (default, byte-identical, reads the external hand-edited
# file at KEY_CONTROLS) or "catalog" (renders from the typed control catalog via
# control_spec; Task 5 of the control-catalog plan).
KEY_CONTROLS_SOURCE = "braunschweig.population.popsim.controls_source"
# Control tiers: comma-separated tier names included when controls_source="catalog".
# Default "tier0" = byte-identical to the pre-Task-7 baseline.
KEY_CONTROL_TIERS = "braunschweig.population.popsim.control_tiers"
# Tier-3 KREIS controls: directory holding the imported cleancensus kreis_* tables
# (kreis_erwerbsstatus/schulabschluss/berufl_abschluss.parquet). Loaded only when
# "tier3" is among control_tiers (catalog source); ignored otherwise.
KEY_KREIS_CONTROLS = "braunschweig.population.popsim.kreis_controls_dir"
# Employment grid control (Task 5): when "on", activates the ten age-group x sex-resolved
# 100m employment controls (EMPLOYED_{M,F}_{16_29,30_39,40_49,50_59,60plus}_agg). The targets are
# computed per cell from the Zensus 2000S-2001 employment-by-age SHAPE rescaled per
# Kreis x sex x group to the census Erwerbstaetige Kreis level
# (braunschweig.popsim.employment_grid). Default "off" = byte-identical to today.
KEY_EMPLOYMENT_GRID = "braunschweig.population.popsim.employment_grid"
# PopulationSim per-control importance profile name (see control_spec.IMPORTANCE_PROFILES).
KEY_IMPORTANCE_PROFILE = "braunschweig.population.popsim.importance_profile"
# Seed reporting-day filter: which MiD kernwo values to KEEP in the PopulationSim
# seed. "default" -> (1,2,3) Mo-Fr (legacy: weekend / kernwo=4 households dropped).
# "off"/"all" -> keep ALL reporting days (no day filter). The reporting day is a
# trip-modelling concern, irrelevant to the population's employment/education/HH
# composition; "off" enlarges the donor pool (reduces IPU weight concentration).
KEY_SEED_DAY_FILTER = "braunschweig.population.popsim.seed_day_filter"
# Spatial income tilt (Nettokaltmiete GAMMA layer): default ON per project rule.
# When ON, applies a within-Kreis income redistribution scaled by the per-cell
# net cold rent index (renters) or Eigentümerquote index (owners), preserving the
# per-Kreis income mean exactly. When OFF, the income frame is unchanged (byte-identical).
KEY_INCOME_TILT = "braunschweig.population.popsim.income_spatial_tilt"
KEY_INCOME_TILT_BETA = "braunschweig.population.popsim.income_tilt_beta"
KEY_INCOME_TILT_CLIP = "braunschweig.population.popsim.income_tilt_clip"

# Tilt-specific cell columns (cleaned parquet names; see prepared_cells.clean_col_name):
#   raw: "durchschnMieteQM_Durchschn_Nettokaltmiete_100m-Gitter"
#     -> clean: "durchschnMieteQM_Durchschn_Nettokaltmiete_100m_Gitter"
#   raw: "Eigentuemerquote_Eigentuemerquote_100m-Gitter"
#     -> clean: "Eigentuemerquote_Eigentuemerquote_100m_Gitter"
# Suppression-ADJUSTED household totals are the correct tilt weight: the raw cell
# totals suppress small cells (NaN), making them 0-weight and biasing the Kreis-mean
# normalization toward large dense cells only. The _adj column fills suppressed cells
# with the cleancensus imputed estimates so every cell carries a proper weight.
_TILT_RENT_COL = "durchschnMieteQM_Durchschn_Nettokaltmiete_100m_Gitter"
_TILT_QUOTE_COL = "Eigentuemerquote_Eigentuemerquote_100m_Gitter"
_TILT_HH_COL = "Insgesamt_Haushalte_Groesse_des_privaten_Haushalts_100m_Gitter_adj"
_TILT_ARS_COL = "RegionalSchlussel_ARS"


def tilt_extra_load_columns(enabled: bool, load_cols: list[str]) -> list[str]:
    """Extend the parquet load column list with the income-tilt cell columns.

    Issue #136: the tilt columns are fetched in the SINGLE ``load_control_cells``
    read instead of a second national parquet scan. When ``enabled`` is False the
    input list is returned as an unchanged copy (OFF path byte-identical);
    ``load_control_cells`` silently skips columns absent from the parquet, exactly
    like the old raw-name mapping did.
    """
    out = list(load_cols)
    if not enabled:
        return out
    for column in (_TILT_RENT_COL, _TILT_QUOTE_COL, _TILT_HH_COL):
        if column not in out:
            out.append(column)
    return out


def extract_tilt_cells(cells: pd.DataFrame) -> pd.DataFrame:
    """Build the income-tilt working frame from the already-loaded cells frame.

    Selects the cell id + the tilt columns (rent, Eigentuemerquote, HH weight,
    ARS) that are present; absent optional columns stay absent, matching the old
    raw-parquet mapping (the downstream code then warns and uses a neutral
    index / uniform weight). The cells frame is already ZGB-filtered, so no
    row filtering is needed (this replaces a full national-row parquet re-read).
    """
    if "ZENSUS100m" not in cells.columns:
        raise ValueError(
            "[popsim.stage] cells frame carries no 'ZENSUS100m' column; cannot "
            "build the income-tilt cell frame from it."
        )
    columns = ["ZENSUS100m"] + [
        c for c in (_TILT_RENT_COL, _TILT_QUOTE_COL, _TILT_HH_COL, _TILT_ARS_COL)
        if c in cells.columns
    ]
    return cells[columns].copy()
# Kreis-Income-Control: real MiD income draw + max-entropy per-Kreis calibration.
# Default ON (project rule). When ON it OVERWRITES the apply_inkar_income_eur output
# (build_persons) with a real continuous draw reshaped to the per-Kreis INKAR target.
# When OFF, build_persons' midpoint x INKAR_scale output is left byte-identical.
KEY_INCOME_KC = "braunschweig.population.popsim.income_kreis_control"
KEY_INCOME_KC_METHOD = "braunschweig.population.popsim.income_draw_method"
KEY_INCOME_KC_HHSIZE = "braunschweig.population.popsim.income_kreis_control_hhsize_correct"
KEY_INCOME_KC_PARETO = "braunschweig.population.popsim.income_open_top_pareto"
KEY_INCOME_KC_PARETO_ALPHA = "braunschweig.population.popsim.income_open_top_pareto_alpha"
# placement_income (L2, issue #108): donor keeps its OWN MiD income; the per-Kreis
# INKAR relativity is approached by signature-preserving donor reallocation after the
# popsim merge. Default ON (project rule). ON overrides income_kreis_control AND
# income_spatial_tilt (logged); OFF is byte-identical to the legacy path.
KEY_PLACEMENT_INCOME = "braunschweig.population.popsim.placement_income"
# economic_status x Kreis control (Level 1, issue #109). Default "on" (project rule:
# new features default on). "off" -> no status control + seed schema unchanged (byte-
# identical). MiD-only (oek_status has no ENTD pendant); ignored for source="entd".
KEY_STATUS_KREIS_CONTROL = "braunschweig.population.popsim.status_kreis_control"
# Dirichlet shrinkage of the per-Kreis H4 status target toward the ZGB aggregate, in
# pseudo-households. Default 0.0 = raw per-Kreis H4 (no shrinkage). Range: >= 0.
KEY_STATUS_KREIS_SHRINKAGE_N = "braunschweig.population.popsim.status_kreis_shrinkage_n"
# Additional per-Kreis attribute controls (S1c, issue #109 follow-up), each driven by a
# committed blended target (target2026_*) and individually toggleable; MiD-only (their
# seed columns have no ENTD pendant). "off" for a given attribute drops its control + its
# seed column (byte-identical to today for that attribute). The blended targets are FINAL
# (consumed with prior_n = 0). number_of_cars / number_of_bicycles / has_ebike all default
# "on" (project rule: new features default on) -- has_ebike was blocked pending server
# verification of the MiD household e-bike column; that verification landed 2026-07-08
# (H_ANZPED, see KEY_EBIKE_SEED_COLUMN), so it is now wired and defaults on like the rest.
KEY_CARS_KREIS_CONTROL = "braunschweig.population.popsim.number_of_cars_kreis_control"
KEY_BIKES_KREIS_CONTROL = "braunschweig.population.popsim.number_of_bicycles_kreis_control"
KEY_EBIKE_KREIS_CONTROL = "braunschweig.population.popsim.has_ebike_kreis_control"
# trip_class x Kreis control (first PERSON-level KREIS attribute control, issue #116
# follow-on, 2026-07-08): steers the per-Kreis distribution of trips-on-the-reporting-day
# (0 / 1-2 / 3-4 / 5+), targeted at the committed SrV 2023 aggregate. Default "on"
# (project rule: new features default on). "off" drops its control + seed column
# (byte-identical for that attribute). MiD-only (its seed column, anzwege1, has no ENTD
# pendant); ignored for source="entd".
KEY_TRIPS_KREIS_CONTROL = "braunschweig.population.popsim.trip_class_kreis_control"
# employment_status x Kreis control (feature #172 task 4, second PERSON-level entry):
# steers the per-Kreis distribution of the seven MiD P_BKAT employment-extent classes
# (vollzeit/teilzeit/geringfuegig/sonstiges/erwerbstaetig_unspec/in_ausbildung/
# nicht_erwerbstaetig) to the committed MiD-P9 x SrV-V_ERW blended target. Default "on"
# (project rule: new features default on). "off" drops its control + seed column
# (byte-identical for that attribute). MiD-only (employment_status has no ENTD pendant);
# ignored for source="entd". Its committed target + seed universe are BOTH restricted to
# age >= 14 (kreis_attribute_control.REGISTRY entry min_age=14) -- see
# person_total_by_kreis_min_age below.
KEY_EMPLOYMENT_STATUS_KREIS_CONTROL = "braunschweig.population.popsim.employment_status_kreis_control"
# Name of the MiD household e-bike column feeding the has_ebike control. Default
# "H_ANZPED" (Anzahl Pedelecs, 0..10, missing code 99) -- verified 2026-07-08 against the
# server MiD B1 microdata (see braunschweig.popsim.attributes.map_has_ebike). Kept
# configurable in case a future MiD delivery renames the column (no silent fallback if
# has_ebike is active and this resolves empty).
KEY_EBIKE_SEED_COLUMN = "braunschweig.population.popsim.ebike_seed_column"
# Weekend-plan match: include weekend-surveyed MiD households in the seed by
# relaxing the day filter to ALL_REPORTING_KERNWO and remapping their
# source_H_ID/source_P_ID to a matched weekday household.  Default ON
# (project rule: new features default on).  When OFF, the donor build is
# byte-identical to today (weekday (1,2,3) filter only, no remap).
KEY_WEEKEND_PLAN_MATCH = "braunschweig.population.popsim.weekend_plan_match"


def _resolve_source(source_name: str) -> sources.PopsimSource:
    """Return a PopsimSource adapter for the given source name.

    This thin helper is factored out of ``execute`` so it can be called and
    tested independently without running PopulationSim.

    Parameters
    ----------
    source_name:
        Short lowercase source identifier, e.g. ``"mid"``.  Passed through to
        :func:`braunschweig.popsim.sources.get_source`.

    Returns
    -------
    PopsimSource
        A fresh adapter instance for ``source_name``.

    Raises
    ------
    NotImplementedError
        If ``source_name`` is planned-but-not-yet-implemented (e.g. ``"entd"``).
    ValueError
        If ``source_name`` is not a known or planned source name.
    """
    return sources.get_source(source_name)


# Config toggle per KREIS attribute control (kreis_attribute_control.REGISTRY entry).
# economic_status keeps its historical key; the S1c additions get their own keys.
_KREIS_CONTROL_TOGGLE_KEY = {
    "economic_status": KEY_STATUS_KREIS_CONTROL,
    "number_of_cars": KEY_CARS_KREIS_CONTROL,
    "number_of_bicycles": KEY_BIKES_KREIS_CONTROL,
    "has_ebike": KEY_EBIKE_KREIS_CONTROL,
    "trip_class": KEY_TRIPS_KREIS_CONTROL,
    "employment_status": KEY_EMPLOYMENT_STATUS_KREIS_CONTROL,
}

# Per-entry default for its toggle (project rule: new features default "on"). has_ebike
# was blocked pending server verification of the MiD household e-bike column (issue
# #116); that verification landed 2026-07-08 (H_ANZPED, see KEY_EBIKE_SEED_COLUMN), and
# both seed paths (load_mid_seed and project_completed_seed) now derive it, so it
# defaults "on" like the other three entries.
_KREIS_CONTROL_DEFAULT = {
    "economic_status": "on",
    "number_of_cars": "on",
    "number_of_bicycles": "on",
    "has_ebike": "on",
    "trip_class": "on",
    "employment_status": "on",
}


def active_kreis_entries(context, source_name):
    """Return the KREIS attribute-control REGISTRY entries active for this run.

    An entry is active when its per-attribute toggle resolves to "on" AND the donor
    source is MiD. All KREIS attribute controls are MiD-only (their seed columns have no
    ENTD pendant), so the list is empty for any non-"mid" source. Each toggle defaults per
    ``_KREIS_CONTROL_DEFAULT`` (project rule: new features default on) -- all six entries
    (economic_status, number_of_cars, number_of_bicycles, has_ebike, trip_class,
    employment_status) default "on". The has_ebike source column (H_ANZPED) was
    server-verified 2026-07-08 (issue #116). trip_class (2026-07-08 follow-on) and
    employment_status (feature #172 task 4) are PERSON-level entries; each is wired on
    both seed paths (its per-Kreis target partitions the PERSON total, not the household
    total -- see the KREIS block in execute()). employment_status additionally restricts
    that PERSON total to age >= 14 (its REGISTRY entry's min_age), see
    person_total_by_kreis_min_age.

    Called at EXECUTE time: synpp's ``ExecuteContext.config(key)`` takes NO default
    argument (a positional default raises ``TypeError``; the same pitfall was fixed for
    home_cell's ``KEY_HOME_MATCHING`` before). The per-entry defaults are therefore
    declared once in :func:`configure` (``context.config(KEY, default)`` on the
    ConfigContext) and this function reads the RESOLVED value by key only.

    Returns the entries in REGISTRY order (economic_status first), so downstream
    catalog rendering and count-table merges are deterministic.
    """
    from braunschweig.popsim import kreis_attribute_control as _kac
    if source_name != "mid":
        return []
    active = []
    for entry in _kac.REGISTRY:
        toggle_key = _KREIS_CONTROL_TOGGLE_KEY.get(entry.name)
        if toggle_key is None:
            raise ValueError(
                f"active_kreis_entries: no config toggle registered for REGISTRY entry "
                f"{entry.name!r}; add it to _KREIS_CONTROL_TOGGLE_KEY.")
        if str(context.config(toggle_key)).strip().lower() == "on":
            active.append(entry)
    return active


def build_controls_df(*, controls_source="csv", controls_path=None, seed="mid", tiers=("tier0",),
                      employment_grid=False, kreis_control_names=(), status_kreis=False,
                      importance_profile="uniform"):
    """Return the PopulationSim controls.csv frame.

    controls_source="csv": read the external hand-edited file at controls_path (today's
    behaviour, byte-identical). controls_source="catalog": render the seed-filtered
    catalog via control_spec (the new source of truth).

    tiers: tuple of tier names to include when controls_source="catalog". Default
    ("tier0",) reproduces the pre-Task-7 baseline byte-identically.

    employment_grid: when True (catalog source only), append the six age-group x
    sex-resolved 100m employment controls (EMPLOYED_{M,F}_{young,prime,old}_agg) to the
    catalog. Default False = byte-identical to the pre-employment-grid catalog.

    kreis_control_names: names of the active KREIS attribute-control REGISTRY entries
    (kreis_attribute_control.REGISTRY) to render as GEO_KREIS household controls (catalog
    source only), e.g. ("economic_status", "number_of_cars"). Default () = none appended
    (byte-identical). The hand-edited CSV source cannot express these controls, so a
    non-empty kreis_control_names with controls_source="csv" is a fail-fast error (no
    silent drop of a requested control).

    status_kreis: backward-compat alias for kreis_control_names=("economic_status",).
    Default False. Kept so existing callers/tests stay byte-identical.

    importance_profile: a key of control_spec.IMPORTANCE_PROFILES selecting per-group
    PopulationSim importance weights. Default "uniform" leaves every control's importance
    untouched (byte-identical). Applied to BOTH sources after the frame is built.
    """
    from braunschweig.popsim import control_spec as cs
    # status_kreis is the historical alias for the economic_status entry.
    effective_kreis_names = list(kreis_control_names)
    if status_kreis and "economic_status" not in effective_kreis_names:
        effective_kreis_names.append("economic_status")
    if controls_source == "csv":
        if effective_kreis_names:
            raise ValueError(
                "KREIS attribute controls require controls_source='catalog'; the hand-edited "
                f"controls.csv cannot express {effective_kreis_names}.")
        df = pd.read_csv(controls_path, sep=";")
    elif controls_source == "catalog":
        catalog = cs.full_catalog(include_tiers=tiers, include_employment_grid=employment_grid,
                                  kreis_control_names=effective_kreis_names)
        df = cs.render_catalog_csv(cs.controls_for_seed(catalog, seed), seed)
    else:
        raise ValueError(f"unknown controls_source {controls_source!r}")
    return cs.apply_importance_profile(df, importance_profile)


def _kreis_controls_map(controls):
    """Map each KREIS control to its census_source columns, keyed by the column name
    the control_totals_KREIS.csv must carry.

    The key is the control_field ``f"{name}_{geography}"`` (e.g. ``employed_KREIS``) --
    the SAME name render_catalog_csv writes into controls.csv, and what PopulationSim
    looks up in the control-totals table. The grid path achieves this via the geography
    column suffix (build_control_totals); the KREIS path must mirror it here, else
    PopulationSim errors ``<field> not in index``.
    """
    return {f"{c.name}_{c.geography}": tuple(c.census_source) for c in controls}


def person_band_census_columns():
    """The 18 age-x-sex 100m band census-source column names (tier0 backbone).

    Person-level KREIS attribute controls (e.g. ``trip_class``) partition the per-Kreis
    PERSON total, not the household total. That person total is the per-Kreis sum over ALL
    18 age-x-sex 100m band controls of the tier0 backbone (9 ten-year bands x {male,
    female}), whose census-source column names are derived HERE from the backbone catalog
    rather than hardcoded, so a backbone change (renamed/added bands) propagates
    automatically instead of drifting out of sync.
    """
    from braunschweig.popsim import control_spec as cs
    cols: list[str] = []
    for control in cs.tier0_backbone_catalog():
        if control.geography == cs.GEO_100M and control.seed_table == cs.SEED_TABLE_PERSONS:
            cols.extend(control.census_source)
    return tuple(cols)


def person_total_by_kreis(cells, kreis_by_row):
    """Per-Kreis PERSON total = per-Kreis sum over the 18 age-x-sex 100m band columns.

    Parameters
    ----------
    cells:
        The loaded (ZGB-filtered) cells frame; must carry all 18 band census-source
        columns (:func:`person_band_census_columns`).
    kreis_by_row:
        A Series aligned to ``cells`` giving the 5-digit Kreis code per row (e.g.
        ``cells[ARS][:5]``).

    Returns
    -------
    dict[str, float]
        ``{ars5: person_total}`` summed over the 18 band columns per Kreis.

    Raises
    ------
    RuntimeError
        If any of the 18 band columns is absent from ``cells`` (no silent fallback:
        a person-level control cannot be constrained without the person totals).
    """
    band_cols = list(person_band_census_columns())
    missing = [c for c in band_cols if c not in cells.columns]
    if missing:
        raise RuntimeError(
            "person_total_by_kreis: a person-level KREIS control is ON but the age-x-sex "
            f"band columns {missing} are absent from the cells frame (has "
            f"{[c for c in band_cols if c in cells.columns]} of the 18 bands); cannot derive "
            "the per-Kreis PERSON total (no silent fallback).")
    return cells.groupby(kreis_by_row)[band_cols].sum().sum(axis=1).to_dict()


def person_total_by_kreis_min_age(cells, kreis_by_row, min_age, *, single_year_max=100):
    """Per-Kreis PERSON total restricted to age >= ``min_age``.

    Sums the single-year ``{M,F}_AGE_<year>`` cell columns (the same age-SHAPE columns
    ``employment_grid`` reads; see its ``_group_cell_pop``) for ``year`` in
    ``[min_age, single_year_max]``, grouped by Kreis. Unlike :func:`person_total_by_kreis`
    (which sums the 18 ten-year age x sex BAND columns), this uses the finer single-year
    columns so the total can be restricted to an arbitrary age boundary that does not
    align with a ten-year band edge -- e.g. "age >= 14" cannot be expressed as a sum of
    whole ``AGE_0_9`` / ``AGE_10_19`` bands.

    Used for KREIS attribute controls whose committed target's shares are reported over
    an age-restricted base (e.g. ``employment_status``: MiD P9 / SrV 14+, feature #172
    task 4). Without this restriction, persons below ``min_age`` would be counted into
    the per-Kreis total the category counts partition, silently distorting the target
    shares -- the same universe mismatch as the #97 bug.

    Parameters
    ----------
    cells:
        The loaded (ZGB-filtered) cells frame; expected to carry the single-year
        ``{M,F}_AGE_<year>`` columns.
    kreis_by_row:
        A Series aligned to ``cells`` giving the 5-digit Kreis code per row.
    min_age:
        Inclusive lower age bound in years.
    single_year_max:
        Inclusive upper age bound in years. Default 100, mirroring the single-year cap
        ``employment_grid.per_cell_employment_targets`` uses.

    Returns
    -------
    dict[str, float]
        ``{ars5: person_total}`` summed over the single-year columns with
        ``year >= min_age`` per Kreis.

    Raises
    ------
    RuntimeError
        If NO single-year ``{M,F}_AGE_<year>`` column for ``year >= min_age`` is present
        in ``cells`` at all (no silent fallback: a min_age-restricted person-level
        control cannot be constrained without at least some of its denominator columns).
    """
    cols = [
        f"{prefix}_AGE_{year}"
        for prefix in ("M", "F")
        for year in range(min_age, single_year_max + 1)
        if f"{prefix}_AGE_{year}" in cells.columns
    ]
    if not cols:
        raise RuntimeError(
            "person_total_by_kreis_min_age: a min_age-restricted person-level KREIS "
            f"control is ON (min_age={min_age}) but NO single-year age columns "
            f"{{M,F}}_AGE_<year> for year in [{min_age}, {single_year_max}] are present "
            "in the cells frame; cannot derive the per-Kreis age-restricted PERSON total "
            "(no silent fallback).")
    return cells.groupby(kreis_by_row)[cols].sum().sum(axis=1).to_dict()


def _grid_geography_controls(controls, cs):
    """Keep only controls sourced from the GRID parquet (ZENSUS100m / ZENSUS1km).

    The grid column load + aggregation read columns from the prepared 100m parquet.
    KREIS-geography Tier-3 controls (employment/education) carry census_source columns
    that live in the imported kreis table, NOT the grid -- including them here would
    request Kreis-census columns from the grid (spurious WARNINGs + bogus all-zero
    columns). Their KREIS totals are built separately via folders.build_kreis_control_totals.
    """
    grid_geos = (cs.GEO_100M, cs.GEO_1KM)
    return [c for c in controls if c.geography in grid_geos]


def build_aggregation_map(*, controls_source="csv", controls_path=None, seed="mid", tiers=("tier0",)):
    """Return the multi-column aggregation map for the active controls.

    For ``controls_source="catalog"``: derives the aggregation map from the typed
    catalog -- maps ``{derived_name: source_cols}`` for controls whose name is NOT
    a raw parquet column (i.e. multi-source / derived names like
    ``building_type_ein_zweifamilienhaus``).  Empty dict for tier0-only (all controls
    are single-source identity -> no aggregation needed).

    For ``controls_source="csv"``: returns an empty dict (the CSV hand-edited file
    does not carry census_source metadata; caller handles no aggregation).

    The returned map is consumed by
    :func:`braunschweig.popsim.prepared_cells.add_aggregated_controls`.
    """
    if controls_source != "catalog":
        return {}
    from braunschweig.popsim import control_spec as cs
    catalog = cs.full_catalog(include_tiers=tiers)
    active = _grid_geography_controls(cs.controls_for_seed(catalog, seed), cs)
    return cs.build_aggregation_map(active)


def build_source_columns(*, controls_source="csv", controls_df=None, seed="mid", tiers=("tier0",)):
    """Return the RAW census parquet columns to load for the active controls.

    For ``controls_source="catalog"``: returns the union of all census_source columns
    of the active seed-filtered controls.  For single-source identity controls
    (tier0) this equals the current ``control_base_columns`` output exactly.

    For ``controls_source="csv"``: returns None (caller uses control_base_columns as
    today).
    """
    if controls_source != "catalog":
        return None
    from braunschweig.popsim import control_spec as cs
    catalog = cs.full_catalog(include_tiers=tiers)
    active = _grid_geography_controls(cs.controls_for_seed(catalog, seed), cs)
    return cs.source_columns_union(active)


def configure(context):
    """Declare the popsim config keys.

    When the donor source is "entd" (popsim_open workflow), an additional
    dependency on ``data.hts.selected`` is registered so synpp wires the
    cleaned ENTD frames into the DAG.  For source="mid" (default) the stage
    depends only on the local config paths and no HTS stage is needed.

    The INKAR per-Kreis income scale (``braunschweig.data.inkar.household_income``)
    is registered as a dependency for BOTH sources so that ``household_income_eur``
    is scaled by the per-Kreis factor and ``high_income`` is set to the unified
    numeric threshold (>= 5000 EUR) for all popsim producers.
    """
    context.config(KEY_CELLS)
    context.config(KEY_MID)
    context.config(KEY_CONTROLS)
    context.config(KEY_SETTINGS)
    context.config(KEY_LOGGING)
    context.config(KEY_POPSIMPREP)
    context.config(KEY_UV)
    context.config(KEY_MAX_CELLS, 3000)
    context.config(KEY_WORKERS, 3)
    context.config(KEY_WORK_DIR)
    context.config(KEY_BATCH_TIMEOUT, batch.DEFAULT_POPSIM_TIMEOUT_S)
    # Cleanup of the dead per-batch pipeline.h5 checkpoint store (issue #153).
    context.config(KEY_CLEANUP_H5, True)
    context.config(KEY_KREISE)
    context.config(KEY_SOURCE, "mid")
    # Seeded attribute imputation in build_persons; declaring the key also makes
    # synpp invalidate the stage cache when the pipeline random_seed changes.
    context.config("random_seed")
    # RegioStaR donor stratification (Phase 4B): default OFF (user decision 2026-07-09).
    # The FULL donor pool is the core of the popsim_mid design: PopulationSim selects
    # suitable donor households by their mobility-relevant characteristics from the
    # ENTIRE national pool; restricting donors to the cell's RegioStaR class breaks
    # that (e.g. Salzgitter's mobility character is rural-like despite its statistical
    # urban class). Set True to opt back into the Phase 4B stratified path.
    context.config(KEY_STRATIFY, False)
    # Member completion (D3). Default True; False -> legacy path (see execute()).
    context.config(KEY_COMPLETE_MEMBERS, True)
    # Controls source (Task 5). Default "csv" = byte-identical to today's behaviour.
    context.config(KEY_CONTROLS_SOURCE, "csv")
    # Control tiers (Task 7). Default "tier0" = byte-identical to pre-Task-7 baseline.
    context.config(KEY_CONTROL_TIERS, "tier0")
    # Tier-3 KREIS controls directory. Default "" = not configured (no Tier-3).
    context.config(KEY_KREIS_CONTROLS, "")
    # Employment grid control (Task 5). Default "off" = byte-identical to today.
    context.config(KEY_EMPLOYMENT_GRID, "off")
    # PopulationSim per-control importance profile (control_spec.IMPORTANCE_PROFILES).
    # Default "uniform" = every control importance untouched (byte-identical). Set to
    # "optimized_2026_06_30" to apply the searched per-group weights (see control_spec).
    context.config(KEY_IMPORTANCE_PROFILE, "uniform")
    # When the employment grid control is ON, the per-cell employment targets are
    # built from the Zensus 2000S-2001 employment-by-age SHAPE (a committed reference
    # CSV under data_path; see braunschweig.popsim.zensus_employment_age) rescaled per
    # Kreis×sex×group to the census Erwerbstaetige Kreis level. No synpp stage
    # dependency is needed (the former GENESIS SvB stage dependency is gone). We only
    # ensure data_path is declared so the reference CSV can be located; this is a
    # no-op when data_path is already declared (KEY_INCOME_KC / housing_tenure).
    if str(context.config(KEY_EMPLOYMENT_GRID, "off")).strip().lower() == "on":
        context.config("data_path")
    # KREIS attribute controls (issue #109 + S1c). Each defaults per _KREIS_CONTROL_DEFAULT
    # (project rule: new features default on) -- all four entries default "on"; has_ebike's
    # source column (H_ANZPED) was server-verified 2026-07-08, so it is wired on both seed
    # paths (see mid.load_mid_seed / mid.project_completed_seed). When any is on, the
    # committed per-Kreis target CSV under data_path is needed, so ensure data_path is
    # declared (no-op if already). economic_status also carries a configurable Dirichlet
    # shrinkage prior; the S1c targets are FINAL (prior_n = 0, no key).
    context.config(KEY_STATUS_KREIS_CONTROL, _KREIS_CONTROL_DEFAULT["economic_status"])
    context.config(KEY_STATUS_KREIS_SHRINKAGE_N, 0.0)
    context.config(KEY_CARS_KREIS_CONTROL, _KREIS_CONTROL_DEFAULT["number_of_cars"])
    context.config(KEY_BIKES_KREIS_CONTROL, _KREIS_CONTROL_DEFAULT["number_of_bicycles"])
    context.config(KEY_EBIKE_KREIS_CONTROL, _KREIS_CONTROL_DEFAULT["has_ebike"])
    # trip_class (first PERSON-level KREIS control, 2026-07-08). Default "on"; its
    # committed SrV target lives under data_path (declared below via the any()-gate).
    context.config(KEY_TRIPS_KREIS_CONTROL, _KREIS_CONTROL_DEFAULT["trip_class"])
    # employment_status (second PERSON-level KREIS control, feature #172 task 4).
    # Default "on"; its committed MiD-P9 x SrV-V_ERW blended target lives under
    # data_path (declared below via the any()-gate). 14+ universe restriction (min_age)
    # is carried on the REGISTRY entry itself, not a separate config key.
    context.config(KEY_EMPLOYMENT_STATUS_KREIS_CONTROL, _KREIS_CONTROL_DEFAULT["employment_status"])
    # Default "H_ANZPED": the server-verified MiD household e-bike column (see
    # KEY_EBIKE_SEED_COLUMN above); configurable in case a future MiD delivery renames it.
    context.config(KEY_EBIKE_SEED_COLUMN, "H_ANZPED")
    _kreis_control_keys_and_defaults = (
        (KEY_STATUS_KREIS_CONTROL, _KREIS_CONTROL_DEFAULT["economic_status"]),
        (KEY_CARS_KREIS_CONTROL, _KREIS_CONTROL_DEFAULT["number_of_cars"]),
        (KEY_BIKES_KREIS_CONTROL, _KREIS_CONTROL_DEFAULT["number_of_bicycles"]),
        (KEY_EBIKE_KREIS_CONTROL, _KREIS_CONTROL_DEFAULT["has_ebike"]),
        (KEY_TRIPS_KREIS_CONTROL, _KREIS_CONTROL_DEFAULT["trip_class"]),
        (KEY_EMPLOYMENT_STATUS_KREIS_CONTROL, _KREIS_CONTROL_DEFAULT["employment_status"]),
    )
    if any(
        str(context.config(k, default)).strip().lower() == "on"
        for k, default in _kreis_control_keys_and_defaults
    ):
        context.config("data_path")
    # Seed reporting-day filter. Default "default" = legacy (1,2,3) Mo-Fr.
    context.config(KEY_SEED_DAY_FILTER, "default")
    # Spatial income tilt (Task 3). Default ON (project rule: features default on).
    # When OFF, the income frame is unchanged (byte-identical); no cells parquet
    # re-read occurs.
    context.config(KEY_INCOME_TILT, True)
    context.config(KEY_INCOME_TILT_BETA, 0.3)
    context.config(KEY_INCOME_TILT_CLIP, 0.30)
    # Kreis-Income-Control (default ON; OFF = byte-identical midpoint x INKAR_scale).
    context.config(KEY_INCOME_KC, True)
    context.config(KEY_INCOME_KC_METHOD, "combined")
    context.config(KEY_INCOME_KC_HHSIZE, True)
    context.config(KEY_INCOME_KC_PARETO, True)
    context.config(KEY_INCOME_KC_PARETO_ALPHA, 3.0)
    # placement_income (L2, issue #108). Default ON (project rule). ON overrides both
    # income_kreis_control and income_spatial_tilt (logged); OFF is byte-identical.
    # MiD-only (needs the hheink_gr1 donor income); inactive for source="entd".
    context.config(KEY_PLACEMENT_INCOME, True)
    # Weekend-plan match (default ON; OFF = byte-identical to pre-feature donor build).
    context.config(KEY_WEEKEND_PLAN_MATCH, True)
    if context.config(KEY_INCOME_KC, True):
        context.config("data_path")  # MiD income tables + Zensus household file
        context.config("braunschweig.zensus_households_path",
                       "braunschweig/5000H-2001_de_flat.csv")

    # INKAR per-Kreis household income scale: used by both popsim_mid and popsim_open
    # to apply the same income scaling as the IPF/enriched path.
    context.stage("braunschweig.data.inkar.household_income", alias="inkar_income")

    # housing_tenure parity (legacy enriched feature, parity gap P2): sample the
    # completeness attribute per household from P(tenure | income_bracket,
    # raumtyp) using the SAME _apply_housing_tenure implementation as the
    # IPF/enriched path (no duplicated logic; dedicated RNG offset +83947).
    # Default ON; False -> column absent, byte-identical to the pre-parity output.
    context.config("synthesise_housing_tenure", True)
    if context.config("synthesise_housing_tenure", True):
        context.config("data_path")
        context.stage("braunschweig.data.bbsr.regiostar", alias="regiostar_tenure")

    source_name = context.config(KEY_SOURCE, "mid")
    # Member completion (D3) runs for the MiD source only. When active, the donor
    # build (member completion + weekend-plan match) is delegated to the cached
    # braunschweig.popsim.completed_donor stage so it runs ONCE across all runs
    # (it depends only on mid/seed/day_filter/weekend, not controls/sampling/work_dir).
    if source_name == "mid" and bool(context.config(KEY_COMPLETE_MEMBERS, True)):
        context.stage("braunschweig.popsim.completed_donor", alias="completed_donor")
    if source_name == "entd":
        # popsim_open: the ENTD donor for the PopulationSim SEED + attribute/trip
        # mapping must carry the FULL household composition (multi-person households).
        # We deliberately use data.hts.entd.FILTERED, NOT data.hts.selected:
        # data.hts.selected resolves (via hts="entd") to data.hts.entd.reweighted,
        # which collapses the survey to ONE person per household for the eqasim IPF
        # person-matching path (49283 -> 17997 persons, mean 1.0/household). Seeding
        # PopulationSim with that would yield all-1-person synthetic households.
        # data.hts.entd.filtered keeps the full composition (20178 hh / 49283 persons,
        # mean 2.44) with the natural ENTD weights -- the unbiased seed PopulationSim
        # then reweights to the German Zensus controls. (ENTD-specific because
        # popsim_open is ENTD-only; extend this branch for another survey.)
        context.stage("data.hts.entd.filtered", alias="hts_donor")


def join_cell_attributes(
    combined: pd.DataFrame,
    cells: pd.DataFrame,
    *,
    ars_col: str = mid._ARS_COLUMN,
) -> pd.DataFrame:
    """Join the per-cell attributes onto the merged PopulationSim output.

    PopulationSim writes only ``ZENSUS100m`` + ``H_ID`` to its output CSV, so
    everything the downstream assembly needs per SYNTHETIC HOME cell must be
    recovered here from the loaded cells frame:

    - the 12-digit ARS (``RegionalSchlussel_ARS``): required by
      ``assembly.derive_zone_ids`` for commune_id / departement_id / iris_id
      (bug D1: spatial home.zones KeyError without it);
    - ``RegioStaR7`` (when the cells parquet carries it): the synthetic home's
      urban/rural class. ``assembly.build_persons`` expands it onto every
      synthetic person, where it serves as the SPATIAL stage-B matching key
      (``braunschweig.popsim.trips.MATCHED_REPLACEMENT_COLUMNS``). Note this is
      deliberately the CELL's RS7, not the MiD donor household's survey RS7:
      the donor frame's ``RegioStaR7`` is never merged onto persons (it is not
      part of ``assembly._HOUSEHOLD_ATTRS``), so no collision can occur.

    Parameters
    ----------
    combined:
        Merged PopulationSim output (one row per synthetic household, with
        ``ZENSUS100m``).
    cells:
        Loaded (ZGB-filtered) cells frame from ``mid.load_control_cells``.
    ars_col:
        Name of the 12-digit ARS column on the cells frame.

    Returns
    -------
    pandas.DataFrame
        ``combined`` with ``ars_col`` (always) and ``RegioStaR7`` (when
        available on ``cells``) joined per 100 m cell.
    """
    join_cols = ["ZENSUS100m", ars_col]
    has_rs7 = "RegioStaR7" in cells.columns
    if has_rs7:
        join_cols.append("RegioStaR7")
    else:
        logger.info(
            "[popsim.stage] cells frame carries no 'RegioStaR7' column (older "
            "parquet); synthetic persons get no home-cell RS7 and stage-B chain "
            "matching falls back to the non-spatial key list."
        )

    cell_attributes = cells[join_cols].drop_duplicates("ZENSUS100m")
    combined = combined.merge(cell_attributes, on="ZENSUS100m", how="left")

    n_missing_ars = int(combined[ars_col].isna().sum())
    if n_missing_ars:
        logger.warning(
            "[popsim.stage] %d/%d households could not be matched to an ARS after "
            "the cells join (unexpected; cells used in PopulationSim must be a subset "
            "of the loaded cells frame).",
            n_missing_ars, len(combined),
        )

    if has_rs7:
        n_missing_rs7 = int(combined["RegioStaR7"].isna().sum())
        logger.info(
            "[popsim.stage] cell RegioStaR7 joined onto %d households "
            "(%d missing -> NaN).",
            len(combined), n_missing_rs7,
        )

    return combined


# Filename of the per-work_dir config signature used to detect a config change and
# purge stale batch folders (see purge_stale_batches_on_config_change).
WORK_DIR_SIGNATURE_FILE = ".popsim_config_signature"


def purge_stale_batches_on_config_change(work_dir, signature: str) -> int:
    """Remove stale ``batch_*`` folders when the popsim config/control set changed.

    The PopulationSim ``work_dir`` persists across runs (it lives OUTSIDE synpp's
    stage cache), and the batch runner SKIPS any batch whose completion marker
    (``output/final_expanded_household_ids.csv``) already exists. If the config changed
    since the run that produced those outputs (e.g. tier3 / employment_grid controls
    were added, changing the per-batch inputs), skipping them would merge an
    old-config population for those cells -- a silent correctness bug.

    Guard: a signature file in ``work_dir`` records the config that produced the
    current batches. On a MISMATCH (or first run with pre-existing folders) every
    ``batch_*`` folder is removed so all batches re-run with the current config. On a
    MATCH (same config -- e.g. a resumed interrupted run) the folders are kept, so the
    skip-completed-batches resume optimisation still works. Returns the number of
    batch folders purged.
    """
    work_dir = Path(work_dir)
    sig_path = work_dir / WORK_DIR_SIGNATURE_FILE
    previous = sig_path.read_text(encoding="utf-8").strip() if sig_path.is_file() else None
    if previous == signature:
        return 0
    purged = 0
    for batch_folder in sorted(work_dir.glob("batch_*")):
        if batch_folder.is_dir():
            shutil.rmtree(batch_folder)
            purged += 1
    work_dir.mkdir(parents=True, exist_ok=True)
    sig_path.write_text(signature, encoding="utf-8")
    if purged:
        logger.warning(
            "[popsim.stage] popsim config changed since the last run in this work_dir "
            "(or signature was absent) -> purged %d stale batch folder(s) so every batch "
            "re-runs with the CURRENT config (prevents stale-batch skips).", purged)
    else:
        logger.info("[popsim.stage] wrote work_dir config signature (no stale batches).")
    return purged


def _frame_content_signature(df):
    """Content signature of a seed/target frame (row values + column layout).

    Hashes the actual VALUES (via pandas' row hash) plus the column names and dtypes.
    Returns ``None`` for ``None`` (an inactive optional input). Used by
    :func:`compute_batch_config_signature` so that any change to the seed tables or the
    per-Kreis control target counts flips the work-dir signature.
    """
    if df is None:
        return None
    row_hash = hashlib.sha256(
        pd.util.hash_pandas_object(df, index=True).values.tobytes()
    ).hexdigest()
    return {
        "columns": [str(c) for c in df.columns],
        "dtypes": [str(t) for t in df.dtypes],
        "n_rows": int(len(df)),
        "rows": row_hash,
    }


def compute_batch_config_signature(*, controls_df, settings_text, max_cells,
                                   stratify_regiostar, source_name, employment_grid_on,
                                   kreis_controls_map, seed_day_filter, seed_households,
                                   seed_persons, kreis_table, active_entries=None,
                                   status_prior_n=0.0) -> str:
    """Compute the work-dir batch-input signature (sha256 hex).

    The signature captures EVERYTHING that determines a batch's inputs, so that a change
    since the last run in the same ``work_dir`` purges the stale completed batches
    (:func:`purge_stale_batches_on_config_change`). Beyond the control set, settings,
    batching and source, it hashes the CONTENT of the seed frames and the per-Kreis
    target table: the seed content captures the full seed identity (weekend_plan_match /
    complete_members / e-bike column / imputation seed all flow into the seed VALUES), and
    ``kreis_table`` captures the per-Kreis control target COUNTS. Hashing content (not just
    the config-knob names) closes the audit gap where editing a ``target2026_*`` CSV or a
    seed toggle in the same work_dir left completed batches silently reused with outdated
    inputs (2026-07-09).
    """
    # NOTE (one-time signature change): hashing (key, census_source) PAIRS instead of
    # just the sorted keys means a catalog composition change (same control names,
    # different census_source columns) now invalidates a persistent work_dir's
    # completed batches exactly once on the next run. Before this fix, a KEY-only
    # hash could not see such a change and silently reused stale batches built
    # against the OLD census_source composition.
    kreis_controls_signature = (
        sorted((key, list(census_source)) for key, census_source in kreis_controls_map.items())
        if kreis_controls_map else None
    )
    payload = {
        "controls": controls_df.to_csv(index=False),
        "settings": settings_text,
        "max_cells": max_cells,
        "stratify_regiostar": stratify_regiostar,
        "source": source_name,
        "employment_grid": employment_grid_on,
        "kreis_controls": kreis_controls_signature,
        "seed_day_filter": str(seed_day_filter),
        "seed_households": _frame_content_signature(seed_households),
        "seed_persons": _frame_content_signature(seed_persons),
        "kreis_targets": _frame_content_signature(kreis_table),
    }
    if active_entries:
        payload["kreis_attribute_controls"] = {
            c.name: (status_prior_n if c.name == "economic_status" else 0.0)
            for c in active_entries
        }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def derive_geo_kreis_from_ars(ars: pd.Series) -> pd.Series:
    """Derive the 5-digit Kreis ARS from a (nominally) 12-digit cell ARS column.

    Zero-pads to the full 12-digit Regionalschluessel BEFORE slicing the first
    five digits: an ARS that lost a leading zero (e.g. round-tripped through an
    integer column) would otherwise truncate the wrong five characters and
    silently join to the wrong Kreis. Mirrors ``mid.filter_zgb_cells`` and
    ``assembly.derive_zone_ids``, which both ``zfill(12)`` before deriving the
    Kreis-level ARS -- kept as a single reusable helper so the three call sites
    cannot drift apart.
    """
    return ars.astype(str).str.zfill(12).str[:5]


def execute(context) -> pd.DataFrame:
    """Run popsim_mid and return the merged expanded-household table."""
    cells_path = context.config(KEY_CELLS)
    mid_dir = context.config(KEY_MID)
    controls_path = context.config(KEY_CONTROLS)
    settings_path = context.config(KEY_SETTINGS)
    logging_path = context.config(KEY_LOGGING)
    popsimprep_dir = context.config(KEY_POPSIMPREP)
    uv_path = context.config(KEY_UV)
    # synpp's ExecuteContext.config() takes only the key; the defaults are
    # registered in configure() (3000 / 3 / "mid" / False) and resolved here.
    max_cells = int(context.config(KEY_MAX_CELLS))
    # Worker count honours the auto sentinel (0/null/"auto" -> cores - reserve), so
    # the batch runner scales with the box it lands on. An explicit positive integer
    # is used verbatim (pin it when byte-reproducibility across machines matters).
    from braunschweig.parallelism import resolve_workers
    _requested_workers = context.config(KEY_WORKERS)
    num_workers = resolve_workers(_requested_workers)
    logger.info(
        "[popsim.stage] PopulationSim batch workers: %d (requested=%r, cpu_count=%s)",
        num_workers, _requested_workers, os.cpu_count(),
    )
    work_dir = context.config(KEY_WORK_DIR)
    # Create the PopulationSim working directory up front so the stage can write
    # its intermediate artefacts (pseudonym map, per-batch PopulationSim folders)
    # into it. On a FRESH cache this directory does not exist yet; the first writer
    # below (the pseudonym map / per-batch folders) would otherwise fail with
    # "Cannot save file into a non-existent directory". (The weekend_plan_match trace
    # is now written into the completed_donor stage's own cache dir, not here.)
    # Creating it here keeps the stage self-contained (CLAUDE.md: create output
    # directories explicitly).
    Path(work_dir).mkdir(parents=True, exist_ok=True)
    kreise = list(context.config(KEY_KREISE))
    source_name = context.config(KEY_SOURCE)
    stratify_regiostar = bool(context.config(KEY_STRATIFY))
    # Member completion (D3) applies to the MiD donor source only: the ENTD
    # frames have no declared-size column and need no completion.
    complete_members = bool(context.config(KEY_COMPLETE_MEMBERS)) and source_name == "mid"

    # Seeded RNG for the stochastic attribute imputation in build_persons
    # (offset +74511 keeps the stream disjoint from the enriched-stage offsets).
    random_seed = int(context.config("random_seed"))
    rng = np.random.RandomState(random_seed + 74511)
    # Seeded RNG for the count-style KREIS-control seed-column derivations
    # (number_of_cars / number_of_bicycles / has_ebike group-wise 99-code imputation in
    # load_mid_seed). Offset +24680 keeps the stream disjoint from the +74511 imputation
    # stream above; derived from the pipeline random_seed so the seed is reproducible.
    kreis_seed_rng = np.random.RandomState(random_seed + 24680)

    source = _resolve_source(source_name)
    logger.info("[popsim.stage] active donor source: %s", source.name)

    # Parse the comma-separated tier string (e.g. "tier0,tier1") into a tuple.
    control_tiers_str = context.config(KEY_CONTROL_TIERS)
    control_tiers = tuple(t.strip() for t in control_tiers_str.split(",") if t.strip())
    # Seed reporting-day filter: "off"/"all" -> keep all kernwo (no day filter, ()),
    # else None -> the loader's default (1,2,3) Mo-Fr.
    _day_filter_cfg = str(context.config(KEY_SEED_DAY_FILTER)).strip().lower()
    seed_day_filter = () if _day_filter_cfg in ("off", "all", "none", "") else None
    controls_source = context.config(KEY_CONTROLS_SOURCE)
    # Employment grid control (Task 5): default "off" -> byte-identical path.
    employment_grid_on = str(context.config(KEY_EMPLOYMENT_GRID)).strip().lower() == "on"
    # KREIS attribute controls (issue #109 + S1c): the active REGISTRY entries whose toggle
    # is "on" (each default "on"), MiD-only (their seed columns have no ENTD pendant), so an
    # ENTD run is unaffected (empty list). economic_status is one of them; it alone carries
    # the configurable Dirichlet shrinkage prior. The three S1c targets are FINAL (prior_n=0).
    active_entries = active_kreis_entries(context, source_name)
    active_entry_names = tuple(c.name for c in active_entries)
    status_prior_n = float(context.config(KEY_STATUS_KREIS_SHRINKAGE_N))
    # E-bike seed column (server-verified default "H_ANZPED", declared in configure;
    # ExecuteContext.config takes no default argument). An empty string (explicitly
    # cleared config) -> None; the loader fail-fasts if has_ebike is active without it
    # (no silent fallback).
    ebike_seed_column_cfg = str(context.config(KEY_EBIKE_SEED_COLUMN)).strip() or None
    # Importance profile: default "uniform" -> importance untouched (byte-identical).
    importance_profile = str(context.config(KEY_IMPORTANCE_PROFILE)).strip()
    controls_df = build_controls_df(
        controls_source=controls_source,
        controls_path=controls_path,
        seed=source_name,
        tiers=control_tiers,
        employment_grid=employment_grid_on,
        kreis_control_names=active_entry_names,
        importance_profile=importance_profile,
    )
    if importance_profile and importance_profile != "uniform":
        logger.info("[popsim.stage] importance profile applied: %s", importance_profile)
    base_cols = mid.control_base_columns(controls_df, "ZENSUS100m")

    # Tier-3 KREIS controls (employment / education): when active, load the imported
    # per-Kreis census table and derive the {control_name: census_source} map from the
    # catalog's KREIS-geography controls expressible by the active seed. Passed to
    # run_popsim_mid, which builds control_totals_KREIS.csv per batch. When tier3 is
    # absent both stay None -> the tier0-2 folder is byte-identical.
    kreis_table = None
    kreis_controls_map = None
    # Household-level KREIS control names (keys of kreis_controls_map) that must be
    # apportioned across batches by the batch's HOUSEHOLD share, not the population
    # share (issue #148). Populated from the active household-level attribute controls
    # below; empty otherwise (person-level / tier0-2 -> pop-share everywhere, unchanged).
    household_control_names: set[str] = set()
    if "tier3" in control_tiers and controls_source == "catalog":
        from braunschweig.popsim import control_spec as _cs
        tier3 = [c for c in _cs.tier3_controls() if c.expression_for(source_name) is not None]
        kreis_controls_map = _kreis_controls_map(tier3)
        kreis_dir = context.config(KEY_KREIS_CONTROLS)
        if not kreis_dir:
            raise ValueError(
                "control_tiers includes 'tier3' but "
                f"{KEY_KREIS_CONTROLS} is not set; cannot source the KREIS controls."
            )
        # Restrict the national Tier-3 table (~400 Kreise) to the run's Kreise at load
        # time so the accumulator carries only rows looked up downstream (issue #147);
        # the resolved dominant Kreis of any in-scope cell is itself in `kreise`
        # (cells are filtered to `kreise` before the crosswalk is built), so no needed
        # Kreis row is dropped.
        kreis_table = mid.load_kreis_control_table(kreis_dir, restrict_to_kreise=kreise)
        logger.info(
            "[popsim.stage] Tier-3 KREIS controls active: %d controls, kreis table %d rows "
            "(restricted to the run's %d Kreise) from %s",
            len(kreis_controls_map), len(kreis_table), len(kreise), kreis_dir,
        )

    # For catalog-based controls with multi-column census sources (e.g. building_type),
    # load the raw source columns from the parquet (union of all census_source tuples)
    # rather than the derived control names.  For tier0-only or CSV-based controls,
    # source_cols == base_cols == current behaviour -> byte-identical.
    source_cols_override = build_source_columns(
        controls_source=controls_source,
        seed=source_name,
        tiers=control_tiers,
    )
    load_cols = source_cols_override if source_cols_override is not None else base_cols

    # Employment grid control (Task 5): the ten EMPLOYED_{M,F}_{16_29,30_39,40_49,50_59,60plus}_agg
    # targets are COMPUTED per cell (not stored in the parquet), so strip them from the
    # parquet load set and add the single-year {M,F}_AGE_<year> input columns (the age
    # SHAPE denominator, y>=16) that ARE present in the parquet. When OFF, load_cols is
    # untouched (byte-identical).
    if employment_grid_on:
        from braunschweig.popsim import employment_grid as _eg
        import pyarrow.parquet as _pq_eg

        _eg_raw_names = _pq_eg.ParquetFile(cells_path).schema.names
        _eg_available = [prepared_cells.clean_col_name(_n) for _n in _eg_raw_names]
        load_cols = _eg.select_load_columns(
            load_cols, _eg_available,
            computed_cols={
                "EMPLOYED_M_16_29_agg", "EMPLOYED_M_30_39_agg", "EMPLOYED_M_40_49_agg",
                "EMPLOYED_M_50_59_agg", "EMPLOYED_M_60plus_agg",
                "EMPLOYED_F_16_29_agg", "EMPLOYED_F_30_39_agg", "EMPLOYED_F_40_49_agg",
                "EMPLOYED_F_50_59_agg", "EMPLOYED_F_60plus_agg",
            },
        )

    # Income spatial tilt (issue #136): fetch the tilt cell columns (rent /
    # Eigentuemerquote / HH weight) in this SINGLE read instead of re-scanning
    # the national parquet later. When the tilt is OFF, load_cols is returned
    # unchanged (byte-identical).
    load_cols = tilt_extra_load_columns(
        bool(context.config(KEY_INCOME_TILT)), list(load_cols)
    )

    cells = mid.load_control_cells(cells_path, load_cols)
    cells = mid.filter_zgb_cells(cells, kreise)

    # Derive the multi-column aggregated control columns (e.g. building_type_*).
    # For tier0-only: agg_map is empty -> add_aggregated_controls returns cells
    # unchanged -> byte-identical.
    agg_map = build_aggregation_map(
        controls_source=controls_source,
        seed=source_name,
        tiers=control_tiers,
    )
    cells = prepared_cells.add_aggregated_controls(cells, agg_map)

    # Employment grid control (Task 5): inject the ten per-cell
    # EMPLOYED_{M,F}_{16_29,30_39,40_49,50_59,60plus}_agg target columns. The age SHAPE comes from the
    # committed Zensus 2000S-2001 employment-by-age reference (zensus_employment_age.
    # load_age_shares; exact for the kreisfreie Staedte, national fallback for the
    # Landkreise) and is rescaled per Kreis x sex x group to the census Erwerbstaetige
    # Kreis level (kreis_erwerbsstatus.parquet). The former GENESIS SvB synpp stage
    # dependency is no longer used. When OFF, none of this runs -> byte-identical.
    if employment_grid_on:
        from braunschweig.popsim import employment_grid as _eg
        from braunschweig.popsim import zensus_employment_age as _za
        from braunschweig.popsim import folders as _folders

        # Census LEVEL: the sex-split Erwerbstaetige Kreis totals. Sourced from the
        # imported cleancensus kreis_erwerbsstatus table (same dir as the Tier-3
        # KREIS controls). Fail fast (Tier-3 style) if the dir is not configured.
        _eg_kreis_dir = context.config(KEY_KREIS_CONTROLS)
        if not _eg_kreis_dir:
            raise ValueError(
                f"{KEY_EMPLOYMENT_GRID} is 'on' but {KEY_KREIS_CONTROLS} is not set; "
                "cannot source the census Erwerbstaetige Kreis levels "
                "(kreis_erwerbsstatus.parquet)."
            )
        _eg_levels_path = os.path.join(_eg_kreis_dir, "kreis_erwerbsstatus.parquet")
        if not os.path.exists(_eg_levels_path):
            raise FileNotFoundError(
                f"employment grid control requires {_eg_levels_path}; import the "
                "cleancensus kreis_erwerbsstatus parquet into the kreis_controls dir."
            )
        _eg_levels = pd.read_parquet(_eg_levels_path)
        _eg_levels["ARS_kreis"] = _eg_levels["ARS_kreis"].astype(str).str.zfill(5)
        _eg_census_levels = _eg_levels[
            ["ARS_kreis", "ERWERBSTAT_KURZ_STP__11_M", "ERWERBSTAT_KURZ_STP__11_W"]
        ].copy()

        # Derive the 5-digit Kreis on the cells frame from the 12-digit ARS column,
        # matching folders.GEO_KREIS = ARS[:5].
        if mid._ARS_COLUMN not in cells.columns:
            raise ValueError(
                f"{KEY_EMPLOYMENT_GRID} is 'on' but the cells frame carries no "
                f"{mid._ARS_COLUMN!r} column; cannot derive the Kreis code."
            )
        cells = cells.copy()
        cells[_folders.GEO_KREIS] = derive_geo_kreis_from_ars(cells[mid._ARS_COLUMN])

        # SHAPE: Zensus 2000S-2001 employment-by-age-group shares per Kreis, loaded from
        # the committed reference CSV under data_path. Built once per distinct Kreis on
        # the cells frame (exact for 03101/02/03, national fallback for the Landkreise).
        _eg_data_path = context.config("data_path")
        _eg_ref = os.path.join(
            _eg_data_path, "braunschweig/popsim/zensus2022_employment_by_age_ref.csv"
        )
        if not os.path.exists(_eg_ref):
            raise FileNotFoundError(
                f"employment grid control requires the Zensus age-share reference "
                f"{_eg_ref}; import zensus2022_employment_by_age_ref.csv into "
                "data/braunschweig/popsim/."
            )
        _eg_kreise = sorted(cells[_folders.GEO_KREIS].astype(str).unique())
        _eg_age_shares = {k: _za.load_age_shares(_eg_ref, k) for k in _eg_kreise}

        # Fallback observability (CLAUDE.md, no silent fallback): load_age_shares
        # silently substitutes the national "DE_large_gemeinden" shape for a Kreis absent
        # from the reference. The Landkreise fall back BY DESIGN (only the kreisfreie
        # Staedte 03101/02/03 have an exact 2001 shape), so log the exact-vs-fallback split
        # at info; escalate to warning ONLY if a known-exact kreisfreie Stadt fell back
        # (that signals a broken/renamed reference, not the expected Landkreis fallback).
        _eg_ref_regions = set(
            pd.read_csv(_eg_ref, dtype={"region": str})["region"].astype(str).unique()
        )
        _eg_exact = [k for k in _eg_kreise if k in _eg_ref_regions]
        _eg_fallback = [k for k in _eg_kreise if k not in _eg_ref_regions]
        _eg_kreisfrei_exact = {"03101", "03102", "03103"}
        _eg_unexpected = sorted(_eg_kreisfrei_exact.intersection(_eg_fallback))
        _eg_msg = (
            "[popsim.stage] employment grid age-shape: %d/%d Kreise exact, %d used the "
            "national DE_large_gemeinden fallback %s"
        )
        if _eg_unexpected:
            logger.warning(
                _eg_msg + " -- INCLUDING kreisfreie Stadt/Staedte %s that should be exact; "
                "check the reference CSV region coding.",
                len(_eg_exact), len(_eg_kreise), len(_eg_fallback), _eg_fallback, _eg_unexpected,
            )
        else:
            logger.info(
                _eg_msg + " (Landkreis fallback is by design).",
                len(_eg_exact), len(_eg_kreise), len(_eg_fallback), _eg_fallback,
            )

        cells = _eg.add_employment_grid_columns(
            cells, _eg_census_levels, _eg_age_shares, kreis_col=_folders.GEO_KREIS,
        )
        logger.info(
            "[popsim.stage] employment grid control ON: injected 10 "
            "EMPLOYED_{M,F}_{16_29,30_39,40_49,50_59,60plus}_agg per-cell targets "
            "(census levels from %s, age shape from %s, %d Kreise).",
            _eg_levels_path, _eg_ref, _eg_census_levels["ARS_kreis"].nunique(),
        )

    # Build the PopulationSim seed.
    # For source="mid": delegates to mid.load_mid_seed which reads the MiD CSV
    # files with MiD column names (H_ID/H_GEW/HP_ALTER/HP_SEX/P_GEW).
    # For source="entd": the ENTD donor frames are transformed to MiD column
    # schema by EntdSource.build_seed so the downstream (expand, map_demographics)
    # runs unchanged; only map_person_attributes is ENTD-specific.
    # The completed donor frames (member completion ON) are loaded here, ahead
    # of PopulationSim, because the SEED is derived from them; they are reused
    # verbatim as the expansion donor tables further below (ONE completion pass
    # -> seed and expansion contain the same fillers).
    completed_donor_households = None
    completed_donor_persons = None
    if source_name == "entd":
        # popsim_open: retrieve the cleaned ENTD frames from the synpp DAG
        # (registered in configure() as alias "hts_donor") and build the seed.
        # context.stage() is idempotent in synpp; retrieving the same alias twice
        # returns the same cached result, so this does not re-run the stage.
        hts_hh_seed, hts_persons_seed, _hts_trips_seed = context.stage("hts_donor")
        seed_households, seed_persons, report = source.build_seed(
            hts_hh_seed, hts_persons_seed
        )
    elif complete_members:
        # popsim_mid with member completion (D3, default ON): the donor build
        # (member completion + weekend-plan match) is produced by the cached
        # braunschweig.popsim.completed_donor stage (ONE pass, shared across runs).
        # The same completed frames feed BOTH the PopulationSim seed (projected
        # here) AND the expansion donor tables below.
        donor = context.stage("completed_donor")
        completed_donor_households = donor.households
        completed_donor_persons = donor.persons
        report = donor.completeness_report
        completion_report = donor.completion_report
        seed_columns = source.seed_columns()
        # project_completed_seed derives hh_type5 (Tier-1 household_type) like
        # load_mid_seed does, so the seed carries it for the household_type control.
        # number_of_cars / number_of_bicycles / has_ebike are derived here too, from
        # the raw H_ANZAUTO / anzpedrad / H_ANZPED columns the completed_donor stage
        # already carries (mid.MID_HOUSEHOLD_ATTR_COLS). has_ebike is fully wired
        # (server-verified 2026-07-08, issue #116 resolved); project_completed_seed
        # only raises if has_ebike is active AND ebike_seed_column_cfg is unset.
        seed_households, seed_persons = mid.project_completed_seed(
            completed_donor_households, completed_donor_persons, seed_columns,
            kreis_control_entries=active_entries,
            kreis_seed_rng=kreis_seed_rng,
            ebike_seed_column=ebike_seed_column_cfg,
        )
        # Surface the build reports on THIS run too (so they are present even when
        # the completed_donor stage was served from cache and its execute did not run).
        context.set_info(
            "member_completion_filled", completion_report.n_households_filled
        )
        context.set_info(
            "member_completion_persons_added", completion_report.n_persons_added
        )
    else:
        # popsim_mid, complete_members=False: reads MiD CSV files directly from
        # mid_dir. This path is byte-identical to all prior versions.
        seed_columns = source.seed_columns()
        seed_households, seed_persons, report = mid.load_mid_seed(
            mid_dir, columns=seed_columns, day_filter_values=seed_day_filter,
            kreis_control_entries=active_entries,
            kreis_seed_rng=kreis_seed_rng,
            ebike_seed_column=ebike_seed_column_cfg,
        )
    context.set_info("seed_completeness_rate", report.completeness_rate)

    run_one = batch.make_populationsim_run_one(
        command_prefix=(str(uv_path), "run", "--no-sync", "populationsim"),
        cwd=popsimprep_dir,
        timeout_s=int(context.config(KEY_BATCH_TIMEOUT)),
        cleanup_pipeline_h5=bool(context.config(KEY_CLEANUP_H5)),
    )

    logger.info(
        "[popsim.stage] stratify_regiostar=%s (Phase 4B donor stratification).",
        stratify_regiostar,
    )
    # KREIS attribute controls (issue #109 + S1c): derive each ACTIVE registered attribute's
    # per-Kreis household targets from its committed blended MiD shares x the per-Kreis
    # household total (summed cell HH_TOTAL, so the category targets partition EXACTLY the
    # household total PopulationSim controls per Kreis -> IPF-consistent). Merge into the KREIS
    # control totals + map so run_popsim_mid emits them in control_totals_KREIS.csv. Runs BEFORE
    # the config signature below so a control toggle invalidates stale batches. MiD-only
    # (active_kreis_entries returns [] for non-MiD sources). With only economic_status active,
    # this is byte-identical to the L1 wiring except the target now comes from the blended CSV.
    if active_entries:
        from braunschweig.popsim import kreis_attribute_control as _kac
        from braunschweig.popsim import control_spec as _cs_kac
        _kac_data_path = context.config("data_path")
        _kac_hh_col = _cs_kac.HH_TOTAL_CENSUS_COLUMN
        if _kac_hh_col not in cells.columns:
            raise RuntimeError(
                f"KREIS attribute control is ON but the household-total column {_kac_hh_col!r} is "
                f"absent from the cells frame; cannot derive the per-Kreis targets (no silent fallback).")
        # Align the per-Kreis attribute-control universe with the batch KREIS backbone
        # (issue #147, sub-item 1): partition the household/person targets over the SAME
        # RESOLVED dominant Kreis per 1 km parent that folders.build_kreis_control_totals
        # keys on, not the raw ARS[:5]. NOTE (scientific output change): this reassigns the
        # target attribution of the ~0.1% of cells that sit on a Kreis border and whose
        # 1 km parent is dominated by a neighbouring Kreis; region-wide per-Kreis sums are
        # unchanged (a 1 km parent is atomic to one resolved Kreis).
        _kac_kreis = mid.resolved_kreis_per_cell(cells)
        _kac_hh_by_kreis = cells.groupby(_kac_kreis)[_kac_hh_col].sum().to_dict()
        # Per-Kreis PERSON totals (sum over the 18 age-x-sex 100m band columns) are needed
        # only for PERSON-level entries (e.g. trip_class), so compute them LAZILY the first
        # time such an entry is seen (fail-fast on a missing band column; no silent fallback).
        _kac_persons_by_kreis = None
        # Age-restricted PERSON totals (min_age is not None, e.g. employment_status: 14+)
        # use the single-year age columns instead (person_total_by_kreis_min_age) and are
        # cached PER min_age value, so two entries sharing the same min_age reuse one
        # computation while a different min_age recomputes correctly (no cross-entry reuse
        # of the wrong universe -- the #97 universe trap this whole field exists to avoid).
        _kac_persons_by_kreis_min_age: dict = {}
        # The crosswalk Kreise the per-Kreis control totals are built over; each active
        # target CSV must cover them (load_kreis_target fail-fasts on a missing Kreis row).
        _kac_expected_ars5 = sorted(_kac_hh_by_kreis)
        # The committed blended targets (target2026_*) store shares rounded to 4 decimals, so a
        # row can sum to 0.9999 / 1.0001 (max observed deviation 1e-4). Accept that rounding via
        # a 1e-3 share tolerance (still catches a genuinely mis-normalised row, e.g. 0.9 / 1.1);
        # the per-Kreis counts are renormalised + integer-partitioned downstream regardless.
        _kac_share_tol = 1e-3
        for _ctl in active_entries:
            # economic_status keeps the configurable Dirichlet shrinkage prior (status_prior_n);
            # the S1c blended targets are FINAL (CONSUMER NOTE in the CSV headers) -> prior_n = 0.
            _entry_prior_n = status_prior_n if _ctl.name == "economic_status" else 0.0
            _tgt = _kac.load_kreis_target(
                _kac_data_path, _ctl, expected_ars5=_kac_expected_ars5,
                share_tolerance=_kac_share_tol)
            # Per-entry total by level: household entries partition the per-Kreis household
            # total; person entries (e.g. trip_class) partition the per-Kreis PERSON total
            # (sum of the 18 age-x-sex 100m band columns), so the category targets partition
            # EXACTLY the population PopulationSim controls per Kreis -> IPU-consistent.
            # Entries with min_age set (e.g. employment_status: 14+, feature #172 task 4)
            # instead partition the min_age-restricted PERSON total (single-year age
            # columns) -- using the ALL-ages total here would let <min_age persons distort
            # the category counts (the #97 universe trap).
            if _ctl.level == "person":
                _entry_min_age = getattr(_ctl, "min_age", None)
                if _entry_min_age is not None:
                    if _entry_min_age not in _kac_persons_by_kreis_min_age:
                        _kac_persons_by_kreis_min_age[_entry_min_age] = person_total_by_kreis_min_age(
                            cells, _kac_kreis, _entry_min_age)
                    _total_by_kreis = _kac_persons_by_kreis_min_age[_entry_min_age]
                    _total_label = f"persons (age>={_entry_min_age})"
                else:
                    if _kac_persons_by_kreis is None:
                        _kac_persons_by_kreis = person_total_by_kreis(cells, _kac_kreis)
                    _total_by_kreis = _kac_persons_by_kreis
                    _total_label = "persons"
            else:
                _total_by_kreis = _kac_hh_by_kreis
                _total_label = "households"
            _tbl = _kac.attribute_kreis_count_table(
                _ctl, _tgt, _total_by_kreis, prior_n=_entry_prior_n)
            _map = _kreis_controls_map(_cs_kac.attribute_kreis_controls([_ctl]))
            # Household-level entries (economic_status, number_of_cars, number_of_bicycles,
            # has_ebike) are apportioned across batches by the household share (issue #148);
            # person-level entries (e.g. trip_class) keep the population share.
            if _ctl.level == "household":
                household_control_names.update(_map.keys())
            logger.info(
                "[popsim.stage] KREIS attribute control ON: %s (%s, %s-level), %d Kreise, "
                "prior_n=%.1f, total %s=%d", _ctl.name, _ctl.tier, _ctl.level, len(_tbl),
                _entry_prior_n, _total_label, int(sum(_total_by_kreis.values())))
            if kreis_table is None:
                kreis_table = _tbl
                kreis_controls_map = dict(_map)
            else:
                kreis_table = kreis_table.merge(
                    _tbl, on="ARS_kreis", how="left", validate="one_to_one")
                kreis_controls_map = {**kreis_controls_map, **_map}
                # Fail-fast: every Kreis the run actually uses (_kac_expected_ars5 = the
                # cells' Kreise, which is exactly what build_kreis_control_totals looks up
                # via the crosswalk) must carry a non-NaN target for this control after the
                # LEFT merge (no silently under-constrained control). We mask to those
                # Kreise defensively: the tier3 kreis-control table is now restricted to the
                # run's Kreise at load time (issue #147), so national reference rows no longer
                # enter the accumulator, but masking keeps the guard correct even if a future
                # caller reintroduces out-of-scope rows (whose NaN would be harmless -- they
                # are never looked up downstream).
                _new_cols = list(_kac.control_columns(_ctl))
                _relevant = kreis_table["ARS_kreis"].astype(str).isin(_kac_expected_ars5)
                _relevant_na = _relevant & kreis_table[_new_cols].isna().any(axis=1)
                if _relevant_na.any():
                    _bad = kreis_table.loc[_relevant_na, "ARS_kreis"].tolist()
                    raise RuntimeError(
                        f"KREIS attribute control merge left NaN targets for {_ctl.name} at "
                        f"ARS_kreis {_bad} (missing from this control's target; refusing to "
                        f"under-constrain a Kreis the run synthesises).")

    # Purge stale batch folders if the popsim config/control set changed since the last
    # run that used this work_dir (the work_dir persists outside synpp's stage cache, so
    # a config change would otherwise leave old completion markers that the batch runner
    # skips -> stale-config population for those cells). Signature = everything that
    # determines a batch's inputs (the full control set, the PopulationSim settings, the
    # batching/stratification, the donor source, the KREIS controls, the seed-day filter).
    _config_signature = compute_batch_config_signature(
        controls_df=controls_df,
        settings_text=Path(settings_path).read_text(encoding="utf-8"),
        max_cells=max_cells,
        stratify_regiostar=stratify_regiostar,
        source_name=source_name,
        employment_grid_on=employment_grid_on,
        kreis_controls_map=kreis_controls_map,
        seed_day_filter=seed_day_filter,
        seed_households=seed_households,
        seed_persons=seed_persons,
        kreis_table=kreis_table,
        active_entries=active_entries,
        status_prior_n=status_prior_n,
    )
    purge_stale_batches_on_config_change(work_dir, _config_signature)
    merge_report = mid.run_popsim_mid(
        cells, base_cols, controls_df, seed_households, seed_persons,
        work_dir=Path(work_dir),
        settings_yaml=Path(settings_path).read_text(encoding="utf-8"),
        logging_yaml=Path(logging_path).read_text(encoding="utf-8"),
        max_cells=max_cells,
        run_one=run_one,
        num_workers=num_workers,
        source=source,
        stratify_regiostar=stratify_regiostar,
        kreis_table=kreis_table,
        kreis_controls_map=kreis_controls_map,
        household_control_names=household_control_names,
    )
    context.set_info("popsim_n_households", merge_report.n_rows)
    context.set_info("popsim_n_cells", merge_report.n_cells)
    context.set_info("popsim_n_missing_batches", merge_report.n_missing)

    # Surface the PopulationSim integerizer feasibility (no-silent-fallback): some
    # zones return INFEASIBLE and fall back to smart-rounded weights inside
    # PopulationSim. That is otherwise buried in the per-batch logs; aggregate and
    # log it here (WARNING above INTEGERIZER_INFEASIBLE_WARN_RATE). A high rate is a
    # quality signal (control set over-constrained for small cells -- common at low
    # sampling rates), not a hard failure: a smart-rounded population is still produced.
    feas = mid.summarize_integerizer_feasibility(work_dir)
    context.set_info("popsim_integerizer_infeasible_rate", feas["infeasible_rate"])
    context.set_info("popsim_integerizer_n_infeasible", feas["n_infeasible"])
    _feas_log = (
        logger.warning
        if feas["infeasible_rate"] > mid.INTEGERIZER_INFEASIBLE_WARN_RATE
        else logger.info
    )
    _feas_log(
        "[popsim.stage] PopulationSim integerizer: %d/%d zones OPTIMAL (%.1f%%), "
        "%d INFEASIBLE -> smart-rounded (%.1f%%), %d simul-retry-failed, across %d "
        "batch log(s). A high INFEASIBLE rate means the control set is "
        "over-constrained for small cells (expected to shrink at higher sampling "
        "rates where cells hold more households).",
        feas["n_optimal"], feas["n_total"],
        100.0 * (1.0 - feas["infeasible_rate"]),
        feas["n_infeasible"], 100.0 * feas["infeasible_rate"],
        feas["n_simul_retry_failed"], feas["n_logs"],
    )

    # Load the per-Kreis INKAR income scale (registered in configure).
    # Used by assembly.build_persons to scale household_income_eur and set
    # high_income with the unified numeric rule (>= 5000 EUR) for both sources.
    # Hoisted above the cell-attribute join so the placement_income reallocation
    # below can also consume it (build_persons still receives it further down).
    inkar_income = context.stage("inkar_income")

    # Join the per-cell attributes (12-digit ARS + RegioStaR7 when available)
    # from the cells frame back onto the merged PopulationSim output: the ARS
    # feeds assembly.derive_zone_ids (commune/departement/iris ids, bug D1) and
    # the cell RS7 becomes the spatial stage-B chain-matching key on every
    # expanded synthetic person (see join_cell_attributes).
    combined = join_cell_attributes(merge_report.combined, cells)

    # Load the donor attribute tables through the active source adapter.
    # For source="mid": MidSource.load_donor reads from mid_dir (byte-identical).
    # For source="entd": EntdSource.load_donor receives the frames injected from
    # the data.hts.selected stage (no filesystem read).
    # Loaded here, directly above the placement_income reallocation, because that
    # block needs the donor income + household size; it has no dependency on the
    # cell-attribute join above (donor loading and the join are independent).
    if source_name == "entd":
        # popsim_open: retrieve the cleaned ENTD frames from the synpp DAG
        # (registered in configure as alias "hts_donor") and inject them.
        hts_hh, hts_persons, hts_trips = context.stage("hts_donor")
        donor_households, donor_persons, _donor_trips = source.load_donor(
            mid_dir, injected=(hts_hh, hts_persons, hts_trips)
        )
    elif complete_members:
        # Member completion ON: the completed frames ARE the donor tables.
        # Reloading via source.load_donor would lose the fillers and their
        # source_H_ID / source_P_ID traceability (seed/expansion inconsistency).
        donor_households = completed_donor_households
        donor_persons = completed_donor_persons
        logger.info(
            "[popsim.stage] member completion ON: expansion uses the completed "
            "donor frames (%d households, %d persons incl. fillers).",
            len(donor_households), len(donor_persons),
        )
    else:
        # popsim_mid, complete_members=False (legacy): reads MiD CSV files
        # directly from mid_dir.
        donor_households, donor_persons, _donor_trips = source.load_donor(mid_dir)

    # --- placement_income (L2, issue #108): signature-preserving donor reallocation --
    # Runs BEFORE expansion so every downstream attribute/trip join follows the donor.
    # Permutes WHICH donor sits in which Kreis inside exact control-signature groups, so
    # every PopulationSim control aggregate and every donor's clone count are preserved
    # while the per-Kreis income mean is pushed toward the construct-corrected INKAR
    # relativity. MiD-only (needs the hheink_gr1 donor income). OFF -> combined unchanged.
    _placement_flag = bool(context.config(KEY_PLACEMENT_INCOME))
    placement_income_on = _placement_flag and source_name == "mid"
    if _placement_flag and source_name != "mid":
        logger.info("[popsim.stage] placement_income requested but source=%s carries no "
                    "hheink_gr1 donor income; feature inactive for this source.", source_name)
    if placement_income_on:
        from braunschweig.popsim import placement_income as _pi
        from braunschweig.popsim import control_spec as _cs_pi
        _pi.check_controls_source_compatible(placement_income_on, controls_source)
        _pi_catalog = _cs_pi.full_catalog(
            include_tiers=control_tiers,
            include_employment_grid=employment_grid_on,
            kreis_control_names=active_entry_names,
        )
        _pi_controls = _cs_pi.controls_for_seed(_pi_catalog, source_name)
        _signatures = _pi.donor_control_signatures(
            _pi_controls, seed_households, seed_persons, seed=source_name)
        _expected = _pi.donor_expected_income_eur(donor_households)
        _ars5 = derive_geo_kreis_from_ars(combined[mid._ARS_COLUMN])
        _slots = pd.DataFrame({"H_ID": combined["H_ID"].to_numpy(), "ars5": _ars5.to_numpy()},
                              index=combined.index)
        _stats = _pi.slots_kreis_stats(_slots, donor_households)
        _rf = _kic.build_kreis_income_targets(
            inkar_income, _stats, sorted(_slots["ars5"].unique()), hhsize_correct=True)
        _assignment, _pi_diag = _pi.reallocate_slots(
            _slots, signatures=_signatures, expected_income_eur=_expected, target_factor=_rf)
        combined = combined.assign(H_ID=_assignment.to_numpy())
        # Traceable per-run diagnostic (research-reporting rule): one row per Kreis.
        # "converged" (in _pi_diag) refers ONLY to the continuous lambda solve; the
        # achieved per-Kreis fit is realized_after vs target_mean (never call this
        # "calibrated to INKAR" -- convergence is not validation).
        _sorted_kreise = sorted(_pi_diag["kreis_target_mean"])
        _diag_rows = pd.DataFrame({
            "ars5": _sorted_kreise,
            "target_mean_eur": [_pi_diag["kreis_target_mean"][k] for k in _sorted_kreise],
            "realized_before_eur": [_pi_diag["kreis_realized_before"].get(k) for k in _sorted_kreise],
            "realized_after_eur": [_pi_diag["kreis_realized_after"].get(k) for k in _sorted_kreise],
            "lambda": [_pi_diag["kreis_lambda"][k] for k in _sorted_kreise],
            "clamped": [_pi_diag["kreis_clamped"][k] for k in _sorted_kreise],
        })
        _diag_rows.to_csv(Path(work_dir) / "placement_income_diag.csv", index=False)
        _residuals = [
            abs(_pi_diag["kreis_realized_after"].get(k, float("nan")) - v) / max(_pi_diag["region_mean"], 1.0)
            for k, v in _pi_diag["kreis_target_mean"].items()
        ]
        _finite = [r for r in _residuals if not np.isnan(r)]
        _worst = max(_finite) if _finite else float("nan")
        context.set_info("placement_income_moved_share", _pi_diag["moved_share"])
        context.set_info("placement_income_no_freedom_share", _pi_diag["no_freedom_slot_share"])
        context.set_info("placement_income_worst_residual_pct", 100.0 * _worst)

    # Expand the merged donor households into the full eqasim persons frame.
    # pseudonymise=True (MiD): replace raw H_ID/P_ID with sequential surrogates
    # (data-protection requirement for the restricted MiD scientific-use licence).
    # pseudonymise=False (ENTD): source_* are set directly to the open ENTD ids
    # by EntdSource.map_person_attributes; no surrogate mapping is needed.
    pseudonymise = (source_name == "mid")
    # When income_kreis_control is ON it OVERWRITES household_income_eur with a fresh
    # per-Kreis continuous draw further below, so the INKAR midpoint scaling inside
    # build_persons would be redundant -- tell build_persons to skip it (no-op on the
    # final output; see assembly.build_persons skip_inkar_income_scale).
    income_kreis_control_on = bool(context.config(KEY_INCOME_KC))
    _income_tilt_flag = bool(context.config(KEY_INCOME_TILT))
    # placement_income (issue #108) resolves the mutually exclusive income mechanisms
    # into one explicit decision: ON keeps each donor's OWN income and SKIPS both the
    # per-Kreis redraw and the spatial tilt (each override logged inside
    # resolve_income_path); OFF reproduces the legacy booleans (redraw / tilt /
    # skip_inkar_scale) bit-for-bit, so the OFF path is byte-identical to before.
    from braunschweig.popsim import placement_income as _pi_path
    income_path = _pi_path.resolve_income_path(
        placement_income_on, income_kreis_control_on, _income_tilt_flag)
    persons, pseudonym_map = assembly.build_persons(
        combined, donor_households, donor_persons,
        rng=rng,
        attribute_mapper=source.map_person_attributes,
        pseudonymise=pseudonymise,
        inkar_scale=inkar_income,
        skip_inkar_income_scale=income_path["skip_inkar_scale"],
    )
    context.set_info("popsim_n_persons", len(persons))

    # housing_tenure parity (P2): main wired the enriched-path tenure sampler
    # (braunschweig.synthesis.population.enriched._apply_housing_tenure, categories
    # rent/own/other, RNG offset +83947) into the popsim stage. On THIS branch the
    # popsim build_persons ALREADY derives ``housing_tenure`` directly from the MiD
    # donor flag H_MIETE via attributes.map_housing_tenure (categories
    # owner/renter/unknown) -- that donor-derived column is the AUTHORITATIVE tenure
    # source consumed by (a) the Tier-2 tenure CONTROL catalog (control_spec, which
    # matches owner/renter) and (b) the spatial income tilt below (which routes on
    # tenure == "owner" / "renter"). Letting _apply_housing_tenure run
    # unconditionally would OVERWRITE owner/renter/unknown with rent/own/other,
    # silently turning the income tilt into a no-op (no row would equal "owner"/
    # "renter") and changing the control-aligned attribute vocabulary. We therefore
    # keep main's mechanism only as a FALLBACK: run it solely when build_persons did
    # NOT already provide housing_tenure (e.g. the ENTD path, where H_MIETE is
    # absent). When the donor column is present (MiD path) it is preserved verbatim.
    if context.config("synthesise_housing_tenure") and "housing_tenure" not in persons.columns:
        from braunschweig.data.mid.tenure_by_income import (
            load_tenure_by_income_bundesland,
            load_tenure_by_income_raumtyp,
        )
        from braunschweig.synthesis.population.enriched import _apply_housing_tenure

        data_path = context.config("data_path")
        persons = _apply_housing_tenure(
            persons,
            load_tenure_by_income_bundesland(data_path),
            load_tenure_by_income_raumtyp(data_path),
            context.stage("regiostar_tenure"),
            random_seed,
        )

    # --- placement_income (L2, issue #108): keep each donor's OWN income ------------
    # When placement is active, household_income_eur becomes a seeded continuous draw
    # WITHIN each household's own MiD hheink_gr1 bracket (one draw per household); the
    # per-Kreis relativity was already imposed by the donor reallocation above, so the
    # redraw and the spatial tilt below are both skipped (see resolve_income_path).
    # _pi_diag is defined iff placement is active (same condition as income_path
    # ["placement"], which resolve_income_path returns True only when placement_income_on).
    if income_path["placement"]:
        persons, _own_diag = _pi_path.apply_own_income(persons, random_seed=random_seed)
        persons.attrs["placement_income_diag"] = {**_pi_diag, **_own_diag}

    # --- Kreis-Income-Control (real MiD draw + max-entropy per-Kreis calibration) ---
    # Replaces the build_persons midpoint x INKAR_scale income with a real continuous
    # draw reshaped per Kreis to the construct-corrected INKAR target. Runs BEFORE the
    # within-Kreis spatial tilt (which is Kreis-mean-preserving and layers on top).
    if income_path["redraw"]:
        _kc_data_path = context.config("data_path")
        _kc_scope = [str(p) for p in context.config(KEY_KREISE)]
        _income_tables = {
            "size_bl": load_income_by_size_bundesland(_kc_data_path),
            "size_rt": load_income_by_size_raumtyp(_kc_data_path),
            "status_bl": load_income_by_status_bundesland(_kc_data_path),
            "status_rt": load_income_by_status_raumtyp(_kc_data_path),
        }
        _kreis_stats = kreis_household_stats(
            os.path.join(_kc_data_path,
                         context.config("braunschweig.zensus_households_path")),
            _kc_scope,
        )
        persons, _kc_diag = _kic.apply_kreis_income_control(
            persons,
            inkar_df=inkar_income,
            kreis_stats_df=_kreis_stats,
            income_tables=_income_tables,
            enabled=True,
            method=str(context.config(KEY_INCOME_KC_METHOD)),
            hhsize_correct=bool(context.config(KEY_INCOME_KC_HHSIZE)),
            open_top_pareto=bool(context.config(KEY_INCOME_KC_PARETO)),
            pareto_alpha=float(context.config(KEY_INCOME_KC_PARETO_ALPHA)),
            random_seed=random_seed,
        )
        persons.attrs["kreis_income_control_diag"] = {
            "region_mean": float(_kc_diag["region_mean"]),
            "pmf_fallback_rate": float(_kc_diag["pmf_fallback_rate"]),
            "kreis_realized_mean": {k: float(v) for k, v in _kc_diag["kreis_realized_mean"].items()},
            "kreis_target_factor": {k: float(v) for k, v in _kc_diag["kreis_target_factor"].items()},
            "any_clamped": bool(any(_kc_diag["kreis_clamped"].values())),
        }
        logger.info(
            "[popsim.stage] Kreis-Income-Control applied (method=%s, hhsize_correct=%s); "
            "household_income_eur + label + high_income re-derived from the real draw. "
            "Realized per-Kreis means: %s",
            context.config(KEY_INCOME_KC_METHOD), context.config(KEY_INCOME_KC_HHSIZE),
            persons.attrs["kreis_income_control_diag"]["kreis_realized_mean"],
        )

    # --- Spatial income tilt (Nettokaltmiete GAMMA layer, Task 3) ---------------
    # Applies a within-Kreis income redistribution guided by per-cell net cold rent
    # (renters) and Eigentümerquote (owners), preserving each Kreis's income mean
    # exactly.  Controlled by KEY_INCOME_TILT (default ON per project rule), unless
    # placement_income is active -- resolve_income_path then forces income_path["tilt"]
    # False (the tilt would rescale the donor's own income). When OFF, the income frame
    # is byte-identical.
    income_tilt_enabled = income_path["tilt"]
    income_tilt_beta = float(context.config(KEY_INCOME_TILT_BETA))
    income_tilt_clip = float(context.config(KEY_INCOME_TILT_CLIP))

    if income_tilt_enabled:
        # NaN income guard: unclassifiable households (MiD map_household_income_eur
        # default=None; ENTD class -1 midpoint=None) may carry NaN household_income_eur.
        # apply_inkar_income_eur preserves these NaNs intentionally (high_income uses
        # .fillna(0.0) but the eur column itself stays NaN for missing income class).
        # We log the count for fallback-transparency (CLAUDE.md no-silent-fallback rule)
        # and rely on maybe_apply_income_tilt to shield NaN-income rows from the tilt.
        n_nan = int(persons["household_income_eur"].isna().sum())
        if n_nan > 0:
            logger.warning(
                "[popsim.stage] income_spatial_tilt: %d/%d persons (%.1f%%) have "
                "NaN household_income_eur (unclassifiable income class); these rows "
                "will be shielded from the spatial tilt (income stays NaN, excluded "
                "from per-Kreis re-normalization).",
                n_nan, len(persons), 100.0 * n_nan / max(len(persons), 1),
            )

        # Build the tilt working frame (rent + eigentuemerquote + HH weight +
        # ARS) from the already-loaded, already-ZGB-filtered cells frame. The
        # tilt columns were fetched by the single load_control_cells read via
        # tilt_extra_load_columns (issue #136) -- no second parquet scan.
        # Columns absent from the parquet are simply absent here; the checks
        # below then warn and fall back to a neutral index / uniform weight.
        _tilt_cells = extract_tilt_cells(cells)

        # Derive 5-digit Kreis ARS from the 12-digit ARS column.
        if _TILT_ARS_COL in _tilt_cells.columns:
            _tilt_cells["_ars5"] = derive_geo_kreis_from_ars(_tilt_cells[_TILT_ARS_COL])
        else:
            logger.warning(
                "[popsim.stage] income_spatial_tilt: ARS column %r absent from "
                "tilt cells; cannot derive Kreis code. Skipping tilt.",
                _TILT_ARS_COL,
            )
            income_tilt_enabled = False

    if income_tilt_enabled:
        _hh_weight_col = _TILT_HH_COL if _TILT_HH_COL in _tilt_cells.columns else None
        if _hh_weight_col is None:
            logger.warning(
                "[popsim.stage] income_spatial_tilt: HH weight column %r absent "
                "from tilt cells; using uniform weight (n_households=1).",
                _TILT_HH_COL,
            )
            _tilt_cells = _tilt_cells.copy()
            _tilt_cells["_hh_weight"] = 1.0
            _hh_weight_col = "_hh_weight"

        # Build a working frame with all needed columns renamed for the index builders.
        _work = _tilt_cells.rename(columns={"_ars5": "ars5"}).copy()

        # Build the renter rent index from the per-cell net cold rent column.
        if _TILT_RENT_COL in _work.columns:
            _work = _ist.build_renter_rent_index(
                _work,
                rent_col=_TILT_RENT_COL,
                kreis_col="ars5",
                weight_col=_hh_weight_col,
                beta=income_tilt_beta,
            )
        else:
            logger.warning(
                "[popsim.stage] income_spatial_tilt: rent column %r absent "
                "from tilt cells; renter_income_index set to 1.0 (neutral).",
                _TILT_RENT_COL,
            )
            _work["renter_income_index"] = 1.0

        # Build the owner income index from the per-cell Eigentümerquote column.
        if _TILT_QUOTE_COL in _work.columns:
            _work = _ist.build_owner_income_index(
                _work,
                quote_col=_TILT_QUOTE_COL,
                kreis_col="ars5",
                weight_col=_hh_weight_col,
                beta=income_tilt_beta,
            )
        else:
            logger.warning(
                "[popsim.stage] income_spatial_tilt: eigentuemerquote column %r absent "
                "from tilt cells; owner_income_index set to 1.0 (neutral).",
                _TILT_QUOTE_COL,
            )
            _work["owner_income_index"] = 1.0

        # The cell_index needs only: ZENSUS100m, ars5, renter_income_index, owner_income_index.
        _cell_index = _work[["ZENSUS100m", "ars5", "renter_income_index", "owner_income_index"]]

        # Determine tenure and cell columns on the persons frame.
        # ZENSUS100m: always present (joined by join_cell_attributes via expand).
        # housing_tenure: present for MiD (map_housing_tenure runs on H_MIETE);
        #   absent on ENTD path (map_housing_tenure skips when H_MIETE absent).
        _PERSONS_CELL_COL = "ZENSUS100m"
        _PERSONS_KREIS_COL = "departement_id"
        _PERSONS_TENURE_COL = "housing_tenure"

        if _PERSONS_CELL_COL not in persons.columns:
            logger.warning(
                "[popsim.stage] income_spatial_tilt: '%s' absent from persons "
                "frame; cannot apply spatial tilt. Skipping.",
                _PERSONS_CELL_COL,
            )
        elif _PERSONS_KREIS_COL not in persons.columns:
            logger.warning(
                "[popsim.stage] income_spatial_tilt: '%s' absent from persons "
                "frame; cannot apply spatial tilt. Skipping.",
                _PERSONS_KREIS_COL,
            )
        elif _PERSONS_TENURE_COL not in persons.columns:
            logger.info(
                "[popsim.stage] income_spatial_tilt: '%s' absent from persons "
                "frame (ENTD path?); skipping spatial tilt (no tenure signal).",
                _PERSONS_TENURE_COL,
            )
        else:
            persons, _tilt_diag = _ist.maybe_apply_income_tilt(
                persons, _cell_index,
                enabled=True,
                cell_col=_PERSONS_CELL_COL,
                kreis_col=_PERSONS_KREIS_COL,
                tenure_col=_PERSONS_TENURE_COL,
                income_col="household_income_eur",
                clip=income_tilt_clip,
                unknown_neutral=True,
            )
            # Update high_income from the tilted income values using the unified rule.
            persons["high_income"] = (
                persons["household_income_eur"].fillna(0.0) >= HIGH_INCOME_THRESHOLD_EUR
            ).astype(bool)
            logger.info(
                "[popsim.stage] income_spatial_tilt applied (beta=%.2f, clip=%.2f); "
                "high_income re-derived from tilted income. "
                "max_effective_dev=%.4f, kreis_mean_preserved=%s.",
                income_tilt_beta, income_tilt_clip,
                _tilt_diag.get("max_effective_dev", float("nan")),
                _tilt_diag.get("kreis_mean_preserved", "n/a"),
            )
            # Attach the tilt diagnostics to the persons frame so they survive pickling
            # in the synpp cache and can be read back by the gate harness without
            # re-running the tilt.  pandas DataFrame.attrs is preserved through pickle.
            # Only scalars and bools are stored (no numpy types) for JSON-safety.
            persons.attrs["income_tilt_diag"] = {
                k: (bool(v) if isinstance(v, (bool, np.bool_)) else float(v))
                for k, v in _tilt_diag.items()
                if v is not None
            }

    # Per-capita income view alongside the per-household household_income_eur.
    # Computed on the FINAL income (after Kreis-Income-Control + spatial tilt) so both
    # the per-household construct (household_income_eur) and the per-capita construct
    # (≈ INKAR income-je-Einwohner ordering) are available downstream.
    if {"household_income_eur", "household_size"}.issubset(persons.columns):
        persons = _kic.add_per_capita_income(persons)

    # Equivalised income view (issue #130): FINAL household_income_eur divided by
    # the OECD-modified consumption_units set in assembly.build_persons. Additive
    # column only -- high_income deliberately keeps the household-level 5000 EUR
    # rule (no traceable per-consumption-unit threshold reference exists; no
    # invented references).
    if {"household_income_eur", "consumption_units"}.issubset(persons.columns):
        persons = _income.add_income_per_consumption_unit(persons)

    # Write the local-only pseudonym map for MiD so internal re-linking is possible.
    # This file maps each surrogate source_person_id / source_household_id back
    # to the raw MiD H_ID / P_ID.  It MUST NOT be committed or published; it
    # lives in the pipeline work_dir which is a local-only, gitignored path.
    # For ENTD (pseudonymise=False) the map is empty (no surrogates were assigned)
    # but is still written for consistency.
    pseudonym_map_path = Path(work_dir) / "pseudonym_map.csv"
    pseudonym_map.to_csv(pseudonym_map_path, index=False)
    logger.info(
        "[popsim.stage] Pseudonym map written to %s (%d unique donor persons; "
        "pseudonymise=%s).",
        pseudonym_map_path, len(pseudonym_map), pseudonymise,
    )

    # Joint (cross-attribute) plausibility invariants (issue #133): run LAST so
    # every attribute overwrite above (income control, tilt, tenure parity) is
    # covered. WARN-only (measure-before-harden, like the minor-employment
    # guard); the report is attached to persons.attrs so it survives the synpp
    # cache and can feed a validation summary without re-running the stage.
    persons.attrs["joint_plausibility"] = _plausibility.check_joint_plausibility(persons)

    return persons
