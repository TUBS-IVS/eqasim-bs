"""PT-subscription conditioning (A6) and consistent car_availability (A5).

- :func:`_condition_pt_subscription_probs` / :func:`_apply_car_availability_pt_margin`
  -- the ``pt_subscription_conditioned`` (A6) feature: zero the work/study-bound
  PT ticket category for non-employed/non-studying persons, and (when the MiD
  P24.1 x Pkw-Verfuegbarkeit cross-tab is available) couple the PT ticket
  probabilities to car_availability while preserving the P24.1 marginal.
- :func:`_derive_car_availability_consistent` -- the ``consistent_car_availability``
  (A5) feature: derive a licence/car-consistent categorical ``car_availability``
  and rake it to the MiD P19 "jederzeit" marginal.

Extracted verbatim from the stage module (``__init__``); see the package
docstring for the stage-level context.
"""

import pandas as pd
import numpy as np


# --- A6: condition pt_subscription on student / employment status -------------
def _condition_pt_subscription_probs(pt_probs, df_persons, pt_categories):
    """Apply the DATA-FREE logical PT-ticket constraints to the per-person
    probability matrix and re-normalise (A6).

    The MiD P24.1 combined category ``job_or_semester_ticket`` (Jobticket =
    employer-subsidised pass, Semesterticket = student Solidarmodell pass) is a
    work/study-bound ticket: it can only be held by a person who is ``employed``
    OR ``studies`` (see ``PT_TICKET_WORK_STUDY_BOUND``; MiD reports the two as one
    column, so ``employed OR studies`` is the tightest defensible rule). For every
    person who is neither employed nor studying, that category's probability is
    set to zero and the remaining vector re-normalised so it still sums to 1, then
    that person samples among the categories they CAN actually hold.

    Eligible persons (employed or studying) and the remaining categories are left
    untouched, so the P24.1 marginal stays matched within tolerance -- only the
    redistributed work/study mass of the non-eligible minority drifts.

    A defensive guard: if zeroing leaves an all-zero row (a degenerate vector
    whose mass sat entirely on the disallowed category), the person falls back to
    ``never_pt`` deterministically rather than producing an un-normalisable row.
    The fallback count is recorded in ``df_persons.attrs`` for traceability.

    Parameters
    ----------
    pt_probs : np.ndarray
        Per-person probability matrix, shape ``(n_persons, n_categories)``.
    df_persons : pd.DataFrame
        Must carry boolean ``employed`` and ``studies`` columns.
    pt_categories : sequence[str]
        The PT ticket categories in column order (``PT_TICKET_CATEGORIES``).

    Returns
    -------
    np.ndarray
        The conditioned, re-normalised probability matrix (a copy).
    """
    from braunschweig.data.mid.reference_tables import PT_TICKET_WORK_STUDY_BOUND
    from braunschweig.popsim.attributes import PT_TICKET_NEVER

    out = pt_probs.copy()
    categories = list(pt_categories)
    idx_never_pt = categories.index(PT_TICKET_NEVER)
    work_study_idx = [categories.index(c) for c in PT_TICKET_WORK_STUDY_BOUND
                      if c in categories]

    employed = df_persons["employed"].astype(bool).to_numpy()
    studies = df_persons["studies"].astype(bool).to_numpy()
    not_work_study = ~(employed | studies)

    # Zero the work/study-bound categories for persons who are neither employed
    # nor studying.
    if work_study_idx and not_work_study.any():
        out[np.ix_(not_work_study, work_study_idx)] = 0.0

    # Re-normalise rows. Rows that became all-zero (mass had sat entirely on the
    # disallowed category) fall back to never_pt deterministically.
    row_sums = out.sum(axis=1)
    degenerate = row_sums <= 0.0
    fallback_count = int(np.count_nonzero(degenerate))
    if fallback_count > 0:
        out[degenerate, :] = 0.0
        out[degenerate, idx_never_pt] = 1.0
        row_sums = out.sum(axis=1)
    out = out / row_sums[:, None]

    df_persons.attrs["pt_subscription_workstudy_zeroed_count"] = int(
        np.count_nonzero(not_work_study) if work_study_idx else 0
    )
    df_persons.attrs["pt_subscription_degenerate_fallback_count"] = fallback_count
    return out


# Light rake of the re-weighted PT probabilities back to the overall P24.1
# marginal: how many fixed-point iterations to run (each scales every category by
# target_marginal / current_marginal and re-normalises). Five passes drive the
# column mean to within ~0.1 pp of the target on representative input; the result
# is reported in ``df_persons.attrs`` for traceability.
_PT_CAR_MARGIN_RAKE_ITERATIONS = 5


def _apply_car_availability_pt_margin(
    pt_probs, df_persons, pt_categories, pt_by_car_availability,
):
    """Impose the MiD carless<->PT-pass coupling on the per-person PT probability
    matrix while preserving the overall P24.1 marginal (A6, data-dependent).

    The 3-margin {Kreis, sex, age} IPF above does NOT know the car-availability
    dimension, so the carless<->PT-pass correlation observed in MiD P24.1 x Pkw-
    Verfuegbarkeit is absent from ``pt_probs``. This helper re-weights each
    person's vector by the Bayes factor

        factor(person, ticket) = P(ticket | car_availability_of_person)
                                 / P(ticket)

    where ``P(ticket)`` is the population mean of ``pt_probs`` over the persons
    that carry a usable car-availability value (the "anchor" pool). Multiplying
    by this factor tilts each person toward the ticket types that are over-
    represented in their car-availability group and away from the under-
    represented ones, then the vector is re-normalised. To keep the AGGREGATE
    P24.1 marginal matched (the IPF target), the re-weighted matrix is then lightly
    raked: each category is scaled by ``target_marginal / current_marginal`` and
    the rows re-normalised, repeated :data:`_PT_CAR_MARGIN_RAKE_ITERATIONS` times.
    The rake adjusts only the column LEVELS, so the within-person carless tilt is
    preserved while the column means converge back to the pre-coupling marginal.

    Only persons whose ``car_availability`` maps to a key present in the cross-tab
    are re-weighted; persons with an unusable / missing car-availability value
    keep their original vector (counted + logged -- this is the documented primary
    vs. fallback split, no silent fallback). The P24.1 marginal target is computed
    over the anchor pool ONLY, so the unchanged fallback rows do not bias it.

    Parameters
    ----------
    pt_probs : np.ndarray
        Per-person probability matrix, shape ``(n_persons, n_categories)``.
    df_persons : pd.DataFrame
        Must carry a ``car_availability`` column whose values are in
        ``{none, some, all}`` (categorical, A5 ON) or ``{none, all}`` (A5 OFF).
    pt_categories : sequence[str]
        The PT ticket categories in column order (``PT_TICKET_CATEGORIES``).
    pt_by_car_availability : dict[str, np.ndarray]
        ``{car_availability -> P(ticket | car_availability)}`` from the MiD
        cross-tab (each vector sums to 1, column order == ``pt_categories``).

    Returns
    -------
    np.ndarray
        The re-weighted, marginal-preserving probability matrix (a copy).
    """
    out = pt_probs.copy()
    n_cats = len(pt_categories)
    eps = 1e-12

    car_values = df_persons["car_availability"].astype(str).to_numpy()
    # Anchor pool: persons whose car-availability is covered by the cross-tab.
    anchor = np.zeros(len(out), dtype=bool)
    for key in pt_by_car_availability:
        anchor |= car_values == key

    n_anchor = int(np.count_nonzero(anchor))
    n_total = len(out)
    df_persons.attrs["pt_subscription_car_margin_primary_count"] = n_anchor
    df_persons.attrs["pt_subscription_car_margin_fallback_count"] = n_total - n_anchor
    df_persons.attrs["pt_subscription_car_margin_fallback_rate"] = (
        (n_total - n_anchor) / n_total if n_total else 0.0
    )

    if n_anchor == 0:
        # No person carries a usable car-availability value: nothing to couple.
        # Loudly surfaced by the caller (fallback rate == 100%).
        return out

    # Population ticket marginal over the anchor pool BEFORE re-weighting -- this
    # is the P24.1 target the rake restores.
    target_marginal = out[anchor].mean(axis=0)
    target_marginal = target_marginal / max(target_marginal.sum(), eps)

    # Bayes re-weight: multiply each anchor person's vector by
    # P(ticket | their car_availability) / P(ticket).
    safe_marginal = np.maximum(target_marginal, eps)
    for key, cond_vec in pt_by_car_availability.items():
        f = anchor & (car_values == key)
        if not f.any():
            continue
        factor = np.asarray(cond_vec, dtype=float) / safe_marginal
        out[f] *= factor[None, :]

    # Re-normalise the re-weighted rows.
    row_sums = out[anchor].sum(axis=1, keepdims=True)
    row_sums[row_sums <= 0.0] = 1.0
    out[anchor] = out[anchor] / row_sums

    # Light rake back to the pre-coupling P24.1 marginal (column-level only).
    for _ in range(_PT_CAR_MARGIN_RAKE_ITERATIONS):
        current = out[anchor].mean(axis=0)
        scale = np.where(current > eps, target_marginal / np.maximum(current, eps), 1.0)
        out[anchor] *= scale[None, :]
        row_sums = out[anchor].sum(axis=1, keepdims=True)
        row_sums[row_sums <= 0.0] = 1.0
        out[anchor] = out[anchor] / row_sums

    # Final marginal-deviation diagnostic (max abs over categories, in pp).
    final_marginal = out[anchor].mean(axis=0)
    df_persons.attrs["pt_subscription_car_margin_max_dev_pp"] = float(
        np.max(np.abs(final_marginal - target_marginal)) * 100.0
    )
    assert n_cats == out.shape[1]  # column order preserved
    return out


# --- A5: consistent car_availability ----------------------------------------
# Categorical car_availability values (eqasim core vocabulary). The Java
# mode-choice side and the MATSim writers treat the value as an opaque string,
# so all three pass through unchanged; "some" is fully supported (the eqasim
# core itself emits it, see synthesis/population/enriched.py:96-98).
CAR_AVAILABILITY_CATEGORIES = ("none", "some", "all")

# Fallback-rate threshold above which the per-(Kreis) P19 raking fallback in
# :func:`_derive_car_availability_consistent` is logged at WARNING level. The
# fallback fires only for a Kreis cell whose "some/all" capacity is too small to
# reach the P19 "jederzeit" (>=some) target after the hard floors are applied
# (e.g. almost every person already floored to "none" by the carless/licence
# constraints); such a cell keeps the conditional assignment and is counted.
CAR_AVAILABILITY_RAKE_FALLBACK_WARN_RATE = 0.05


def _derive_car_availability_consistent(df_persons, mid, random_seed,
                                        minimum_age_car):
    """Derive a licence/car-consistent categorical ``car_availability`` (A5).

    Replaces the legacy free P19 IPF + all/none binarisation with a conditional
    derivation that respects the eqasim cars-vs-licences coupling and the MiD H7
    household car ownership, then rakes the residual to the MiD P19 "jederzeit"
    (= car available at any time) marginal so that aggregate target is still
    matched. The result is one of {none, some, all}:

    1. HARD FLOOR ``none`` if the person has no driving licence
       (``has_license == False``) OR lives in a 0-car household
       (household ``number_of_cars == 0``) OR is below the minimum age. These
       persons can never have a car available, so the P19 marginal is matched on
       the eligible remainder only.
    2. ``all`` if the household has at least as many cars as licensed adults
       (``number_of_cars >= n_licensed_adults``): no intra-household competition,
       every licensed adult can always take a car.
    3. otherwise ``some`` (more licensed adults than cars -> the car is shared /
       not always available).

    The conditional rule reproduces the eqasim-core all/some/none logic at
    HOUSEHOLD level (``synthesis/population/enriched.py:91-101``) but on the
    MiD-derived ``number_of_cars`` and the MiD-P17.1-derived ``has_license``,
    instead of the stale HTS-matched values the core saw before this stage
    overwrote them. On top of that, within each Kreis the ELIGIBLE persons
    (floor not applied) are raked toward the P19 per-zone "jederzeit" target:
    P19 reports the share of persons with a car available at any time, which maps
    to ``car_availability == "all"``. If the conditional "all" share in a Kreis
    is above the target, the surplus "all" persons (those in the most-competitive
    households first) are demoted to "some"; if it is below, "some" persons are
    promoted to "all". The carless/licenceless floor is never violated by the
    rake (only eligible persons move), so consistency is preserved while the
    aggregate P19 marginal is matched within the granularity of the eligible
    pool.

    Fallback transparency (CLAUDE.md): a Kreis whose eligible pool is too small
    to reach its P19 "all" target (target above the eligible share) cannot be
    fully raked; it keeps the conditional assignment and is counted. The
    primary/fallback split (persons and Kreis codes) is logged, WARNING above
    :data:`CAR_AVAILABILITY_RAKE_FALLBACK_WARN_RATE`.
    """
    n = len(df_persons)
    has_license = df_persons["has_license"].fillna(False).to_numpy().astype(bool)
    number_of_cars = df_persons["number_of_cars"].to_numpy().astype(np.int64)
    age = df_persons["age"].to_numpy()

    # Household aggregates: total cars per household are stored per-person but
    # represent a household quantity (MiD H7, "Autos im HH"); aggregate to a
    # single household value via the max so a single household car count drives
    # the coupling (drop_duplicates downstream takes one person's value, so the
    # per-person column must be made household-consistent here). Licensed adults
    # per household = count of has_license within the household.
    cars_by_hh = (
        pd.Series(number_of_cars, index=df_persons["household_id"])
        .groupby(level=0).max()
    )
    licensed_by_hh = (
        pd.Series(has_license.astype(np.int64), index=df_persons["household_id"])
        .groupby(level=0).sum()
    )
    hh_cars = df_persons["household_id"].map(cars_by_hh).to_numpy().astype(np.int64)
    hh_licensed = df_persons["household_id"].map(licensed_by_hh).to_numpy().astype(np.int64)

    # Make number_of_cars household-consistent (max within household) so the
    # downstream household table (drop_duplicates) reports a coherent count and
    # the A5 logic is reproducible regardless of which person is kept.
    df_persons["number_of_cars"] = hh_cars

    # Conditional base assignment.
    floor_none = (
        (~has_license)
        | (hh_cars == 0)
        | (age < minimum_age_car)
    )
    eligible = ~floor_none
    base = np.empty(n, dtype=object)
    base[floor_none] = "none"
    all_mask = eligible & (hh_cars >= np.maximum(hh_licensed, 1))
    some_mask = eligible & ~all_mask
    base[all_mask] = "all"
    base[some_mask] = "some"

    # P19 "jederzeit" per-zone targets (share of persons with a car at any time,
    # i.e. car_availability == "all"). Read from the same constraint list the
    # legacy IPF used; only the per-zone targets are needed for the rake.
    zone_target = {}
    for constraint in mid["car_availability_constraints"]:
        if "zone" in constraint:
            zone_target[constraint["zone"]] = float(constraint["target"])

    # A "competition score" orders eligible persons within a Kreis for promotion
    # / demotion: persons with the largest licensed-adults-minus-cars gap are the
    # most plausibly car-sharing ("some"); they are demoted first / promoted
    # last. Ties are broken by a seeded shuffle so the choice is reproducible but
    # not systematically biased by row order.
    rng = np.random.RandomState(random_seed + 41719)
    competition = (hh_licensed - hh_cars).astype(float)
    jitter = rng.random_sample(n)
    score = competition + jitter  # higher score -> more competition -> prefer "some"

    result = base.copy()
    n_total = n
    n_fallback = 0
    fallback_zones = []

    for zone, target in sorted(zone_target.items()):
        col = "inside_{}".format(zone)
        if col not in df_persons.columns:
            continue
        in_zone = df_persons[col].fillna(False).to_numpy().astype(bool)
        if not in_zone.any():
            continue
        zone_rows = np.where(in_zone)[0]
        n_zone = zone_rows.size
        # Target count of "all" persons in this zone (P19 share x persons).
        target_all = int(round(target * n_zone))
        zone_eligible = eligible[zone_rows]
        elig_rows = zone_rows[zone_eligible]
        n_elig = elig_rows.size
        cur_all_rows = elig_rows[result[elig_rows] == "all"]
        n_cur_all = cur_all_rows.size

        if target_all > n_elig:
            # Cannot reach the target: not enough eligible persons (the rest are
            # floored to "none"). Promote ALL eligible to "all" and record the
            # shortfall as fallback.
            result[elig_rows] = "all"
            n_fallback += n_zone
            fallback_zones.append(zone)
            continue

        if n_cur_all > target_all:
            # Demote the surplus "all" persons to "some": pick those with the
            # HIGHEST competition score (most car-sharing) first.
            surplus = n_cur_all - target_all
            order = cur_all_rows[np.argsort(-score[cur_all_rows], kind="stable")]
            result[order[:surplus]] = "some"
        elif n_cur_all < target_all:
            # Promote "some" persons to "all": pick those with the LOWEST
            # competition score (least car-sharing) first.
            deficit = target_all - n_cur_all
            some_rows = elig_rows[result[elig_rows] == "some"]
            order = some_rows[np.argsort(score[some_rows], kind="stable")]
            result[order[:deficit]] = "all"

    fallback_rate = (n_fallback / n_total) if n_total else 0.0
    df_persons.attrs["car_availability_rake_primary_count"] = n_total - n_fallback
    df_persons.attrs["car_availability_rake_fallback_count"] = n_fallback
    df_persons.attrs["car_availability_rake_fallback_rate"] = fallback_rate
    df_persons.attrs["car_availability_rake_fallback_zones"] = list(fallback_zones)

    df_persons["car_availability"] = pd.Categorical(
        result, categories=list(CAR_AVAILABILITY_CATEGORIES)
    )

    if n_fallback:
        level = (
            "WARNING: "
            if fallback_rate > CAR_AVAILABILITY_RAKE_FALLBACK_WARN_RATE
            else ""
        )
        print(
            f"[braunschweig.enriched] {level}car_availability P19 rake fallback "
            f"for {n_fallback}/{n_total} persons ({fallback_rate:.2%}) in zones "
            f"{sorted(fallback_zones)}: eligible pool too small to reach the P19 "
            f"'jederzeit' target after the carless/licence floor; kept conditional."
        )
    else:
        print(
            f"[braunschweig.enriched] car_availability P19 rake matched all "
            f"{n_total} persons (fallback rate 0.00%)."
        )
    print(
        "[braunschweig.enriched] consistent car_availability share = "
        + ", ".join(
            f"{k}={v:.1%}"
            for k, v in pd.Series(result).value_counts(normalize=True)
            .reindex(CAR_AVAILABILITY_CATEGORIES).fillna(0.0).items()
        )
    )
    return df_persons
