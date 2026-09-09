"""MidSource: the MiD 2023 donor-source adapter.

Wraps the existing ``braunschweig.popsim.mid`` I/O functions and
``braunschweig.popsim.assembly.map_mid_person_attributes`` under the
:class:`braunschweig.popsim.sources.base.PopsimSource` Protocol.

No survey-specific logic is duplicated here: every method is a thin delegation
to the authoritative module so the popsim_mid behaviour is preserved
byte-identically.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Tuple, Union

import pandas as pd

from braunschweig.popsim import mid as mid_mod
from braunschweig.popsim import trips_stage
from braunschweig.popsim.assembly import map_mid_person_attributes
from braunschweig.popsim.seed import MID_SEED_COLUMNS, SeedColumns
from braunschweig.popsim.stratum import cell_urban_class_from_rs7

logger = logging.getLogger(__name__)


class MidSource:
    """Donor-source adapter for MiD 2023 (Mobilitat in Deutschland).

    Delegates to the existing popsim.mid and popsim.assembly modules;
    no logic is duplicated.
    """

    name: str = "mid"

    def seed_columns(self) -> SeedColumns:
        """Return the canonical MiD 2023 seed column mapping.

        Delegates to ``braunschweig.popsim.seed.MID_SEED_COLUMNS``; the
        constant is defined there so both the seed module and this adapter
        always agree on the column names.
        """
        return MID_SEED_COLUMNS

    def load_donor(
        self, data_dir: Union[str, Path]
    ) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        """Load the MiD 2023 donor tables from data_dir.

        Parameters
        ----------
        data_dir:
            Directory containing ``MiD2023_Haushalte.csv``,
            ``MiD2023_Personen.csv``, and ``MiD2023_Wege.csv``.

        Returns
        -------
        tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]
            ``(households, persons, trips)`` where ``households`` and
            ``persons`` come from :func:`braunschweig.popsim.mid.load_mid_attributes`
            and ``trips`` comes from :func:`braunschweig.popsim.mid.load_mid_wege`.
        """
        data_dir = Path(data_dir)
        households, persons = mid_mod.load_mid_attributes(data_dir)
        trips = mid_mod.load_mid_wege(data_dir)
        logger.info(
            "[MidSource] loaded donor: %d households, %d persons, %d trips from %s",
            len(households), len(persons), len(trips), data_dir,
        )
        return households, persons, trips

    def map_person_attributes(
        self,
        persons: pd.DataFrame,
        households: pd.DataFrame,
        *,
        rng=None,
    ) -> Tuple[pd.DataFrame, pd.DataFrame]:
        """Map MiD donor attributes to the eqasim synthesis schema.

        Delegates to :func:`braunschweig.popsim.assembly.map_mid_person_attributes`,
        the same function that ``assembly.build_persons`` calls internally, so the
        two code paths are byte-identical.

        Parameters
        ----------
        persons:
            Pre-expanded persons frame (after ``expand.map_demographics`` and
            ``assembly.derive_zone_ids`` have been applied).
        households:
            MiD donor household table (from :meth:`load_donor`).
        rng:
            NumPy RandomState.  Defaults to ``np.random.RandomState(0)`` inside
            ``map_mid_person_attributes``.

        Returns
        -------
        tuple[pd.DataFrame, pd.DataFrame]
            ``(persons, pseudonym_map)`` per the unified mapper contract: the
            persons frame with all eqasim attribute columns appended (including
            ``source_person_id`` / ``source_household_id`` surrogates and
            ``weight = 1.0``), and the POPULATED pseudonym map
            (``[source_person_id, source_household_id, H_ID, P_ID]``) required
            for local-only re-linking of the pseudonymised MiD donor ids.
            Discarding the map here would silently break the re-linking file
            written by the stage (data-protection requirement).
        """
        return map_mid_person_attributes(persons, households, rng=rng)

    def donor_stratum(self, seed_households: pd.DataFrame) -> pd.Series:
        """Return the per-household stratum label for donor stratification.

        For MiD, the stratum is the raw RegioStaR-7 code (7-class: 71-77).
        This is the FINE granularity mandated by Phase 4B: cells and donors are
        matched at RS7 resolution so urban sub-types (e.g. 71 core city vs.
        74 small town hinterland) are kept distinct.

        Parameters
        ----------
        seed_households:
            MiD seed household frame.  Must carry ``RegioStaR7`` (loaded by
            :func:`braunschweig.popsim.mid.load_mid_seed`).

        Returns
        -------
        pd.Series
            Integer RS7 code per household row, same index as ``seed_households``.

        Raises
        ------
        KeyError
            If ``RegioStaR7`` is absent from ``seed_households``.
        """
        return seed_households["RegioStaR7"]

    def cell_stratum(self, cells: pd.DataFrame) -> pd.Series:
        """Return the per-100m-cell stratum label for donor stratification.

        For MiD, the stratum is ``RegioStaR7`` directly (same label space as
        :meth:`donor_stratum`), so urban cells draw MiD donors from the same
        RS7 class.

        Parameters
        ----------
        cells:
            Cells frame.  Must carry ``RegioStaR7`` (loaded by
            :func:`braunschweig.popsim.mid.load_control_cells` via
            ``_EXTRA_CELL_COLUMNS``).

        Returns
        -------
        pd.Series
            Integer RS7 code per cell row, same index as ``cells``.

        Raises
        ------
        KeyError
            If ``RegioStaR7`` is absent from ``cells``.
        """
        return cells["RegioStaR7"]

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
        w_zweck_10_as_leisure: bool = False,
        escort_passive_from_adult: bool = False,
        passive_pair_max_gap_minutes: float = 15.0,
    ) -> pd.DataFrame:
        """Build the synthesis.population.trips contract DataFrame.

        Delegates to :func:`braunschweig.popsim.trips_stage.run` unchanged;
        MidSource is a thin wrapper.

        Parameters
        ----------
        persons:
            Synthetic persons with ``person_id``, ``H_ID``, ``P_ID``.
        donor_trips:
            MiD Wege table from :meth:`load_donor`.
        random_seed:
            Integer seed for the per-person departure-time jitter RNG.
        escort_purpose:
            map MiD W_ZWECK {6, 13} to the dedicated 'escort' purpose (issue #201).
        escort_passive_education:
            map the passive escort leg (W_ZWECK 13) to 'education' instead of
            'escort' (issue #256). Requires ``escort_purpose=True``.
        explicit_round_trip_purposes:
            give the round-trip leisure W_ZWECK codes their own purposes instead
            of the 'other' catch-all (issue #241); ``False`` restores the pre-#241
            assignment for an A/B.
        exclude_rbw_legs:
            drop rbW legs (``W_RBW == 1``) before the join (issue #366).
        drop_leading_arrive_home_leg:
            drop a donor's leading "arrive home from elsewhere" leg (issue #366).
        closure_dwell_model:
            ``"empirical"`` or ``"fixed_1h"`` dwell for the synthesised chain
            closure (issue #367); any other value raises ``ValueError``.
        closure_dwell_min_obs:
            minimum observations per (purpose x arrival band) cell of the
            empirical dwell model (issue #367); inert for ``"fixed_1h"``.
        w_zweck_10_as_leisure:
            map MiD W_ZWECK 10 ("anderer Zweck") to the ``"leisure"`` purpose
            instead of ``"other"`` (issue #373, ADR-0111), following MiD's own
            hwzweck1 fold (mid2023_w_zweck_by_hwzweck1.csv).
        escort_passive_from_adult:
            give a PAIRED passive escort leg (W_ZWECK 13) the purpose derived
            from the accompanying adult's W_ZWECK (issue #372, ADR-0112); an
            unpaired one keeps the ``escort_passive_education`` relabel.
        passive_pair_max_gap_minutes:
            maximum |departure-time gap| in MINUTES for that pairing; inert
            while ``escort_passive_from_adult`` is False.

        Returns
        -------
        pd.DataFrame
            11-column synthesis.population.trips contract + ``euclidean_distance``
            + MiD extras (incl. ``is_synthetic_closure``).
        """
        return trips_stage.run(
            persons, donor_trips, random_seed=random_seed,
            escort_purpose=escort_purpose,
            escort_passive_education=escort_passive_education,
            explicit_round_trip_purposes=explicit_round_trip_purposes,
            exclude_rbw_legs=exclude_rbw_legs,
            drop_leading_arrive_home_leg=drop_leading_arrive_home_leg,
            closure_dwell_model=closure_dwell_model,
            closure_dwell_min_obs=closure_dwell_min_obs,
            w_zweck_10_as_leisure=w_zweck_10_as_leisure,
            escort_passive_from_adult=escort_passive_from_adult,
            passive_pair_max_gap_minutes=passive_pair_max_gap_minutes,
        )
