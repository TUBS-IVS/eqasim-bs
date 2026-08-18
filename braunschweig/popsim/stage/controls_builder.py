"""Control-frame assembly: the PopulationSim controls.csv frame plus the derived
per-control aggregation map, source-column list and per-Kreis census-column map.

- :func:`build_controls_df` -- the PopulationSim ``controls.csv`` frame, either
  read from the hand-edited CSV or rendered from the typed catalog
  (``braunschweig.popsim.control_spec``).
- :func:`_kreis_controls_map` -- maps each KREIS control to its census_source
  columns, keyed by the ``control_totals_KREIS.csv`` column name.
- :func:`person_band_census_columns` -- the 18 age-x-sex 100m band census-source
  column names (tier0 backbone), used to derive the per-Kreis PERSON total.
- :func:`person_total_by_kreis` -- per-Kreis PERSON total summed over the 18
  age-x-sex band columns.
- :func:`person_total_by_kreis_min_age` -- per-Kreis PERSON total restricted to
  age >= ``min_age``, summed over the single-year age columns.
- :func:`_grid_geography_controls` -- keep only controls sourced from the GRID
  parquet (ZENSUS100m / ZENSUS1km), excluding KREIS-geography Tier-3 controls.
- :func:`build_aggregation_map` -- the multi-column aggregation map for the
  active controls (catalog source only).
- :func:`build_source_columns` -- the raw census parquet columns to load for
  the active controls (catalog source only).

Extracted verbatim from the stage module (``__init__``); see the package
docstring for the stage-level context.
"""

import pandas as pd


def build_controls_df(*, controls_source="csv", controls_path=None, seed="mid", tiers=("tier0",),
                      employment_grid=False, kreis_control_names=(), status_kreis=False,
                      importance_profile="uniform", fine_teen_age_bands=True):
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

    fine_teen_age_bands: when True (default, issue #320) the tier0 10-19 age x sex controls
    are replaced by 10-15 / 16-17 / 18-19. MUST carry the same value as the
    build_source_columns / build_aggregation_map calls in the same run, or the rendered
    controls.csv and the loaded/derived cell columns disagree.

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
                                  kreis_control_names=effective_kreis_names,
                                  fine_teen_age_bands=fine_teen_age_bands)
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


def person_band_census_columns(*, fine_teen_age_bands=True):
    """The age-x-sex 100m band census-source column names (tier0 backbone).

    Person-level KREIS attribute controls (e.g. ``trip_class``) partition the per-Kreis
    PERSON total, not the household total. That person total is the per-Kreis sum over ALL
    age-x-sex 100m band controls of the tier0 backbone, whose census-source column names are
    derived HERE from the backbone catalog rather than hardcoded, so a backbone change
    (renamed/added bands) propagates automatically instead of drifting out of sync.

    18 columns without the #320 fine teen bands (9 ten-year bands x 2 sexes, all
    precomputed ``_agg`` columns); 36 with them, because the three fine bands are sourced
    from single-year columns (6 + 2 + 2 per sex) instead of one ``_agg`` column. The SUM is
    the same population either way -- the single-year columns partition the ten-year band
    exactly -- but the column NAMES differ, so ``fine_teen_age_bands`` MUST match the value
    used to build the controls frame and the parquet column selection. Asking for the ON
    columns on a frame loaded for the OFF path raises in :func:`person_total_by_kreis`.
    """
    from braunschweig.popsim import control_spec as cs
    cols: list[str] = []
    for control in cs.tier0_backbone_catalog(fine_teen_age_bands=fine_teen_age_bands):
        if control.geography == cs.GEO_100M and control.seed_table == cs.SEED_TABLE_PERSONS:
            cols.extend(control.census_source)
    return tuple(cols)


def person_total_by_kreis(cells, kreis_by_row, *, fine_teen_age_bands=True):
    """Per-Kreis PERSON total = per-Kreis sum over the age-x-sex 100m band columns.

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
        ``{ars5: person_total}`` summed over the band columns per Kreis.

    Raises
    ------
    RuntimeError
        If any of the band columns is absent from ``cells`` (no silent fallback:
        a person-level control cannot be constrained without the person totals).
    """
    band_cols = list(person_band_census_columns(fine_teen_age_bands=fine_teen_age_bands))
    missing = [c for c in band_cols if c not in cells.columns]
    if missing:
        raise RuntimeError(
            "person_total_by_kreis: a person-level KREIS control is ON but the age-x-sex "
            f"band columns {missing} are absent from the cells frame (has "
            f"{[c for c in band_cols if c in cells.columns]} of {len(band_cols)}); cannot "
            "derive the per-Kreis PERSON total (no silent fallback). If the run has "
            "fine_teen_age_bands OFF, this helper must be called with the same value.")
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


def build_aggregation_map(*, controls_source="csv", controls_path=None, seed="mid", tiers=("tier0",),
                          fine_teen_age_bands=True):
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
    catalog = cs.full_catalog(include_tiers=tiers, fine_teen_age_bands=fine_teen_age_bands)
    active = _grid_geography_controls(cs.controls_for_seed(catalog, seed), cs)
    return cs.build_aggregation_map(active)


def build_source_columns(*, controls_source="csv", controls_df=None, seed="mid", tiers=("tier0",),
                         fine_teen_age_bands=True):
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
    catalog = cs.full_catalog(include_tiers=tiers, fine_teen_age_bands=fine_teen_age_bands)
    active = _grid_geography_controls(cs.controls_for_seed(catalog, seed), cs)
    return cs.source_columns_union(active)
