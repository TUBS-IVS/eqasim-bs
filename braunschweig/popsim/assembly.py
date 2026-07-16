"""Assemble the popsim_mid persons frame from the expanded donor population.

Composes the small building blocks (``expand`` + ``attributes``) into one persons
frame: each synthetic household is expanded to its MiD donor persons, demographics
and person attributes are mapped, the MiD donor household attributes are joined,
and car availability is derived per synthetic household from cars vs. adults.

This is the harmonisation core of popsim_mid; PT subscription, bicycle
availability and the activity chains (``braunschweig.popsim.trips``) + home
coordinates (``braunschweig.popsim.handoff``) are layered on top.
"""

from __future__ import annotations

import functools

import numpy as np
import pandas as pd

from braunschweig.popsim import attributes
from braunschweig.popsim import expand
from braunschweig.popsim import income as _income_module
from braunschweig.population import schema

def assign_donor_surrogates(
    persons: pd.DataFrame,
    *,
    donor_col: str = "H_ID",
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Replace raw MiD donor ids with sequential surrogate integers.

    The MiD survey data are restricted scientific-use microdata (BMDV licence).
    Publishing the raw ``H_ID`` / ``P_ID`` values in the synthetic output would
    allow re-identification of the survey respondent each synthetic agent was
    derived from. This function assigns deterministic, reproducible surrogate
    integers that reveal nothing about the real MiD respondent without the mapping.

    Surrogate assignment:
    - ``source_household_id``: ``pd.factorize(H_ID, sort=True)[0] + 1`` -- each
      unique donor household is assigned a unique sequential integer starting at 1.
    - ``source_person_id``: ``pd.factorize((H_ID, P_ID), sort=True)[0] + 1`` --
      each unique donor (H_ID, P_ID) pair is assigned a unique sequential integer.

    Member-completion traceability: when the frame carries the total columns
    ``source_H_ID`` / ``source_P_ID`` (set by
    ``braunschweig.popsim.member_completion``), those are PREFERRED as the
    factorization keys.  A filler person carries a synthetic (host ``H_ID``,
    fresh ``P_ID``) pair that corresponds to NO real MiD respondent; its
    ``source_*`` ids reference the MIRROR donor person, so the surrogate (and
    the pseudonym map) stays re-linkable to a real respondent.  Regular persons
    carry their own ids in ``source_*``, so this is a no-op for them; frames
    without the columns (legacy flag-OFF path) fall back to ``H_ID`` / ``P_ID``
    unchanged.

    Both surrogates are deterministic because ``sort=True`` ensures the same input
    always produces the same mapping. Using ``+ 1`` shifts the range from [0, N-1]
    to [1, N] so surrogates are clean positive integers (``java.lang.Long`` in the
    MATSim XML output).

    The raw ``H_ID`` / ``P_ID`` columns are NOT removed; they remain on the frame
    for the trips join (``braunschweig.popsim.trips`` needs them). They are never
    included in any output/writer field list (verified against
    ``matsim.scenario.population.PERSON_FIELDS`` and
    ``synthesis.output.select_person_output_columns``).

    Parameters
    ----------
    persons:
        Persons frame from ``expand.expand_to_persons``; must carry ``H_ID``
        (the donor household key) and ``P_ID`` (the donor person key).
    donor_col:
        Column name of the donor household key (default ``H_ID``).

    Returns
    -------
    tuple[pandas.DataFrame, pandas.DataFrame]
        ``(persons, mapping)`` where ``persons`` carries the new
        ``source_household_id`` and ``source_person_id`` columns, and
        ``mapping`` is a DataFrame with columns
        ``[source_person_id, source_household_id, H_ID, P_ID]`` suitable for
        writing as the local-only pseudonym map (``work_dir/pseudonym_map.csv``).

    Raises
    ------
    KeyError
        If ``donor_col`` or ``P_ID`` is absent from ``persons`` (fail-fast:
        these columns must be present after expand.expand_to_persons).
    """
    for col in (donor_col, "P_ID"):
        if col not in persons.columns:
            raise KeyError(
                f"[popsim.assembly] Column {col!r} not found in persons frame. "
                "assign_donor_surrogates requires H_ID and P_ID from "
                "expand.expand_to_persons."
            )

    out = persons.copy()

    # Prefer the member-completion traceability ids when present (see docstring):
    # fillers then factorize to their MIRROR donor's real (H_ID, P_ID); for
    # regular persons source_* equal the own ids, so the result is identical to
    # the legacy keys. Without the columns (flag-OFF path) behaviour is unchanged.
    use_source_ids = (
        "source_H_ID" in out.columns and "source_P_ID" in out.columns
    )
    donor_hh_key = out["source_H_ID"] if use_source_ids else out[donor_col]
    donor_p_key = out["source_P_ID"] if use_source_ids else out["P_ID"]

    # --- household surrogate ---
    hh_codes, _ = pd.factorize(donor_hh_key, sort=True)
    out["source_household_id"] = (hh_codes + 1).astype(int)

    # --- person surrogate: unique (H_ID, P_ID) pair ---
    # Encode the pair as a single sortable key for factorize. pandas >= 2.x requires
    # an array-like (not a bare list); wrap the tuples in a 1-D object Index
    # (tupleize_cols=False keeps each pair a single element, not a MultiIndex) so the
    # sort order and surrogate assignment are identical to the pre-2.x behaviour.
    pair_key = pd.Index(
        list(zip(donor_hh_key.tolist(), donor_p_key.tolist())), tupleize_cols=False
    )
    p_codes, unique_pairs = pd.factorize(pair_key, sort=True)
    out["source_person_id"] = (p_codes + 1).astype(int)

    # --- build the local-only mapping for re-linking ---
    # One row per unique (source_person_id, source_household_id, H_ID, P_ID).
    # Use the first occurrence of each person-surrogate code (all occurrences of
    # a given pair are identical in H_ID / P_ID by construction).
    # unique_pairs from factorize(sort=True) is sorted, so surrogate i+1 maps to
    # unique_pairs[i].
    n_pairs = len(unique_pairs)
    h_ids = [pair[0] for pair in unique_pairs]
    p_ids = [pair[1] for pair in unique_pairs]
    # The household surrogate for each pair: factorize the H_IDs of the mapping.
    # (pandas >= 2.x: pass an array-like, not a bare list.)
    hh_surr_codes, _ = pd.factorize(pd.Index(h_ids), sort=True)
    mapping = pd.DataFrame({
        "source_person_id":    range(1, n_pairs + 1),
        "source_household_id": (hh_surr_codes + 1).tolist(),
        "H_ID": h_ids,
        "P_ID": p_ids,
    })

    return out, mapping


# Column name of the 12-digit ARS key that the cells parquet carries and that
# stage.py joins onto the merged PopulationSim output before calling build_persons.
# The name is spelled with one 's' ("Schlussel") to match the parquet source column.
ARS_COLUMN = "RegionalSchlussel_ARS"


def derive_zone_ids(df: pd.DataFrame, *, ars_col: str = ARS_COLUMN) -> pd.DataFrame:
    """Derive the three spatial zone IDs from the 12-digit ARS column.

    The eqasim spatial pipeline keys homes and home-location candidates on the
    SAME ``commune_id`` / ``iris_id`` that ``data.spatial.municipalities`` and the
    default IPF producer (braunschweig.ipf.attributed lines 814-816) use, which is
    the **full 12-digit ARS** (NOT the 8-digit AGS). Using the AGS here produces an
    ``iris_id`` that matches no home-location candidate, so the home-location
    sampling stage asserts ``location_count > 0`` and crashes. The formats are:

    - ``commune_id``    = the 12-digit ARS string, e.g. "031010000000" -- identical
                          to ``data.spatial.municipalities.commune_id`` (verified
                          against the working IPF cache). AGS8 consumers (RegioStaR)
                          convert via ``ars_to_ags8`` themselves, which is idempotent.
    - ``departement_id``= first 5 chars of commune_id = 5-digit Kreis string, e.g. "03101".
                          Matches ipf/prepare.py line 126 (commune_id[:5]).
    - ``iris_id``       = commune_id + "0000" stored as category, e.g. "0310100000000000".
                          Source: ipf/attributed.py lines 815-816 (= eqasim_common
                          .data.spatial.iris line 17). Germany has no sub-commune
                          IRIS zones; "0000" is the eqasim placeholder.

    Parameters
    ----------
    df:
        Frame that carries ``ars_col`` (the 12-digit ARS from the Zensus cell parquet).
    ars_col:
        Name of the ARS column in ``df``.

    Returns
    -------
    pandas.DataFrame
        A copy of ``df`` with three new columns: ``commune_id``, ``departement_id``,
        ``iris_id``.

    Raises
    ------
    KeyError
        If ``ars_col`` is not present in ``df`` (fail-fast: a missing ARS column
        means stage.py did not join it; the spatial home.zones stage would crash
        with a less informative KeyError otherwise).
    """
    if ars_col not in df.columns:
        raise KeyError(
            f"[popsim.assembly] ARS column {ars_col!r} not found in persons frame. "
            "stage.py must join the cells ARS onto merge_report.combined before "
            "calling build_persons (fix for spatial home.zones KeyError D1)."
        )
    out = df.copy()
    # commune_id: the full 12-digit ARS (zero-padded), identical to
    # data.spatial.municipalities.commune_id and the IPF producer. This is what the
    # home-location candidates are keyed on; using the 8-digit AGS broke the
    # home-location join (the popsim iris_id matched no candidate).
    out["commune_id"] = out[ars_col].astype(str).str.zfill(12)
    # departement_id: 5-digit Kreis prefix.  Matches ipf/prepare.py:126
    # (df_population["commune_id"].str[:5]).
    out["departement_id"] = out["commune_id"].str[:5]
    # iris_id: commune_id + "0000".  Matches ipf/attributed.py lines 815-816 and
    # eqasim_common.data.spatial.iris line 17. Germany has no sub-commune IRIS zones.
    out["iris_id"] = (out["commune_id"] + "0000").astype("category")
    return out

# age_range bins and labels — MUST match synthesis/population/enriched.py lines
# 110-114 exactly so both population workflows produce the same categorical values
# consumed by the spatial (education / gravity) stages. The bins correspond to:
#   (-1, 10] = primary_school (age <= 10)
#   (10, 14] = middle_school  (age 11-14)
#   (14, 17] = high_school    (age 15-17)
#   (17, inf) = higher_education (age >= 18, the default in enriched.py)
_AGE_RANGE_BINS: list = [-1, 10, 14, 17, np.inf]
_AGE_RANGE_LABELS: list[str] = [
    "primary_school", "middle_school", "high_school", "higher_education"
]

# Age (completed years) from which a person counts as an adult for car availability.
ADULT_AGE = 18

_HOUSEHOLD_ATTRS = [
    "economic_status", "household_income", "household_income_eur",
    "number_of_cars", "number_of_bicycles",
    # has_ebike (0/1 int, attributes.map_has_ebike from H_ANZPED): written onto the
    # persons frame so the has_ebike KREIS control is measurable against the realized
    # population, not just derivable on the seed (server-verified 2026-07-08).
    "has_ebike",
    # Tier-2 popsim control attributes: housing_tenure and building_type_3class are
    # derived from H_MIETE / haustyp in attributes.map_housing_tenure /
    # map_building_type_3class and joined from donor_hh onto the persons frame so
    # the popsim control-fit validation can compare realized vs. census target.
    # Absent when the donor lacks the column (ENTD path); the categorical_household_control
    # extractor logs a WARNING and skips the control when the column is absent.
    "housing_tenure", "building_type_3class",
]


def map_mid_person_attributes(
    persons: pd.DataFrame,
    mid_households: pd.DataFrame,
    *,
    donor_col: str = "H_ID",
    rng=None,
    rs7_conditioning: bool = True,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Apply the MiD attribute-mapping sequence to a pre-expanded, pre-zoned persons frame.

    This function encapsulates the attribute-mapping portion of ``build_persons``
    (everything after ``expand.map_demographics`` and ``derive_zone_ids`` have been
    applied).  It is called by BOTH ``build_persons`` AND
    ``braunschweig.popsim.sources.mid.MidSource.map_person_attributes`` so that
    the two code paths are byte-identical.

    INKAR income scaling is NOT applied here.  It is applied in ``build_persons``
    AFTER this mapper returns, so that all sources (MiD and ENTD) go through the
    same shared INKAR step.  The ``household_income_eur`` set here is the raw MiD
    class midpoint (= ``INCOME_GROUP_MIDPOINT_EUR``); ``build_persons`` overwrites it
    with ``midpoint * INKAR_scale[Kreis]`` and sets ``high_income`` accordingly.

    Parameters
    ----------
    persons:
        Persons frame produced by ``expand.expand_to_persons`` +
        ``expand.map_demographics`` + ``derive_zone_ids``.
    mid_households:
        The MiD donor household table (must contain the columns expected by the
        ``attributes.*`` household mappers: ``H_ID``, ``oek_status``,
        ``hheink_gr1``, ``H_ANZAUTO``, ``anzpedrad`` (bicycles including pedelecs,
        the ``map_number_of_bicycles`` default source), and ``H_ANZPED`` (the
        verified e-bike column, the ``map_has_ebike`` default source).
    donor_col:
        Column name of the donor household key (default ``H_ID``).
    rng:
        Random state for stochastic attribute imputation (employment, licence,
        PT subscription).  Defaults to ``np.random.RandomState(0)`` for backward
        compatibility.
    rs7_conditioning:
        Condition the item-nonresponse imputation pools additionally on
        ``RegioStaR7`` (issue #131; default ON). Person-level mappers use the
        PLACED home cell's RS7, household-level mappers the donor's survey home
        region. ``False`` restores the one-dimensional pools (A/B escape hatch).

    Returns
    -------
    tuple[pandas.DataFrame, pandas.DataFrame]
        ``(persons, pseudonym_map)`` — same contract as ``build_persons``.
    """
    rng = rng if rng is not None else np.random.RandomState(0)

    persons = attributes.map_employed(persons, rng=rng, rs7_conditioning=rs7_conditioning)
    # employment_status (P9 taxonomy, MiD P_BKAT) rides alongside the boolean
    # employed flag -- NOT a popsim control, additive for analysis/validation
    # (see attributes.map_employment_status docstring).
    persons = attributes.map_employment_status(persons, rng=rng, rs7_conditioning=rs7_conditioning)
    # Derive studies from P_TAET (Ausbildung/Schueler/Student -> True) BEFORE
    # map_socioprofessional_class, which uses the studies flag in its fallback path.
    # Bug D4: studies was absent, so the fallback treated all students as studies=False.
    persons = attributes.map_studies(persons)
    persons = attributes.map_has_license(persons, rng=rng, rs7_conditioning=rs7_conditioning)
    persons = attributes.map_has_pt_subscription(persons, rng=rng, rs7_conditioning=rs7_conditioning)

    donor_hh = attributes.map_building_type_3class(
        attributes.map_housing_tenure(
            attributes.map_has_ebike(
                # map_number_of_bicycles now defaults to bikes_col="anzpedrad" (bicycles
                # INCLUDING pedelecs/e-bikes, MiD H12.3 / SrV alle-Raeder construct;
                # verified 2026-07-08), a deliberate construct change from the previous
                # H_ANZRAD (conventional-bikes-only) default -- see the function docstring.
                attributes.map_number_of_bicycles(
                    attributes.map_number_of_cars(
                        attributes.map_household_income(
                            attributes.map_household_income_eur(
                                attributes.map_economic_status(
                                    mid_households, rs7_conditioning=rs7_conditioning
                                ),
                                rs7_conditioning=rs7_conditioning,
                            ),
                            rs7_conditioning=rs7_conditioning,
                        ),
                        rs7_conditioning=rs7_conditioning,
                    ),
                    rs7_conditioning=rs7_conditioning,
                ),
                rs7_conditioning=rs7_conditioning,
            )
        )
    )
    # RegioStaR7 collision guard: the donor household frame ALSO carries a
    # 'RegioStaR7' column (the DONOR's survey home region, MID_HOUSEHOLD_ATTR_COLS,
    # used upstream for Phase 4B donor stratification). It is deliberately NOT
    # part of _HOUSEHOLD_ATTRS, so this merge never brings it onto the persons
    # frame -- the persons frame's 'RegioStaR7' is the SYNTHETIC HOME's cell
    # value (joined by stage.join_cell_attributes onto the merged households and
    # carried through the expansion), which is the spatial stage-B matching key.
    # If the donor RS7 is ever needed on persons, merge it as 'donor_RegioStaR7'.
    # Select only the household-attribute columns that are actually present on
    # donor_hh (the new Tier-2 columns housing_tenure / building_type_3class are
    # absent when the donor lacks H_MIETE / haustyp, e.g. on the ENTD path or in
    # existing test fixtures that pre-date the Tier-2 addition). The core attrs
    # (economic_status, household_income, household_income_eur, number_of_cars,
    # number_of_bicycles, has_ebike) are always present after the attribute mappers
    # above (MiD donor only; map_has_ebike raises if H_ANZPED is absent).
    available_attrs = [a for a in _HOUSEHOLD_ATTRS if a in donor_hh.columns]
    persons = _attach_donor_household_attrs(
        persons, donor_hh, donor_col, available_attrs,
    )

    persons["car_availability"] = _household_availability(
        persons, count_col="number_of_cars", adults_only=True,
        derive=attributes.derive_car_availability,
    )
    persons["bicycle_availability"] = _household_availability(
        persons, count_col="number_of_bicycles", adults_only=False,
        derive=attributes.derive_bicycle_availability,
    )

    # --- Popsim control-fit attributes ----------------------------------------
    # hh_type5: 5-class Zensus Familientyp per synthetic household, derived from
    # the expanded persons' ages. Uses the same derive_hh_type5 function called
    # at seed-build time so realized and target use the same classification logic.
    # ``age`` is always present after expand.map_demographics; ``household_id``
    # after expand.assign_synthetic_household_ids. The result is a per-household
    # Series (index = household_id values); broadcast to every person via map().
    if "age" in persons.columns and "household_id" in persons.columns:
        from braunschweig.popsim import seed as _seedmod
        hh_type5_series = _seedmod.derive_hh_type5(
            persons,
            household_id_col="household_id",
            age_col="age",
        )
        persons["hh_type5"] = persons["household_id"].map(hh_type5_series)
    # --- Schema-gap columns (integration spec Section 5.1) -------------------
    # age_range: matches synthesis/population/enriched.py lines 110-114 exactly.
    # (-1,10] = primary_school; (10,14] = middle_school; (14,17] = high_school;
    # (17,inf) = higher_education (the default in enriched.py, age >= 18).
    persons["age_range"] = pd.cut(
        persons["age"],
        bins=_AGE_RANGE_BINS,
        labels=_AGE_RANGE_LABELS,
    )

    # high_income: interim flag set from the MiD label; will be overwritten by
    # the INKAR scaling step in build_persons which applies the unified numeric rule.
    # This placeholder ensures the column exists before schema validation.
    persons["high_income"] = persons["household_income"] == "over_7000"

    # household_size: number of persons in the synthetic household.
    persons["household_size"] = (
        persons.groupby("household_id")["person_id"].transform("size")
    )

    # is_urban_resident: True when the person lives inside the Braunschweig core
    # city (Kreisfreie Stadt Braunschweig, AGS-5 = "03101").
    #
    # Replicates the DEFAULT braunschweig path exactly:
    #   synthesis/population/enriched.py line 2785:
    #       is_urban_resident = inside_braunschweig
    # where ``inside_braunschweig`` is the flag set when the person's Kreis-5
    # code equals "03101" (see INSIDE_FLAG_TO_ARS5 in enriched.py line 1275).
    # commune_id is the 8-digit AGS (e.g. "03101000") produced by derive_zone_ids
    # above; departement_id == commune_id[:5] is the exact equivalent predicate.
    # Since Braunschweig is a kreisfreie Stadt (one commune, "03101000"), the
    # predicates commune_id.startswith("03101") and departement_id == "03101"
    # are equivalent and both faithful to the default's single-commune definition.
    _BS_KREIS5 = "03101"
    persons["is_urban_resident"] = persons["departement_id"] == _BS_KREIS5

    # Pseudonymise donor ids: replace raw MiD H_ID / P_ID with sequential surrogate
    # integers so the published output is not re-identifiable without the mapping.
    # The surrogates are deterministic (factorize sort=True) and reproducible.
    # The mapping (surrogate -> H_ID / P_ID) is returned as the second element of
    # the return tuple; stage.py writes it to work_dir as a local-only
    # pseudonym_map.csv for internal re-linking.
    # Raw H_ID / P_ID columns remain on the frame for the trips join (trips_stage
    # needs them) but are NOT in any output/writer field list (verified: PERSON_FIELDS
    # in matsim/scenario/population.py and select_person_output_columns in synthesis/output.py
    # contain only source_person_id / source_household_id via hts_id / hts_household_id,
    # never H_ID or P_ID directly).
    persons, donor_map = assign_donor_surrogates(persons, donor_col=donor_col)

    persons = attributes.map_socioprofessional_class(persons)
    persons = attributes.map_pt_subscription_type(persons, rng=rng, rs7_conditioning=rs7_conditioning)

    # weight = 1.0: popsim_mid produces an already-expanded population (each row
    # is one synthetic person, no stochastic rounding needed). synthesis.population.sampled
    # requires this column; it uses floor(weight) + Bernoulli(frac) to replicate households,
    # so weight=1.0 means every synthetic household is replicated exactly once before the
    # sampling_rate selection, matching the behaviour of braunschweig.ipf.attributed.
    persons["weight"] = 1.0

    return persons, donor_map


def _attach_donor_household_attrs(
    persons: pd.DataFrame,
    donor_hh: pd.DataFrame,
    donor_col: str,
    available_attrs: list,
) -> pd.DataFrame:
    """Left-merge donor household attributes onto persons, with join-coverage logging.

    Every synthetic person's ``donor_col`` should reference an existing donor
    household row (referential integrity of the expansion). An unmatched donor
    id leaves the count attributes NaN, which the fills below silently turn
    into "0 cars / 0 bicycles / no e-bike" -- so the match coverage is counted
    and any unmatched person is surfaced as a WARNING instead of silently
    zero-filled (CLAUDE.md no-silent-fallback).
    """
    persons = persons.merge(
        donor_hh[[donor_col, *available_attrs]],
        on=donor_col, how="left", suffixes=("", "_hh"),
    )
    unmatched = persons["number_of_cars"].isna()
    n_unmatched = int(unmatched.sum())
    n_total = len(persons)
    if n_unmatched:
        sample = sorted(persons.loc[unmatched, donor_col].astype(str).unique())[:5]
        print(
            f"[popsim.assembly] WARNING: donor household attrs: "
            f"{n_unmatched}/{n_total} persons "
            f"({100.0 * n_unmatched / n_total:.2f}%) reference a {donor_col} "
            f"absent from the donor household frame -> count attributes "
            f"zero-filled. Example ids: {sample}. A non-zero rate means the "
            f"expansion and the donor pool diverged (key mismatch)."
        )
    persons["number_of_cars"] = persons["number_of_cars"].fillna(0).astype(int)
    persons["number_of_bicycles"] = persons["number_of_bicycles"].fillna(0).astype(int)
    persons["has_ebike"] = persons["has_ebike"].fillna(0).astype(int)
    return persons


def build_persons(
    merged_households: pd.DataFrame,
    mid_households: pd.DataFrame,
    mid_persons: pd.DataFrame,
    *,
    donor_col: str = "H_ID",
    rng=None,
    attribute_mapper=None,
    pseudonymise: bool = True,
    inkar_scale=None,
    skip_inkar_income_scale: bool = False,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Build the synthetic persons frame with demographics + attributes.

    Parameters
    ----------
    merged_households:
        Merged PopulationSim output (one row per synthetic household, donor
        ``H_ID`` + cell).
    mid_households / mid_persons:
        The MiD donor household / person tables.
    rng:
        Random state for stochastic attribute imputation (employment, licence,
        PT subscription). Defaults to ``np.random.RandomState(0)`` for backward
        compatibility; the calling stage should pass the pipeline's seeded rng.
    attribute_mapper:
        Optional callable with the signature
        ``mapper(persons, households, *, rng)`` that maps donor-survey
        attributes to the eqasim schema columns.
        When ``None`` (the default), ``map_mid_person_attributes`` is used
        (with ``donor_col`` bound via ``functools.partial``) and the result is
        byte-identical to the pre-pluggable behaviour.
        The popsim stage passes ``source.map_person_attributes`` here so
        that the ENTD and MiD workflows share the same expand + zone derivation
        path and differ only in attribute mapping.

        Contract: EVERY mapper must return the 2-tuple
        ``(persons_frame, pseudonym_map)``.  The pseudonym map may be empty
        (open-data sources like ENTD that need no surrogates) but must be
        explicit.  A non-tuple return raises :class:`TypeError` -- silently
        substituting an empty map would lose the re-linking map for a
        pseudonymisation-required source (the file would still be written,
        just empty: "it ran").
    pseudonymise:
        When ``True`` (default, MiD path), the default ``map_mid_person_attributes``
        mapper calls :func:`assign_donor_surrogates` internally to replace the
        raw MiD ``H_ID`` / ``P_ID`` with sequential surrogate integers
        (data-protection requirement for the restricted MiD scientific-use licence).

        When ``False`` (ENTD / popsim_open path), the custom ``attribute_mapper``
        (``EntdSource.map_person_attributes``) sets ``source_person_id`` and
        ``source_household_id`` directly to the open ENTD ids; no surrogate
        mapping is performed and the pseudonym map returned is empty.
        Passing ``pseudonymise=False`` without also supplying a custom
        ``attribute_mapper`` that populates ``source_*`` will raise a
        :class:`braunschweig.population.schema.PopulationSchemaError` at the
        final schema validation step (required columns missing) -- this is
        intentional fail-fast behaviour.
    inkar_scale:
        Optional per-Kreis INKAR scale DataFrame from
        ``braunschweig.data.inkar.household_income`` (columns ``ars5``, ``scale``).
        Passed through to the attribute mapper so that ``household_income_eur`` is
        scaled by the per-Kreis INKAR factor and ``high_income`` is set consistently
        to ``household_income_eur >= 5000 EUR``.  When ``None`` (unit tests, or the
        stage has not wired the INKAR dependency yet), scale=1.0 is used for all
        persons and the absence is logged at INFO level.

    Returns
    -------
    tuple[pandas.DataFrame, pandas.DataFrame]
        ``(persons, pseudonym_map)`` where ``persons`` is one row per synthetic
        person with ``household_id`` / ``person_id``, the cell, demographics
        (``age`` / ``sex``), person attributes (``employed`` / ``has_license``),
        the joined household attributes (``economic_status`` /
        ``household_income_eur`` / ``number_of_cars``), the derived
        ``car_availability``, and the schema-gap columns (``age_range``,
        ``high_income``, ``household_size``, ``is_urban_resident``,
        ``pt_subscription_type``, ``socioprofessional_class``,
        ``source_person_id``, ``source_household_id``);
        and ``pseudonym_map`` is a DataFrame with columns
        ``[source_person_id, source_household_id, H_ID, P_ID]`` for local-only
        re-linking (write to work_dir as ``pseudonym_map.csv``; never commit).
        When ``pseudonymise=False`` (ENTD path) or an alternative mapper is used
        the pseudonym map may be empty.
    """
    # Guard: pseudonymise=False without a custom mapper means the default MiD mapper
    # will run and call assign_donor_surrogates, which contradicts the caller's intent.
    # Catch this misuse early with a clear message (CLAUDE.md: fail-fast on bad config).
    if not pseudonymise and attribute_mapper is None:
        raise ValueError(
            "[popsim.assembly] pseudonymise=False requires a custom attribute_mapper "
            "that sets source_person_id / source_household_id directly (e.g. "
            "EntdSource.map_person_attributes).  The default map_mid_person_attributes "
            "always pseudonymises and is incompatible with pseudonymise=False."
        )

    rng = rng if rng is not None else np.random.RandomState(0)
    households = expand.assign_synthetic_household_ids(
        merged_households, donor_col=donor_col
    )
    persons = expand.expand_to_persons(households, mid_persons, donor_col=donor_col)
    persons = expand.map_demographics(persons, rng=rng)

    # Derive commune_id, departement_id, iris_id from the 12-digit ARS column
    # (joined by stage.py from the cells parquet onto the merged households).
    # Format matches the default IPF producer exactly -- see derive_zone_ids docstring.
    persons = derive_zone_ids(persons)

    # Apply the attribute-mapping sequence through ONE unified signature
    # mapper(persons, households, *, rng). The default mapper is
    # map_mid_person_attributes (MiD path, byte-identical to all prior versions);
    # the MiD-specific donor_col is bound here via functools.partial so the call
    # site is identical for every mapper. An alternative mapper (e.g.
    # EntdSource.map_person_attributes) may be supplied by the popsim stage.
    # INKAR scaling is applied AFTER this call (see below), not inside the mapper.
    effective_mapper = (
        attribute_mapper
        if attribute_mapper is not None
        else functools.partial(map_mid_person_attributes, donor_col=donor_col)
    )
    result = effective_mapper(persons, mid_households, rng=rng)

    # Contract: every mapper returns (persons, pseudonym_map); the map may be
    # empty (open-data sources) but must be explicit. Silently substituting an
    # empty map here would lose the re-linking map for a pseudonymisation-
    # required source (the file would still be written, just empty: "it ran").
    if type(result) is not tuple or len(result) != 2:
        raise TypeError(
            "[popsim.assembly] attribute_mapper must return (persons, pseudonym_map) "
            "-- the pseudonym map may be empty but must be explicit, so a "
            "pseudonymisation-required source can never silently lose it. "
            f"Got {type(result).__name__} from {getattr(effective_mapper, '__name__', effective_mapper)!r}."
        )
    persons, donor_map = result

    # --- INKAR per-Kreis income scaling (shared, applied to ALL sources) --------
    # Replicates braunschweig.synthesis.population.enriched._apply_inkar_income_scale:
    #   household_income_eur = midpoint * INKAR_scale[departement_id]
    #   high_income          = (household_income_eur >= 5000 EUR)
    # Applied AFTER the source mapper so both MiD and ENTD go through the same step.
    # The mapper has already set household_income_eur to the raw income-class midpoint
    # (MiD: INCOME_GROUP_MIDPOINT_EUR; ENTD: ENTD_INCOME_CLASS_MIDPOINT_EUR via entd.py).
    # build_persons overwrites it with the INKAR-scaled value.
    # With inkar_scale=None (unit tests or stage not wired): scale=1.0 for all, logged.
    #
    # ``skip_inkar_income_scale`` is set by the stage when income_kreis_control is ON:
    # that step draws a fresh per-Kreis continuous income and OVERWRITES
    # household_income_eur / household_income / high_income downstream
    # (income_kreis_control.apply_kreis_income_control), so the INKAR midpoint scaling
    # here would be redundant. We then leave the raw income-class midpoint in place
    # (it is never read before the control step replaces it), making this a no-op on
    # the final output while avoiding the wasted per-Kreis scaling join. The default
    # (False) preserves the legacy INKAR-scaled path byte-identically.
    if not skip_inkar_income_scale and "household_income_eur" in persons.columns:
        midpoint_series = persons["household_income_eur"].copy()
        persons = _income_module.apply_inkar_income_eur(
            persons, inkar_scale, midpoint_series=midpoint_series,
        )

    # OECD-modified consumption units per synthetic household (issue #130),
    # reusing the upstream eqasim implementation. Pure age-structure -- stable
    # under the later income overwrites (Kreis-Income-Control, spatial tilt);
    # the equivalised income view is derived from the FINAL income in
    # stage.execute (income.add_income_per_consumption_unit).
    persons = _income_module.add_consumption_units(persons)

    schema.validate_person_columns(persons.columns)
    return persons, donor_map


def _household_availability(
    persons: pd.DataFrame,
    *,
    count_col: str,
    adults_only: bool,
    derive,
) -> pd.Series:
    """Derive a per-household availability {none, some, all} and broadcast to persons.

    ``count_col`` is the per-household vehicle count (cars / bicycles); the demand
    side is the adult members (cars) or all members (bicycles).
    """
    if adults_only:
        members = (persons["age"] >= ADULT_AGE).groupby(persons["household_id"]).sum()
    else:
        members = persons.groupby("household_id").size()
    counts = persons.groupby("household_id")[count_col].first()

    # `derive` is a pure function of (count, members), so it only needs to run
    # once per unique pair (a handful of combinations) instead of once per
    # household (~600k at 100%, where the old per-household dict comprehension
    # with scalar .__getitem__ lookups dominated). Result is value-identical.
    pairs = pd.DataFrame({
        "count": counts.to_numpy(),
        "members": members.reindex(counts.index).to_numpy(),
    }, index=counts.index)
    unique_pairs = pairs.drop_duplicates()
    pair_value = {
        (int(count), int(n_members)): derive(int(count), int(n_members))
        for count, n_members in unique_pairs.itertuples(index=False, name=None)
    }
    availability = pd.Series(
        [pair_value[(int(c), int(m))] for c, m in
         zip(pairs["count"].to_numpy(), pairs["members"].to_numpy())],
        index=pairs.index,
    )
    return persons["household_id"].map(availability)
