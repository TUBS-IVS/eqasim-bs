"""MiD seed loading for the popsim mid stage.

- ``load_mid_seed``           -- the consistent (complete-household) MiD seed,
                                  loaded directly from the raw MiD CSV delivery
- ``project_completed_seed``  -- project the member-completed donor frames
                                  (``load_completed_donor``) onto the same seed
                                  schema ``load_mid_seed`` produces

Extracted verbatim from the stage module (``__init__``); see the package
docstring for the stage-level context.

``detect_csv_separator`` moved to its own leaf module ``csv_format.py`` (task 4
ruling, see that module's docstring): it has call sites in this module AND in
``donor.py`` (task 5's ``load_mid_attributes`` / ``load_mid_wege``), so it is a
multi-module dependency rather than a single-consumer helper -- both later
submodules import it from ``csv_format`` directly rather than duplicating it.

``load_mid_wege`` is called by both functions below but still lives in the
package ``__init__`` at this point in the split (it moves to ``donor.py`` in
task 5; it also has consumers outside this package, e.g. ``trips_stage.py`` /
``stage.py`` / ``sources/mid.py``, so relocating it is not a task-4 concern).
Importing it at THIS module's top level would fail: the package facade below
imports ``seed_loading`` before ``__init__.py`` defines ``load_mid_wege``
further down the file, so ``from braunschweig.popsim.mid import
load_mid_wege`` at module scope would raise an ImportError against the
partially initialized package. Both call sites therefore import it locally,
immediately before use; by the time either function is actually CALLED
(always after the package has fully initialized), the name resolves normally.
This is a mechanical consequence of the split ordering (task 4 runs before
task 5), not a behavior change.
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
from .participation import derive_participation_seed
from .participation import derive_trip_class_seed


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
    # Deprecated alias: include_status_seed_col=True is equivalent to activating the
    # economic_status registry entry, so old callers/tests stay byte-identical
    # (oek_status is a raw pass-through -- the same behaviour as before this task).
    effective_kreis_entries: list[KreisAttributeControl] = list(kreis_control_entries)
    if include_status_seed_col and not any(
        entry.name == "economic_status" for entry in effective_kreis_entries
    ):
        effective_kreis_entries.append(
            next(entry for entry in KREIS_CONTROL_REGISTRY if entry.name == "economic_status")
        )
    active_kreis_entry_names = {entry.name for entry in effective_kreis_entries}
    mid_dir = Path(mid_dir)
    # Load household id, weight, and RegioStaR7 (Phase 4A plumbing: the RS7 code
    # is carried onto the seed households so Phase 4B donor stratification can use
    # the cell's urban/rural class to restrict the donor pool without an extra join).
    households_path = mid_dir / "MiD2023_Haushalte.csv"
    persons_path = mid_dir / "MiD2023_Personen.csv"
    # Always load H_GR (declared household size) so the Tier-1 household_size
    # control expression ``(households.H_GR == N)`` can be evaluated by
    # PopulationSim. H_GR was previously loaded only when complete_members=True;
    # the Tier-7 addition makes it unconditionally required in the seed.
    # Always load H_MIETE (tenure flag: 1=renter, 2=owner) so the Tier-2 tenure
    # control expressions ``(households.H_MIETE == 1/2)`` can be evaluated by
    # PopulationSim. Values 3/9/309 (ambiguous) are kept in the seed column;
    # the control expressions simply do not match them (they contribute 0 to
    # either tenure control, which is the correct treatment for excluded codes).
    # Always load haustyp (building type: 1=EFH/ZFH, 2=MFH, 3=Geschosswohnung,
    # 4=sonstiges, 95=n.z.) so the Tier-2 building_type control expressions
    # can be evaluated by PopulationSim. Code 95 (n.z.) does not match any
    # building_type expression and is therefore silently excluded from all three
    # building_type controls (correct behaviour, no fabricated assignments).
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
    # participation controls (work_participation task 4; leisure_participation /
    # education_participation task 5, feature #224): load the raw diary trip count
    # (anzwege1, the default trips_col mid.compute_has_purpose_trip uses to carry
    # through the 803/804 diary-nonresponse codes) and the age-band conditioning column
    # (alter_gr1) so the has-<purpose>-trip flag can be derived from the MiD Wege table +
    # the 803/804 codes imputed within alter_gr1, mirroring the trip_class block above.
    # Dedup-safe (anzwege1/alter_gr1 may already be present via trip_class/
    # employment_status/tier3); shared across all three participation entries since
    # they all read the same two raw columns.
    if active_kreis_entry_names & {"work_participation", "leisure_participation", "education_participation"}:
        for _pp_col in ("anzwege1", "alter_gr1"):
            if _pp_col not in person_cols:
                person_cols.append(_pp_col)
    persons = pd.read_csv(
        persons_path,
        usecols=list(dict.fromkeys(person_cols)),
        sep=_persons_sep,
    )

    # `None` -> standard weekday default; explicit empty iterable -> day filter OFF
    # (filter_complete_households treats `None` as "no day filtering"); a plain
    # `or` here would silently resurrect the default for an empty tuple.
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

    # Derive the clean, MECE seed columns for the active count-style kreis controls
    # AFTER the complete-household filter (so the group-wise imputation pool reflects
    # only the kept seed households). economic_status needs no derivation here -- its
    # seed column (oek_status) is used RAW by the == k predicate (byte-identical to the
    # pre-existing include_status_seed_col=True behaviour).
    # Count-style entries impute the 99 missing code (household level); the person-level
    # trip_class entry imputes the 803/804 diary-nonresponse codes (within alter_gr1); the
    # person-level employment_status entry imputes the P_BKAT code-9 (keine Angabe) cases
    # (also within alter_gr1). All are random processes that REQUIRE the seeded
    # kreis_seed_rng (no unseeded randomness).
    _count_style_entries = active_kreis_entry_names & {
        "number_of_cars", "number_of_bicycles", "has_ebike"
    }
    _rng_style_entries = _count_style_entries | (
        active_kreis_entry_names & {
            "trip_class", "employment_status",
            "work_participation", "leisure_participation", "education_participation",
        }
    )
    if _rng_style_entries and kreis_seed_rng is None:
        raise ValueError(
            "load_mid_seed: kreis controls with seeded imputation "
            f"{sorted(_rng_style_entries)} are active but kreis_seed_rng is not set; "
            "random imputation of the missing/nonresponse codes must use an explicit seed."
        )
    if "number_of_cars" in active_kreis_entry_names:
        households = attributes.map_number_of_cars(households, rng=kreis_seed_rng)
    if "number_of_bicycles" in active_kreis_entry_names:
        households = attributes.map_number_of_bicycles(households, rng=kreis_seed_rng)
    if "has_ebike" in active_kreis_entry_names:
        households = attributes.map_has_ebike(
            households, ebike_col=ebike_seed_column, rng=kreis_seed_rng
        )

    extra_person_cols: tuple[str, ...] = ()
    if complete_members:
        # Fill member-incomplete households AFTER the day filter (the host
        # household must have passed it) and BEFORE the column selection (the
        # match keys H_GR/hhgr_gr/oek_status are dropped again below, so the
        # output household schema is unchanged).
        households, persons, _ = completion.complete_members(
            households, persons, rng=completion_rng,
            household_id=columns.household_id,
        )
        extra_person_cols = ("member_imputed", "source_H_ID", "source_P_ID")

    # trip_class (person-level KREIS control): derive the int-coded class (0..3) from the
    # raw diary trip count AFTER the complete-household filter + member completion, so the
    # 803/804 diary-nonresponse imputation pool reflects only the kept seed persons and any
    # mirror-imputed member inherits the mirror donor's diary (documented, see
    # MID_PERSON_ATTR_COLS). The rng guard above ensures kreis_seed_rng is set. The class
    # is seeded from the person's REALISED weekday plan source (not their own reporting-day
    # diary) so the control matches the SrV weekday target universe -- see
    # derive_trip_class_seed (audit 2026-07-09).
    if "trip_class" in active_kreis_entry_names:
        persons = derive_trip_class_seed(
            persons, rng=kreis_seed_rng,
            household_id=columns.person_household_id, person_id=columns.person_id)

    # employment_status (second PERSON-level KREIS control): derive the P9 seven-class
    # string from the raw P_BKAT code AFTER the complete-household filter + member
    # completion, so a mirror-imputed filler's employment_status matches its inherited
    # (donor-mirrored) P_BKAT and the code-9 imputation pool reflects only the kept seed
    # persons -- exactly mirroring the trip_class derivation above. The rng guard above
    # ensures kreis_seed_rng is set. Uses the SAME attributes.map_employment_status as the
    # post-expansion assembly.build_persons, so seed and expanded values agree
    # deterministically for the 99.87% of persons with a valid (non-9) P_BKAT; the code-9
    # imputed cases may differ by an independent rng draw -- acceptable for a control
    # (matches the trip_class precedent, task 4b).
    if "employment_status" in active_kreis_entry_names:
        persons = attributes.map_employment_status(persons, rng=kreis_seed_rng)

    # participation controls (work_participation task 4; leisure_participation /
    # education_participation task 5, feature #224): derive the 0/1 has-a-<purpose>-trip
    # flag from the MiD Wege table AFTER the complete-household filter + member
    # completion, seeded from the person's REALISED weekday plan source exactly like
    # trip_class (mid.derive_participation_seed) -- see that function's docstring for the
    # full weekday-vs-realised-plan rationale. The rng guard above ensures
    # kreis_seed_rng is set. Requires the full MiD Wege table, which load_mid_seed does
    # not otherwise read; loaded ONCE here (gated on ANY participation control being
    # active) so the OFF path never touches MiD2023_Wege.csv (byte-identical no-op).
    _active_participation_purposes = [
        purpose for purpose in ("work", "leisure", "education")
        if f"{purpose}_participation" in active_kreis_entry_names
    ]
    if _active_participation_purposes:
        # Local import: load_mid_wege still lives in the package __init__ at this point
        # in the #267 split (moves to donor.py in task 5); see the module docstring above
        # for why this cannot be a module-level import here.
        from braunschweig.popsim.mid import load_mid_wege
        wege = load_mid_wege(mid_dir)
        for purpose in _active_participation_purposes:
            persons = derive_participation_seed(
                persons, wege, purpose, rng=kreis_seed_rng,
                household_id=columns.person_household_id, person_id=columns.person_id)

    # Derive hh_type5 (Tier-1 household_type/Familientyp 5-class) from the
    # filtered persons frame.  derive_hh_type5 runs map_households_to_hhtype
    # (11-class) then collapses to the 5 Zensus Familientyp labels.  The result
    # is a per-household Series indexed by H_ID that is merged onto households.
    # This must happen BEFORE select_seed_columns so hh_type5 can be retained as
    # an extra_household_col; it uses the raw MiD column names (H_ID / HP_ALTER).
    hh_type5_series = seedmod.derive_hh_type5(
        persons,
        household_id_col=columns.person_household_id,
        age_col=columns.age,
    )
    households = households.join(
        hh_type5_series.rename("hh_type5"),
        on=columns.household_id,
    )

    _hh_extra = ("RegioStaR7", "H_GR", "hh_type5", "H_MIETE", "haustyp")
    _person_extra: tuple[str, ...] = ()
    for _entry in effective_kreis_entries:
        # Household-level entries retain their seed column on the households frame; the
        # first PERSON-level entry (trip_class) retains its derived seed column on the
        # persons frame so the population control expression (persons.trip_class == k)
        # can be evaluated by PopulationSim.
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
    # Deprecated alias: include_status_seed_col=True is equivalent to activating the
    # economic_status registry entry (byte-identical to the pre-existing behaviour).
    effective_kreis_entries: list[KreisAttributeControl] = list(kreis_control_entries)
    if include_status_seed_col and not any(
        entry.name == "economic_status" for entry in effective_kreis_entries
    ):
        effective_kreis_entries.append(
            next(entry for entry in KREIS_CONTROL_REGISTRY if entry.name == "economic_status")
        )
    active_kreis_entry_names = {entry.name for entry in effective_kreis_entries}

    if "has_ebike" in active_kreis_entry_names and not ebike_seed_column:
        raise ValueError(
            "project_completed_seed: has_ebike kreis control is active but ebike_seed_column "
            "is not configured; set braunschweig.population.popsim.ebike_seed_column to the "
            "verified MiD household e-bike column (no silent fallback)."
        )

    # Derive the clean, MECE seed columns for the active count-style kreis controls from
    # the raw H_ANZAUTO / anzpedrad / ebike_seed_column columns the completed-donor
    # households already carry (see MID_HOUSEHOLD_ATTR_COLS); mirrors the load_mid_seed
    # derivation exactly.
    # Count-style entries impute the 99 missing code (household level); trip_class imputes
    # the 803/804 diary-nonresponse codes (person level); employment_status imputes the
    # P_BKAT code-9 (keine Angabe) cases (also person level). All REQUIRE the seeded rng.
    _count_style_entries = active_kreis_entry_names & {
        "number_of_cars", "number_of_bicycles", "has_ebike"
    }
    _rng_style_entries = _count_style_entries | (
        active_kreis_entry_names & {
            "trip_class", "employment_status",
            "work_participation", "leisure_participation", "education_participation",
        }
    )
    if _rng_style_entries and kreis_seed_rng is None:
        raise ValueError(
            "project_completed_seed: kreis controls with seeded imputation "
            f"{sorted(_rng_style_entries)} are active but kreis_seed_rng is not set; "
            "random imputation of the missing/nonresponse codes must use an explicit seed."
        )
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
    if "trip_class" in active_kreis_entry_names:
        # Derive trip_class from each person's REALISED weekday plan source, not their own
        # reporting-day diary: after weekend_plan_match the completed-donor frame's
        # source_(H_ID,P_ID) point to weekday donors, so the source's anzwege1 is both the
        # SrV Di-Do target universe and the trips the synthetic person executes. See
        # derive_trip_class_seed (audit 2026-07-09; ~29% weekend reporters otherwise
        # carried a weekend diary count into the weekday-anchored control).
        persons = derive_trip_class_seed(
            persons, rng=kreis_seed_rng,
            household_id=columns.person_household_id, person_id=columns.person_id)
    if "employment_status" in active_kreis_entry_names:
        # Derive employment_status from the completed donor's P_BKAT (already loaded via
        # MID_PERSON_ATTR_COLS -- see mid.MID_PERSON_ATTR_COLS); mirrors load_mid_seed's
        # derivation exactly. A mirror-imputed filler inherits the mirror donor's P_BKAT
        # (member completion samples whole donor person rows, same as anzwege1/trip_class),
        # so this reflects the full completed population, agreeing deterministically with
        # assembly.build_persons for the 99.87% of persons with a valid (non-9) P_BKAT.
        persons = attributes.map_employment_status(persons, rng=kreis_seed_rng)
    _active_participation_purposes = [
        purpose for purpose in ("work", "leisure", "education")
        if f"{purpose}_participation" in active_kreis_entry_names
    ]
    if _active_participation_purposes:
        # Derive each active <purpose>_participation from the completed donor's MiD Wege
        # table (loaded from mid_dir; the completed-donor frames do not carry the Wege
        # rows themselves). Seeded from the person's REALISED weekday plan source exactly
        # like trip_class (mid.derive_participation_seed) -- see that function's
        # docstring for the full weekday-vs-realised-plan rationale. Wege is loaded ONCE
        # and reused for every active purpose (work_participation task 4; leisure_
        # participation / education_participation task 5, feature #224).
        if mid_dir is None:
            raise ValueError(
                "project_completed_seed: a participation kreis control "
                f"{_active_participation_purposes} is active but mid_dir is not set; cannot "
                f"load the MiD Wege table to derive participation flags for {_active_participation_purposes} (no silent fallback)."
            )
        # Local import: load_mid_wege still lives in the package __init__ at this point
        # in the #267 split (moves to donor.py in task 5); see the module docstring above
        # for why this cannot be a module-level import here.
        from braunschweig.popsim.mid import load_mid_wege
        wege = load_mid_wege(mid_dir)
        for purpose in _active_participation_purposes:
            persons = derive_participation_seed(
                persons, wege, purpose, rng=kreis_seed_rng,
                household_id=columns.person_household_id, person_id=columns.person_id)

    hh_type5_series = seedmod.derive_hh_type5(
        persons,
        household_id_col=columns.person_household_id,
        age_col=columns.age,
    )
    households = households.join(
        hh_type5_series.rename("hh_type5"),
        on=columns.household_id,
    )
    _hh_extra = ("RegioStaR7", "H_GR", "hh_type5", "H_MIETE", "haustyp")
    _person_extra: tuple[str, ...] = ()
    for _entry in effective_kreis_entries:
        # Household-level entries retain their seed column on the households frame; the
        # first PERSON-level entry (trip_class) retains its derived seed column on the
        # persons frame (mirrors load_mid_seed).
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
    return seedmod.select_seed_columns(
        households, persons, columns,
        extra_household_cols=_hh_extra,
        extra_person_cols=tuple(
            c for c in ("P_TAET", "bildung1", "bildung2") if c in persons.columns
        ) + _person_extra,
    )
