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

    # --- household surrogate ---
    hh_codes, _ = pd.factorize(out[donor_col], sort=True)
    out["source_household_id"] = (hh_codes + 1).astype(int)

    # --- person surrogate: unique (H_ID, P_ID) pair ---
    # Encode the pair as a single sortable key for factorize.
    pair_key = list(zip(out[donor_col].tolist(), out["P_ID"].tolist()))
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
    hh_surr_codes, _ = pd.factorize(h_ids, sort=True)
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
]


def map_mid_person_attributes(
    persons: pd.DataFrame,
    mid_households: pd.DataFrame,
    *,
    donor_col: str = "H_ID",
    rng=None,
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
        ``hheink_gr1``, ``H_ANZAUTO``, ``H_ANZRAD``).
    donor_col:
        Column name of the donor household key (default ``H_ID``).
    rng:
        Random state for stochastic attribute imputation (employment, licence,
        PT subscription).  Defaults to ``np.random.RandomState(0)`` for backward
        compatibility.

    Returns
    -------
    tuple[pandas.DataFrame, pandas.DataFrame]
        ``(persons, pseudonym_map)`` — same contract as ``build_persons``.
    """
    rng = rng if rng is not None else np.random.RandomState(0)

    persons = attributes.map_employed(persons, rng=rng)
    # Derive studies from P_TAET (Ausbildung/Schueler/Student -> True) BEFORE
    # map_socioprofessional_class, which uses the studies flag in its fallback path.
    # Bug D4: studies was absent, so the fallback treated all students as studies=False.
    persons = attributes.map_studies(persons)
    persons = attributes.map_has_license(persons, rng=rng)
    persons = attributes.map_has_pt_subscription(persons, rng=rng)

    donor_hh = attributes.map_number_of_bicycles(
        attributes.map_number_of_cars(
            attributes.map_household_income(
                attributes.map_household_income_eur(
                    attributes.map_economic_status(mid_households)
                )
            )
        )
    )
    persons = persons.merge(
        donor_hh[[donor_col, *_HOUSEHOLD_ATTRS]],
        on=donor_col, how="left", suffixes=("", "_hh"),
    )
    persons["number_of_cars"] = persons["number_of_cars"].fillna(0).astype(int)
    persons["number_of_bicycles"] = persons["number_of_bicycles"].fillna(0).astype(int)

    persons["car_availability"] = _household_availability(
        persons, count_col="number_of_cars", adults_only=True,
        derive=attributes.derive_car_availability,
    )
    persons["bicycle_availability"] = _household_availability(
        persons, count_col="number_of_bicycles", adults_only=False,
        derive=attributes.derive_bicycle_availability,
    )

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
    persons = attributes.map_pt_subscription_type(persons, rng=rng)

    # weight = 1.0: popsim_mid produces an already-expanded population (each row
    # is one synthetic person, no stochastic rounding needed). synthesis.population.sampled
    # requires this column; it uses floor(weight) + Bernoulli(frac) to replicate households,
    # so weight=1.0 means every synthetic household is replicated exactly once before the
    # sampling_rate selection, matching the behaviour of braunschweig.ipf.attributed.
    persons["weight"] = 1.0

    return persons, donor_map


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
        Optional callable with the same signature as
        ``map_mid_person_attributes(persons, households, *, donor_col, rng)``
        that maps donor-survey attributes to the eqasim schema columns.
        When ``None`` (the default), ``map_mid_person_attributes`` is used
        and the result is byte-identical to the pre-pluggable behaviour.
        The popsim_open stage passes ``source.map_person_attributes`` here so
        that the ENTD and MiD workflows share the same expand + zone derivation
        path and differ only in attribute mapping.

        The mapper must return ``(persons_frame, pseudonym_map_or_None)``.
        If it returns a single DataFrame (no pseudonym map), ``build_persons``
        wraps it with an empty pseudonym map automatically.
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
    persons = expand.map_demographics(persons)

    # Derive commune_id, departement_id, iris_id from the 12-digit ARS column
    # (joined by stage.py from the cells parquet onto the merged households).
    # Format matches the default IPF producer exactly -- see derive_zone_ids docstring.
    persons = derive_zone_ids(persons)

    # Apply the attribute-mapping sequence. The default mapper is
    # map_mid_person_attributes (MiD path, byte-identical to all prior versions).
    # An alternative mapper (e.g. EntdSource.map_person_attributes) may be
    # supplied by the popsim_open stage.
    effective_mapper = attribute_mapper if attribute_mapper is not None else map_mid_person_attributes

    if attribute_mapper is None:
        # Default MiD mapper (pseudonymise=True): pass donor_col.
        # assign_donor_surrogates is called inside map_mid_person_attributes, so the
        # returned pseudonym_map is populated.
        # INKAR scaling is applied AFTER this call (see below), not inside the mapper.
        result = effective_mapper(
            persons, mid_households, donor_col=donor_col, rng=rng,
        )
    else:
        # Alternative mapper (e.g. EntdSource, pseudonymise=False): does not use the
        # MiD-specific donor_col argument; calls the PopsimSource protocol signature
        # map_person_attributes(persons, households, *, rng).  The mapper sets
        # source_* directly to the open ENTD ids -- no surrogate mapping is performed.
        # INKAR scaling is applied AFTER this call (see below), not inside the mapper.
        result = effective_mapper(persons, mid_households, rng=rng)

    # The mapper returns either (persons, pseudonym_map) or just persons.
    # Handle both forms so EntdSource (no pseudonym map) works without
    # the caller needing to know which form was returned.
    if isinstance(result, tuple):
        persons, donor_map = result
    else:
        persons = result
        donor_map = pd.DataFrame(
            columns=["source_person_id", "source_household_id", "H_ID", "P_ID"]
        )

    # --- INKAR per-Kreis income scaling (shared, applied to ALL sources) --------
    # Replicates braunschweig.synthesis.population.enriched._apply_inkar_income_scale:
    #   household_income_eur = midpoint * INKAR_scale[departement_id]
    #   high_income          = (household_income_eur >= 5000 EUR)
    # Applied AFTER the source mapper so both MiD and ENTD go through the same step.
    # The mapper has already set household_income_eur to the raw income-class midpoint
    # (MiD: INCOME_GROUP_MIDPOINT_EUR; ENTD: ENTD_INCOME_CLASS_MIDPOINT_EUR via entd.py).
    # build_persons overwrites it with the INKAR-scaled value.
    # With inkar_scale=None (unit tests or stage not wired): scale=1.0 for all, logged.
    if "household_income_eur" in persons.columns:
        midpoint_series = persons["household_income_eur"].copy()
        persons = _income_module.apply_inkar_income_eur(
            persons, inkar_scale, midpoint_series=midpoint_series,
        )

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
    availability = {
        household_id: derive(int(counts[household_id]), int(members[household_id]))
        for household_id in counts.index
    }
    return persons["household_id"].map(availability)
