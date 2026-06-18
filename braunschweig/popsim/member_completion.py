"""Mirror-household member completion for the MiD PopulationSim seed (D3).

Purpose
-------
In the raw MiD 2023 delivery, 16.9 % of the seed households (after the weekday
``kernwo`` filter) carry FEWER person rows than their declared household size
``H_GR``: some members never completed an interview (up to 6 missing rows per
household; a household never has MORE rows than ``H_GR``). Expanding these
donors as-is yields systematically undersized synthetic households. Per the
user-approved decision D3 the affected households are NOT dropped; the missing
members are FILLED by copying person rows from a structurally similar COMPLETE
household ("mirror household").

ASSUMPTION (explicit, scientific)
---------------------------------
The missing members of an incomplete household are assumed to resemble the
surplus members of a structurally similar complete household, matched on
``H_GR`` (hard, equal size) and then preferentially on ``hhgr_gr`` ->
``oek_status`` -> ``RegioStaR7`` (each key narrows the candidate pool only
while candidates remain -- the match is relaxed in reverse order, never to an
empty pool). The intra-household correlation of the transplanted members is
therefore approximate: the filler is a real MiD person, but from a different
(structurally similar) household.

Filler attribute semantics
--------------------------
A filler person row is copied VERBATIM from the mirror person, including any
additional per-person columns present in the frame (e.g. ``P_GEW``,
``kernwo``): the filler inherits the source person's weight and reporting day.
This is acceptable because the HOST household already passed the weekday day
filter and person weights enter PopulationSim per person.

Traceability contract (trips join)
----------------------------------
A filler receives a FRESH ``P_ID`` within its host household (host max + 1,
+2, ...). That (``H_ID``, ``P_ID``) pair does not exist in the MiD Wege file,
so the trips join would silently yield no trips for the filler. Therefore ALL
person rows carry total traceability columns:

- ``source_H_ID`` / ``source_P_ID``: equal to ``H_ID`` / ``P_ID`` for regular
  persons; equal to the MIRROR household's / person's ids for fillers. The
  downstream trips join must use these source ids so the filler brings along
  the real MiD Wege of its source person.
- ``member_imputed``: True for fillers, False otherwise.

Fallback transparency
---------------------
The fill rate (households filled / incomplete / unfillable) is logged via
``logger.info``; incomplete households that could NOT be filled (no complete
mirror of equal size exists) are logged via ``logger.warning`` (CLAUDE.md
mandatory no-silent-fallback rule).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Relaxation hierarchy AFTER the hard equal-size (H_GR) condition: each key
# narrows the mirror candidate pool only while candidates remain.
MIRROR_MATCH_KEYS = ("hhgr_gr", "oek_status", "RegioStaR7")

# Coarse age-band edges for the role matching of present members against the
# mirror household's members (pd.cut bins; ages in completed years).
AGE_BAND_EDGES = (-1, 5, 13, 17, 200)


@dataclass(frozen=True)
class MemberCompletionReport:
    """Outcome of the mirror-household member completion.

    Attributes:
        n_households_total: Households present in the input frame.
        n_households_incomplete: Households with fewer person rows than their
            declared size (candidates for filling).
        n_households_filled: Incomplete households that received fillers.
        n_persons_added: Filler person rows appended in total.
    """

    n_households_total: int
    n_households_incomplete: int
    n_households_filled: int
    n_persons_added: int


def _age_band(ages: pd.Series) -> pd.Series:
    """Map ages (completed years) to coarse band labels for role matching."""
    return pd.cut(ages, bins=list(AGE_BAND_EDGES), labels=False)


def _select_mirror(
    incomplete_row: pd.Series,
    candidates: pd.DataFrame,
    *,
    household_id: str,
    rng: np.random.RandomState,
):
    """Pick one mirror household id from the complete equal-size candidates.

    Progressively narrows the pool by each key in :data:`MIRROR_MATCH_KEYS`
    (only while at least one candidate remains), then draws uniformly with the
    supplied seeded RNG from the id-sorted final pool (deterministic given the
    RNG state).
    """
    pool = candidates
    for key in MIRROR_MATCH_KEYS:
        if key not in pool.columns:
            continue
        narrowed = pool[pool[key] == incomplete_row[key]]
        if len(narrowed) > 0:
            pool = narrowed
    ids = sorted(pool[household_id].tolist())
    return ids[int(rng.randint(len(ids)))]


def _match_present_members(
    present: pd.DataFrame, mirror_members: pd.DataFrame
) -> list:
    """Greedily match present host members to mirror members by (age band, sex).

    Returns the positional indices (into ``mirror_members``) of the mirror
    members that were matched (i.e. that correspond to an already-present host
    member). Matching prefers an exact (coarse age band, sex) match, falls back
    to the age band only, then to any unused mirror member.
    """
    mirror_bands = _age_band(mirror_members["HP_ALTER"]).to_numpy()
    mirror_sex = mirror_members["HP_SEX"].to_numpy()
    used = np.zeros(len(mirror_members), dtype=bool)

    present_bands = _age_band(present["HP_ALTER"]).to_numpy()
    present_sex = present["HP_SEX"].to_numpy()

    for band, sex in zip(present_bands, present_sex):
        exact = np.flatnonzero(~used & (mirror_bands == band) & (mirror_sex == sex))
        band_only = np.flatnonzero(~used & (mirror_bands == band))
        any_free = np.flatnonzero(~used)
        for pool in (exact, band_only, any_free):
            if len(pool) > 0:
                used[pool[0]] = True
                break
    return list(np.flatnonzero(used))


def complete_members(
    households: pd.DataFrame,
    persons: pd.DataFrame,
    *,
    rng: np.random.RandomState,
    household_id: str = "H_ID",
    size_col: str = "H_GR",
    kernwo_col: str = "kernwo",
) -> Tuple[pd.DataFrame, pd.DataFrame, MemberCompletionReport]:
    """Fill member-incomplete households by mirror-household sampling.

    See the module docstring for the scientific assumption, the relaxation
    hierarchy, and the traceability contract.

    Args:
        households: Household frame carrying ``household_id``, ``size_col`` and
            (optionally) the :data:`MIRROR_MATCH_KEYS` columns.
        persons: Person frame carrying ``household_id``, ``P_ID``,
            ``HP_ALTER``, ``HP_SEX`` (plus arbitrary extra columns that are
            copied verbatim onto fillers).
        rng: Seeded :class:`numpy.random.RandomState` (mandatory seeded
            randomness; the mirror draw is the only stochastic step).
        household_id: Household id column name (default ``H_ID``).
        size_col: Declared household size column name (default ``H_GR``).
        kernwo_col: Column carrying the MiD core-week reporting-day code
            (default ``kernwo``). When present, mirror selection is
            constrained to the same day type (weekday/weekend) as the
            incomplete host. If absent or if the data contains only one
            day type, the behaviour is identical to the legacy path.

    Returns:
        ``(households, persons_filled, MemberCompletionReport)``. The household
        frame is returned unchanged; the person frame gains the total columns
        ``member_imputed``, ``source_H_ID``, ``source_P_ID`` and one appended
        row per filled member.
    """
    from braunschweig.popsim import day_type as _dt

    persons = persons.copy()
    persons["member_imputed"] = False
    persons["source_H_ID"] = persons[household_id]
    persons["source_P_ID"] = persons["P_ID"]

    # --- Day-type awareness (day-type-aware mirror selection) ---
    # When kernwo is present, compute per-household day_type so that weekday
    # hosts only receive weekday mirrors and vice versa.  When kernwo is absent
    # or all households share one day type, hh_dt is None and the candidate
    # set is IDENTICAL to the legacy path (byte-identical draw).
    hh_dt = None
    if kernwo_col in persons.columns:
        member_dt = _dt.person_day_type(persons[kernwo_col])
        hh_dt_raw = member_dt.groupby(persons[household_id]).agg(
            lambda s: s.mode().iat[0]
        )
        if hh_dt_raw.nunique() <= 1:
            hh_dt = None  # single day type -> filter is a NO-OP, keep legacy path
        else:
            hh_dt = hh_dt_raw
            logger.info(
                "[popsim.member_completion] kernwo-aware mode active: "
                "mirror selection is constrained to the same day type "
                "(weekday/weekend) as each incomplete host household."
            )

    person_counts = persons.groupby(household_id).size()
    counts = households[household_id].map(person_counts).fillna(0).astype(int)
    declared = households[size_col].astype(int)

    incomplete_mask = counts < declared
    complete_mask = (counts >= declared) & (counts > 0)

    incomplete = households[incomplete_mask]
    complete = households[complete_mask]

    persons_by_household = dict(tuple(persons.groupby(household_id, sort=False)))

    filler_frames: list = []
    n_filled = 0
    n_added = 0
    n_unfillable = 0

    # Deterministic iteration order: sorted by household id.
    for _, row in incomplete.sort_values(household_id).iterrows():
        host_id = row[household_id]
        candidates = complete[complete[size_col] == row[size_col]]
        # Day-type filter: when active, restrict mirrors to the same day type
        # as the host household (hard filter, same priority as equal size).
        if hh_dt is not None:
            host_dt = hh_dt.get(host_id)
            candidates = candidates[candidates[household_id].map(hh_dt) == host_dt]
        if len(candidates) == 0:
            n_unfillable += 1
            continue

        mirror_id = _select_mirror(
            row, candidates, household_id=household_id, rng=rng
        )
        mirror_members = persons_by_household[mirror_id].reset_index(drop=True)
        present = persons_by_household.get(
            host_id, persons.iloc[0:0]
        ).reset_index(drop=True)

        n_missing = int(row[size_col]) - len(present)
        matched_positions = _match_present_members(present, mirror_members)
        unmatched = mirror_members.drop(index=matched_positions)
        fillers = unmatched.head(n_missing).copy()

        # Traceability FIRST (source = the mirror ids), then re-home the row.
        fillers["source_H_ID"] = fillers[household_id]
        fillers["source_P_ID"] = fillers["P_ID"]
        fillers["member_imputed"] = True
        fillers[household_id] = host_id
        max_p_id = int(present["P_ID"].max()) if len(present) > 0 else 0
        fillers["P_ID"] = range(max_p_id + 1, max_p_id + 1 + len(fillers))

        filler_frames.append(fillers)
        n_filled += 1
        n_added += len(fillers)

    if filler_frames:
        persons = pd.concat([persons, *filler_frames], ignore_index=True)

    n_total = len(households)
    n_incomplete = int(incomplete_mask.sum())
    report = MemberCompletionReport(
        n_households_total=n_total,
        n_households_incomplete=n_incomplete,
        n_households_filled=n_filled,
        n_persons_added=n_added,
    )

    logger.info(
        "[popsim.member_completion] %d/%d households incomplete (%.1f%%); "
        "filled %d (%.1f%% of incomplete) by mirror sampling, adding %d persons.",
        n_incomplete, n_total,
        100.0 * n_incomplete / n_total if n_total else 0.0,
        n_filled,
        100.0 * n_filled / n_incomplete if n_incomplete else 0.0,
        n_added,
    )
    if n_unfillable > 0:
        logger.warning(
            "[popsim.member_completion] %d incomplete household(s) could NOT be "
            "filled (no complete mirror household of equal %s exists); they "
            "remain undersized in the seed.",
            n_unfillable, size_col,
        )

    return households, persons, report
