"""PopsimSource Protocol: the interface every donor-source adapter must implement.

A donor source encapsulates the survey-specific I/O and attribute-mapping logic
for one household travel survey (currently MiD 2023; ENTD planned for Phase 2).
All popsim workflows that need a donor population depend only on this Protocol,
not on any survey-specific module, so the workflow code is survey-agnostic.

Protocol methods
----------------
name:
    Short lowercase identifier for the source, e.g. ``"mid"`` or ``"entd"``.
    Used as a registry key and in log messages.

seed_columns() -> SeedColumns:
    Return the :class:`braunschweig.popsim.seed.SeedColumns` mapping that
    describes the survey's household/person column names for PopulationSim seed
    building.

load_donor(data_dir) -> (households, persons, trips):
    Load the raw donor tables from ``data_dir``.  Returns three DataFrames:
    the donor household table, the donor person table, and the donor trip table
    (MiD Wege / ENTD Deplacements).  The caller owns the returned objects.

map_person_attributes(persons, households, *, rng) -> (persons, pseudonym_map):
    Map survey-specific person and household columns to the eqasim synthesis
    schema attributes (``employed``, ``has_license``, ``economic_status``, …).
    ``persons`` is the pre-expanded frame produced by
    ``braunschweig.popsim.expand`` (demographic mapping already applied).
    ``households`` is the donor household table from ``load_donor``.
    Returns the 2-tuple ``(persons, pseudonym_map)``: the persons frame with
    all attribute columns appended, and the pseudonym map
    (``[source_person_id, source_household_id, H_ID, P_ID]``) for local-only
    re-linking of pseudonymised donor ids.  The map may be EMPTY (open-data
    sources like ENTD) but must always be returned explicitly --
    ``assembly.build_persons`` raises :class:`TypeError` on a non-tuple return
    so a pseudonymisation-required source can never silently lose its map.

build_trips(persons, donor_trips, *, random_seed, escort_purpose=False,
            escort_passive_education=False, exclude_rbw_legs=False,
            drop_leading_arrive_home_leg=False, closure_dwell_model="fixed_1h",
            closure_dwell_min_obs=30) -> trips:
    Build the 11-column synthesis.population.trips contract DataFrame from the
    per-synthetic-person donor trip chains.  ``persons`` carries
    ``person_id``, ``H_ID``, ``P_ID``; ``donor_trips`` is the table returned
    by ``load_donor``.  ``random_seed`` controls the per-person departure-time
    jitter (deterministic, reproducible).  ``escort_purpose`` maps MiD W_ZWECK
    {6, 13} to the dedicated 'escort' purpose (issue #201).
    ``escort_passive_education`` further maps the passive leg (W_ZWECK 13) to
    'education' instead of 'escort' (issue #256); requires ``escort_purpose``.
    ``exclude_rbw_legs`` / ``drop_leading_arrive_home_leg`` drop the two
    non-diary leg kinds (issue #366) and ``closure_dwell_model`` selects the
    dwell model for the synthesised chain closure (issue #367); an adapter
    whose survey cannot support one of them must REJECT the non-default value
    instead of ignoring it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, Tuple, Union

import pandas as pd

from braunschweig.popsim.seed import SeedColumns


class PopsimSource(Protocol):
    """Protocol that every donor-source adapter must satisfy.

    Implementations must provide a ``name`` attribute and four methods
    (``seed_columns``, ``load_donor``, ``map_person_attributes``,
    ``build_trips``).  See module-level docstring for the full contract.
    """

    name: str

    def seed_columns(self) -> SeedColumns:
        """Return the SeedColumns mapping for this survey's household/person tables."""
        ...

    def load_donor(
        self, data_dir: Union[str, Path]
    ) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        """Load (households, persons, trips) from data_dir.

        Parameters
        ----------
        data_dir:
            Directory containing the survey CSV files.

        Returns
        -------
        tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]
            ``(households, persons, trips)`` donor tables.
        """
        ...

    def map_person_attributes(
        self,
        persons: pd.DataFrame,
        households: pd.DataFrame,
        *,
        rng,
    ) -> Tuple[pd.DataFrame, pd.DataFrame]:
        """Map donor attributes to the eqasim synthesis schema.

        Parameters
        ----------
        persons:
            Expanded persons frame (after expand.map_demographics and
            assembly.derive_zone_ids have been applied).
        households:
            Donor household table (from load_donor).
        rng:
            NumPy RandomState for stochastic attribute assignments.

        Returns
        -------
        tuple[pd.DataFrame, pd.DataFrame]
            ``(persons, pseudonym_map)``: the persons frame with all attribute
            columns appended, and the pseudonym map
            (``[source_person_id, source_household_id, H_ID, P_ID]``).
            The map may be empty (open-data sources) but must be explicit;
            ``assembly.build_persons`` raises TypeError on a non-tuple return.
        """
        ...

    def donor_stratum(self, seed_households: pd.DataFrame) -> pd.Series:
        """Return the per-household stratum label for donor stratification (Phase 4B).

        The stratum granularity depends on the source:
        - MiD: RegioStaR-7 code (integer, 7 classes 71-77).
        - ENTD: RegioStaR-2 label string (``"urban"`` / ``"rural"``).

        The returned Series must use the SAME label space as :meth:`cell_stratum`
        so that cells and donors can be matched by a simple equality check.

        Parameters
        ----------
        seed_households:
            Donor household frame returned by :meth:`load_donor`.

        Returns
        -------
        pd.Series
            Stratum label per household row, same index as ``seed_households``.
        """
        ...

    def cell_stratum(self, cells: pd.DataFrame) -> pd.Series:
        """Return the per-100m-cell stratum label for donor stratification (Phase 4B).

        The stratum granularity depends on the source:
        - MiD: ``cells["RegioStaR7"]`` directly (7-class).
        - ENTD: ``cells["RegioStaR7"].map(cell_urban_class_from_rs7)`` (2-class).

        The returned Series must use the SAME label space as :meth:`donor_stratum`
        so that cells and donors can be matched by a simple equality check.

        Parameters
        ----------
        cells:
            100m cells frame carrying ``RegioStaR7``.

        Returns
        -------
        pd.Series
            Stratum label per cell row, same index as ``cells``.
        """
        ...

    def build_trips(
        self,
        persons: pd.DataFrame,
        donor_trips: pd.DataFrame,
        *,
        random_seed: int,
        escort_purpose: bool = False,
        escort_passive_education: bool = False,
        explicit_round_trip_purposes: bool = True,
        exclude_rbw_legs: bool = False,
        drop_leading_arrive_home_leg: bool = False,
        closure_dwell_model: str = "fixed_1h",
        closure_dwell_min_obs: int = 30,
    ) -> pd.DataFrame:
        """Build the synthesis.population.trips contract DataFrame.

        Parameters
        ----------
        persons:
            Synthetic persons with ``person_id``, ``H_ID``, ``P_ID``.
        donor_trips:
            Donor trip table from load_donor.
        random_seed:
            Integer seed for the per-person departure-time jitter RNG.
        escort_purpose:
            map MiD W_ZWECK {6, 13} to the dedicated 'escort' purpose (issue #201).
        escort_passive_education:
            map the passive escort leg (W_ZWECK 13) to 'education' instead of
            'escort' (issue #256). Requires ``escort_purpose=True``.
        explicit_round_trip_purposes:
            give the MiD round-trip leisure W_ZWECK codes their own purposes
            instead of the 'other' catch-all (issue #241). Donor taxonomies
            without those codes ignore it; the implementing adapter documents
            what it does with the flag.
        exclude_rbw_legs:
            drop the survey's rbW legs (MiD ``W_RBW == 1``, the
            regelmaessiger-beruflicher-Weg summary records) before the join
            (issue #366). Donor taxonomies without such records must reject
            ``True`` rather than ignore it (the caller would otherwise believe a
            filter was applied that never was).
        drop_leading_arrive_home_leg:
            drop a donor's leading "arrive home from elsewhere" leg (MiD
            ``W_SO1 == 2`` with a home purpose), which precedes the observed
            diary window (issue #366). Same rejection rule as
            ``exclude_rbw_legs`` for donors without that coding.
        closure_dwell_model:
            dwell-time model for the SYNTHESISED return-home trip that closes a
            chain not ending at home (issue #367): ``"empirical"`` draws from the
            donor diaries' observed activity durations, ``"fixed_1h"`` (default)
            keeps the constant one-hour dwell. An adapter that cannot build the
            empirical pools must reject the value rather than silently downgrade
            to the constant.
        closure_dwell_min_obs:
            minimum observations a (purpose x arrival band) cell of the empirical
            dwell model must hold before it is drawn from directly (issue #367).
            Inert for an adapter that only supports ``"fixed_1h"``, which never
            builds those pools.

        Returns
        -------
        pd.DataFrame
            One row per (synthetic person, donor trip), columns: the 11-column
            synthesis.population.trips contract + ``euclidean_distance`` + extras.
        """
        ...
