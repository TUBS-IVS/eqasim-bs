"""MiD donor attribute + trip table loading for the popsim mid stage.

- ``MID_PERSON_ATTR_COLS``     -- required MiD person donor attribute columns
- ``MID_PERSON_OPTIONAL_COLS`` -- optional Tier-3 education donor columns
- ``MID_HOUSEHOLD_ATTR_COLS``  -- required MiD household donor attribute columns
- ``MID_WEGE_REQUIRED_COLS``   -- minimum MiD Wege columns for the trips_stage
- ``load_mid_attributes``      -- load the MiD donor attribute tables (households + persons)
- ``drop_invalid_households``  -- drop the H_ID=0/null invalid-household sentinel
- ``load_completed_donor``     -- day-filtered + member-completed donor frames (the ONE
                                   completion pass feeding seed AND expansion)
- ``load_mid_wege``            -- load the full MiD Wege (trip) table for the trips_stage

Extracted verbatim from the stage module (``__init__``); see the package
docstring for the stage-level context.

``detect_csv_separator`` is imported from the leaf module ``csv_format.py``
(task 4 ruling; see that module's docstring): both ``load_mid_attributes`` and
``load_mid_wege`` below need it, mirroring ``seed_loading.load_mid_seed``.

``load_mid_wege`` moves here from the package ``__init__`` (it previously had
to stay there because ``seed_loading.py`` -- extracted in task 4, before this
task -- imports it locally inside ``load_mid_seed`` / ``project_completed_seed``
to avoid a partially-initialized-package ImportError; see that module's
docstring). Now that ``load_mid_wege`` lives in this sibling leaf module,
``seed_loading.py`` imports it as a normal module-level sibling import
(``from .donor import load_mid_wege``) and both function-local workaround
imports + their explanatory comments are removed (task 5 cleanup, carried
forward from the task 4 review). ``donor.py`` does not import ``seed_loading``,
so no import cycle results.

``MID_SEED_COLUMNS`` is aliased again here (module-level) so
``load_completed_donor``'s body -- which references the bare name -- moves
verbatim. The authoritative definition and the public facade re-export
(``braunschweig.popsim.mid.MID_SEED_COLUMNS``) remain in ``__init__.py``; see
the comment there. This is a two-line alias, not a duplicated derivation.

The ``braunschweig.popsim.member_completion`` import (aliased ``completion``,
needed for ``completion.complete_members`` / ``completion.MemberCompletionReport``
in ``load_completed_donor``) moves here for the same reason as the leaf-module
constants in earlier tasks: it had no other consumer left in ``__init__.py``
once ``load_completed_donor`` moved. It is re-exported from ``__init__.py`` so
the public namespace is unchanged.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional, Sequence, Union

import pandas as pd

from braunschweig.popsim import member_completion as completion
from braunschweig.popsim import seed as seedmod

from .csv_format import detect_csv_separator

logger = logging.getLogger(__name__)

# See the module docstring: kept in sync with the identical alias in
# __init__.py, which is the authoritative definition + public facade export.
MID_SEED_COLUMNS = seedmod.MID_SEED_COLUMNS

# MiD columns needed to enrich the synthetic persons (beyond the seed control cols).
MID_PERSON_ATTR_COLS = (
    "H_ID", "P_ID", "HP_ALTER", "HP_SEX", "P_TAET", "P_FSCHEIN", "P_FKARTE", "P_BKAT",
    "alter_gr1",  # conditioning column for grouped item-nonresponse imputation
    # anzwege1: raw MiD diary trip count (Anzahl Wege am Stichtag; valid 0..50, missing
    # codes 803/804 = trip module not covered) feeding the person-level trip_class KREIS
    # control (attributes.map_trip_class). Carried on the completed-donor frames so
    # project_completed_seed can derive trip_class; a mirror-imputed member inherits the
    # mirror donor's diary trip count (documented -- the completion samples whole donor
    # person rows, so the filler's anzwege1 is the donor's).
    "anzwege1",
    # P_GEW (seed person weight) + kernwo (day filter): needed so the completed
    # donor frames (load_completed_donor) can serve BOTH the expansion AND the
    # PopulationSim seed from ONE member-completion pass.
    "P_GEW", "kernwo",
)
# Tier-3 education control inputs: present in the real MiD person table, absent in some
# small test fixtures -> loaded only when present (load_mid_attributes / load_mid_seed).
MID_PERSON_OPTIONAL_COLS = ("bildung1", "bildung2")
MID_HOUSEHOLD_ATTR_COLS = (
    "H_ID", "oek_status", "hheink_gr1", "H_ANZAUTO", "H_ANZRAD",
    # anzpedrad: MiD-provided combined bicycle count INCLUDING pedelecs/e-bikes
    # (verified 2026-07-08 to equal min(H_ANZRAD + H_ANZPED, 10) on all 218,039 valid
    # MiD B1 household rows). This is the number_of_bicycles construct (MiD H12.3 /
    # SrV alle-Raeder); H_ANZRAD (conventional bikes only) is retained for any
    # downstream consumer that still needs the exclusive count.
    "anzpedrad",
    # H_ANZPED: verified MiD household e-bike (Pedelec) column feeding has_ebike
    # (attributes.map_has_ebike); see the module docstring there for the full
    # verification note.
    "H_ANZPED",
    "RegioStaR7",  # Phase 4A: RegioStaR-7 code for donor urban/rural stratification
    "hhgr_gr",  # conditioning column for grouped item-nonresponse imputation
    # H_GR (declared household size; drives member completion) + H_GEW (seed
    # household weight): needed so the completed frames can serve BOTH the
    # expansion AND the PopulationSim seed.
    "H_GR", "H_GEW",
    # Tier-2 popsim control attributes: carried from the MiD donor table onto the
    # synthetic persons frame so the popsim control-fit validation can compare
    # realized vs. target distributions.
    # H_MIETE: tenure flag (1=renter/Mieter, 2=owner/Eigentuemer; 3/9/309=excluded).
    # haustyp: building type (1=EFH/ZFH, 2=MFH 3-12Wohn., 3=Geschosswohn. 13+, 4=sonstiges, 95=n.z.).
    "H_MIETE", "haustyp",
)

# Minimum columns required by build_trip_table / trips_stage.
# All remaining columns are carried as extras (loaded via usecols=None -> all).
MID_WEGE_REQUIRED_COLS = (
    "H_ID", "P_ID", "W_ID",
    "W_ZWECK", "hvm_imp",
    "W_SZS", "W_SZM",
    "W_AZS", "W_AZM",
    "wegkm_imp",
    # MiD-imputed trip duration in MINUTES; fully populated and code-free for
    # all rbW (time code 701) rows.  Consumed by the stage A time imputation
    # (braunschweig.popsim.time_imputation) — never use raw wegmin, which
    # carries the code 70701 on those rows.
    "wegmin_imp1",
)


def load_mid_attributes(
    mid_dir: Union[str, Path],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Load the MiD donor attribute columns (households + persons) for enrichment.

    Reads only the columns the eqasim attribute mapping needs (not all MiD
    columns); returns ``(households, persons)`` for
    ``braunschweig.popsim.assembly.build_persons``.
    """
    mid_dir = Path(mid_dir)
    households_path = mid_dir / "MiD2023_Haushalte.csv"
    persons_path = mid_dir / "MiD2023_Personen.csv"
    households = pd.read_csv(
        households_path, usecols=list(MID_HOUSEHOLD_ATTR_COLS),
        sep=detect_csv_separator(households_path),
    )
    _persons_sep = detect_csv_separator(persons_path)
    _persons_header = pd.read_csv(persons_path, sep=_persons_sep, nrows=0).columns
    persons = pd.read_csv(
        persons_path,
        usecols=list(MID_PERSON_ATTR_COLS)
        + [c for c in MID_PERSON_OPTIONAL_COLS if c in _persons_header],
        sep=_persons_sep,
    )
    return households, persons


def drop_invalid_households(households, persons, *, household_id="H_ID"):
    """Drop the invalid-household sentinel: rows whose household id is 0 or null.

    Real MiD 2023 has an H_ID=0 bucket of 312 unrelated persons with mixed
    reporting days -- not a real household; it must not be a population donor.
    Returns ``(households, persons, n_hh_dropped, n_persons_dropped)``.
    """
    bad_hh = households[household_id].isna() | (households[household_id] == 0)
    n_hh = int(bad_hh.sum())
    keep_p = ~(persons[household_id].isna() | (persons[household_id] == 0))
    n_p = int((~keep_p).sum())
    return (
        households[~bad_hh].copy(),
        persons[keep_p].copy(),
        n_hh,
        n_p,
    )


def load_completed_donor(
    mid_dir: Union[str, Path],
    *,
    completion_rng,
    day_filter_values: Optional[Sequence[int]] = None,
) -> tuple[
    pd.DataFrame, pd.DataFrame,
    seedmod.CompletenessReport, completion.MemberCompletionReport,
]:
    """Load the donor attribute tables, apply the day-filter completeness rule,
    and fill member-incomplete households (mirror-household sampling).

    This is the ONE member-completion pass of the popsim_mid workflow: BOTH the
    PopulationSim seed (via ``seed.select_seed_columns`` on the returned frames)
    AND the expansion (``assembly.build_persons``) derive from the completed
    frames, so seed and expansion are guaranteed to contain the SAME fillers.
    The attribute usecols therefore include the seed columns (``P_GEW`` /
    ``kernwo`` / ``H_GR`` / ``H_GEW``, see ``MID_PERSON_ATTR_COLS`` /
    ``MID_HOUSEHOLD_ATTR_COLS``).

    Args:
        mid_dir: Directory with ``MiD2023_Haushalte.csv`` / ``MiD2023_Personen.csv``.
        completion_rng: Seeded :class:`numpy.random.RandomState` driving the
            mirror draw (REQUIRED; no random process without an explicit seed).
        day_filter_values: Accepted ``kernwo`` values. ``None`` (default) ->
            the standard weekday default on ``MID_SEED_COLUMNS``; an empty
            iterable DISABLES the day filter; any non-empty iterable is used
            verbatim (same tri-state contract as :func:`load_mid_seed`).

    Returns:
        ``(households, persons, completeness_report, completion_report)``.
        The persons frame carries ``member_imputed`` + ``source_H_ID`` /
        ``source_P_ID`` (total columns: regular persons reference themselves;
        fillers reference their mirror donor for the trips join and the
        pseudonym map).
    """
    if completion_rng is None:
        raise ValueError(
            "load_completed_donor requires completion_rng (a seeded "
            "numpy.random.RandomState); random processes must use an explicit seed."
        )
    households, persons = load_mid_attributes(mid_dir)

    # Drop H_ID=0 / null sentinel before any downstream logic (donor validity).
    households, persons, _n_hh_bad, _n_p_bad = drop_invalid_households(
        households, persons, household_id=MID_SEED_COLUMNS.household_id)
    if _n_hh_bad or _n_p_bad:
        logger.warning(
            "[mid.load_completed_donor] dropped %d invalid-household row(s) "
            "(H_ID=0 or null) and %d associated person(s) from the donor data; "
            "these are not real households and must not be population donors.",
            _n_hh_bad, _n_p_bad,
        )

    # Same tri-state day-filter contract as load_mid_seed: `None` -> standard
    # weekday default; explicit empty iterable -> day filter OFF; else verbatim.
    if day_filter_values is None:
        effective_day_filter = MID_SEED_COLUMNS.day_filter_values
    elif len(tuple(day_filter_values)) == 0:
        effective_day_filter = None
    else:
        effective_day_filter = tuple(day_filter_values)

    households, persons, completeness_report = seedmod.filter_complete_households(
        households, persons, MID_SEED_COLUMNS,
        day_filter_values=effective_day_filter,
    )
    households, persons, completion_report = completion.complete_members(
        households, persons, rng=completion_rng,
        household_id=MID_SEED_COLUMNS.household_id,
    )
    return households, persons, completeness_report, completion_report


def load_mid_wege(
    mid_dir: Union[str, Path],
) -> pd.DataFrame:
    """Load the MiD 2023 Wege (trip) table for the trips_stage.

    The field separator is detected from the header (``detect_csv_separator``)
    because the MiD 2023 delivery occurs in both comma- and semicolon-separated
    variants; assuming one silently mis-parses the other into a single column.
    All columns are loaded (no usecols filter) so that every MiD Wege extra
    column is available as a traceability/analysis extra in the output trip
    table. The minimum columns required by
    ``braunschweig.popsim.trips.build_trip_table`` are listed in
    ``MID_WEGE_REQUIRED_COLS``; the file is validated to contain them.

    Parameters
    ----------
    mid_dir:
        Directory containing ``MiD2023_Wege.csv``.

    Returns
    -------
    pd.DataFrame
        Full Wege table, one row per (household, person, trip).
    """
    mid_dir = Path(mid_dir)
    wege_path = mid_dir / "MiD2023_Wege.csv"
    if not wege_path.exists():
        raise FileNotFoundError(
            f"MiD Wege file not found: {wege_path}. "
            "Ensure the MiD 2023 delivery is present in the configured mid_dir."
        )
    df = pd.read_csv(wege_path, sep=detect_csv_separator(wege_path), low_memory=False)
    missing = [c for c in MID_WEGE_REQUIRED_COLS if c not in df.columns]
    if missing:
        raise ValueError(
            f"MiD Wege file is missing required columns: {missing}. "
            f"Available columns: {list(df.columns[:20])} ..."
        )
    return df
