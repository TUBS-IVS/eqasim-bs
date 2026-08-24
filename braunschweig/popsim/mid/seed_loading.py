"""MiD seed loading for the popsim mid stage.

- ``load_mid_seed``           -- the consistent (complete-household) MiD seed,
                                  loaded directly from the raw MiD CSV delivery
- ``project_completed_seed``  -- project the member-completed donor frames
                                  (``load_completed_donor``) onto the same seed
                                  schema ``load_mid_seed`` produces

Extracted verbatim from the stage module (``__init__``); see the package
docstring for the stage-level context.

``detect_csv_separator`` moved to its own leaf module ``csv_format.py``
(issue #267 task 4 ruling; see that module's docstring): it has call sites
in this module AND in ``donor.py`` (``load_mid_attributes`` / ``load_mid_wege``),
so it is a multi-module dependency rather than a single-consumer helper --
both later submodules import it from ``csv_format`` directly rather than
duplicating it.

``load_mid_wege`` is called by both functions below and now lives in the
sibling module ``donor.py`` (it also has consumers outside this package,
e.g. ``trips_stage.py`` / ``stage.py`` / ``sources/mid.py``, so relocating it
was out of scope for this module's own extraction). It is imported here as
a normal module-level sibling import (``from .donor import load_mid_wege``);
the earlier function-local workaround imports (needed only while
``load_mid_wege`` still lived in the partially-initialized package
``__init__``) are gone. ``donor.py`` does not import this module, so no
import cycle results.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Sequence, Union

import pandas as pd

from braunschweig.popsim import attributes
from braunschweig.popsim import member_completion as completion
from braunschweig.popsim import seed as seedmod
from braunschweig.popsim.kreis_attribute_control import KreisAttributeControl
from braunschweig.popsim.kreis_attribute_control import REGISTRY as KREIS_CONTROL_REGISTRY

from .csv_format import detect_csv_separator
from .donor import load_mid_wege
from .participation import PARTICIPATION_W_ZWECK
from .participation import derive_participation_seed
from .participation import derive_trip_class_seed


# --------------------------------------------------------------------------- #
# Seed loading steps
#
# Named orchestration steps for the two public functions below. Every step is a
# verbatim move of one commented block out of ``load_mid_seed`` /
# ``project_completed_seed``; the block's comment is kept in the step docstring.
# The steps are called in exactly the original order and thread their data
# explicitly through parameters and return values (no module-level state), so
# the call order -- and with it the seeded RNG draw order -- is unchanged.
# Steps whose block was byte-identical in both public functions are shared;
# steps carrying a caller-specific message literal exist once per caller so the
# raised message stays byte-identical.
# --------------------------------------------------------------------------- #

def _resolve_effective_kreis_entries(
    kreis_control_entries: Sequence[KreisAttributeControl],
    include_status_seed_col: bool,
) -> tuple[list[KreisAttributeControl], set[str]]:
    """Resolve the deprecated ``include_status_seed_col`` alias.

    Deprecated alias: include_status_seed_col=True is equivalent to activating the
    economic_status registry entry, so old callers/tests stay byte-identical
    (oek_status is a raw pass-through -- the same behaviour as before this task).

    Returns: ``(effective_kreis_entries, active_kreis_entry_names)``.
    Mutates: nothing (``kreis_control_entries`` is copied into a new list).
    """
    effective_kreis_entries: list[KreisAttributeControl] = list(kreis_control_entries)
    if include_status_seed_col and not any(
        entry.name == "economic_status" for entry in effective_kreis_entries
    ):
        effective_kreis_entries.append(
            next(entry for entry in KREIS_CONTROL_REGISTRY if entry.name == "economic_status")
        )
    active_kreis_entry_names = {entry.name for entry in effective_kreis_entries}
    return effective_kreis_entries, active_kreis_entry_names


def _read_seed_households(
    households_path: Path,
    *,
    columns: seedmod.SeedColumns,
    complete_members: bool,
    active_kreis_entry_names: set[str],
    ebike_seed_column: Optional[str],
) -> pd.DataFrame:
    """Read the seed household columns from the raw MiD household CSV.

    Load household id, weight, and RegioStaR7 (Phase 4A plumbing: the RS7 code
    is carried onto the seed households so Phase 4B donor stratification can use
    the cell's urban/rural class to restrict the donor pool without an extra join).

    Always load H_GR (declared household size) so the Tier-1 household_size
    control expression ``(households.H_GR == N)`` can be evaluated by
    PopulationSim. H_GR was previously loaded only when complete_members=True;
    the Tier-7 addition makes it unconditionally required in the seed.
    Always load H_MIETE (tenure flag: 1=renter, 2=owner) so the Tier-2 tenure
    control expressions ``(households.H_MIETE == 1/2)`` can be evaluated by
    PopulationSim. Values 3/9/309 (ambiguous) are kept in the seed column;
    the control expressions simply do not match them (they contribute 0 to
    either tenure control, which is the correct treatment for excluded codes).
    Always load haustyp (building type: 1=EFH/ZFH, 2=MFH, 3=Geschosswohnung,
    4=sonstiges, 95=n.z.) so the Tier-2 building_type control expressions
    can be evaluated by PopulationSim. Code 95 (n.z.) does not match any
    building_type expression and is therefore silently excluded from all three
    building_type controls (correct behaviour, no fabricated assignments).

    Returns: the raw (unfiltered) seed household frame.
    Mutates: nothing; reads ``households_path``.
    """
    household_cols = [columns.household_id, columns.household_weight, "RegioStaR7", "H_GR", "H_MIETE", "haustyp"]
    if complete_members:
        # Member completion additionally needs the mirror match keys
        # (hhgr_gr -> oek_status; RegioStaR7 and H_GR are already loaded above).
        household_cols.extend(("hhgr_gr", "oek_status"))
    if "economic_status" in active_kreis_entry_names and "oek_status" not in household_cols:
        # economic_status x Kreis control (issue #109): load oek_status so the seed
        # households can carry it for the control expression (households.oek_status == k).
        household_cols.append("oek_status")
    if (
        active_kreis_entry_names & {"number_of_cars", "number_of_bicycles", "has_ebike"}
        and "hhgr_gr" not in household_cols
    ):
        # Count-style kreis controls resolve their raw column via attributes.map_* with
        # group-wise (hhgr_gr) imputation of the 99 missing code (see below).
        household_cols.append("hhgr_gr")
    if "number_of_cars" in active_kreis_entry_names:
        household_cols.append("H_ANZAUTO")
    if "number_of_bicycles" in active_kreis_entry_names:
        # anzpedrad = bicycles INCLUDING pedelecs/e-bikes (MiD H12.3 / SrV alle-Raeder
        # construct; verified 2026-07-08 to equal min(H_ANZRAD + H_ANZPED, 10) on all
        # 218,039 valid MiD B1 household rows). See attributes.map_number_of_bicycles.
        household_cols.append("anzpedrad")
    if "has_ebike" in active_kreis_entry_names:
        if not ebike_seed_column:
            raise ValueError(
                "load_mid_seed: has_ebike kreis control is active but ebike_seed_column is "
                "not configured; set braunschweig.population.popsim.ebike_seed_column to the "
                "verified MiD household e-bike column (no silent fallback)."
            )
        household_cols.append(ebike_seed_column)
    households = pd.read_csv(
        households_path,
        usecols=list(dict.fromkeys(household_cols)),
        sep=detect_csv_separator(households_path),
    )
    return households


def _read_seed_persons(
    persons_path: Path,
    *,
    columns: seedmod.SeedColumns,
    active_kreis_entry_names: set[str],
) -> tuple[pd.DataFrame, tuple[str, ...]]:
    """Read the seed person columns from the raw MiD person CSV.

    Returns: ``(persons, _tier3_seed_cols)`` -- the raw (unfiltered) seed person
    frame and the Tier-3 raw control columns that were actually present in the
    CSV header (they are retained on the seed by ``select_seed_columns``).
    Mutates: nothing; reads ``persons_path`` (header row first, then the data).
    """
    person_cols = [
        columns.person_household_id, columns.person_id, columns.person_weight,
        columns.age, columns.sex,
    ]
    if columns.day_filter_col:
        person_cols.append(columns.day_filter_col)
    # Tier-3 raw control inputs (P_TAET / bildung1 / bildung2) for the employment/
    # education controls -- read + retained on the seed only when present (real MiD has
    # them; some fixtures don't), so tier0-2 + fixtures stay unaffected.
    _persons_sep = detect_csv_separator(persons_path)
    _persons_header = pd.read_csv(persons_path, sep=_persons_sep, nrows=0).columns
    _tier3_seed_cols = tuple(
        c for c in ("P_TAET", "bildung1", "bildung2")
        if c in _persons_header and c not in person_cols
    )
    person_cols.extend(_tier3_seed_cols)
    # trip_class (first PERSON-level KREIS control): load the raw diary trip count
    # (anzwege1) and the age-band conditioning column (alter_gr1) so the class can be
    # derived + the 803/804 item-nonresponse imputed within alter_gr1 after the
    # complete-household filter. Dedup-safe (alter_gr1 may already be present).
    if "trip_class" in active_kreis_entry_names:
        for _tc_col in ("anzwege1", "alter_gr1"):
            if _tc_col not in person_cols:
                person_cols.append(_tc_col)
    # employment_status (second PERSON-level KREIS control, task 4b / feature #172): load
    # the raw MiD Umfang-der-Erwerbstaetigkeit code (P_BKAT) and the age-band conditioning
    # column (alter_gr1) so the P9 seven-class label can be derived + the ~0.13% code-9
    # (keine Angabe) cases imputed within alter_gr1, mirroring the trip_class block above
    # exactly. Dedup-safe (alter_gr1 may already be present via trip_class or tier3).
    if "employment_status" in active_kreis_entry_names:
        for _es_col in ("P_BKAT", "alter_gr1"):
            if _es_col not in person_cols:
                person_cols.append(_es_col)
    # pt_ticket_group (PERSON-level KREIS control, issue #321): load the raw MiD
    # Fahrkartenart code (P_FKARTE) and the age-band conditioning column (alter_gr1) so the
    # ticket category can be resolved + the 99/202/206 coverage codes imputed within
    # alter_gr1, then collapsed to the three control groups -- mirroring the
    # employment_status block above exactly. Dedup-safe (alter_gr1 may already be present).
    # Either resolution of the control (three-group pt_ticket_group, issue #321, or its
    # four-group refinement pt_ticket_group4, issue #329) reads the same two raw columns.
    if active_kreis_entry_names & {"pt_ticket_group", "pt_ticket_group4"}:
        for _pt_col in ("P_FKARTE", "alter_gr1"):
            if _pt_col not in person_cols:
                person_cols.append(_pt_col)
    # participation controls (work_participation task 4; leisure_participation /
    # education_participation task 5, feature #224): load the raw diary trip count
    # (anzwege1, the default trips_col mid.compute_has_purpose_trip uses to carry
    # through the 803/804 diary-nonresponse codes) and the age-band conditioning column
    # (alter_gr1) so the has-<purpose>-trip flag can be derived from the MiD Wege table +
    # the 803/804 codes imputed within alter_gr1, mirroring the trip_class block above.
    # Dedup-safe (anzwege1/alter_gr1 may already be present via trip_class/
    # employment_status/tier3); shared across all participation entries since they all
    # read the same two raw columns. The entry-name set is derived from
    # PARTICIPATION_W_ZWECK (the single source of truth for the purpose set), so a
    # newly registered purpose (e.g. escort, issue #227) needs no edit here.
    if active_kreis_entry_names & {f"{p}_participation" for p in PARTICIPATION_W_ZWECK}:
        for _pp_col in ("anzwege1", "alter_gr1"):
            if _pp_col not in person_cols:
                person_cols.append(_pp_col)
    persons = pd.read_csv(
        persons_path,
        usecols=list(dict.fromkeys(person_cols)),
        sep=_persons_sep,
    )
    return persons, _tier3_seed_cols


def _filter_complete_seed_households(
    households: pd.DataFrame,
    persons: pd.DataFrame,
    columns: seedmod.SeedColumns,
    *,
    day_filter_values: Optional[Sequence[int]],
) -> tuple[pd.DataFrame, pd.DataFrame, seedmod.CompletenessReport]:
    """Apply the day filter and the complete-household (``kernwo``) filter.

    ``None`` -> standard weekday default; explicit empty iterable -> day filter OFF
    (filter_complete_households treats ``None`` as "no day filtering"); a plain
    ``or`` here would silently resurrect the default for an empty tuple.

    Returns: ``(households, persons, report)`` -- both frames filtered, plus the
    completeness report.
    Mutates: nothing in place; the filtered frames are new objects.
    """
    if day_filter_values is None:
        effective_day_filter = columns.day_filter_values
    elif len(tuple(day_filter_values)) == 0:
        effective_day_filter = None
    else:
        effective_day_filter = tuple(day_filter_values)
    households, persons, report = seedmod.filter_complete_households(
        households, persons, columns,
        day_filter_values=effective_day_filter,
    )
    return households, persons, report


def _classify_rng_style_kreis_entries(active_kreis_entry_names: set[str]) -> set[str]:
    """Determine which active kreis entries need the seeded ``kreis_seed_rng``.

    Count-style entries impute the 99 missing code (household level); the person-level
    trip_class entry imputes the 803/804 diary-nonresponse codes (within alter_gr1); the
    person-level employment_status entry imputes the P_BKAT code-9 (keine Angabe) cases
    (also within alter_gr1); both person-level PT entries impute the P_FKARTE coverage
    codes (also within alter_gr1). All are random processes that REQUIRE the seeded
    kreis_seed_rng (no unseeded randomness).

    The caller raises the (caller-specific) ValueError when the returned set is
    non-empty and no rng was passed, so the message literal stays with its caller.

    Returns: the subset of ``active_kreis_entry_names`` whose derivation draws
    random numbers.
    Mutates: nothing.
    """
    _count_style_entries = active_kreis_entry_names & {
        "number_of_cars", "number_of_bicycles", "has_ebike"
    }
    # The participation entry names are derived from PARTICIPATION_W_ZWECK (single
    # source of truth for the purpose set): every participation control imputes the
    # 803/804 diary-nonresponse codes within alter_gr1, so all of them draw random
    # numbers and must be gated on the seeded rng.
    # Both PT entries (the three-group pt_ticket_group, issue #321, and its four-group
    # refinement pt_ticket_group4, issue #329) draw: _derive_pt_ticket_group_seed_column
    # calls attributes.map_pt_subscription_type, which IMPUTES the P_FKARTE coverage codes
    # 99 (keine Angabe), 202 (PAPI interview mode) and 206 (Erwachsener ab 14, Proxy) from
    # the valid pool within alter_gr1 x RegioStaR7 -- 6.18% of persons in a real run. The
    # boolean has_pt_subscription AND both group columns are all derived from that one
    # resolved pt_subscription_type category, so this single draw feeds every PT quantity;
    # leaving the entries out let map_pt_subscription_type's RandomState(0) default stand in
    # for the run's seeded rng whenever only a PT control was active.
    #
    # FOLLOW-UP: this list is hand-maintained, so a drawing entry added to the REGISTRY does
    # NOT update it -- deriving it from the registry (e.g. a `draws_random` field on
    # KreisAttributeControl) would make the coverage structural instead of remembered. That
    # same follow-up should guard the post-expansion twin of this defect:
    # braunschweig.popsim.assembly.build_persons applies its own
    # `rng if rng is not None else RandomState(0)` before calling
    # attributes.map_pt_subscription_type, and no guard covers that path.
    _rng_style_entries = _count_style_entries | (
        active_kreis_entry_names & (
            {"trip_class", "employment_status", "pt_ticket_group", "pt_ticket_group4"}
            | {f"{p}_participation" for p in PARTICIPATION_W_ZWECK}
        )
    )
    return _rng_style_entries


def _derive_count_style_kreis_columns(
    households: pd.DataFrame,
    *,
    active_kreis_entry_names: set[str],
    kreis_seed_rng,
    ebike_seed_column: Optional[str],
) -> pd.DataFrame:
    """Derive the clean, MECE seed columns for the active count-style kreis controls.

    In ``load_mid_seed`` this runs AFTER the complete-household filter (so the
    group-wise imputation pool reflects only the kept seed households).
    economic_status needs no derivation here -- its seed column (oek_status) is used
    RAW by the == k predicate (byte-identical to the pre-existing
    include_status_seed_col=True behaviour).

    In ``project_completed_seed`` the same derivation runs on the raw H_ANZAUTO /
    anzpedrad / ebike_seed_column columns the completed-donor households already
    carry (see MID_HOUSEHOLD_ATTR_COLS); it mirrors the load_mid_seed derivation
    exactly, which is why both callers share this step.

    Returns: the households frame with the derived seed columns (the
    ``attributes.map_*`` resolvers return new frames, so the return value MUST be
    reassigned by the caller).
    Mutates: nothing in place.
    """
    if "number_of_cars" in active_kreis_entry_names:
        households = attributes.map_number_of_cars(households, rng=kreis_seed_rng)
    if "number_of_bicycles" in active_kreis_entry_names:
        # Default bikes_col="anzpedrad" (bicycles INCLUDING pedelecs/e-bikes; MiD H12.3 /
        # SrV alle-Raeder construct); see attributes.map_number_of_bicycles.
        households = attributes.map_number_of_bicycles(households, rng=kreis_seed_rng)
    if "has_ebike" in active_kreis_entry_names:
        households = attributes.map_has_ebike(
            households, ebike_col=ebike_seed_column, rng=kreis_seed_rng
        )
    return households


def _complete_seed_members(
    households: pd.DataFrame,
    persons: pd.DataFrame,
    columns: seedmod.SeedColumns,
    *,
    complete_members: bool,
    completion_rng,
) -> tuple[pd.DataFrame, pd.DataFrame, tuple[str, ...]]:
    """Fill member-incomplete households by mirror-household sampling (opt-in).

    Fill member-incomplete households AFTER the day filter (the host
    household must have passed it) and BEFORE the column selection (the
    match keys H_GR/hhgr_gr/oek_status are dropped again below, so the
    output household schema is unchanged).

    Returns: ``(households, persons, extra_person_cols)`` -- both frames as
    returned by ``completion.complete_members`` and the traceability columns to
    retain on the persons frame (empty when ``complete_members`` is False).
    Mutates: nothing in place.
    """
    extra_person_cols: tuple[str, ...] = ()
    if complete_members:
        households, persons, _ = completion.complete_members(
            households, persons, rng=completion_rng,
            household_id=columns.household_id,
        )
        extra_person_cols = ("member_imputed", "source_H_ID", "source_P_ID")
    return households, persons, extra_person_cols


def _derive_trip_class_seed_column(
    persons: pd.DataFrame,
    columns: seedmod.SeedColumns,
    *,
    active_kreis_entry_names: set[str],
    kreis_seed_rng,
) -> pd.DataFrame:
    """Derive the person-level ``trip_class`` KREIS control seed column.

    In ``load_mid_seed``: derive the int-coded class (0..3) from the raw diary trip
    count AFTER the complete-household filter + member completion, so the 803/804
    diary-nonresponse imputation pool reflects only the kept seed persons and any
    mirror-imputed member inherits the mirror donor's diary (documented, see
    MID_PERSON_ATTR_COLS). The caller's rng guard ensures kreis_seed_rng is set. The
    class is seeded from the person's REALISED weekday plan source (not their own
    reporting-day diary) so the control matches the SrV weekday target universe --
    see derive_trip_class_seed (audit 2026-07-09).

    In ``project_completed_seed``: derive trip_class from each person's REALISED
    weekday plan source, not their own reporting-day diary: after
    weekend_plan_match the completed-donor frame's source_(H_ID,P_ID) point to
    weekday donors, so the source's anzwege1 is both the SrV Di-Do target universe
    and the trips the synthetic person executes. See derive_trip_class_seed (audit
    2026-07-09; ~29% weekend reporters otherwise carried a weekend diary count into
    the weekday-anchored control).

    Returns: the persons frame with the derived column (MUST be reassigned).
    Mutates: nothing in place.
    """
    if "trip_class" in active_kreis_entry_names:
        persons = derive_trip_class_seed(
            persons, rng=kreis_seed_rng,
            household_id=columns.person_household_id, person_id=columns.person_id)
    return persons


def _derive_employment_status_seed_column(
    persons: pd.DataFrame,
    *,
    active_kreis_entry_names: set[str],
    kreis_seed_rng,
) -> pd.DataFrame:
    """Derive the person-level ``employment_status`` KREIS control seed column.

    In ``load_mid_seed``: derive the P9 seven-class string from the raw P_BKAT code
    AFTER the complete-household filter + member completion, so a mirror-imputed
    filler's employment_status matches its inherited (donor-mirrored) P_BKAT and the
    code-9 imputation pool reflects only the kept seed persons -- exactly mirroring
    the trip_class derivation above. The caller's rng guard ensures kreis_seed_rng is
    set. Uses the SAME attributes.map_employment_status as the post-expansion
    assembly.build_persons, so seed and expanded values agree deterministically for
    the 99.87% of persons with a valid (non-9) P_BKAT; the code-9 imputed cases may
    differ by an independent rng draw -- acceptable for a control (matches the
    trip_class precedent, task 4b).

    In ``project_completed_seed``: derive employment_status from the completed
    donor's P_BKAT (already loaded via MID_PERSON_ATTR_COLS -- see
    mid.MID_PERSON_ATTR_COLS); mirrors load_mid_seed's derivation exactly. A
    mirror-imputed filler inherits the mirror donor's P_BKAT (member completion
    samples whole donor person rows, same as anzwege1/trip_class), so this reflects
    the full completed population, agreeing deterministically with
    assembly.build_persons for the 99.87% of persons with a valid (non-9) P_BKAT.

    Returns: the persons frame with the derived column (MUST be reassigned).
    Mutates: nothing in place.
    """
    if "employment_status" in active_kreis_entry_names:
        persons = attributes.map_employment_status(persons, rng=kreis_seed_rng)
    return persons


def _derive_pt_ticket_group_seed_column(
    persons: pd.DataFrame,
    *,
    active_kreis_entry_names: set[str],
    kreis_seed_rng,
) -> pd.DataFrame:
    """Derive the person-level ``pt_ticket_group`` KREIS control seed column (issue #321).

    Runs for EITHER resolution of the control -- the three-group ``pt_ticket_group`` entry or
    its four-group refinement ``pt_ticket_group4`` (issue #329, which replaces it when
    active): ``attributes.map_pt_ticket_group`` emits both columns in one pass, so no further
    branching is needed and the two can never disagree.

    Two steps, in this order and only this order: resolve the ticket CATEGORY from the raw
    ``P_FKARTE`` via ``attributes.map_pt_subscription_type`` (imputing the coverage codes
    99 / 202 / 206 within ``alter_gr1``), then collapse it onto the control groups via
    ``attributes.map_pt_ticket_group``. Resolving the group directly from the raw code would
    be a SECOND independent draw over the same imputed codes -- the defect ADR-0087 removed,
    which would let the control steer a quantity that differs from the ``has_pt_subscription``
    the fare model reads.

    Mirrors :func:`_derive_employment_status_seed_column`: derived AFTER the
    complete-household filter + member completion in ``load_mid_seed`` (so a mirror-imputed
    filler's group matches its inherited P_FKARTE and the imputation pool reflects only the
    kept seed persons), and from the completed donor's P_FKARTE in
    ``project_completed_seed``. Seed and expanded population agree deterministically for
    every person with a valid P_FKARTE code; the imputed cases may differ by an independent
    rng draw, which is acceptable for a control (the trip_class / employment_status
    precedent).

    Returns: the persons frame with the derived column (MUST be reassigned).
    Mutates: nothing in place.
    """
    if active_kreis_entry_names & {"pt_ticket_group", "pt_ticket_group4"}:
        persons = attributes.map_pt_subscription_type(persons, rng=kreis_seed_rng)
        persons = attributes.map_pt_ticket_group(persons)
    return persons


def _derive_participation_seed_columns(
    persons: pd.DataFrame,
    columns: seedmod.SeedColumns,
    mid_dir: Union[str, Path],
    *,
    active_kreis_entry_names: set[str],
    kreis_seed_rng,
) -> pd.DataFrame:
    """Derive the ``<purpose>_participation`` seed columns for ``load_mid_seed``.

    participation controls (work_participation task 4; leisure_participation /
    education_participation task 5, feature #224): derive the 0/1 has-a-<purpose>-trip
    flag from the MiD Wege table AFTER the complete-household filter + member
    completion, seeded from the person's REALISED weekday plan source exactly like
    trip_class (mid.derive_participation_seed) -- see that function's docstring for the
    full weekday-vs-realised-plan rationale. The caller's rng guard ensures
    kreis_seed_rng is set. Requires the full MiD Wege table, which load_mid_seed does
    not otherwise read; loaded ONCE here (gated on ANY participation control being
    active) so the OFF path never touches MiD2023_Wege.csv (byte-identical no-op).

    Returns: the persons frame with one derived column per active purpose (MUST be
    reassigned).
    Mutates: nothing in place; reads ``MiD2023_Wege.csv`` from ``mid_dir`` when at
    least one participation control is active.
    """
    # Every purpose in PARTICIPATION_W_ZWECK (work/leisure/education, feature #224;
    # escort, issue #227) is seedable; the single source of truth for the purpose set
    # is that constant, so a newly registered purpose needs no edit here.
    _active_participation_purposes = [
        purpose for purpose in PARTICIPATION_W_ZWECK
        if f"{purpose}_participation" in active_kreis_entry_names
    ]
    if _active_participation_purposes:
        wege = load_mid_wege(mid_dir)
        for purpose in _active_participation_purposes:
            persons = derive_participation_seed(
                persons, wege, purpose, rng=kreis_seed_rng,
                household_id=columns.person_household_id, person_id=columns.person_id)
    return persons


def _join_hh_type5_column(
    households: pd.DataFrame,
    persons: pd.DataFrame,
    columns: seedmod.SeedColumns,
) -> pd.DataFrame:
    """Derive ``hh_type5`` from the persons frame and join it onto households.

    Derive hh_type5 (Tier-1 household_type/Familientyp 5-class) from the
    filtered persons frame.  derive_hh_type5 runs map_households_to_hhtype
    (11-class) then collapses to the 5 Zensus Familientyp labels.  The result
    is a per-household Series indexed by H_ID that is merged onto households.
    This must happen BEFORE select_seed_columns so hh_type5 can be retained as
    an extra_household_col; it uses the raw MiD column names (H_ID / HP_ALTER).

    Returns: the households frame with the joined ``hh_type5`` column (MUST be
    reassigned).
    Mutates: nothing in place.
    """
    hh_type5_series = seedmod.derive_hh_type5(
        persons,
        household_id_col=columns.person_household_id,
        age_col=columns.age,
    )
    households = households.join(
        hh_type5_series.rename("hh_type5"),
        on=columns.household_id,
    )
    return households


def _split_kreis_entries_by_level(
    effective_kreis_entries: Sequence[KreisAttributeControl],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Split the active kreis seed columns into household-level and person-level.

    Household-level entries retain their seed column on the households frame; the
    first PERSON-level entry (trip_class) retains its derived seed column on the
    persons frame so the population control expression (persons.trip_class == k)
    can be evaluated by PopulationSim.

    Kept separate from the ``project_completed_seed`` twin
    (``_split_projected_kreis_entries_by_level``) because the NotImplementedError
    message names its caller and is raised inside the loop, so it cannot stay with
    the caller; duplicating the block keeps both message literals byte-identical.

    Returns: ``(_hh_extra, _person_extra)`` for ``select_seed_columns``.
    Mutates: nothing.
    """
    _hh_extra = ("RegioStaR7", "H_GR", "hh_type5", "H_MIETE", "haustyp")
    _person_extra: tuple[str, ...] = ()
    for _entry in effective_kreis_entries:
        if _entry.level == "household":
            if _entry.seed_column not in _hh_extra:
                _hh_extra = _hh_extra + (_entry.seed_column,)
        elif _entry.level == "person":
            if _entry.seed_column not in _person_extra:
                _person_extra = _person_extra + (_entry.seed_column,)
        else:
            raise NotImplementedError(
                f"load_mid_seed: kreis control entry {_entry.name!r} has unsupported level "
                f"{_entry.level!r} (expected 'household' or 'person')."
            )
    return _hh_extra, _person_extra


def _derive_projected_participation_seed_columns(
    persons: pd.DataFrame,
    columns: seedmod.SeedColumns,
    mid_dir: Union[str, Path, None],
    *,
    active_kreis_entry_names: set[str],
    kreis_seed_rng,
) -> pd.DataFrame:
    """Derive the ``<purpose>_participation`` columns for ``project_completed_seed``.

    Derive each active <purpose>_participation from the completed donor's MiD Wege
    table (loaded from mid_dir; the completed-donor frames do not carry the Wege
    rows themselves). Seeded from the person's REALISED weekday plan source exactly
    like trip_class (mid.derive_participation_seed) -- see that function's
    docstring for the full weekday-vs-realised-plan rationale. Wege is loaded ONCE
    and reused for every active purpose (work_participation task 4; leisure_
    participation / education_participation task 5, feature #224).

    Kept separate from the ``load_mid_seed`` twin
    (``_derive_participation_seed_columns``) because of the extra ``mid_dir``
    requirement check, whose ValueError names its caller and is raised inside the
    guarded branch, so it cannot stay with the caller.

    Returns: the persons frame with one derived column per active purpose (MUST be
    reassigned).
    Mutates: nothing in place; reads ``MiD2023_Wege.csv`` from ``mid_dir`` when at
    least one participation control is active.
    """
    # Same single-source-of-truth purpose set as the load_mid_seed twin (see
    # _derive_participation_seed_columns).
    _active_participation_purposes = [
        purpose for purpose in PARTICIPATION_W_ZWECK
        if f"{purpose}_participation" in active_kreis_entry_names
    ]
    if _active_participation_purposes:
        if mid_dir is None:
            raise ValueError(
                "project_completed_seed: a participation kreis control "
                f"{_active_participation_purposes} is active but mid_dir is not set; cannot "
                f"load the MiD Wege table to derive participation flags for {_active_participation_purposes} (no silent fallback)."
            )
        wege = load_mid_wege(mid_dir)
        for purpose in _active_participation_purposes:
            persons = derive_participation_seed(
                persons, wege, purpose, rng=kreis_seed_rng,
                household_id=columns.person_household_id, person_id=columns.person_id)
    return persons


def _split_projected_kreis_entries_by_level(
    effective_kreis_entries: Sequence[KreisAttributeControl],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """``project_completed_seed`` twin of :func:`_split_kreis_entries_by_level`.

    Household-level entries retain their seed column on the households frame; the
    first PERSON-level entry (trip_class) retains its derived seed column on the
    persons frame (mirrors load_mid_seed).

    Kept separate from the ``load_mid_seed`` twin because the NotImplementedError
    message names its caller and is raised inside the loop, so it cannot stay with
    the caller; duplicating the block keeps both message literals byte-identical.

    Returns: ``(_hh_extra, _person_extra)`` for ``select_seed_columns``.
    Mutates: nothing.
    """
    _hh_extra = ("RegioStaR7", "H_GR", "hh_type5", "H_MIETE", "haustyp")
    _person_extra: tuple[str, ...] = ()
    for _entry in effective_kreis_entries:
        if _entry.level == "household":
            if _entry.seed_column not in _hh_extra:
                _hh_extra = _hh_extra + (_entry.seed_column,)
        elif _entry.level == "person":
            if _entry.seed_column not in _person_extra:
                _person_extra = _person_extra + (_entry.seed_column,)
        else:
            raise NotImplementedError(
                f"project_completed_seed: kreis control entry {_entry.name!r} has unsupported "
                f"level {_entry.level!r} (expected 'household' or 'person')."
            )
    return _hh_extra, _person_extra


# --------------------------------------------------------------------------- #
# Seed
# --------------------------------------------------------------------------- #

def load_mid_seed(
    mid_dir: Union[str, Path],
    *,
    columns: seedmod.SeedColumns = seedmod.MID_SEED_COLUMNS,
    day_filter_values: Optional[Sequence[int]] = None,
    complete_members: bool = False,
    completion_rng=None,
    include_status_seed_col: bool = False,
    kreis_control_entries: Sequence[KreisAttributeControl] = (),
    kreis_seed_rng=None,
    ebike_seed_column: Optional[str] = None,
) -> tuple[pd.DataFrame, pd.DataFrame, seedmod.CompletenessReport]:
    """Load the consistent MiD seed (complete-household filtered) -- performant.

    Reads only the seed columns the controls need (not all 79 / 261 MiD columns),
    applies the complete-household (``kernwo``) filter so every kept household has
    its persons (no NaN incidence in PopulationSim), and returns
    ``(households, persons, report)`` with the essentials + ``STAAT``.

    Args:
        day_filter_values: Accepted ``kernwo`` values for the day filter.
            ``None`` (default) selects the standard weekday default declared on
            ``columns.day_filter_values``; ``()`` or any empty iterable DISABLES
            the day filter (all households kept); any non-empty iterable is used
            verbatim.
        complete_members: Opt-in (default False -> behaviour byte-identical to
            before). When True, member-incomplete households (fewer person rows
            than the declared ``H_GR``; 16.9 % of weekday-filtered seed
            households) are FILLED by mirror-household sampling (decision D3;
            see :mod:`braunschweig.popsim.member_completion`), and the person
            frame gains the total traceability columns ``member_imputed``,
            ``source_H_ID``, ``source_P_ID`` for the downstream trips join.
        completion_rng: Seeded :class:`numpy.random.RandomState` driving the
            mirror draw. REQUIRED when ``complete_members=True`` (seeded
            randomness rule: no random process without an explicit seed).
        include_status_seed_col: DEPRECATED alias for
            ``kreis_control_entries=(economic_status entry,)``; kept so existing
            callers/tests stay byte-identical (raw ``oek_status`` pass-through,
            no resolve/derivation).
        kreis_control_entries: The ACTIVE :class:`KreisAttributeControl` registry
            entries (see :mod:`braunschweig.popsim.kreis_attribute_control`) whose
            ``seed_column`` must be present on the returned seed households as a
            clean, MECE column. ``economic_status`` is a raw ``oek_status``
            pass-through; ``number_of_cars`` / ``number_of_bicycles`` / ``has_ebike``
            are derived via the corresponding ``attributes.map_*`` resolver (99
            missing code imputed) so the count-style predicates (e.g. ``>= 3``)
            never see the raw missing code.
        kreis_seed_rng: Seeded :class:`numpy.random.RandomState` for the count-style
            entry derivations (``number_of_cars`` / ``number_of_bicycles`` /
            ``has_ebike``). REQUIRED when any such entry is active (seeded
            randomness rule: no random process without an explicit seed).
        ebike_seed_column: Name of the (server-verified) MiD household e-bike
            column feeding the ``has_ebike`` entry. REQUIRED when ``has_ebike`` is
            active (no silent fallback to a guessed column name).
    """
    if complete_members and completion_rng is None:
        raise ValueError(
            "load_mid_seed(complete_members=True) requires completion_rng "
            "(a seeded numpy.random.RandomState); random processes must use an "
            "explicit seed."
        )
    effective_kreis_entries, active_kreis_entry_names = _resolve_effective_kreis_entries(
        kreis_control_entries, include_status_seed_col,
    )
    mid_dir = Path(mid_dir)
    households_path = mid_dir / "MiD2023_Haushalte.csv"
    persons_path = mid_dir / "MiD2023_Personen.csv"
    households = _read_seed_households(
        households_path,
        columns=columns,
        complete_members=complete_members,
        active_kreis_entry_names=active_kreis_entry_names,
        ebike_seed_column=ebike_seed_column,
    )
    persons, _tier3_seed_cols = _read_seed_persons(
        persons_path,
        columns=columns,
        active_kreis_entry_names=active_kreis_entry_names,
    )
    households, persons, report = _filter_complete_seed_households(
        households, persons, columns,
        day_filter_values=day_filter_values,
    )

    _rng_style_entries = _classify_rng_style_kreis_entries(active_kreis_entry_names)
    if _rng_style_entries and kreis_seed_rng is None:
        raise ValueError(
            "load_mid_seed: kreis controls with seeded imputation "
            f"{sorted(_rng_style_entries)} are active but kreis_seed_rng is not set; "
            "random imputation of the missing/nonresponse codes must use an explicit seed."
        )
    households = _derive_count_style_kreis_columns(
        households,
        active_kreis_entry_names=active_kreis_entry_names,
        kreis_seed_rng=kreis_seed_rng,
        ebike_seed_column=ebike_seed_column,
    )
    households, persons, extra_person_cols = _complete_seed_members(
        households, persons, columns,
        complete_members=complete_members,
        completion_rng=completion_rng,
    )
    persons = _derive_trip_class_seed_column(
        persons, columns,
        active_kreis_entry_names=active_kreis_entry_names,
        kreis_seed_rng=kreis_seed_rng,
    )
    persons = _derive_pt_ticket_group_seed_column(
        persons,
        active_kreis_entry_names=active_kreis_entry_names,
        kreis_seed_rng=kreis_seed_rng,
    )
    persons = _derive_employment_status_seed_column(
        persons,
        active_kreis_entry_names=active_kreis_entry_names,
        kreis_seed_rng=kreis_seed_rng,
    )
    persons = _derive_participation_seed_columns(
        persons, columns, mid_dir,
        active_kreis_entry_names=active_kreis_entry_names,
        kreis_seed_rng=kreis_seed_rng,
    )
    households = _join_hh_type5_column(households, persons, columns)
    _hh_extra, _person_extra = _split_kreis_entries_by_level(effective_kreis_entries)
    households, persons = seedmod.select_seed_columns(
        households, persons, columns,
        extra_household_cols=_hh_extra,
        extra_person_cols=extra_person_cols + _tier3_seed_cols + _person_extra,
    )
    return households, persons, report


def project_completed_seed(
    households, persons, columns, *,
    kreis_control_entries: Sequence[KreisAttributeControl] = (),
    kreis_seed_rng=None,
    ebike_seed_column: Optional[str] = None,
    include_status_seed_col: bool = False,
    mid_dir: Union[str, Path, None] = None,
):
    """Project completed-donor frames onto the PopulationSim seed, deriving the
    Tier-1 household_type column ``hh_type5`` exactly like :func:`load_mid_seed`.

    The complete_members=True path (stage.py) builds the seed from the completed
    donor frames; without the hh_type5 derivation below it called
    ``select_seed_columns`` WITHOUT deriving ``hh_type5``, so the per-batch seed CSV
    lacked the column and PopulationSim crashed (``'DataFrame' object has no
    attribute 'hh_type5'``) once the Tier-1 household_type control was enabled.
    This mirrors load_mid_seed's projection: derive the active count-style KREIS
    control seed columns from the raw donor columns the completed-donor frame
    already carries (H_ANZAUTO / anzpedrad / H_ANZPED / hhgr_gr; see
    :data:`MID_HOUSEHOLD_ATTR_COLS`), derive hh_type5 from the persons frame and
    join it onto households, then select the seed columns retaining the raw
    control cols (H_GR / H_MIETE / haustyp) plus hh_type5, RegioStaR7, and each
    active KREIS control's seed column.

    Args:
        kreis_control_entries: The ACTIVE :class:`KreisAttributeControl` registry
            entries (see :mod:`braunschweig.popsim.kreis_attribute_control`) whose
            ``seed_column`` must be present on the returned seed households.
            ``economic_status`` is a raw ``oek_status`` pass-through (already
            present on the completed-donor households). ``number_of_cars`` /
            ``number_of_bicycles`` / ``has_ebike`` are derived here via the
            corresponding ``attributes.map_*`` resolver (99 missing code imputed)
            from the raw H_ANZAUTO / anzpedrad / ``ebike_seed_column`` columns the
            completed-donor households already carry (``anzpedrad`` = bicycles
            INCLUDING pedelecs, MiD H12.3 / SrV alle-Raeder construct; the raw
            e-bike column was VERIFIED 2026-07-08 on the MiD B1 microdata to be
            ``H_ANZPED``, see :mod:`braunschweig.popsim.attributes`). has_ebike is
            now fully wired on this path (formerly deferred, issue #116).
        kreis_seed_rng: Seeded :class:`numpy.random.RandomState` for the
            count-style entry derivations (``number_of_cars`` /
            ``number_of_bicycles`` / ``has_ebike``). REQUIRED when any is active
            (seeded randomness rule: no random process without an explicit seed).
        ebike_seed_column: Name of the (server-verified) MiD household e-bike
            column feeding the ``has_ebike`` entry (default ``H_ANZPED`` at the
            stage config layer, see ``stage.KEY_EBIKE_SEED_COLUMN``). REQUIRED
            when ``has_ebike`` is active (no silent fallback to a guessed column
            name), mirroring :func:`load_mid_seed`.
        include_status_seed_col: DEPRECATED alias for
            ``kreis_control_entries=(economic_status entry,)``; kept so existing
            callers/tests stay byte-identical (raw ``oek_status`` pass-through,
            no resolve/derivation).
        mid_dir: Directory containing the MiD 2023 delivery (``MiD2023_Wege.csv``).
            REQUIRED when any participation entry (``work_participation`` feature #224
            task 4; ``leisure_participation`` / ``education_participation`` task 5) is
            active: unlike the other KREIS control seed columns, these are derived from
            the full MiD Wege table (mid.load_mid_wege), which the completed-donor
            frames do not carry. ``None`` (default) is a no-op when no participation
            control is active (no silent fallback if one is).
    """
    effective_kreis_entries, active_kreis_entry_names = _resolve_effective_kreis_entries(
        kreis_control_entries, include_status_seed_col,
    )

    if "has_ebike" in active_kreis_entry_names and not ebike_seed_column:
        raise ValueError(
            "project_completed_seed: has_ebike kreis control is active but ebike_seed_column "
            "is not configured; set braunschweig.population.popsim.ebike_seed_column to the "
            "verified MiD household e-bike column (no silent fallback)."
        )

    _rng_style_entries = _classify_rng_style_kreis_entries(active_kreis_entry_names)
    if _rng_style_entries and kreis_seed_rng is None:
        raise ValueError(
            "project_completed_seed: kreis controls with seeded imputation "
            f"{sorted(_rng_style_entries)} are active but kreis_seed_rng is not set; "
            "random imputation of the missing/nonresponse codes must use an explicit seed."
        )
    households = _derive_count_style_kreis_columns(
        households,
        active_kreis_entry_names=active_kreis_entry_names,
        kreis_seed_rng=kreis_seed_rng,
        ebike_seed_column=ebike_seed_column,
    )
    persons = _derive_trip_class_seed_column(
        persons, columns,
        active_kreis_entry_names=active_kreis_entry_names,
        kreis_seed_rng=kreis_seed_rng,
    )
    persons = _derive_pt_ticket_group_seed_column(
        persons,
        active_kreis_entry_names=active_kreis_entry_names,
        kreis_seed_rng=kreis_seed_rng,
    )
    persons = _derive_employment_status_seed_column(
        persons,
        active_kreis_entry_names=active_kreis_entry_names,
        kreis_seed_rng=kreis_seed_rng,
    )
    persons = _derive_projected_participation_seed_columns(
        persons, columns, mid_dir,
        active_kreis_entry_names=active_kreis_entry_names,
        kreis_seed_rng=kreis_seed_rng,
    )

    households = _join_hh_type5_column(households, persons, columns)
    _hh_extra, _person_extra = _split_projected_kreis_entries_by_level(effective_kreis_entries)
    return seedmod.select_seed_columns(
        households, persons, columns,
        extra_household_cols=_hh_extra,
        extra_person_cols=tuple(
            c for c in ("P_TAET", "bildung1", "bildung2") if c in persons.columns
        ) + _person_extra,
    )
