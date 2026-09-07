"""Participation-control seed derivation for the popsim mid stage.

- ``derive_trip_class_seed``          -- ``trip_class`` seed from the realised weekday plan
- ``compute_has_purpose_trip``        -- per-person has-a-<purpose>-trip flag from MiD Wege
- ``compute_has_work_trip``           -- thin ``compute_has_purpose_trip`` wrapper (purpose="work")
- ``derive_participation_seed``       -- ``<purpose>_participation`` seed from the realised plan
- ``derive_work_participation_seed``  -- thin ``derive_participation_seed`` wrapper (purpose="work")

Extracted verbatim from the stage module (``__init__``); see the package
docstring for the stage-level context.

``PARTICIPATION_W_ZWECK`` moved here alongside ``compute_has_purpose_trip``
(its only consumer in the original module) rather than staying in
``__init__.py``: submodules must not import from the package ``__init__``
(#267 split constraint), so a constant used exclusively by moved functions
travels with them. The ``braunschweig.popsim.trips`` import (aliased
``trips``, needed for ``trips.PURPOSE_BY_W_ZWECK``) moves here for the same
reason -- it had no other consumer left in ``__init__.py``. Both are
re-exported from ``__init__.py`` so the public namespace is unchanged.
"""

from __future__ import annotations

import logging

import pandas as pd

from braunschweig.popsim import attributes
from braunschweig.popsim import trips
# The 803/804 diary non-response codes are DECLARED by the diary plan match (the module
# that decides which of them are remapped and which are kept); the seed guard below must
# use that single declaration rather than repeating the literals here.
from braunschweig.popsim.diary_plan_match import NO_DIARY_CODES

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Participation
# --------------------------------------------------------------------------- #

def derive_trip_class_seed(persons, *, rng, household_id="H_ID", person_id="P_ID",
                            counts_closure: bool = False, forbid_no_diary_sources: bool = False,
                            drop_leading_arrive_home_leg: bool = False):
    """Derive the ``trip_class`` control seed from each person's REALISED weekday plan.

    A synthetic person executes the MiD plan identified by ``(source_H_ID, source_P_ID)``.
    After ``weekend_plan_match`` every plan source resolves to a weekday (kernwo 1-3)
    donor (its A2 sweep guarantees no person sources a weekend diary), so that source's
    diary trip count (``anzwege1``) is BOTH the weekday universe the SrV Di-Do target
    measures AND the trips the synthetic person actually realises. The control must
    therefore be seeded from the SOURCE's ``anzwege1``, not the person's own
    reporting-day ``anzwege1``.

    Rationale (audit 2026-07-09): the default pipeline keeps ALL reporting days in the
    donor (``weekend_plan_match`` forces ``ALL_REPORTING_KERNWO``), so ~29% of donor
    persons are weekend reporters whose OWN ``anzwege1`` is a Saturday/Sunday count
    (measured ~2pp more immobile than weekday). Seeding ``trip_class`` from their own
    weekend count fit a weekday-anchored control to the wrong universe AND steered on a
    variable the person never realises (their plan is a remapped weekday donor). Sourcing
    the plan's ``anzwege1`` removes both mismatches.

    When the plan-source columns are absent (no member completion / weekend match ran),
    the seed is already weekday-filtered, so ``trip_class`` is derived from the person's
    own ``anzwege1``; the path taken is logged (no silent fallback). The 803/804 diary
    non-response codes on the resolved trip count are imputed within the PERSON's own
    ``alter_gr1`` age band, exactly as before.

    Args:
        counts_closure: when True, count the CLOSED day (plan-structure-fix Task 7,
            issue #367): a plan source whose diary does not end at home
            (``src_ends_at_home == False``, attached by
            ``diary_facts.attach_plan_source_facts``, Task 3) but does carry at
            least one direct leg (``src_n_direct_legs > 0``) gets its resolved
            ``anzwege1`` incremented by one BEFORE classing -- the synthetic
            return-home trip the completed-donor plan will carry after trip-chain
            closure (spec 2026-09-05-plan-structure-fix-design.md). The 803/804
            diary non-response codes are left untouched (still imputed as before);
            a source with zero direct legs (e.g. an unresolved 803/804 code) never
            qualifies. The incremented count is clipped at 50 (controller ruling
            R11): ``attributes.map_trip_class``'s ``value_map`` only enumerates
            ``anzwege1`` 0..50 (the MiD codebook's valid range), so an
            ``anzwege1 == 50`` source with an open-ended diary would otherwise
            become 51, an unenumerated code ``missing.resolve`` raises on; 51
            would land in the same 5+ class as 50 under the class scheme, so the
            clip changes no classification. Requires the ``src_ends_at_home`` /
            ``src_n_direct_legs`` columns on ``persons`` (raises ``KeyError``
            naming the missing column otherwise -- no silent no-op). Default
            False (byte-identical to the pre-Task-7 behaviour).
        forbid_no_diary_sources: when True, raise ``ValueError`` if any resolved
            plan source is one ``diary_plan_match`` PROMISES to have remapped
            (issue #365 guard): ``anzwege1 == 804`` ("Mobilitaet unbekannt"), or
            ``anzwege1 == 803`` ("Person ohne Wegeerfassung") on a source whose
            MiD ``mobil == 1``. A surviving such source means the remap did not
            actually run (a flag-wiring defect) -- this must fail loudly rather
            than silently impute a code that should not exist.

            A source with ``anzwege1 == 803`` and ``mobil != 1`` is NOT an error:
            ``diary_plan_match._own_diary_reason`` deliberately KEEPS it
            (``REASON_KEEP_IMMOBILE``), because "not mobile, no diary" IS the
            observed day -- zero trips is what the person reported (design spec
            2026-09-05, section 2.1 table; controller ruling R18). Raising on it
            aborted ``popsim.stage`` on real data although the pipeline was
            behaving exactly as designed. Such a source is seeded as **0 trips**
            instead of being age-band imputed: its plan is empty by construction,
            so imputing a mobile count would ask the control for trips the plan
            cannot contain. (The imputation policy is unchanged for a person's OWN
            row and on the guard-off path, where 803 IS item non-response.)

            The source's ``mobil`` is resolved through the SAME
            ``(source_H_ID, source_P_ID)`` lookup as its ``anzwege1``, so the
            guard reads the donor row the plan is actually realised from, not the
            receiving person's own mobility. ``mobil`` is an OPTIONAL MiD person
            column (``mid.donor.MID_PERSON_OPTIONAL_COLS``); when the guard is on
            and it is absent, a ``KeyError`` naming it is raised rather than
            evaluating a weaker guard silently (``diary_plan_match`` requires the
            same column, so the guard being on implies it was loaded).

            Default False (no guard; the code is imputed as before, matching the
            legacy / diary-match-off behaviour).
        drop_leading_arrive_home_leg: when True AND ``counts_closure`` is True,
            SUBTRACT one from the resolved ``anzwege1`` of every plan source
            whose diary starts by ARRIVING home (``src_starts_arriving_home``,
            MiD ``W_SO1 == 2``): ``trips.expand_persons_to_trips`` drops that
            leading leg from the realised plan under the same flag (ADR-0108), so
            counting it in the seed would make the control and the plan describe
            different days again -- exactly the mismatch ``counts_closure`` exists
            to remove (controller ruling R20). Floored at 0, never applied to a
            803/804 code, and requires ``src_starts_arriving_home`` (raises
            ``KeyError`` naming it -- no silent no-op). Ignored when
            ``counts_closure`` is False, where the seed reproduces the plain
            ``anzwege1`` by contract. Default False.

            Two residual seed-vs-plan deviations remain BY DECISION and are
            recorded in ADR-0108: a person the closure EXCLUDES (NaN plan time /
            over-bound chain) still gets the +1 although no return trip is
            appended before the resample, and with ``exclude_rbw_legs`` OFF the
            last DIRECT leg (which ``src_ends_at_home`` describes) need not be
            the realised last purpose.
    """
    has_source = "source_H_ID" in persons.columns and "source_P_ID" in persons.columns
    if not has_source:
        if counts_closure:
            logger.info(
                "[popsim.mid] trip_class seed: counts_closure requested but ignored -- no "
                "plan-source columns are present, so the seed is already derived from the "
                "person's own (already weekday-filtered) anzwege1.")
        logger.info(
            "[popsim.mid] trip_class seed: derived from each person's own anzwege1 "
            "(no plan-source columns present -> seed is weekday-filtered).")
        return attributes.map_trip_class(persons, rng=rng)

    # Real (non-imputed) donor persons are the only valid plan sources; a filler's source
    # points at its mirror donor, which is one of these real persons.
    real = persons[~persons["member_imputed"].astype(bool)] if "member_imputed" in persons.columns else persons
    source_anzwege1 = real.set_index([household_id, person_id])["anzwege1"]
    src_idx = pd.MultiIndex.from_arrays([persons["source_H_ID"], persons["source_P_ID"]])
    mapped = pd.Series(source_anzwege1.reindex(src_idx).to_numpy(), index=persons.index)
    n_unresolved = int(mapped.isna().sum())
    if n_unresolved:
        # Defense-in-depth (no silent fallback): a plan source MUST resolve to a real
        # donor person; a NaN means upstream donor/source corruption (mirrors the
        # weekend_plan_match A2-sweep guard). Fail loudly rather than seed a NaN class.
        raise ValueError(
            f"derive_trip_class_seed: {n_unresolved} person(s) have a plan source "
            f"(source_H_ID, source_P_ID) absent from the donor frame; cannot derive "
            "trip_class from the realised plan (upstream donor/source corruption).")

    codes = mapped.isin(NO_DIARY_CODES)
    if forbid_no_diary_sources:
        # No silent fallback (issue #365): with diary_plan_match on, the completed_donor
        # build must have already remapped every plan source its own classification calls
        # non-realisable -- a surviving one here means that remap did not run. The guard
        # must mirror that classification EXACTLY (controller ruling R18): 803 with
        # mobil != 1 is KEPT by diary_plan_match by design (REASON_KEEP_IMMOBILE, "zero
        # trips is the observed day"), so raising on it would abort a correctly behaving
        # pipeline. mobil is resolved through the plan-source keys, exactly like anzwege1.
        if "mobil" not in real.columns:
            raise KeyError(
                "derive_trip_class_seed(forbid_no_diary_sources=True) requires the MiD person "
                "column 'mobil' on the donor frame to distinguish a 803 source diary_plan_match "
                "KEEPS (mobil != 1, immobile by design) from one it promises to remap "
                f"(mobil == 1); absent from the donor frame (has {list(real.columns)}).")
        source_mobil = real.set_index([household_id, person_id])["mobil"]
        mapped_mobil = pd.Series(source_mobil.reindex(src_idx).to_numpy(), index=persons.index)
        anzwege1_not_collected, anzwege1_unknown = NO_DIARY_CODES  # 803, 804
        must_have_been_remapped = (
            (mapped == anzwege1_unknown)
            | ((mapped == anzwege1_not_collected) & (mapped_mobil == 1))
        )
        n_offending = int(must_have_been_remapped.sum())
        kept_immobile = (mapped == anzwege1_not_collected) & (mapped_mobil != 1)
        n_kept_immobile = int(kept_immobile.sum())
        # A kept-immobile source has NO diary and MiD says the person was not mobile, so
        # the plan it produces is empty: the person makes zero trips. Seeding the age-band
        # IMPUTATION of the 803 code (the policy for a person's OWN row, where the code is
        # item non-response) would make the control ask for trips the plan cannot contain
        # -- the same seed-vs-plan mismatch trip_class_seed_counts_closure exists to close.
        # Under this guard the code is therefore RESOLVED to 0 trips rather than imputed.
        # Only reachable when diary_plan_match is on (the guard's own precondition); the
        # OFF path keeps the imputation unchanged.
        mapped = mapped.where(~kept_immobile, 0)
        codes = mapped.isin(NO_DIARY_CODES)
        logger.info(
            "[popsim.mid] trip_class seed no-diary guard: %d/%d (%.2f%%) plan sources are 803 "
            "with mobil != 1 (kept immobile by diary_plan_match's design, not an error) and are "
            "seeded as 0 trips; %d/%d (%.2f%%) must have been remapped.",
            n_kept_immobile, len(persons), 100.0 * n_kept_immobile / max(len(persons), 1),
            n_offending, len(persons), 100.0 * n_offending / max(len(persons), 1))
        if n_offending:
            raise ValueError(
                f"derive_trip_class_seed: {n_offending} plan source(s) carry anzwege1 804, or 803 "
                "with mobil == 1, although diary_plan_match is on; the completed_donor build did "
                "not remap them (check the flag wiring). Sources with anzwege1 803 and mobil != 1 "
                "are kept by design and do not trigger this guard.")
    if counts_closure:
        for col in ("src_ends_at_home", "src_n_direct_legs"):
            if col not in persons.columns:
                raise KeyError(
                    f"derive_trip_class_seed(counts_closure=True) requires {col!r} from "
                    "diary_facts.attach_plan_source_facts (Task 3); absent from the person "
                    f"frame (has {list(persons.columns)}).")
        # Count the closed day: a source whose diary does not end at home gets a
        # synthetic return-home trip added by trip-chain closure, so the seed trip
        # count must include it. 803/804 codes (~codes already False for a resolved
        # count) and sources with zero direct legs never qualify.
        open_end = (~persons["src_ends_at_home"].astype(bool)) & (persons["src_n_direct_legs"] > 0) & ~codes
        # Clip at 50 (controller ruling R11): attributes.map_trip_class's value_map only
        # enumerates anzwege1 0..50 (the MiD codebook's valid range), so an anzwege1==50
        # source with an open-ended diary would otherwise become 51 -- an unenumerated
        # code missing.resolve raises on. 51 would fall in the same 5+ class as 50 under
        # the class scheme (0; 1-2; 3-4; 5+), so clipping changes no classification and
        # keeps value_map the single source of truth for the valid range.
        mapped = mapped.where(~open_end, (mapped + 1).clip(upper=50))
        logger.info(
            "[popsim.mid] trip_class seed counts the closed day: %d/%d (%.1f%%) sources get "
            "+1 for the synthetic return-home trip.",
            int(open_end.sum()), len(persons), 100.0 * open_end.mean())
        if drop_leading_arrive_home_leg:
            # The realised plan also LOSES the leading "arrive home from elsewhere" leg
            # (MiD W_SO1 == 2, ADR-0108): trips.expand_persons_to_trips drops it, so the
            # seed must not count it either (controller ruling R20). Same universe rule
            # as the +1: diary non-response codes are never arithmetic operands, and the
            # count never falls below 0.
            if "src_starts_arriving_home" not in persons.columns:
                raise KeyError(
                    "derive_trip_class_seed(counts_closure=True, drop_leading_arrive_home_leg="
                    "True) requires 'src_starts_arriving_home' from "
                    "diary_facts.attach_plan_source_facts; absent from the person frame "
                    f"(has {list(persons.columns)}).")
            dropped_lead = persons["src_starts_arriving_home"].astype(bool) & ~codes
            mapped = mapped.where(~dropped_lead, (mapped - 1).clip(lower=0))
            logger.info(
                "[popsim.mid] trip_class seed subtracts the dropped leading arrive-home leg: "
                "%d/%d (%.1f%%) sources get -1.",
                int(dropped_lead.sum()), len(persons), 100.0 * dropped_lead.mean())

    persons = persons.copy()
    persons["_plan_source_anzwege1"] = mapped.to_numpy()
    logger.info(
        "[popsim.mid] trip_class seed: derived from the realised weekday plan source "
        "(source anzwege1) for %d persons -- aligns the control with the SrV weekday "
        "target universe and the trips the synthetic person actually executes.",
        len(persons))
    out = attributes.map_trip_class(persons, trips_col="_plan_source_anzwege1", rng=rng)
    return out.drop(columns=["_plan_source_anzwege1"])


# Purpose -> W_ZWECK code set for each per-Kreis "participation" control (feature #224).
# Derived from the single source of truth trips.PURPOSE_BY_W_ZWECK rather than
# duplicating the code lists here: {"work": {1, 2}, "leisure": {7}, "education": {3, 11, 12}}.
# work_participation (task 4) is the first control built on this map; leisure_participation
# / education_participation (task 5) reuse the SAME derivation machinery, parametrized by
# purpose, rather than duplicating compute_has_work_trip / derive_work_participation_seed
# three times over.
PARTICIPATION_W_ZWECK: dict[str, set[int]] = {
    purpose: {code for code, p in trips.PURPOSE_BY_W_ZWECK.items() if p == purpose}
    for purpose in ("work", "leisure", "education")
}
# escort (issue #227): NOT derivable from PURPOSE_BY_W_ZWECK (which maps the escort
# codes to "other" unless the #201 escort-purpose flag rewrites the trips table), so
# the code set is declared explicitly. It is the ACTIVE escort leg ONLY -- W_ZWECK 6
# (Bringen/Holen, the escorter's own trip). W_ZWECK 13 (the PASSIVE leg: the escorted
# person's own trip, 100% minors on the raw MiD file -- see trips.ESCORT_W_ZWECK and
# the issue #256 active/passive split) is deliberately EXCLUDED: the SrV target this
# control is anchored to is built from E_ZWECK_9 == 6 ("Holen/Bringen", verified
# against SrV2023_Datenkodierung_SciUse.xlsx), which codes only the escorter's trip
# (the escorted person's SrV trip carries its own destination purpose, e.g. Kita).
# Counting 13 would put escorted minors into the MiD-side universe that the SrV
# target does not measure (the #97 universe trap).
PARTICIPATION_W_ZWECK["escort"] = {6}


def compute_has_purpose_trip(
    persons: pd.DataFrame, wege: pd.DataFrame, purpose: str, *,
    household_id: str = "H_ID", person_id: str = "P_ID",
    trips_col: str = "anzwege1", zweck_col: str = "W_ZWECK",
) -> pd.Series:
    """Derive the per-person ``has_<purpose>_trip`` flag (0/1, or a carried 803/804 diary
    non-response code) from each person's MiD Wege, for a ``<purpose>_participation`` seed
    (generic core behind ``compute_has_work_trip`` / the leisure / education controls,
    feature #224 task 5).

    A person has a ``purpose`` trip (flag = 1) if at least one of their Wege has
    ``zweck_col`` in ``PARTICIPATION_W_ZWECK[purpose]`` -- the ``W_ZWECK`` codes
    ``trips.PURPOSE_BY_W_ZWECK`` maps to that activity purpose; otherwise 0.

    Exception: if the person's own diary trip count (``trips_col``, default
    ``anzwege1``) is one of the 803/804 item non-response codes (trip module not
    covered -- no diary / rueckwirkende Wegeerhebung only; see
    ``attributes.map_trip_class``), the flag is UNKNOWN, not "no trip". The code is
    carried through unchanged so ``attributes.map_participation``'s
    ``missing.AttributeSpec(impute_codes=(803, 804))`` imputes it from the valid {0, 1}
    pool within the person's age band, exactly as ``trip_class`` handles the same codes.
    A diary non-response person must never be forced to 0.

    Returns a ``pd.Series`` indexed like ``persons`` (index preserved, not reset).

    Raises ``ValueError`` if ``purpose`` is not one of ``PARTICIPATION_W_ZWECK``.
    Raises ``KeyError`` if ``trips_col`` is absent from ``persons``, or if
    ``household_id`` / ``person_id`` / ``zweck_col`` are absent from ``wege`` (no silent
    fallback to a guessed column name).
    """
    if purpose not in PARTICIPATION_W_ZWECK:
        raise ValueError(
            f"compute_has_purpose_trip: purpose must be one of {sorted(PARTICIPATION_W_ZWECK)}, "
            f"got {purpose!r}.")
    if trips_col not in persons.columns:
        raise KeyError(
            f"compute_has_purpose_trip: source column {trips_col!r} absent from the person "
            f"frame (has {list(persons.columns)}); cannot derive has_{purpose}_trip.")
    missing_wege_cols = [c for c in (household_id, person_id, zweck_col) if c not in wege.columns]
    if missing_wege_cols:
        raise KeyError(
            f"compute_has_purpose_trip: column(s) {missing_wege_cols} absent from the Wege "
            f"frame (has {list(wege.columns)}); cannot derive has_{purpose}_trip.")

    purpose_codes = PARTICIPATION_W_ZWECK[purpose]
    purpose_wege = wege[wege[zweck_col].isin(purpose_codes)]
    purpose_person_keys = pd.MultiIndex.from_arrays(
        [purpose_wege[household_id], purpose_wege[person_id]]).unique()
    person_keys = pd.MultiIndex.from_arrays([persons[household_id], persons[person_id]])
    has_purpose_weg = person_keys.isin(purpose_person_keys)
    result = pd.Series(has_purpose_weg.astype(int), index=persons.index)

    nonresponse_mask = persons[trips_col].isin((803, 804))
    result = result.where(~nonresponse_mask, persons[trips_col])
    return result


def compute_has_work_trip(
    persons: pd.DataFrame, wege: pd.DataFrame, *,
    household_id: str = "H_ID", person_id: str = "P_ID",
    trips_col: str = "anzwege1", zweck_col: str = "W_ZWECK",
) -> pd.Series:
    """Derive the per-person ``has_work_trip`` flag (0/1, or a carried 803/804 diary
    non-response code) from each person's MiD Wege, for the ``work_participation`` seed.

    Thin wrapper over :func:`compute_has_purpose_trip` (purpose="work"); kept as a
    named entry point so existing callers/tests stay unchanged. See that function's
    docstring for the full 803/804 non-response handling.

    Returns a ``pd.Series`` indexed like ``persons`` (index preserved, not reset).

    Raises ``KeyError`` if ``trips_col`` is absent from ``persons``, or if
    ``household_id`` / ``person_id`` / ``zweck_col`` are absent from ``wege`` (no silent
    fallback to a guessed column name).
    """
    return compute_has_purpose_trip(
        persons, wege, "work",
        household_id=household_id, person_id=person_id,
        trips_col=trips_col, zweck_col=zweck_col)


def derive_participation_seed(persons, wege, purpose, *, rng, household_id="H_ID", person_id="P_ID"):
    """Derive the ``<purpose>_participation`` control seed from each person's REALISED
    weekday plan (generic core behind ``derive_work_participation_seed`` / the leisure /
    education controls, feature #224 task 5; mirrors ``derive_trip_class_seed`` -- see
    that docstring for the full weekday-vs-realised-plan rationale, which applies
    identically here).

    A synthetic person executes the MiD plan identified by ``(source_H_ID,
    source_P_ID)``, so ``<purpose>_participation`` must be seeded from that SOURCE
    donor's ``has_<purpose>_trip`` (derived from the source's own Wege via
    ``compute_has_purpose_trip``), not the person's own Wege -- the same weekday-reporter
    mismatch ``derive_trip_class_seed`` fixes for ``anzwege1`` applies here: a weekend
    reporter's own Wege are a Saturday/Sunday diary, not the weekday plan the synthetic
    person actually realises.

    When the plan-source columns are absent (no member completion / weekend match ran),
    the seed is already weekday-filtered, so ``has_<purpose>_trip`` is derived from the
    person's own Wege directly; the path taken is logged (no silent fallback). The
    803/804 diary non-response codes are imputed within the PERSON's own ``alter_gr1``
    age band, exactly as ``derive_trip_class_seed`` does.
    """
    name = f"{purpose}_participation"
    has_source = "source_H_ID" in persons.columns and "source_P_ID" in persons.columns
    if not has_source:
        logger.info(
            "[popsim.mid] %s seed: derived from each person's own Wege "
            "(no plan-source columns present -> seed is weekday-filtered).", name)
        persons = persons.copy()
        persons[f"has_{purpose}_trip"] = compute_has_purpose_trip(
            persons, wege, purpose, household_id=household_id, person_id=person_id)
        out = attributes.map_participation(persons, name, source_col=f"has_{purpose}_trip", rng=rng)
        return out.drop(columns=[f"has_{purpose}_trip"])

    # Real (non-imputed) donor persons are the only valid plan sources; a filler's source
    # points at its mirror donor, which is one of these real persons.
    real = persons[~persons["member_imputed"].astype(bool)] if "member_imputed" in persons.columns else persons
    real_has_purpose_trip = compute_has_purpose_trip(
        real, wege, purpose, household_id=household_id, person_id=person_id)
    source_has_purpose_trip = pd.Series(
        real_has_purpose_trip.to_numpy(),
        index=pd.MultiIndex.from_arrays([real[household_id], real[person_id]]))
    src_idx = pd.MultiIndex.from_arrays([persons["source_H_ID"], persons["source_P_ID"]])
    mapped = pd.Series(source_has_purpose_trip.reindex(src_idx).to_numpy(), index=persons.index)
    n_unresolved = int(mapped.isna().sum())
    if n_unresolved:
        # Defense-in-depth (no silent fallback): a plan source MUST resolve to a real
        # donor person; a NaN means upstream donor/source corruption (mirrors the
        # weekend_plan_match A2-sweep guard). Fail loudly rather than seed a NaN flag.
        raise ValueError(
            f"derive_participation_seed: {n_unresolved} person(s) have a plan source "
            f"(source_H_ID, source_P_ID) absent from the donor frame; cannot derive "
            f"{name} from the realised plan (upstream donor/source corruption).")
    persons = persons.copy()
    persons[f"_plan_source_has_{purpose}_trip"] = mapped.to_numpy()
    logger.info(
        "[popsim.mid] %s seed: derived from the realised weekday plan "
        "source (source has_%s_trip) for %d persons -- aligns the control with the "
        "trips the synthetic person actually executes.",
        name, purpose, len(persons))
    out = attributes.map_participation(
        persons, name, source_col=f"_plan_source_has_{purpose}_trip", rng=rng)
    return out.drop(columns=[f"_plan_source_has_{purpose}_trip"])


def derive_work_participation_seed(persons, wege, *, rng, household_id="H_ID", person_id="P_ID"):
    """Derive the ``work_participation`` control seed from each person's REALISED
    weekday plan.

    Thin wrapper over :func:`derive_participation_seed` (purpose="work"); kept as a
    named entry point so existing callers/tests stay unchanged. See that function's
    docstring for the full weekday-vs-realised-plan rationale.
    """
    return derive_participation_seed(
        persons, wege, "work", rng=rng, household_id=household_id, person_id=person_id)
