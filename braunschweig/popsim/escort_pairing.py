"""Pair MiD passive escort legs (W_ZWECK 13) with the accompanying adult's leg (issue #372, ADR-0112).

MiD code 13 ("Begleitung, passiv") is recorded for a person -- mostly a child -- who is being
taken along on someone else's trip; MiD records no companion link for this (W_BEGL_* are only
yes/no flags on the OTHER person's leg), so the accompanying adult has to be inferred. Raw MiD
B1 analysis (2026-09-09) found that 91.9 % of code-13 legs depart in the SAME minute as a
same-household adult's leg (94.8 % within 15 min; 88 % additionally share the adult's
wegkm_imp), so this module pairs each passive leg with the nearest-in-time leg of a household
member aged >= adult_min_age and reports the outcome. ``trips.map_purpose`` (Task 4, imported
LAZILY there to avoid a cycle with this module's ``trips.mid_time_seconds`` import) turns the
pairing into the child's actual purpose (the adult's destination purpose). Pure pandas; no file
I/O, no MATSim dependency.

The adult candidate pool excludes legs whose own W_ZWECK is itself PASSIVE_W_ZWECK (Ruling
C-R8): a person who is being passively escorted on a leg cannot simultaneously be the escorting
adult for that leg (they need an escort themselves), and PASSIVE_W_ZWECK is not a real
destination purpose that ``trips.map_purpose`` could resolve the child onto anyway. A household
where every code-13-eligible-age member's legs are themselves code 13 therefore has NO eligible
adult candidate at all, not a spurious self-pairing.

Tie-breaking, most to least specific (all ties are possible because MiD only resolves
departure time to the minute): (1) the smallest absolute gap in minutes between the passive
leg and the candidate adult leg; (2) among equal gaps, the candidate whose wegkm_imp matches
the passive leg's wegkm_imp exactly, since a shared trip distance is the strongest available
signal that the two legs are actually the same trip; (3) the lowest candidate P_ID; (4) the
lowest candidate W_ID. Ties (3)-(4) only matter for otherwise-indistinguishable candidates and
exist solely to make the result deterministic and reproducible.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from braunschweig.popsim.trips import mid_time_seconds

logger = logging.getLogger(__name__)
_LOG_TAG = "[escort pairing]"

# MiD Wege codeplan (W_ZWECK): 13 = "Begleitung, passiv" (see module docstring).
PASSIVE_W_ZWECK = 13
DEFAULT_MAX_GAP_MINUTES = 15.0
DEFAULT_ADULT_MIN_AGE = 18

STATUS_PAIRED = "paired"
STATUS_UNPAIRED_GAP = "unpaired_gap"
STATUS_UNPAIRED_NO_ADULT = "unpaired_no_adult"
STATUS_UNPAIRED_NO_TIME = "unpaired_no_time"

# Columns added to the Wege frame by pair_passive_legs; NaN / None on every non-code-13 row.
PAIRING_COLUMNS = (
    "passive_pair_status",
    "passive_pair_adult_w_zweck",
    "passive_pair_adult_p_id",
    "passive_pair_adult_w_id",
    "passive_pair_gap_minutes",
)

# Columns pair_passive_legs needs on the input frame; HP_ALTER carries the person's age
# (needed to decide who counts as "adult"), the rest identify the household/person/leg
# and the leg's purpose and departure time.
REQUIRED_COLUMNS = ("H_ID", "P_ID", "W_ID", "W_ZWECK", "W_SZS", "W_SZM", "HP_ALTER")

# Below this share of paired code-13 legs, the primary pairing method is almost certainly
# broken (a household join gone wrong, a time-column mismatch, ...) rather than genuinely
# unpairable data -- see CLAUDE.md "Fallback transparency": a fallback (or, here, a failure
# to pair) that fires this often must be surfaced loudly, not absorbed silently.
WARN_PAIRED_SHARE = 0.80


def pair_passive_legs(
    wege: pd.DataFrame,
    *,
    max_gap_minutes: float = DEFAULT_MAX_GAP_MINUTES,
    adult_min_age: int = DEFAULT_ADULT_MIN_AGE,
) -> tuple[pd.DataFrame, dict]:
    """Pair every W_ZWECK == 13 (passive escort) leg with the nearest same-household adult leg.

    For each passive leg, candidate adult legs are every OTHER household member's leg with a
    valid departure time, HP_ALTER >= adult_min_age, and a W_ZWECK other than PASSIVE_W_ZWECK
    (module docstring, Ruling C-R8). The candidate with the smallest departure-time gap wins;
    see the module docstring for the deterministic tie-break order. A passive leg is PAIRED
    only if a candidate exists AND its gap is <= max_gap_minutes; otherwise it is
    UNPAIRED_NO_ADULT (no eligible adult leg in the household at all), UNPAIRED_GAP (an
    eligible adult leg exists but the nearest one is farther than max_gap_minutes), or
    UNPAIRED_NO_TIME (the passive leg itself has no valid departure time, e.g. a MiD "keine
    Angabe" code). When more than one of those could apply, the reported status follows a fixed
    precedence, most to least fundamental: own-time invalidity (UNPAIRED_NO_TIME) first, then no
    eligible adult leg in the household (UNPAIRED_NO_ADULT), then the nearest one being out of
    range (UNPAIRED_GAP) -- so each leg is reported under the FIRST reason that already made
    pairing impossible, never under a later one that is merely a consequence of it. Status is
    derived from three explicit per-leg facts -- own time valid, any
    eligible adult leg in the household, any eligible adult leg within the gap -- each computed
    over EVERY passive leg rather than inferred from whether a leg happens to survive an
    intermediate join; a passive leg that is its household's only age-eligible member (a
    single-occupant household, or the sole adult also having a non-13 leg of their own) would
    otherwise have every one of its join candidates removed as a self-match and silently keep a
    default status instead of correctly reporting UNPAIRED_NO_ADULT.

    Args:
        wege: MiD Wege (trip) records, at least REQUIRED_COLUMNS; one row per leg. Not mutated.
        max_gap_minutes: Maximum |departure time gap| in minutes for a candidate to count as
            paired rather than UNPAIRED_GAP. Unit: minutes.
        adult_min_age: Minimum HP_ALTER (age in years) for a household member's leg to be
            considered a candidate escorting adult.

    Returns:
        A tuple ``(out, diagnostics)``:
        - ``out``: a copy of ``wege`` with PAIRING_COLUMNS appended (NaN / None on non-13 rows).
        - ``diagnostics``: ``{n_passive, n_paired, share_paired, n_unpaired_gap,
          n_unpaired_no_adult, n_unpaired_no_time, adult_w_zweck_counts}``, where
          ``adult_w_zweck_counts`` maps EVERY adult W_ZWECK code seen among paired legs to its
          count (no code is filtered out, including codes not otherwise handled downstream --
          see CLAUDE.md "Fallback transparency": an unexpected code must be visible, not
          silently dropped). PASSIVE_W_ZWECK never appears in this mapping because it is
          excluded from the adult candidate pool in the first place.

    Raises:
        KeyError: If ``wege`` is missing one or more REQUIRED_COLUMNS.
    """
    missing = [column for column in REQUIRED_COLUMNS if column not in wege.columns]
    if missing:
        raise KeyError(f"{_LOG_TAG} Wege frame lacks required column(s) {missing}")

    out = wege.copy()
    for column in PAIRING_COLUMNS:
        out[column] = np.nan
    out["passive_pair_status"] = out["passive_pair_status"].astype(object)

    departure_minutes = mid_time_seconds(out, "W_SZS", "W_SZM") / 60.0
    is_passive = out["W_ZWECK"] == PASSIVE_W_ZWECK
    n_passive = int(is_passive.sum())
    if n_passive == 0:
        return out, {
            "n_passive": 0,
            "n_paired": 0,
            "share_paired": float("nan"),
            "n_unpaired_gap": 0,
            "n_unpaired_no_adult": 0,
            "n_unpaired_no_time": 0,
            "adult_w_zweck_counts": {},
        }

    # wegkm_imp is used only for the distance tie-break (module docstring); it is not a
    # required column because the pairing must still work on frames that lack it.
    distance_km = out["wegkm_imp"] if "wegkm_imp" in out.columns else pd.Series(np.nan, index=out.index)

    passive_legs = pd.DataFrame({
        "row_index": out.index[is_passive],
        "H_ID": out.loc[is_passive, "H_ID"].values,
        "P_ID": out.loc[is_passive, "P_ID"].values,
        "departure_minutes": departure_minutes[is_passive].values,
        "distance_km": distance_km[is_passive].values,
    })

    # Adult candidate pool: household members old enough to count as an escorting adult, on a
    # leg that is NOT itself PASSIVE_W_ZWECK -- see the module docstring and Ruling C-R8.
    is_adult = (pd.to_numeric(out["HP_ALTER"], errors="coerce") >= adult_min_age) & (out["W_ZWECK"] != PASSIVE_W_ZWECK)
    adult_legs = pd.DataFrame({
        "H_ID": out.loc[is_adult, "H_ID"].values,
        "adult_P_ID": out.loc[is_adult, "P_ID"].values,
        "adult_W_ID": out.loc[is_adult, "W_ID"].values,
        "adult_W_ZWECK": out.loc[is_adult, "W_ZWECK"].values,
        "adult_departure_minutes": departure_minutes[is_adult].values,
        "adult_distance_km": distance_km[is_adult].values,
    })
    # An adult leg with no valid departure time cannot be matched by time and would otherwise
    # produce a spurious NaN-gap candidate; drop it rather than let it silently win a tie.
    adult_legs = adult_legs[adult_legs["adult_departure_minutes"].notna()]

    has_own_time = passive_legs["departure_minutes"].notna()
    candidates = passive_legs[has_own_time].merge(adult_legs, on="H_ID", how="left")
    # A person cannot escort themselves: drop candidate rows that are the passive person's own
    # OTHER legs (relevant only if that same person also satisfies the adult-age threshold).
    # This must NOT be the only mechanism that decides "no eligible adult in the household": if
    # EVERY merged row for a passive leg happens to be a self-match (e.g. a single-occupant
    # household, or a lone household adult who also has a non-13 leg of their own), filtering
    # those rows out would empty the group entirely, and relying on group survival to signal
    # "no adult" would leave such a leg silently stuck on whatever status was set beforehand
    # (this was a Critical bug: the leg kept the initial UNPAIRED_NO_TIME default even though
    # its own departure time was valid). The three facts computed below therefore reindex over
    # EVERY row of passive_legs instead of relying on which rows survive this filter.
    is_self_match = (candidates["adult_P_ID"] == candidates["P_ID"]) & candidates["adult_P_ID"].notna()
    eligible = candidates[~is_self_match & candidates["adult_P_ID"].notna()].copy()
    eligible["gap_minutes"] = (eligible["departure_minutes"] - eligible["adult_departure_minutes"]).abs()
    eligible["distance_mismatch"] = (eligible["distance_km"] != eligible["adult_distance_km"]).astype(int)

    best_candidate = (
        eligible.sort_values(
            ["row_index", "gap_minutes", "distance_mismatch", "adult_P_ID", "adult_W_ID"],
            na_position="last",
        )
        .drop_duplicates("row_index")
        .set_index("row_index")
    )

    # Fact 1: does the passive leg itself have a valid departure time?
    own_time_valid = pd.Series(False, index=passive_legs["row_index"])
    own_time_valid.loc[passive_legs.loc[has_own_time, "row_index"]] = True
    # Fact 2: does at least one eligible (non-self, non-passive-purpose) adult leg exist in the
    # household at all, regardless of gap? True exactly for the row_index values that produced
    # at least one row in `eligible` -- an existence check, not a "did a row survive" heuristic.
    has_eligible_adult = pd.Series(False, index=passive_legs["row_index"])
    has_eligible_adult.loc[best_candidate.index] = True

    status = pd.Series(STATUS_UNPAIRED_NO_TIME, index=passive_legs["row_index"], dtype=object)
    status.loc[own_time_valid & ~has_eligible_adult] = STATUS_UNPAIRED_NO_ADULT
    status.loc[best_candidate.index[best_candidate["gap_minutes"] > max_gap_minutes]] = STATUS_UNPAIRED_GAP
    status.loc[best_candidate.index[best_candidate["gap_minutes"] <= max_gap_minutes]] = STATUS_PAIRED
    out.loc[status.index, "passive_pair_status"] = status.values

    paired = best_candidate[best_candidate["gap_minutes"] <= max_gap_minutes]
    out.loc[paired.index, "passive_pair_adult_w_zweck"] = paired["adult_W_ZWECK"].values
    out.loc[paired.index, "passive_pair_adult_p_id"] = paired["adult_P_ID"].values
    out.loc[paired.index, "passive_pair_adult_w_id"] = paired["adult_W_ID"].values
    out.loc[paired.index, "passive_pair_gap_minutes"] = paired["gap_minutes"].values

    status_counts = status.value_counts().to_dict()
    n_paired = int(status_counts.get(STATUS_PAIRED, 0))
    share_paired = n_paired / n_passive
    # Every adult W_ZWECK code seen among the paired legs is counted, unfiltered (see the
    # diagnostics docstring above and CLAUDE.md "Fallback transparency").
    adult_w_zweck_counts = {int(code): int(count) for code, count in paired["adult_W_ZWECK"].value_counts().sort_index().items()}

    diagnostics = {
        "n_passive": n_passive,
        "n_paired": n_paired,
        "share_paired": float(share_paired),
        "n_unpaired_gap": int(status_counts.get(STATUS_UNPAIRED_GAP, 0)),
        "n_unpaired_no_adult": int(status_counts.get(STATUS_UNPAIRED_NO_ADULT, 0)),
        "n_unpaired_no_time": int(status_counts.get(STATUS_UNPAIRED_NO_TIME, 0)),
        "adult_w_zweck_counts": adult_w_zweck_counts,
    }

    logger.info(
        "%s %d/%d passive legs paired (%.1f%%, gap <= %.0f min); unpaired: no adult leg %d, "
        "gap too large %d, no time %d; adult W_ZWECK among the pairs %s",
        _LOG_TAG, n_paired, n_passive, 100.0 * share_paired, max_gap_minutes,
        diagnostics["n_unpaired_no_adult"], diagnostics["n_unpaired_gap"], diagnostics["n_unpaired_no_time"],
        adult_w_zweck_counts,
    )
    if share_paired < WARN_PAIRED_SHARE:
        logger.warning(
            "%s only %d/%d (%.1f%%) passive legs could be paired with an adult leg (raw MiD B1 "
            "reference: 94.8%% within 15 min, see the module docstring); check HP_ALTER, the "
            "W_SZS/W_SZM time columns and the H_ID household join before trusting the "
            "passive-leg purposes downstream",
            _LOG_TAG, n_paired, n_passive, 100.0 * share_paired,
        )

    return out, diagnostics
