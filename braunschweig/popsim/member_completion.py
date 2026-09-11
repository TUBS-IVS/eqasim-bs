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

from braunschweig.popsim.sampling import weighted_choice
# ONE owner for the donor-matching age bands: weekend_plan_match defines both edge
# sets and the banding helper, and this module's mirror role matching bands ages for
# exactly the same purpose (issue #386). A second copy here is what let the two drift
# apart in the first place. No import cycle: weekend_plan_match does not import this
# module (completed_donor imports both).
from braunschweig.popsim.weekend_plan_match import (
    AGE_BAND_EDGES, FINE_CHILD_AGE_BAND_EDGES, age_band_index,
)

logger = logging.getLogger(__name__)

# Relaxation hierarchy AFTER the hard equal-size (H_GR) condition: each key
# narrows the mirror candidate pool only while candidates remain.
MIRROR_MATCH_KEYS = ("hhgr_gr", "oek_status", "RegioStaR7")


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


def _select_mirror(
    incomplete_row: pd.Series,
    candidates: pd.DataFrame,
    *,
    household_id: str,
    rng: np.random.RandomState,
):
    """Pick one mirror household id from the complete equal-size candidates.

    Progressively narrows the pool by each key in :data:`MIRROR_MATCH_KEYS`
    (only while at least one candidate remains), then draws one mirror
    **proportional to its ``H_GEW`` survey weight** via
    :func:`braunschweig.popsim.sampling.weighted_choice` (deterministic given the
    RNG state; a uniform fallback fires only if all candidate weights are invalid).
    """
    pool = candidates
    for key in MIRROR_MATCH_KEYS:
        if key not in pool.columns:
            continue
        narrowed = pool[pool[key] == incomplete_row[key]]
        if len(narrowed) > 0:
            pool = narrowed
    ids = sorted(pool[household_id].tolist())
    weights = pool.set_index(household_id)["H_GEW"].reindex(ids).to_numpy()
    return weighted_choice(ids, weights, rng=rng)


def _match_present_members(
    present: pd.DataFrame, mirror_members: pd.DataFrame, *,
    age_band_edges=AGE_BAND_EDGES,
) -> list:
    """Greedily match present host members to mirror members by (age band, sex).

    Returns the positional indices (into ``mirror_members``) of the mirror
    members that were matched (i.e. that correspond to an already-present host
    member). Matching prefers an exact (age band, sex) match, falls back
    to the age band only, then to any unused mirror member.

    The mirror members this does NOT mark are the ones copied in as fillers, so the
    bands decide which surplus member the host receives. Under the coarse
    :data:`AGE_BAND_EDGES` a present 7-year-old can consume the mirror's 13-year-old
    slot, and the host then receives a SECOND 7-year-old instead of the 13-year-old
    sibling the mirror household actually has -- the assumption stated in the module
    docstring says the latter (issue #386). Pass
    :data:`FINE_CHILD_AGE_BAND_EDGES` to separate 6-9 from 10-13.

    Consumes no rng, and marks ``min(len(present), len(mirror_members))`` members under
    ANY edge set, so the filler COUNT is unchanged and the caller's seeded mirror draw
    stays in lockstep.
    """
    mirror_bands = age_band_index(mirror_members["HP_ALTER"], edges=age_band_edges)
    mirror_sex = mirror_members["HP_SEX"].to_numpy()
    used = np.zeros(len(mirror_members), dtype=bool)

    present_bands = age_band_index(present["HP_ALTER"], edges=age_band_edges)
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
    fine_child_age_bands: bool = True,
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
            randomness; the mirror draw is the only stochastic step, and it is
            drawn proportional to the candidate ``H_GEW`` survey weight).
        household_id: Household id column name (default ``H_ID``).
        size_col: Declared household size column name (default ``H_GR``).
        kernwo_col: Column carrying the MiD core-week reporting-day code
            (default ``kernwo``). When present, mirror selection is
            constrained to the same day type (weekday/weekend) as the
            incomplete host. If absent or if the data contains only one
            day type, the behaviour is identical to the legacy path.
        fine_child_age_bands: When True (default, issue #386), band 6-13-year-olds
            finely (6-9 / 10-13) in the mirror role matching, so a present
            primary-school child does not consume the mirror's secondary-school slot
            and leave the host with a duplicate of itself. Changes WHICH mirror member
            is copied, never how many -- the seeded mirror draw is unaffected.

    Returns:
        ``(households, persons_filled, MemberCompletionReport)``. The household
        frame is returned unchanged; the person frame gains the total columns
        ``member_imputed``, ``source_H_ID``, ``source_P_ID`` and one appended
        row per filled member.
    """
    from braunschweig.popsim import day_type as _dt

    age_band_edges = FINE_CHILD_AGE_BAND_EDGES if fine_child_age_bands else AGE_BAND_EDGES
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

    # Pre-group the complete-household candidate pool ONCE, instead of scanning the
    # full `complete` frame on every incomplete host (which was O(n_incomplete x
    # n_complete) -- the dominant cost of this stage). The grouped subframes
    # preserve `complete`'s row order, so the candidate set handed to
    # _select_mirror is identical to the per-iteration boolean-mask version and the
    # seeded weighted draw is BYTE-IDENTICAL. Two lookup tables:
    #   complete_by_size            -> legacy/no-day-type path (size only)
    #   complete_by_size_daytype    -> kernwo-aware path (size AND host day type)
    complete_by_size = {s: g for s, g in complete.groupby(size_col, sort=False)}
    complete_by_size_daytype: dict = {}
    if hh_dt is not None:
        complete_daytype = complete[household_id].map(hh_dt)
        complete_by_size_daytype = {
            key: g
            for key, g in complete.groupby([complete[size_col], complete_daytype], sort=False)
        }

    filler_frames: list = []
    n_filled = 0
    n_added = 0
    n_unfillable = 0

    incomplete_sorted = incomplete.sort_values(household_id)
    n_incomplete_total = len(incomplete_sorted)
    progress_step = max(1, n_incomplete_total // 10)  # ~10 heartbeats over the loop

    # Deterministic iteration order: sorted by household id.
    for loop_index, (_, row) in enumerate(incomplete_sorted.iterrows()):
        host_id = row[household_id]
        # Day-type filter: when active, restrict mirrors to the same day type
        # as the host household (hard filter, same priority as equal size).
        # Guard: only apply when BOTH hh_dt is available AND the host has a
        # determinable day type (host_dt is not None).  A host with zero
        # present persons is absent from hh_dt; filtering on None would empty
        # the candidate set and silently make the household unfillable -- a
        # regression vs. the pre-Option-B behaviour.  When host_dt is None we
        # fall back to the un-day-constrained candidate set (legacy path).
        host_dt = hh_dt.get(host_id) if hh_dt is not None else None
        if host_dt is not None:
            candidates = complete_by_size_daytype.get((row[size_col], host_dt))
        else:
            candidates = complete_by_size.get(row[size_col])
        if candidates is None or len(candidates) == 0:
            n_unfillable += 1
            continue
        # Plain-text progress heartbeat (the live progress bar only renders on a
        # TTY; this keeps a non-TTY file log from looking frozen during the loop).
        if loop_index % progress_step == 0 and loop_index > 0:
            logger.info(
                "[popsim.member_completion] progress %d/%d incomplete households (%.0f%%)",
                loop_index, n_incomplete_total, 100.0 * loop_index / n_incomplete_total,
            )

        mirror_id = _select_mirror(
            row, candidates, household_id=household_id, rng=rng
        )
        mirror_members = persons_by_household[mirror_id].reset_index(drop=True)
        present = persons_by_household.get(
            host_id, persons.iloc[0:0]
        ).reset_index(drop=True)

        n_missing = int(row[size_col]) - len(present)
        matched_positions = _match_present_members(
            present, mirror_members, age_band_edges=age_band_edges)
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
