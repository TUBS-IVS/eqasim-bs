"""General day absence: the seeded two-stage state draw (issue #370, ADR-0110; issue #388).

Every person gets ``day_absence_state`` in {present, absent_household, absent_individual}:

1. HOUSEHOLD stage -- with probability ``p_all_absent[size_class]`` (SrV, by household size) the
   whole household is away (family vacation, joint travel): ``absent_household``.
2. INDIVIDUAL residual stage -- restricted to ELIGIBLE present persons, where eligibility is
   ``present & (household_size >= individual_stage_min_household_size)`` (``household_size`` is the
   UNCLIPPED member count, not the capped ``household_size_class``): per age band the SrV
   person-level rate ``r(a)`` minus what stage 1 already realised in that band, ``r_hh(a)``, gives a
   residual probability that every ELIGIBLE present person draws against. With the CODE default
   ``individual_stage_min_household_size=1`` every present person is eligible and the residual is
   the PR #387 expression ``p_individual(a) = (r(a) - r_hh(a)) / (1 - r_hh(a))`` (clipped at 0, an
   overshoot is WARNED) kept BYTE-IDENTICAL in :func:`_residual_probability_legacy`; a higher
   threshold routes non-eligible households out of the residual pool via the general
   :func:`_residual_probability_eligible` expression, WARNING when a band's target cannot be
   reached because the eligible pool is too small -- empty OR merely insufficient to cover the
   shortfall (issue #388: this is what makes the individual-stage residual scientifically
   defensible for households below the SrV-observed size at which the household-level clustering
   effect was measured). In expectation the per-band rates
   equal the SrV rates while the household clustering (56 % of absent persons in fully absent
   households) is reproduced by construction. Pure pandas/numpy; no file I/O apart from the
   reference loader.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass

import numpy as np
import pandas as pd

from braunschweig.calibration.srv_absence import (ABSENCE_BY_AGE_TABLE, ABSENCE_HOUSEHOLD_TABLE,
                                                  AGE_BAND_LABELS, ALL_BAND, HOUSEHOLD_SIZE_CLASS_TOP,
                                                  age_band, household_size_class)

logger = logging.getLogger(__name__)
_LOG_TAG = "[day absence]"

DAY_ABSENCE_SEED_OFFSET = 7351

STATE_PRESENT = "present"
STATE_ABSENT_HOUSEHOLD = "absent_household"
STATE_ABSENT_INDIVIDUAL = "absent_individual"
STATES = (STATE_PRESENT, STATE_ABSENT_HOUSEHOLD, STATE_ABSENT_INDIVIDUAL)
REASON_HOUSEHOLD = "household_draw"
REASON_INDIVIDUAL = "individual_draw"
REASON_PRESENT = "present"
REASON_DISABLED = "disabled"
ABSENCE_COLUMNS = ("person_id", "household_id", "day_absence_state", "age_band",
                   "household_size_class", "p_household", "p_individual", "reason")


def _check_reference_coverage(name: str, values: dict, expected_keys: set, *, allow_nan: bool = False) -> None:
    """Raise unless ``values`` covers exactly ``expected_keys`` with every rate in [0, 1].

    Called from :meth:`AbsenceReference.__post_init__` so an incomplete or out-of-range reference
    fails at construction rather than surfacing later as a silently mapped NaN inside the
    probability draw (the "scalar-default-when-map-missing" pattern CLAUDE.md forbids). With
    ``allow_nan=True`` a NaN VALUE is accepted (used only for ``p_absent_person_by_size``, a
    REPORTING reference where an empty size class legitimately has no rate) -- a missing KEY is
    never accepted either way, NaN is a valid value, not a substitute for an absent key.
    """
    missing = sorted(expected_keys - set(values), key=str)
    extra = sorted(set(values) - expected_keys, key=str)
    if missing or extra:
        raise ValueError(f"{_LOG_TAG} {name} must cover exactly {sorted(expected_keys, key=str)}; "
                         f"missing {missing}, unexpected {extra}")
    out_of_range = {k: v for k, v in values.items()
                    if not ((allow_nan and pd.isna(v)) or (0.0 <= float(v) <= 1.0))}
    if out_of_range:
        bound = "NaN or in [0, 1]" if allow_nan else "in [0, 1]"
        raise ValueError(f"{_LOG_TAG} {name} values must be {bound}; out of range: {out_of_range}")


def _map_size_class_probability(size_classes: pd.Series, table: dict) -> pd.Series:
    """Map ``household_size_class`` -> ``p_all_absent_by_size``; raise on an uncovered class.

    ``AbsenceReference`` is a frozen dataclass, but its dict fields stay mutable after
    construction, so this is a second, point-of-use guard against the same missing-key failure
    mode ``__post_init__`` already checks: a plain ``Series.map`` would turn a missing size class
    into a silent NaN (``NaN < probability`` is False, so the household would be "never absent"
    with no signal that anything went wrong); this raises instead.
    """
    mapped = size_classes.map(table).astype(float)
    if mapped.isna().any():
        missing = sorted(set(size_classes[mapped.isna()].unique().tolist()))
        raise ValueError(f"{_LOG_TAG} p_all_absent_by_size has no rate for household size "
                         f"class(es) {missing}; every class 1..{HOUSEHOLD_SIZE_CLASS_TOP} must be covered")
    return mapped


@dataclass(frozen=True)
class AbsenceReference:
    """Household-size and age-band absence rates behind :func:`draw_absence`.

    ``p_absent_by_band`` must cover exactly ``AGE_BAND_LABELS`` and ``p_all_absent_by_size``
    exactly the size classes ``1..HOUSEHOLD_SIZE_CLASS_TOP``, every rate in [0, 1]; checked eagerly
    in ``__post_init__`` (see :func:`_check_reference_coverage`). ``p_absent_person_by_size``
    (issue #388) is the SrV PERSON-level absence rate per household size class -- a REPORTING
    reference only, used for the ``by_size_class`` diagnostics' ``reference_rate``/``delta_pp``, and
    NEVER consumed by the draw itself (unlike ``p_all_absent_by_size``); its values may be NaN
    (an empty size class in the source table), but every size class 1..HOUSEHOLD_SIZE_CLASS_TOP
    must still be a KEY.
    """
    p_absent_by_band: dict
    p_all_absent_by_size: dict
    p_absent_person_by_size: dict

    def __post_init__(self) -> None:
        _check_reference_coverage("p_absent_by_band", self.p_absent_by_band, set(AGE_BAND_LABELS))
        _check_reference_coverage("p_all_absent_by_size", self.p_all_absent_by_size,
                                  set(range(1, HOUSEHOLD_SIZE_CLASS_TOP + 1)))
        _check_reference_coverage("p_absent_person_by_size", self.p_absent_person_by_size,
                                  set(range(1, HOUSEHOLD_SIZE_CLASS_TOP + 1)), allow_nan=True)


def load_absence_reference(srv_dir: str) -> AbsenceReference:
    """Read the two committed tables BY COLUMN NAME; raise on a missing band / size class."""
    by_age = pd.read_csv(os.path.join(srv_dir, ABSENCE_BY_AGE_TABLE), comment="#")
    by_size = pd.read_csv(os.path.join(srv_dir, ABSENCE_HOUSEHOLD_TABLE), comment="#")
    bands = by_age[by_age["band"] != ALL_BAND].set_index("band")["p_absent"]
    missing = sorted(set(AGE_BAND_LABELS) - set(bands.index))
    if missing or bands.isna().any():
        raise ValueError(f"{_LOG_TAG} {ABSENCE_BY_AGE_TABLE} lacks a rate for band(s) "
                         f"{missing or bands[bands.isna()].index.tolist()}")
    sizes = by_size.set_index("size_class")["p_all_absent"]
    expected = list(range(1, HOUSEHOLD_SIZE_CLASS_TOP + 1))
    if sorted(sizes.index.tolist()) != expected or sizes.isna().any():
        raise ValueError(f"{_LOG_TAG} {ABSENCE_HOUSEHOLD_TABLE} must carry a rate for size classes {expected}")
    # Person-level reporting reference (issue #388): NaN IS accepted here (an empty size class
    # would report NaN), unlike p_all_absent_by_size above, which the household draw itself
    # consumes and which must therefore never be NaN. An older by-size table (pre-#388) has no
    # p_absent_person column at all; check its presence up front rather than let a bare KeyError
    # surface from the column lookup below.
    if "p_absent_person" not in by_size.columns:
        raise ValueError(f"{_LOG_TAG} {ABSENCE_HOUSEHOLD_TABLE} lacks the p_absent_person column "
                         "(added by issue #388); re-run scripts/extract_srv_absence.py to "
                         "regenerate the table with the person-level reporting reference")
    persons_by_size = by_size.set_index("size_class")["p_absent_person"]
    if sorted(persons_by_size.index.tolist()) != expected:
        raise ValueError(f"{_LOG_TAG} {ABSENCE_HOUSEHOLD_TABLE} must carry a p_absent_person entry "
                         f"for size classes {expected}")
    return AbsenceReference(p_absent_by_band={b: float(bands[b]) for b in AGE_BAND_LABELS},
                            p_all_absent_by_size={int(k): float(v) for k, v in sizes.items()},
                            p_absent_person_by_size={int(k): float(v) for k, v in persons_by_size.items()})


def absent_person_ids(absence: pd.DataFrame) -> set:
    return set(absence.loc[absence["day_absence_state"] != STATE_PRESENT, "person_id"])


def _residual_probability_legacy(target: float, realised_hh: float) -> float:
    """PR #387's residual expression, kept BYTE-IDENTICAL: ``(r - r_hh) / (1 - r_hh)``.

    Valid only for ``realised_hh < 1.0``; the caller (:func:`draw_absence`) guards the
    ``realised_hh == 1.0`` case (a band fully absorbed by the household stage) before calling this,
    exactly as PR #387 did, and clips the result to [0, 1] afterwards. Kept as its own untouched
    function, rather than folded into the general :func:`_residual_probability_eligible`, so the
    ``individual_stage_min_household_size == 1`` path (issue #388's CODE default) is provably
    byte-identical to the pre-#388 behaviour -- see the module tests comparing the two expressions
    on random inputs.
    """
    return (target - realised_hh) / (1.0 - realised_hh)


def _residual_probability_eligible(target_n: float, absent_hh_n: float, n_eligible: int) -> float:
    """General residual expression over the ELIGIBLE present-person pool only, clipped to [0, 1].

    ``clip((target_n - absent_hh_n) / n_eligible, 0, 1)`` when ``n_eligible > 0``, else ``0.0`` (no
    eligible present person can carry the residual; the caller WARNS in that case when the target
    could not otherwise be reached, see :func:`draw_absence`).

    Algebraically identical to :func:`_residual_probability_legacy` whenever every present person is
    eligible (``n_eligible == n_band - absent_hh_n``): substituting ``target_n = target * n_band``
    and ``realised_hh = absent_hh_n / n_band`` recovers the legacy ratio exactly --
    ``(target * n_band - absent_hh_n) / (n_band - absent_hh_n) == (target - realised_hh) /
    (1 - realised_hh)``. A unit test checks the two agree to 1e-12 on random inputs under that
    condition.
    """
    if n_eligible <= 0:
        return 0.0
    return float(min(max((target_n - absent_hh_n) / n_eligible, 0.0), 1.0))


def draw_absence(persons: pd.DataFrame, reference: AbsenceReference, rng: np.random.RandomState, *,
                 household_stage: bool = True, individual_stage_min_household_size: int = 1
                 ) -> tuple[pd.DataFrame, dict]:
    """Two-stage seeded draw over ``persons`` (``person_id``, ``household_id``, ``age``).

    Row order of ``persons`` does not matter (sorted internally); the draw sequence is
    households (sorted by household_id) then persons (sorted by household_id, person_id).
    Raises ``ValueError`` on a missing age (a person that cannot be banded cannot be drawn).

    ``individual_stage_min_household_size`` (issue #388, CODE default 1 -- today's behaviour) gates
    who is ELIGIBLE for the individual residual stage: ``eligible = present & (household_size >=
    individual_stage_min_household_size)``, where ``household_size`` is the UNCLIPPED member count
    (not the capped ``household_size_class``). The person draw vector ``u_ind`` is still drawn for
    ALL persons exactly as before -- eligibility is applied as a MASK on top of it, never by
    skipping the draw -- so the random stream a downstream person consumes never shifts merely
    because of who is eligible. At the default ``1`` every present person is eligible and the
    residual is computed with :func:`_residual_probability_legacy` (byte-identical to PR #387); at
    any higher threshold the residual is computed with the general
    :func:`_residual_probability_eligible` over the eligible pool only, and a band whose eligible
    pool is too small to cover the shortfall (``target_n - absent_hh_n > n_eligible``, which
    includes -- but is not limited to -- an EMPTY eligible pool) logs a WARNING naming the
    shortfall rather than silently under-shooting; ``diagnostics["n_bands_unreachable"]`` counts
    how many bands this happened to.
    """
    missing = [c for c in ("person_id", "household_id", "age") if c not in persons.columns]
    if missing:
        raise ValueError(f"{_LOG_TAG} persons frame is missing {missing}")
    p = persons[["person_id", "household_id", "age"]].sort_values(["household_id", "person_id"]).reset_index(drop=True)
    if p["person_id"].duplicated().any():
        raise ValueError(f"{_LOG_TAG} duplicated person_id in the persons frame")
    band = age_band(p["age"])
    n_missing_age = int(band.isna().sum())
    if n_missing_age:
        raise ValueError(f"{_LOG_TAG} {n_missing_age} persons have no age and cannot be assigned an "
                         "absence band; the enriched persons frame must carry a numeric age for everyone")
    p["age_band"] = band.values
    sizes = p.groupby("household_id")["person_id"].transform("size")
    p["household_size_class"] = household_size_class(sizes.values)
    # UNCLIPPED member count (issue #388), distinct from the capped household_size_class above:
    # the individual-stage eligibility rule compares against the true household size, so a
    # household of e.g. 7 members is not conflated with one of exactly HOUSEHOLD_SIZE_CLASS_TOP.
    household_size = sizes.to_numpy()

    households = p.drop_duplicates("household_id")[["household_id", "household_size_class"]].reset_index(drop=True)
    p_hh = (_map_size_class_probability(households["household_size_class"], reference.p_all_absent_by_size)
            if household_stage else pd.Series(0.0, index=households.index))
    u_hh = rng.random_sample(len(households))
    hh_absent = pd.Series(u_hh < p_hh.to_numpy(), index=households["household_id"].values)
    p["p_household"] = (_map_size_class_probability(p["household_size_class"], reference.p_all_absent_by_size)
                        if household_stage else 0.0)
    is_hh_absent = p["household_id"].map(hh_absent).astype(bool).to_numpy()
    # Eligibility for the individual residual stage (issue #388): a MASK on top of "present", never
    # a change to who is drawn -- u_ind below is still drawn for every person regardless.
    is_present = ~is_hh_absent
    is_eligible = is_present & (household_size >= individual_stage_min_household_size)

    residual_by_band, by_band, n_overshoot, n_unreachable = {}, {}, 0, 0
    for label in AGE_BAND_LABELS:
        in_band = (p["age_band"] == label).to_numpy()
        n_band = int(in_band.sum())
        target = reference.p_absent_by_band[label]
        absent_hh_n = int(is_hh_absent[in_band].sum())
        realised_hh = float(absent_hh_n / n_band) if n_band else 0.0
        n_eligible = int(is_eligible[in_band].sum())
        target_n = target * n_band
        # An overshoot is realised_hh > target, checked directly rather than only via a negative
        # residual: at realised_hh == 1.0 (a band fully absorbed by the household stage) the
        # residual formula below is bypassed by the division-by-zero guard, so a residual-only
        # check would silently miss that case and never emit the mandatory WARNING.
        overshoot = realised_hh > target
        if individual_stage_min_household_size == 1:
            # Byte-identical to PR #387: every present person is eligible when the threshold is 1
            # (household_size >= 1 always holds), so the legacy expression is kept verbatim
            # rather than routed through the general (algebraically equivalent, but not
            # necessarily bit-for-bit identical) eligible-pool formula below.
            residual = _residual_probability_legacy(target, realised_hh) if realised_hh < 1.0 else 0.0
            residual = float(min(max(residual, 0.0), 1.0))
        else:
            residual = _residual_probability_eligible(target_n, absent_hh_n, n_eligible)
        if overshoot:
            n_overshoot += 1
            logger.warning("%s band %s: household stage realised %.2f%% > reference %.2f%% (overshoot); "
                           "residual set to 0", _LOG_TAG, label, 100 * realised_hh, 100 * target)
        # Generalised unreachable-target check (final-review fix wave, Important finding 1): the
        # original condition only fired at n_eligible == 0, but a PARTIALLY filled eligible pool
        # under-shoots exactly the same way -- e.g. 1,000 singles + 10 couples at a 10% band rate
        # with individual_stage_min_household_size=2 clips the residual to 1.0 (every one of the
        # 20 eligible persons drawn absent) and still realises only ~2%, with nothing logged under
        # the old n_eligible == 0 check alone. `target_n - absent_hh_n > n_eligible` subsumes that
        # old check (n_eligible == 0 makes it `target_n > absent_hh_n`) and additionally catches
        # this partial-pool shortfall. Unreachable under individual_stage_min_household_size == 1,
        # where n_eligible == n_band - absent_hh_n always (every present person eligible), so
        # target_n - absent_hh_n - n_eligible == target_n - n_band == n_band * (target - 1) <= 0.
        shortfall = target_n - absent_hh_n - n_eligible
        if shortfall > 0:
            n_unreachable += 1
            logger.warning("%s band %s cannot reach its target: target_n=%.2f, absent_hh_n=%d, "
                           "n_eligible=%d (shortfall %.2f persons)", _LOG_TAG, label, target_n,
                           absent_hh_n, n_eligible, shortfall)
        residual_by_band[label] = residual
        by_band[label] = {"n": n_band, "reference_rate": target, "realised_household_rate": realised_hh,
                          "residual_p": residual, "n_eligible_present": n_eligible}
    p["p_individual"] = p["age_band"].map(residual_by_band).astype(float)
    u_ind = rng.random_sample(len(p))  # drawn for ALL persons, exactly as before; eligibility is a mask
    is_ind_absent = is_eligible & (u_ind < p["p_individual"].to_numpy())

    state = np.full(len(p), STATE_PRESENT, dtype=object)
    reason = np.full(len(p), REASON_PRESENT, dtype=object)
    state[is_hh_absent] = STATE_ABSENT_HOUSEHOLD
    reason[is_hh_absent] = REASON_HOUSEHOLD
    state[is_ind_absent] = STATE_ABSENT_INDIVIDUAL
    reason[is_ind_absent] = REASON_INDIVIDUAL
    p["day_absence_state"] = state
    p["reason"] = reason
    out = p[list(ABSENCE_COLUMNS)]

    absent = out["day_absence_state"] != STATE_PRESENT
    for label in AGE_BAND_LABELS:
        in_band = out["age_band"] == label
        by_band[label]["realised_rate"] = float(absent[in_band].mean()) if in_band.any() else float("nan")
    n_abs = int(absent.sum())
    # Vectorised over households (a per-household Python-level lambda does not scale to a full
    # population): a household is fully absent iff every member's `absent` flag is True.
    hh_all_absent = absent.groupby(out["household_id"]).transform("all")
    share_clustered = float((absent & hh_all_absent).sum() / n_abs) if n_abs else float("nan")

    # Ruling R11 (final-review fix wave): realised absence rate PER HOUSEHOLD SIZE CLASS,
    # reported for transparency only -- the model does NOT target a per-size person-level
    # absence rate (only the household-clustering share and the per-AGE-BAND rate are
    # targeted). Because the household stage marks a WHOLE household absent with one draw per
    # household regardless of its size, a single-person household is effectively drawn at the
    # household rate directly while a member of a large household needs many co-members to also
    # draw absent for the whole household to be marked -- so singles are systematically
    # OVER-absent and members of large households UNDER-absent relative to each other within a
    # band, even though the band-level rate still holds in expectation (ADR-0110 Assumptions).
    by_size_class = {}
    for size_class in range(1, HOUSEHOLD_SIZE_CLASS_TOP + 1):
        in_class = (out["household_size_class"] == size_class).to_numpy()
        n_class = int(in_class.sum())
        realised_rate = float(absent[in_class].mean()) if n_class else float("nan")
        # reference_rate is the SrV PERSON-level reporting reference (issue #388); NaN propagates
        # into delta_pp when either side is NaN (an empty class or an unfilled reference), never
        # substituted with a default -- see AbsenceReference.p_absent_person_by_size.
        reference_rate = float(reference.p_absent_person_by_size[size_class])
        by_size_class[size_class] = {
            "n": n_class,
            "realised_rate": realised_rate,
            "reference_rate": reference_rate,
            "delta_pp": 100.0 * (realised_rate - reference_rate),
        }

    n_persons_ineligible_individual_stage = int((is_present & ~is_eligible).sum())
    diagnostics = {"n_persons": int(len(out)), "n_households": int(len(households)),
                   "n_absent_household": int(is_hh_absent.sum()), "n_absent_individual": int(is_ind_absent.sum()),
                   "n_absent_total": n_abs, "share_absent_total": float(n_abs / max(len(out), 1)),
                   "share_absent_in_fully_absent_households": share_clustered,
                   "n_bands_overshoot": n_overshoot, "n_bands_unreachable": n_unreachable,
                   "household_stage": bool(household_stage),
                   "individual_stage_min_household_size": int(individual_stage_min_household_size),
                   "n_persons_ineligible_individual_stage": n_persons_ineligible_individual_stage,
                   "by_band": by_band, "by_size_class": by_size_class}
    logger.info("%s %d/%d persons absent (%.2f%%): %d by the household stage, %d by the individual stage; "
                "%.1f%% of absent persons live in a fully absent household; per band %s; per household size "
                "class (REPORTED, not targeted -- see ADR-0110 Assumptions) %s", _LOG_TAG, n_abs,
                len(out), 100.0 * diagnostics["share_absent_total"], diagnostics["n_absent_household"],
                diagnostics["n_absent_individual"], 100.0 * share_clustered if n_abs else float("nan"),
                {b: f"{100 * v['realised_rate']:.2f}% vs {100 * v['reference_rate']:.2f}% "
                    f"(n_eligible={v['n_eligible_present']})" for b, v in by_band.items()},
                {sc: f"{100 * v['realised_rate']:.2f}% (n={v['n']})" for sc, v in by_size_class.items()})
    return out, diagnostics
