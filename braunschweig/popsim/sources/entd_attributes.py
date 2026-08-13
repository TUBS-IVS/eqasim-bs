"""ENTD person-attribute mapping: ENTD canonical columns -> the eqasim synthesis schema.

:func:`map_person_attributes` is the largest ENTD mapper (~300 lines): a
direct pass-through of the ENTD-cleaned columns plus three derivations
(``car_availability``, ``bicycle_availability``, ``age_range``) and two
defaults (``pt_subscription_type``, ``household_income``/``economic_status``
from ``income_class``).

Extracted verbatim from ``braunschweig.popsim.sources.entd`` (issue #267);
``entd.py`` re-exports the name so external imports of the facade module are
unaffected. ``EntdSource.map_person_attributes`` is a one-line delegation to
the module-level function here (``EntdSource`` has no instance state, so
``self`` carried nothing the moved body needed).
"""

from __future__ import annotations

import logging
from typing import Tuple

import numpy as np
import pandas as pd

from braunschweig.popsim.assembly import (
    _AGE_RANGE_BINS,
    _AGE_RANGE_LABELS,
    _household_availability,
)
from braunschweig.popsim.attributes import (
    derive_car_availability,
    derive_bicycle_availability,
)
from braunschweig.popsim import income as _income_module
from braunschweig.popsim.sources.entd_schema import _require_columns
from braunschweig.popsim.sources.entd_vocabulary import (
    ENTD_HIGH_INCOME_CLASS,
    _DIRECT_PERSON_COLS,
    _ENTD_INCOME_CLASS_TO_LABEL,
    _H4_INCOME_CLASS_BY_MID_LABEL,
    _HH_JOIN_COLS,
    _PT_TYPE_NONE,
    _PT_TYPE_SUBSCRIBER,
)

# Legacy EUR-class -> 5-class economic status map (the status_from_hhtype=False
# fallback semantics). No circular import: braunschweig.synthesis never imports
# from braunschweig.popsim (verified), and this package already imports from the
# shared synthesis tree (see braunschweig.popsim.sources.entd_diary_matching,
# which imports synthesis.population.matched).
from braunschweig.synthesis.population.enriched import ECONOMIC_STATUS_BY_INCOME_CLASS

# Logger name string identical to the facade's (braunschweig.popsim.sources.entd)
# so log records emitted from here are indistinguishable from records emitted
# before the extraction; logging.getLogger caches by name, so this returns the
# SAME logger object as the facade's `logging.getLogger(__name__)`.
logger = logging.getLogger("braunschweig.popsim.sources.entd")


def map_person_attributes(
    persons: pd.DataFrame,
    households: pd.DataFrame,
    *,
    rng=None,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Map ENTD canonical columns to the eqasim synthesis schema.

    This mapper is called by :func:`braunschweig.popsim.assembly.build_persons`
    AFTER :func:`braunschweig.popsim.expand.expand_to_persons` and
    :func:`braunschweig.popsim.expand.map_demographics` have been applied.

    After ``build_seed`` transforms the ENTD frames to MiD schema and
    ``expand_to_persons`` joins on ``H_ID``, the expanded persons frame carries
    ``H_ID`` as the donor household key (the same integer that was passed to
    PopulationSim as the seed household id).  The ``household_id`` column at
    this point is the SYNTHETIC id (``<cell>_<H_ID>_<occurrence>``), not the
    ENTD donor household id.

    The ENTD household join therefore uses ``H_ID`` as the join key on both
    sides.  The ``_HH_JOIN_COLS`` list maps ``household_id -> H_ID`` via a
    rename so the merge key is unambiguous.

    Parameters
    ----------
    persons:
        Expanded persons frame after ``expand_to_persons`` + ``map_demographics``
        + ``derive_zone_ids`` (i.e. the frame produced inside
        :func:`braunschweig.popsim.assembly.build_persons`).  Carries ``H_ID``
        (donor household key, populated by ``build_seed`` rename) and ``P_ID``
        (donor person key); also carries the ENTD attribute columns retained
        by ``build_seed.select_seed_columns`` (``employed``, ``studies``,
        ``has_license``, ``has_pt_subscription``, ``socioprofessional_class``,
        etc.).
    households:
        ENTD household frame from ``load_donor`` (original ENTD canonical
        schema: ``household_id``, ``household_size``, ``number_of_cars``,
        ``number_of_bicycles``, ``income_class``).
    rng:
        Not used for ENTD (all attributes are directly available); accepted
        for interface compatibility with ``PopsimSource.map_person_attributes``.

    Returns
    -------
    tuple[pd.DataFrame, pd.DataFrame]
        ``(persons, pseudonym_map)`` per the unified mapper contract.
        ``persons`` is the frame extended with all required schema columns
        except ``is_urban_resident`` (added later by ``build_persons`` from
        the home commune).  ``pseudonym_map`` is EMPTY (columns
        ``[source_person_id, source_household_id, H_ID, P_ID]``) because
        ENTD is open data and no surrogate ids are assigned; it is explicit
        so ``build_persons`` can enforce the tuple contract instead of
        silently substituting an empty map.

    Notes
    -----
    - ``source_person_id`` is set to ``P_ID`` (the ENTD donor person integer id).
    - ``source_household_id`` is set to ``H_ID`` (the ENTD donor household integer id).
    - Both are raw ENTD ids (open data, no pseudonymisation).
    - ``weight = 1.0`` (the popsim_open frame is already expanded).
    - ``household_income_eur`` is set to the raw ENTD income-class midpoint.
      The INKAR per-Kreis scaling is applied by ``build_persons`` AFTER this
      mapper returns, so all sources use the same shared scaling step.
    - ``high_income`` is a placeholder (set to False here); ``build_persons``
      overwrites it with ``household_income_eur >= 5000 EUR`` after INKAR scaling.
    - ``economic_status`` is an APPROXIMATION: ENTD has no native status
      field, so it is derived from the categorical ``household_income`` via
      ``_H4_INCOME_CLASS_BY_MID_LABEL`` + ``ECONOMIC_STATUS_BY_INCOME_CLASS``
      (legacy ``status_from_hhtype=False`` semantics); NaN income -> NaN status.
    """
    out = persons.copy()

    # --- Determine the donor household join key ----------------------------
    # After build_seed + expand_to_persons the donor household key is "H_ID"
    # (the ENTD household_id renamed to the MiD seed schema name).  The
    # synthetic household_id is a different column ("ZENSUS100m_H_ID_occ").
    # The ENTD households frame still carries the original "household_id" name,
    # so we must translate: join expanded persons.H_ID onto households.household_id.
    if "H_ID" in out.columns:
        # Post-build_seed path: H_ID is the donor key (populated by rename in build_seed).
        persons_donor_key = "H_ID"
        hh_donor_key = "household_id"
    else:
        # Fallback: direct ENTD injection without build_seed (e.g. direct test call
        # where persons still carry household_id as the ENTD canonical name).
        # This path is only used by tests that call map_person_attributes directly
        # with the raw ENTD persons frame (pre-expand), not the post-expand frame.
        persons_donor_key = "household_id"
        hh_donor_key = "household_id"

    # --- Direct copy: columns already canonical in ENTD cleaned output ---
    # Verify required direct columns are present (fail-fast).
    _require_columns(out, _DIRECT_PERSON_COLS, table_name="ENTD persons")
    _require_columns(households, _HH_JOIN_COLS, table_name="ENTD households")

    # --- Join household attributes onto persons ---
    hh_attrs = households[_HH_JOIN_COLS].copy()
    # Rename the household join key on the households side to a neutral name
    # to avoid column name collisions on the output frame.
    hh_attrs = hh_attrs.rename(columns={hh_donor_key: "_hh_join_key"})
    out = out.merge(
        hh_attrs,
        left_on=persons_donor_key,
        right_on="_hh_join_key",
        how="left",
        suffixes=("", "_hh"),
    ).drop(columns=["_hh_join_key"])

    n_unmatched = int(out["household_size"].isna().sum())
    if n_unmatched > 0:
        logger.warning(
            "[EntdSource] map_person_attributes: %d/%d persons have no matching "
            "household after join (primary household_id merge failed). "
            "Check that persons.household_id keys exist in households.",
            n_unmatched, len(out),
        )

    out["number_of_cars"] = out["number_of_cars"].fillna(0).astype(int)
    out["number_of_bicycles"] = out["number_of_bicycles"].fillna(0).astype(int)
    out["household_size"] = out["household_size"].fillna(0).astype(int)

    # --- car_availability and bicycle_availability ---
    # car_availability: cars vs. adults (age >= 18) per household.
    out["car_availability"] = _household_availability(
        out, count_col="number_of_cars", adults_only=True,
        derive=derive_car_availability,
    )
    # bicycle_availability: bicycles vs. all household members.
    out["bicycle_availability"] = _household_availability(
        out, count_col="number_of_bicycles", adults_only=False,
        derive=derive_bicycle_availability,
    )

    # --- age_range ---
    # Matches assembly._AGE_RANGE_BINS / _AGE_RANGE_LABELS exactly so that
    # the ENTD and MiD workflows produce the same categorical age bands.
    out["age_range"] = pd.cut(
        out["age"],
        bins=_AGE_RANGE_BINS,
        labels=_AGE_RANGE_LABELS,
    )

    # --- household_income (categorical label from ENTD income_class) ---
    # Approximation: ENTD income bands are French-survey bounds; MiD bands
    # are German. The mapping is documented in the module docstring table.
    out["household_income"] = (
        out["income_class"].map(_ENTD_INCOME_CLASS_TO_LABEL)
    )
    n_income_missing = int(out["household_income"].isna().sum())
    if n_income_missing > 0:
        logger.info(
            "[EntdSource] map_person_attributes: %d/%d persons have "
            "household_income=NaN (income_class -1 or unmapped). "
            "Primary income mapping rate: %.1f%%.",
            n_income_missing, len(out),
            100.0 * (len(out) - n_income_missing) / max(len(out), 1),
        )

    # --- economic_status (APPROXIMATION: derived from the income class) ---
    # ENTD has no native economic-status field. Derive the 5-class BMDV
    # status from the MiD-mapped household_income label via the bridge +
    # the legacy inverse map (status_from_hhtype=False fallback semantics;
    # the MiD Bayes hhtype x region machinery is NOT applied). NaN income
    # (ENTD income_class -1) stays NaN -- economic_status is schema-optional
    # and a missing income must not invent a status.
    status = (
        out["household_income"]
        .map(_H4_INCOME_CLASS_BY_MID_LABEL)
        .map(ECONOMIC_STATUS_BY_INCOME_CLASS)
    )
    unmapped_income = out["household_income"].notna() & status.isna()
    if unmapped_income.any():
        unmapped_labels = sorted(out.loc[unmapped_income, "household_income"].unique())
        raise ValueError(
            f"[EntdSource] map_person_attributes: household_income label(s) "
            f"{unmapped_labels!r} have no economic_status mapping "
            f"(_H4_INCOME_CLASS_BY_MID_LABEL / ECONOMIC_STATUS_BY_INCOME_CLASS). "
            f"The income vocabulary drifted; extend the bridge instead of "
            f"silently producing NaN."
        )
    out["economic_status"] = status
    n_status_missing = int(status.isna().sum())
    logger.info(
        "[EntdSource] map_person_attributes: economic_status derived from the "
        "income class (APPROXIMATION, no native ENTD status) for %d/%d persons "
        "(%.1f%%); %d (%.1f%%) stay NaN (missing ENTD income).",
        len(out) - n_status_missing, len(out),
        100.0 * (len(out) - n_status_missing) / max(len(out), 1),
        n_status_missing,
        100.0 * n_status_missing / max(len(out), 1),
    )

    # --- household_income_eur (raw ENTD midpoint; INKAR scaling applied later) ---
    # Set household_income_eur to the raw ENTD income-class midpoint.
    # build_persons applies INKAR per-Kreis scaling AFTER this mapper returns
    # (for all sources), so this value is overwritten there with
    # midpoint * INKAR_scale[Kreis].  The high_income flag is also set by
    # build_persons to the unified rule (eur >= 5000 EUR).
    out["household_income_eur"] = pd.to_numeric(
        out["income_class"].map(_income_module.ENTD_INCOME_CLASS_MIDPOINT_EUR),
        errors="coerce",
    )

    # --- high_income (placeholder; overwritten by build_persons after INKAR) ---
    # Set a placeholder here so the column exists for schema validation.
    # build_persons overwrites this with household_income_eur >= 5000 after
    # the INKAR scaling step.
    out["high_income"] = out["income_class"] >= ENTD_HIGH_INCOME_CLASS

    # --- pt_subscription_type (default: no ticket-type field in ENTD) ---
    # Subscribers get a representative flatrate ticket type;
    # non-subscribers get "fahre_nie" (structurally absent from PT).
    # Use pd.Series then cast to pandas StringDtype (np.astype("string")
    # is not supported in older NumPy versions).
    out["pt_subscription_type"] = pd.Series(
        np.where(
            out["has_pt_subscription"].astype(bool),
            _PT_TYPE_SUBSCRIBER,
            _PT_TYPE_NONE,
        ),
        index=out.index,
    ).astype("string")

    # --- Provenance IDs (ENTD is open data, no pseudonymisation needed) ---
    # After build_seed + expand_to_persons:
    #   - H_ID  = ENTD donor household_id (renamed in build_seed)
    #   - P_ID  = ENTD donor person_id    (renamed in build_seed)
    # In the direct-test path (no build_seed), persons still carry the
    # original ENTD "person_id" and "household_id" column names.
    if "H_ID" in out.columns and "P_ID" in out.columns:
        out["source_person_id"] = out["P_ID"]
        out["source_household_id"] = out["H_ID"]
    else:
        # Fallback for tests that call map_person_attributes directly with
        # the raw ENTD persons frame (pre-expand, pre-build_seed).
        out["source_person_id"] = out["person_id"]
        out["source_household_id"] = out[persons_donor_key]

    # --- weight = 1.0 (popsim_open frame is already expanded, one row per person) ---
    out["weight"] = 1.0

    # --- household_size: number of persons in each SYNTHETIC household ----
    # Replicates map_mid_person_attributes: size is the count of persons
    # sharing the same synthetic household_id (set by assign_synthetic_household_ids).
    # Note: the "household_size" from the ENTD households table (donor HH size)
    # was joined above and may be present as "household_size" from the merge.
    # We OVERWRITE it with the synthetic household size because PopulationSim
    # may replicate one donor household multiple times, changing the effective
    # size.  The "household_size" column on the output must reflect the
    # synthetic household, not the original donor.
    if "household_id" in out.columns:
        out["household_size"] = (
            out.groupby("household_id")["person_id"].transform("size")
        )
    elif "household_size" not in out.columns:
        # If no household_id column (unusual path), keep the joined value if present.
        # Logged so the caller can investigate.
        logger.warning(
            "[EntdSource] map_person_attributes: 'household_id' not found in persons; "
            "household_size may reflect donor household size (not synthetic size)."
        )

    # --- is_urban_resident: True when person lives in Braunschweig city ----
    # Replicates map_mid_person_attributes exactly (see assembly.py line ~310):
    #   is_urban_resident = (departement_id == "03101")
    # ``departement_id`` is derived by derive_zone_ids (assembly.build_persons)
    # from the 12-digit ARS before the mapper is called.  For persons outside
    # the ZGB area the column is still present (derive_zone_ids is unconditional).
    _BS_KREIS5 = "03101"
    if "departement_id" in out.columns:
        out["is_urban_resident"] = out["departement_id"] == _BS_KREIS5
    else:
        # derive_zone_ids was not called (direct-test path without ARS column).
        # Set a placeholder so schema validation doesn't crash; callers that
        # need a correct value must ensure derive_zone_ids runs first.
        out["is_urban_resident"] = False
        logger.warning(
            "[EntdSource] map_person_attributes: 'departement_id' not found; "
            "is_urban_resident set to False (placeholder). "
            "Ensure derive_zone_ids ran before calling map_person_attributes."
        )

    logger.info(
        "[EntdSource] map_person_attributes: %d persons mapped. "
        "car_availability none/some/all: %s; "
        "pt_subscription_type subscriber/non: %d/%d.",
        len(out),
        dict(out["car_availability"].value_counts()),
        int(out["has_pt_subscription"].sum()),
        int((~out["has_pt_subscription"]).sum()),
    )

    # Unified mapper contract: return (persons, pseudonym_map). ENTD is open
    # data, so no surrogates are assigned and the map is empty -- but it is
    # returned EXPLICITLY so build_persons can enforce the tuple contract
    # (a pseudonymisation-required source can never silently lose its map).
    empty_pseudonym_map = pd.DataFrame(
        columns=["source_person_id", "source_household_id", "H_ID", "P_ID"]
    )
    return out, empty_pseudonym_map
