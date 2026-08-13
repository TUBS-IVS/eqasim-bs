"""ENTD PopulationSim seed building: the ENTD -> MiD seed-schema column mapping.

- :func:`seed_columns` / :func:`built_seed_columns`: the pre- and
  post- :func:`build_seed` column-name mappings (:class:`SeedColumns`
  instances), read by :mod:`braunschweig.popsim.mid` /
  :mod:`braunschweig.popsim.expand` to discover the household/person join keys.
- :func:`build_seed`: transforms ENTD canonical donor frames into the MiD
  PopulationSim seed schema (``H_ID``, ``H_GEW``, ``P_ID``, ``HP_ID``,
  ``P_GEW``, ``HP_ALTER``, ``HP_SEX``, ``STAAT``) once, at the stage boundary,
  so the entire proven downstream (seed build, expand, map_demographics) runs
  unchanged.

Extracted verbatim from ``braunschweig.popsim.sources.entd`` (issue #267);
``entd.py`` re-exports these names so external imports of the facade module
are unaffected. ``EntdSource.seed_columns``, ``EntdSource.built_seed_columns``
and ``EntdSource.build_seed`` are one-line delegations to the module-level
functions here (``EntdSource`` has no instance state, so ``self`` carries
nothing the moved bodies needed).
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from braunschweig.popsim.seed import (
    CompletenessReport,
    SeedColumns,
    filter_complete_households,
    select_seed_columns,
)
from braunschweig.popsim.sources.entd_schema import _require_columns
from braunschweig.popsim.sources.entd_vocabulary import (
    ENTD_BUILT_SEED_COLUMNS,
    ENTD_SEED_COLUMNS,
)

# Logger name string identical to the facade's (braunschweig.popsim.sources.entd)
# so log records emitted from here are indistinguishable from records emitted
# before the extraction; logging.getLogger caches by name, so this returns the
# SAME logger object as the facade's `logging.getLogger(__name__)`.
logger = logging.getLogger("braunschweig.popsim.sources.entd")


def seed_columns() -> SeedColumns:
    """Return the ENTD seed column mapping.

    ENTD columns are already in eqasim canonical names; no day-of-week
    completeness filter is needed.
    """
    return ENTD_SEED_COLUMNS


def built_seed_columns() -> SeedColumns:
    """Return the column schema of the seed frames produced by :meth:`build_seed`.

    :meth:`build_seed` renames ENTD canonical column names to the MiD seed
    names (``H_ID``, ``H_GEW``, ``P_ID``, ``P_GEW``, ``HP_ALTER``,
    ``HP_SEX``).  Downstream code that operates on the POST-build_seed frames
    (e.g. :func:`braunschweig.popsim.mid.filter_seed_to_stratum`) must use
    these MiD-schema column names to discover the household join key, not the
    ENTD canonical names from :meth:`seed_columns`.

    Returns
    -------
    SeedColumns
        :data:`ENTD_BUILT_SEED_COLUMNS` (``household_id="H_ID"``, etc.).
    """
    return ENTD_BUILT_SEED_COLUMNS


def build_seed(
    households: pd.DataFrame,
    persons: pd.DataFrame,
) -> tuple:
    """Build a PopulationSim seed in MiD control schema from ENTD donor frames.

    The PopulationSim control spec (popsimprep/_prep3_controls.csv) and the
    downstream expand/map_demographics pipeline all expect MiD column names:
    ``H_ID``, ``H_GEW``, ``P_ID``, ``HP_ID``, ``P_GEW``, ``HP_ALTER``,
    ``HP_SEX`` (1=male, 2=female), ``STAAT``.  This method transforms the ENTD
    canonical column names to that schema once, at the stage boundary, so the
    entire proven downstream (seed build, expand, map_demographics) runs
    unchanged.

    The ENTD person attributes (``employed``, ``has_license``,
    ``has_pt_subscription``, ``socioprofessional_class``, ``urban_type``, …)
    are retained on the transformed persons frame so that
    :meth:`map_person_attributes` can access them after expand.

    Design constraints
    ------------------
    - ``HP_SEX`` must be 1 (male) or 2 (female); any unmapped value raises
      :class:`ValueError` immediately (fail-fast guard).
    - ``HP_ID``: unique per-person integer derived as
      ``household_id * _HP_ID_SCALE + person_id`` (where ``_HP_ID_SCALE``
      is 10^ceil(log10(max(person_id)+1)) rounded up to the next power of 10
      to avoid collisions across households).  If a collision is detected
      ``np.arange`` fallback sequential ids are used and a warning is logged
      (per-run; very large surveys could overflow int64 for this formula, but
      the ENTD donor has ~14 000 persons so the scale is safe).
    - Every ENTD household is considered "complete" (no day-of-week filter;
      ``ENTD_SEED_COLUMNS.day_filter_col = None``), so
      :func:`braunschweig.popsim.seed.filter_complete_households` is called
      with the no-op path (drop rate 0 %, completeness_rate 1.0).
    - :func:`braunschweig.popsim.seed.select_seed_columns` is then called to
      add ``STAAT = 1`` and keep only the columns the control spec needs (plus
      the ENTD attribute extras retained for :meth:`map_person_attributes`).

    Parameters
    ----------
    households:
        ENTD household frame from :meth:`load_donor` or injected by the stage.
        Must carry ``household_id``, ``household_weight``, and ``urban_type``
        (Phase 4A donor stratification key).
    persons:
        ENTD person frame from :meth:`load_donor` or injected by the stage.
        Must carry ``household_id`` (foreign key), ``person_id``, ``person_weight``,
        ``age``, and ``sex`` (``"male"``/``"female"``).

    Returns
    -------
    tuple[pd.DataFrame, pd.DataFrame, CompletenessReport]
        ``(seed_households, seed_persons, report)`` where:

        - ``seed_households`` has columns: ``H_ID``, ``H_GEW``, ``urban_type``,
          ``STAAT``.
        - ``seed_persons`` has columns: ``H_ID``, ``P_ID``, ``HP_ID``,
          ``P_GEW``, ``HP_ALTER``, ``HP_SEX``, ``STAAT``,
          plus all ENTD attribute columns retained for downstream mapping
          (``employed``, ``studies``, ``has_license``, ``has_pt_subscription``,
          ``socioprofessional_class``, and any other columns present).
        - ``report`` is a :class:`braunschweig.popsim.seed.CompletenessReport`
          with ``completeness_rate = 1.0`` (no day filter for ENTD).

    Raises
    ------
    ValueError
        If ``sex`` contains values other than ``"male"`` or ``"female"``
        (fail-fast: an unmapped sex value would silently produce NaN in
        ``HP_SEX``, breaking the PopulationSim sex-margin controls).
    KeyError
        If required columns are absent from either frame.
    """
    _require_columns(households, ["household_id", "household_weight"], table_name="ENTD households")
    _require_columns(
        persons,
        ["household_id", "person_id", "person_weight", "age", "sex"],
        table_name="ENTD persons",
    )

    # Defensive guard (no-silent-fallback): the PopulationSim seed must carry the
    # FULL household composition. If the donor is accidentally the eqasim
    # person-matching frame (data.hts.selected -> data.hts.entd.reweighted keeps
    # ~1 person/household for IPF matching), the synthetic households would all
    # be 1-person. Warn loudly on a near-1 persons/household mean so the wiring
    # mistake is observable instead of producing a silently wrong population.
    n_hh_in = households["household_id"].nunique()
    mean_pph = len(persons) / max(n_hh_in, 1)
    if mean_pph < 1.2:
        logger.warning(
            "[EntdSource.build_seed] donor has only %.2f persons/household "
            "(%d persons / %d households) -- this looks like the reweighted "
            "person-matching frame, NOT the full composition. The PopulationSim "
            "seed must come from data.hts.entd.filtered (multi-person households), "
            "or every synthetic household will have exactly one person.",
            mean_pph, len(persons), n_hh_in,
        )

    # --- Validate and map sex -> HP_SEX (1=male, 2=female) ----------------
    # This is a fail-fast guard: an unmapped value would silently produce NaN
    # in HP_SEX and break the PopulationSim sex-margin controls.
    sex_map = {"male": 1, "female": 2}
    unmapped_sex = set(persons["sex"].unique()) - set(sex_map)
    if unmapped_sex:
        raise ValueError(
            f"[EntdSource.build_seed] persons 'sex' column contains unmapped "
            f"value(s) {unmapped_sex!r}. Only 'male' and 'female' are accepted. "
            f"Fix the ENTD person frame before building the PopulationSim seed."
        )

    # --- Rename household columns to MiD seed schema ----------------------
    hh_seed = households.copy()
    hh_seed = hh_seed.rename(columns={
        "household_id": "H_ID",
        "household_weight": "H_GEW",
    })

    # --- Rename person columns to MiD seed schema (retain ENTD attrs) -----
    p_seed = persons.copy()
    p_seed = p_seed.rename(columns={
        "household_id": "H_ID",
        "person_id": "P_ID",
        "person_weight": "P_GEW",
        "age": "HP_ALTER",
    })
    p_seed["HP_SEX"] = p_seed["sex"].map(sex_map)
    # Retain original sex string for downstream map_demographics (eqasim uses
    # the "sex" column; expand.map_demographics re-derives it from HP_SEX).
    # HP_SEX is the PopulationSim-visible column; sex stays as an extra.

    # --- Build HP_ID: unique integer per person ---------------------------
    # HP_ID is the PopulationSim person id (must be a unique integer).
    # Formula: H_ID * scale + P_ID (avoids collisions within each household's
    # P_ID range when household ids are large ENTD integers).
    # Scale = smallest power of 10 > max(P_ID), so ids don't overlap across
    # households.  The ENTD donor (~14k persons) is small; overflow is impossible.
    max_pid = int(p_seed["P_ID"].max()) if len(p_seed) > 0 else 1
    scale = 1
    while scale <= max_pid:
        scale *= 10
    hp_id_candidate = p_seed["H_ID"].astype(np.int64) * scale + p_seed["P_ID"].astype(np.int64)
    if hp_id_candidate.duplicated().any():
        logger.warning(
            "[EntdSource.build_seed] HP_ID formula H_ID*%d+P_ID produced "
            "%d duplicate(s); falling back to sequential arange(1..n).",
            scale,
            int(hp_id_candidate.duplicated().sum()),
        )
        p_seed["HP_ID"] = np.arange(1, len(p_seed) + 1, dtype=np.int64)
    else:
        p_seed["HP_ID"] = hp_id_candidate

    # --- Apply completeness filter (no-op: ENTD has no day-of-week filter) -
    # Using the standard filter_complete_households with the ENTD column mapping
    # (day_filter_col=None -> every household is "complete").  This produces a
    # CompletenessReport with completeness_rate=1.0 and drop_rate=0.0.
    # We use a temporary SeedColumns with H_ID/H_GEW/P_ID/P_GEW/HP_ALTER/HP_SEX
    # column names (the renamed frame) so the filter runs correctly.
    _mid_like_cols = SeedColumns(
        household_id="H_ID",
        household_weight="H_GEW",
        person_household_id="H_ID",
        person_id="P_ID",
        person_weight="P_GEW",
        age="HP_ALTER",
        sex="HP_SEX",
        day_filter_col=None,
        day_filter_values=None,
    )
    hh_seed, p_seed, report = filter_complete_households(
        hh_seed, p_seed, _mid_like_cols, day_filter_values=None
    )

    # --- Compute H_GR: persons per household (Tier-1 household_size control) --
    # H_GR is derived as the count of persons per (renamed) H_ID on the
    # post-completeness-filter seed frames. This is the DONOR household size.
    # PopulationSim evaluates the Tier-1 expression ``(households.H_GR == N)``
    # on the seed households frame, so H_GR must be present here.
    hgr = p_seed["H_ID"].value_counts().rename("H_GR")
    hh_seed = hh_seed.merge(hgr, left_on="H_ID", right_index=True, how="left")
    hh_seed["H_GR"] = hh_seed["H_GR"].fillna(0).astype(int)

    # --- select_seed_columns: add STAAT=1, keep essentials + extras -------
    # Extra household columns: urban_type (Phase 4B donor stratification),
    # H_GR (Tier-1 household-size control; Task 7).
    # Extra person columns: all ENTD attribute columns present on the renamed
    # frame (minus the essentials already selected, minus HP_SEX which is added
    # separately in the extra_person_cols so it stays).
    # We retain all ENTD-origin columns so map_person_attributes can use them
    # directly without another join -- this is the key design decision.
    hh_extra = [c for c in ("urban_type", "RegioStaR7", "H_GR") if c in hh_seed.columns]
    # Person extras: HP_ID + every ENTD attribute column (after rename, these
    # include employed, studies, has_license, has_pt_subscription,
    # socioprofessional_class, sex (original string), number_of_trips,
    # trip_weight, departement_id, and anything else the ENTD cleaned stage
    # produces).  Essentials are H_ID, P_ID, P_GEW, HP_ALTER, HP_SEX.
    essential_person_cols = {"H_ID", "P_ID", "P_GEW", "HP_ALTER", "HP_SEX"}
    p_extra = ["HP_ID"] + [
        c for c in p_seed.columns
        if c not in essential_person_cols and c not in ("HP_ID", "STAAT")
    ]

    hh_seed, p_seed = select_seed_columns(
        hh_seed, p_seed, _mid_like_cols,
        extra_household_cols=hh_extra,
        extra_person_cols=p_extra,
    )

    logger.info(
        "[EntdSource.build_seed] seed built: %d households, %d persons "
        "(completeness_rate=%.3f). "
        "Person columns: %s.",
        len(hh_seed), len(p_seed), report.completeness_rate,
        list(p_seed.columns),
    )
    return hh_seed, p_seed, report
