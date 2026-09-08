"""Acceptance-gate analysis for the SrV per-Kreis participation controls (#224).

Task 7 of feature #224: a standalone, tested analysis module reused by the
Task-8 popsim smoke to evaluate the pre-registered acceptance gate (spec
Section 9) -- per-Kreis realised-vs-SrV-target participation shares, the
Mobilitaetsquote (mobility rate), and a donor-duplication effective sample
size (N_eff). This module is NOT wired into any live pipeline stage; it is a
reusable analysis imported by whatever evaluates the gate.

HONESTY CAVEAT (mandatory, reproduce in any report built on this module):
these participation controls are a FIT CHECK (the SrV target STEERS the
raking), so a good realised-vs-target fit measures raking convergence, NOT
independent agreement with reality; and ~5-8pp of the mobility level is a
documented SrV-vs-MiD survey method offset (spec Section 7). A tight fit here
must never be reported as "validated against reality".

That caveat is exact for ``leisure`` and ``escort``, whose all-persons controls
still steer. For ``work`` and ``education`` it became half wrong with Plan B
(issue #368, ADR-0109): those two all-persons controls default OFF now, so
nothing steers their rates DIRECTLY and the purpose-level comparison is a
genuine, if weak, one. It is still not independent validation -- the universe
controls that replaced them (below) are built from the SAME SrV aggregate and
constrain the same trips inside their universes, so agreement is largely
inherited from that steering. Which case a given run is in depends on its
configuration, which this module cannot read; the statement above is the
default one (``source_resolution._KREIS_CONTROL_DEFAULT``, configs/base_bs.yml).
``run_population_validation._participation_fit_report`` carries the same split
for the report it writes.

Plan B (issue #368, Task 8) adds a SECOND, related family: the participation-
UNIVERSE controls (``realised_universe_participation`` / ``load_universe_targets``
/ ``universe_participation_fit``). These replace ``work_participation`` /
``education_participation`` (registered over ALL persons) with controls defined
over a SPECIFIC universe, because the all-persons controls were met by the WRONG
persons (arm-3 run manifest plan-structure-fix-arm3-100pct-2026-09-07: employed
persons with a work trip were 58.4% against the SrV target 67.5%, pensioners 7.8%
against 1.8%; 6-17-year-olds in education were 84.3% against 90.0%). Each
function below mirrors its purpose-only namesake exactly, with ONE addition: the
realised share is computed over the control's OWN universe subset of
``persons_kreis``.

The universe of every control (``work_by_employment`` and the three
``education_*`` bands) is read DIRECTLY off the matching
``kreis_attribute_control.REGISTRY`` entry's ``min_age`` / ``max_age`` fields
(fix round 1, item 3; see ``_universe_registry_entries`` below) rather than from
a same-VALUED but separately-imported constant -- a REGISTRY change is then
picked up here automatically, and a REGISTRY shape this module can no longer
resolve fails loudly at IMPORT time rather than silently reporting on the wrong
universe. "Employed" is the ``employment_status``-class test
(``attributes.EMPLOYED_EMPLOYMENT_STATUS_CLASSES``), NEVER the ``employed``
boolean attribute -- a DIFFERENT MiD variable, which the two do not agree on
for every person; the control pins the former, and so does this report.
(ASSUMPTION, not a committed reference: the arm-3 100 % run logged an
``[attributes] employment_status vs employed agreement`` line of ~99.8 %, but
that line is NOT part of the committed excerpt
``eqasim-data/data/braunschweig/calibration/plan_structure_fix_arm3_100pct_2026-09-07/run_log_excerpts_part1.txt``
of run manifest ``plan-structure-fix-arm3-100pct-2026-09-07``, so the figure is
not reproducible from this repository and must not be quoted as an established
one. The choice above rests on the QUALITATIVE fact that the two variables
differ at all, which does not depend on the exact rate.) A share computed over
the WHOLE population would answer a different question and would be wrong --
see :func:`realised_universe_participation`'s docstring for the full universe
definitions. The SAME HONESTY CAVEAT above applies verbatim to
these three functions: their targets also STEER the raking, so a good fit is
convergence, never independent validation.

The raw MiD ``W_ZWECK`` trip schema (see :func:`realised_participation`'s
schema detection) is deliberately NOT supported for these three functions (fix
round 1, item 1): its static ``PARTICIPATION_W_ZWECK`` code sets apply no rbW
filter and no ``escort_passive_education`` W_ZWECK-13 relabelling, so a
"work"/"education" leg on that schema would not match
``mid.participation.derive_work_by_employment_seed`` /
``derive_education_flag_seed``'s realised-plan definition -- the exact
seed-vs-realised-plan mismatch that module's own docstring names as the defect
those functions exist to remove, just reached via the report's trip schema
instead of via the control's seed. ``realised_universe_participation`` raises
``ValueError`` rather than silently mismeasuring; supply the eqasim trip
schema, or use :func:`realised_participation` for a ``W_ZWECK``-only cohort.
"""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd

from braunschweig.popsim import kreis_attribute_control as kac
from braunschweig.popsim.attributes import (
    EMPLOYED_EMPLOYMENT_STATUS_CLASSES, EMPLOYMENT_STATUS_CATEGORIES)
from braunschweig.popsim.mid import PARTICIPATION_W_ZWECK

LOGGER = logging.getLogger("braunschweig.analysis.participation_fit")

# The per-purpose participation controls evaluated by this module, derived from
# mid.PARTICIPATION_W_ZWECK (the single source of truth for the registered purpose
# set: work / leisure / education from feature #224, escort from issue #227), plus
# the derived mobility ("Mobilitaetsquote") pseudo-purpose. A purpose registered
# there is picked up here automatically -- including its committed target file
# requirement in load_participation_targets.
PARTICIPATION_PURPOSES = tuple(PARTICIPATION_W_ZWECK)


def _universe_registry_entries(categories: tuple) -> tuple:
    """``kreis_attribute_control.REGISTRY`` entries whose rendered category-label tuple
    equals ``categories`` exactly (fix round 1, item 3).

    Identifying "the participation-UNIVERSE entries" this way -- by the SAME canonical
    category vocabulary (:data:`kreis_attribute_control.WORK_BY_EMPLOYMENT_CATEGORIES` /
    :data:`kreis_attribute_control.EDUCATION_FLAG_CATEGORIES`) those entries are built
    from, rather than by a hand-maintained list of control names -- means this module
    reads its universe bounds (``min_age``/``max_age``) DIRECTLY off the REGISTRY entry
    instead of a same-valued-today-but-separately-imported constant, so a future change
    to a REGISTRY entry's bounds is picked up here automatically. Verified unique in
    :data:`kreis_attribute_control.REGISTRY` today (module-level assertions below); no
    other entry shares either category tuple.
    """
    return tuple(
        ctl for ctl in kac.REGISTRY
        if tuple(label for label, _ in ctl.categories) == categories)


_WORK_BY_EMPLOYMENT_REGISTRY_ENTRIES = _universe_registry_entries(kac.WORK_BY_EMPLOYMENT_CATEGORIES)
if len(_WORK_BY_EMPLOYMENT_REGISTRY_ENTRIES) != 1:
    # Fail at IMPORT time, not at call time with a wrong result: a REGISTRY shape change
    # this lookup can no longer resolve must never silently report on 0 or 2+ controls.
    raise RuntimeError(
        f"participation_fit: expected exactly 1 kreis_attribute_control.REGISTRY entry "
        f"shaped like WORK_BY_EMPLOYMENT_CATEGORIES, found "
        f"{len(_WORK_BY_EMPLOYMENT_REGISTRY_ENTRIES)}; the work_by_employment universe "
        "filter can no longer be derived from the REGISTRY (has the entry's categories "
        "changed?).")
_WORK_BY_EMPLOYMENT_REGISTRY_ENTRY = _WORK_BY_EMPLOYMENT_REGISTRY_ENTRIES[0]

_EDUCATION_REGISTRY_ENTRIES = _universe_registry_entries(kac.EDUCATION_FLAG_CATEGORIES)
if not _EDUCATION_REGISTRY_ENTRIES:
    raise RuntimeError(
        "participation_fit: found NO kreis_attribute_control.REGISTRY entries shaped like "
        "EDUCATION_FLAG_CATEGORIES; the education universe filters can no longer be "
        "derived from the REGISTRY.")


def realised_participation(trips: pd.DataFrame, persons_kreis: pd.DataFrame) -> pd.DataFrame:
    """Per-Kreis realised participation and mobility rates from a trip diary.

    ``trips`` must carry a ``person_id`` column and either:

    - the eqasim ``eqasim_trips.csv`` schema (``following_purpose`` and
      ``preceding_purpose`` string columns, e.g. "work"/"leisure"/"education"),
      where a person participates in purpose P if any of their trips has
      ``following_purpose == P`` or ``preceding_purpose == P``; or
    - a raw MiD ``W_ZWECK`` int column, where a person participates in
      purpose P if any of their trips has ``W_ZWECK`` in
      ``mid.PARTICIPATION_W_ZWECK[P]``.

    The schema is detected from the columns present; if neither is found this
    raises ``KeyError`` (no silent fallback to an empty/zero result).

    ``persons_kreis`` must carry ``person_id`` and ``ars5`` (5-digit Kreis).
    Every person in ``persons_kreis`` is counted (including persons absent
    from ``trips``, who are immobile / non-participating by construction).

    Returns one row per (``ars5``, ``purpose``) with ``purpose`` in
    ``PARTICIPATION_PURPOSES`` plus ``"mobility"`` (currently work / leisure /
    education / escort / mobility): columns ``ars5, purpose,
    realised_rate, n_persons``. ``mobility`` is the share of persons with at
    least one trip of ANY purpose that day (the Mobilitaetsquote), i.e. the
    complement of the trip-class "0 trips" share.
    """
    missing_persons_cols = [c for c in ("person_id", "ars5") if c not in persons_kreis.columns]
    if missing_persons_cols:
        raise KeyError(
            f"realised_participation: persons_kreis is missing required column(s) "
            f"{missing_persons_cols} (has {list(persons_kreis.columns)}).")
    if "person_id" not in trips.columns:
        raise KeyError(
            f"realised_participation: trips is missing required column 'person_id' "
            f"(has {list(trips.columns)}).")

    has_purpose_string_schema = {"following_purpose", "preceding_purpose"}.issubset(trips.columns)
    has_wzweck_schema = "W_ZWECK" in trips.columns
    if not has_purpose_string_schema and not has_wzweck_schema:
        raise KeyError(
            "realised_participation: trips must carry either the eqasim "
            "'following_purpose'/'preceding_purpose' string pair or a MiD 'W_ZWECK' "
            f"int column; has {list(trips.columns)}. No silent fallback to an empty result.")
    if has_purpose_string_schema and has_wzweck_schema:
        LOGGER.info(
            "realised_participation: trips carries both the eqasim purpose-string "
            "columns and a MiD 'W_ZWECK' column; using the eqasim schema deterministically.")

    # Mobility: any recorded trip that day, regardless of its purpose (including
    # purely "home"-bound legs), makes a person mobile -- mirrors the trip-class
    # target's "0 trips" definition of immobility.
    mobile_person_ids = set(trips["person_id"].unique())

    per_purpose_person_ids: dict[str, set] = {}
    if has_purpose_string_schema:
        for purpose in PARTICIPATION_PURPOSES:
            mask = (trips["following_purpose"] == purpose) | (trips["preceding_purpose"] == purpose)
            per_purpose_person_ids[purpose] = set(trips.loc[mask, "person_id"].unique())
    else:
        for purpose in PARTICIPATION_PURPOSES:
            codes = PARTICIPATION_W_ZWECK[purpose]
            mask = trips["W_ZWECK"].isin(codes)
            per_purpose_person_ids[purpose] = set(trips.loc[mask, "person_id"].unique())

    rows = []
    for ars5, group in persons_kreis.groupby("ars5"):
        n_persons = len(group)
        if n_persons == 0:
            continue
        person_ids = set(group["person_id"])
        for purpose in (*PARTICIPATION_PURPOSES, "mobility"):
            participating_ids = mobile_person_ids if purpose == "mobility" else per_purpose_person_ids[purpose]
            n_participating = len(person_ids & participating_ids)
            rows.append({
                "ars5": ars5,
                "purpose": purpose,
                "realised_rate": n_participating / n_persons,
                "n_persons": n_persons,
            })
    return pd.DataFrame(rows, columns=["ars5", "purpose", "realised_rate", "n_persons"])


def load_participation_targets(targets_dir: Path) -> pd.DataFrame:
    """Load the committed SrV per-Kreis participation targets as a tidy frame.

    Reads ``target2026_<purpose>_participation_by_kreis.csv`` for EVERY purpose in
    ``PARTICIPATION_PURPOSES`` (currently work / leisure / education / escort;
    ``comment="#"``) and takes each purpose's ``<purpose>_yes`` column as the
    target participation rate -- one committed target file per registered purpose
    is REQUIRED, independent of whether that purpose's control is toggled on. Adds
    a ``mobility`` pseudo-purpose row per Kreis from
    ``target2026_trip_class_by_kreis.csv``, with ``target_rate = 1 - trips_0``
    (the Mobilitaetsquote implied by the SrV trip-class distribution).

    Returns tidy columns ``ars5, purpose, target_rate``.

    Raises ``FileNotFoundError`` if a required target file is missing, and
    ``KeyError`` if a required column is missing from a target file that IS
    present (no silent fallback to a guessed column name).
    """
    targets_dir = Path(targets_dir)
    frames = []

    for purpose in PARTICIPATION_PURPOSES:
        path = targets_dir / f"target2026_{purpose}_participation_by_kreis.csv"
        if not path.exists():
            raise FileNotFoundError(
                f"load_participation_targets: required target file {path} is missing.")
        target = pd.read_csv(path, comment="#", dtype={"ars5": str})
        yes_col = f"{purpose}_yes"
        if "ars5" not in target.columns or yes_col not in target.columns:
            raise KeyError(
                f"load_participation_targets: {path} is missing required column(s) among "
                f"['ars5', {yes_col!r}] (has {list(target.columns)}).")
        frames.append(pd.DataFrame({
            "ars5": target["ars5"],
            "purpose": purpose,
            "target_rate": target[yes_col],
        }))

    trip_class_path = targets_dir / "target2026_trip_class_by_kreis.csv"
    if not trip_class_path.exists():
        raise FileNotFoundError(
            f"load_participation_targets: required trip-class target file "
            f"{trip_class_path} is missing (needed to derive the mobility target).")
    trip_class = pd.read_csv(trip_class_path, comment="#", dtype={"ars5": str})
    if "ars5" not in trip_class.columns or "trips_0" not in trip_class.columns:
        raise KeyError(
            f"load_participation_targets: {trip_class_path} is missing required column(s) "
            f"among ['ars5', 'trips_0'] (has {list(trip_class.columns)}).")
    frames.append(pd.DataFrame({
        "ars5": trip_class["ars5"],
        "purpose": "mobility",
        "target_rate": 1.0 - trip_class["trips_0"],
    }))

    return pd.concat(frames, ignore_index=True)


def participation_fit(trips: pd.DataFrame, persons_kreis: pd.DataFrame, targets_dir: Path) -> pd.DataFrame:
    """Join realised participation (interface 1) to the SrV targets (interface 2).

    Returns ``ars5, purpose, realised_rate, n_persons, target_rate, abs_error``.

    ``n_persons`` -- the number of persons in that Kreis, carried through unchanged from
    :func:`realised_participation` -- is what makes an ``abs_error`` interpretable: the
    same deviation means different things in a Kreis of 500 and one of 50,000, and the
    run's headline "worst abs_error" line reports exactly such a single cell. Added for
    parity with :func:`universe_participation_fit`, which has carried it since #368.

    Realised (``ars5``, ``purpose``) cells with no matching target row are
    logged (warning, with examples) and dropped -- mirroring
    ``control_validation.evaluate_control``'s out-of-vocabulary handling --
    rather than silently coercing them to NaN or 0.

    See the module docstring for the mandatory honesty caveat: a good fit here
    reflects raking convergence toward the SrV target, not independent
    agreement with reality.
    """
    realised = realised_participation(trips, persons_kreis)
    targets = load_participation_targets(targets_dir)

    merged = realised.merge(targets, on=["ars5", "purpose"], how="left", indicator=True)
    missing = merged[merged["_merge"] == "left_only"]
    if not missing.empty:
        examples = list(zip(missing["ars5"], missing["purpose"]))[:5]
        LOGGER.warning(
            "participation_fit: %d realised (ars5, purpose) cell(s) have no matching SrV "
            "target and are excluded from the fit; examples: %s", len(missing), examples)
    merged = merged[merged["_merge"] == "both"].drop(columns="_merge")
    merged["abs_error"] = (merged["realised_rate"] - merged["target_rate"]).abs()
    return merged[["ars5", "purpose", "realised_rate", "n_persons", "target_rate",
                   "abs_error"]].reset_index(drop=True)


# --------------------------------------------------------------------------- #
# Participation-UNIVERSE controls (Plan B, issue #368, Task 8)
# --------------------------------------------------------------------------- #

def _purpose_leg_person_ids(trips: pd.DataFrame, purposes) -> dict:
    """Per-``purpose`` set of person ids with >= 1 trip of that purpose, for the
    eqasim trip schema ONLY.

    Schema detection mirrors :func:`realised_participation`'s (the eqasim
    ``following_purpose``/``preceding_purpose`` string pair, preferred -- and used
    deterministically, logged -- when both schemas are present). Kept as a small,
    separate detector rather than a refactor of :func:`realised_participation` --
    mirrors ``mid.participation``'s own precedent of sibling derivations over one
    shared, heavily parametrised core (see that module's package docstring) -- so
    this addition cannot change the already-tested purpose-only behaviour.

    Unlike :func:`realised_participation`, the raw MiD ``W_ZWECK``-only schema is
    NOT supported here (fix round 1, item 1): ``PARTICIPATION_W_ZWECK``'s static
    code sets apply no rbW filter and no ``escort_passive_education`` W_ZWECK-13
    relabelling, so "work"/"education" on that schema would not match
    ``mid.participation.derive_work_by_employment_seed`` /
    ``derive_education_flag_seed``'s realised-plan definition of those purposes --
    the eqasim schema is safe here because a built ``eqasim_trips.csv`` already
    reflects whichever ``exclude_rbw_legs`` / ``escort_passive_education`` flags
    built it, while the raw ``W_ZWECK`` schema carries no such information and this
    function has no parameter to receive it. Raises ``ValueError`` naming the
    reason rather than silently measuring a different universe than the control
    constrains -- see the module docstring.

    Raises ``KeyError`` if neither schema is present at all (no silent fallback to
    an empty participant set).
    """
    has_purpose_string_schema = {"following_purpose", "preceding_purpose"}.issubset(trips.columns)
    has_wzweck_schema = "W_ZWECK" in trips.columns
    if not has_purpose_string_schema and not has_wzweck_schema:
        raise KeyError(
            "realised_universe_participation: trips must carry either the eqasim "
            "'following_purpose'/'preceding_purpose' string pair or a MiD 'W_ZWECK' "
            f"int column; has {list(trips.columns)}. No silent fallback to an empty result.")
    if has_purpose_string_schema and has_wzweck_schema:
        LOGGER.info(
            "realised_universe_participation: trips carries both the eqasim purpose-string "
            "columns and a MiD 'W_ZWECK' column; using the eqasim schema deterministically.")
    if not has_purpose_string_schema:
        # has_wzweck_schema is True here (the only remaining case after the two checks
        # above) -- the raw-MiD-only path fix round 1 item 1 forbids. See the docstring
        # and the module docstring for the full rbW / escort_passive_education rationale.
        raise ValueError(
            "realised_universe_participation: the raw MiD 'W_ZWECK' trip schema is not "
            "supported for the participation-UNIVERSE controls (unlike "
            "realised_participation), because 'work'/'education' on that schema cannot be "
            "corrected for the exclude_rbw_legs / escort_passive_education flags the "
            "production seed derivation (mid.participation.derive_work_by_employment_seed / "
            "derive_education_flag_seed) applies, and this function has no parameter to "
            "receive them -- silently evaluating it would measure a DIFFERENT universe than "
            "the control actually constrains. Provide the eqasim "
            "'following_purpose'/'preceding_purpose' trip schema instead, or use "
            "realised_participation for a W_ZWECK-only cohort.")

    result: dict = {}
    for purpose in purposes:
        mask = (trips["following_purpose"] == purpose) | (trips["preceding_purpose"] == purpose)
        result[purpose] = set(trips.loc[mask, "person_id"].unique())
    return result


def _category_shares(ars5: np.ndarray, category: np.ndarray, control: str, categories) -> pd.DataFrame:
    """Per-Kreis category shares over ONE control's own universe subset.

    ``ars5`` and ``category`` must already be restricted to that universe and
    positionally aligned (e.g. both filtered from the same boolean mask). Every
    (Kreis, category) combination present in ``categories`` gets an explicit row
    -- a Kreis where nobody falls in some category still reports an explicit 0.0
    share, not a missing row, mirroring :func:`realised_participation`'s
    per-purpose reindex. A Kreis with NO universe member at all (e.g. no
    0-5-year-old) is simply absent from ``ars5`` and therefore contributes no
    rows here -- see :func:`realised_universe_participation`'s docstring for why
    that must never default to a defined-but-zero share.
    """
    frame = pd.DataFrame({"ars5": ars5, "category": category})
    rows = []
    for kreis, group in frame.groupby("ars5"):
        n_persons = len(group)
        counts = group["category"].value_counts().reindex(categories, fill_value=0)
        for cat, count in counts.items():
            rows.append({
                "ars5": kreis,
                "control": control,
                "category": cat,
                "realised_share": count / n_persons,
                "n_persons": n_persons,
            })
    return pd.DataFrame(rows, columns=["ars5", "control", "category", "realised_share", "n_persons"])


def realised_universe_participation(trips: pd.DataFrame, persons_kreis: pd.DataFrame) -> pd.DataFrame:
    """Per-Kreis realised share for each participation-UNIVERSE control (Plan B, #368),
    read from the realised trips exactly like :func:`realised_participation` -- see this
    module's docstring for why the trips frame (not the popsim seed) is the source, and
    for why the raw MiD ``W_ZWECK``-only trip schema is NOT supported here.

    Each control's OWN universe -- never the whole population -- is the denominator (the
    #97 universe-mismatch defect these controls exist to fix). The universe bounds are
    read DIRECTLY off the matching ``kreis_attribute_control.REGISTRY`` entry's
    ``min_age`` / ``max_age`` fields (fix round 1, item 3; see
    :func:`_universe_registry_entries`), so a reader -- and a future REGISTRY change --
    cannot silently diverge from what is implemented here:

    - ``work_by_employment``: persons aged >= the REGISTRY entry's ``min_age`` (14 today),
      no upper bound. "Employed" is the ``employment_status``-class test
      (:data:`attributes.EMPLOYED_EMPLOYMENT_STATUS_CLASSES`), NEVER the ``employed``
      boolean attribute -- a DIFFERENT MiD variable the two do not agree on for every
      person; the control pins the former, and so does this report. (The module docstring
      says why no agreement RATE is quoted here: the measured one is not traceable to a
      committed source.)
    - ``education_0_5`` / ``education_6_17`` / ``education_18plus``: persons whose age
      falls in that REGISTRY entry's ``[min_age, max_age]`` band, INCLUSIVE on both
      bounds (``education_18plus`` has no upper bound).

    ``persons_kreis`` must carry ``person_id``, ``ars5``, ``employment_status`` and
    ``age``. ``trips`` schema detection mirrors :func:`realised_participation`'s (the
    eqasim ``following_purpose``/``preceding_purpose`` string pair); ``KeyError`` if
    neither schema is present at all, ``ValueError`` if ONLY the raw MiD ``W_ZWECK``
    schema is present (no silent fallback to an empty result, and no silent
    universe mismatch -- see :func:`_purpose_leg_person_ids`).

    Fallback-transparency instrumentation (fix round 1, item 2 -- MANDATORY, no silent
    degradation): logs a WARNING, with a count and examples, for (a) persons whose ``age``
    is missing/non-numeric (coerced to NaN by ``pd.to_numeric``) and therefore excluded
    from EVERY universe; (b) persons whose ``employment_status`` is outside the
    recognised :data:`attributes.EMPLOYMENT_STATUS_CATEGORIES` vocabulary and are
    therefore silently classified NON-employed by the ``isin`` employment test; (c) a
    purpose ("work"/"education") whose trip participants match NONE of
    ``persons_kreis``'s ``person_id`` values (the empty-join trap
    ``mid.participation.map_flag_from_plan_source`` also documents and defends against --
    typically a dtype mismatch); and (d) any control whose resulting universe is entirely
    EMPTY (0 persons) in the given frame. The per-purpose match rate is always logged at
    INFO for traceability, even when it is not zero.

    Returns one row per (``ars5``, ``control``, ``category``): columns ``ars5, control,
    category, realised_share, n_persons``, where ``n_persons`` is the size of THAT
    control's own universe in that Kreis (the denominator the share is actually computed
    over), NOT the Kreis's total person count -- a share computed over all persons would
    answer a different question and would be wrong. A Kreis with NO member of a control's
    universe (e.g. no 0-5-year-old) produces NO row for that (``ars5``, ``control``) pair,
    rather than a defaulted/zero share -- proof the age filter actually restricts the
    population rather than silently including out-of-band persons under a
    plausible-looking 0.0.

    HONESTY CAVEAT (mandatory, reproduce in any report built on this function): these
    targets STEER the raking (see the module docstring), so a good realised-vs-target fit
    here measures raking convergence, NOT independent agreement with reality.
    """
    required_persons_cols = ["person_id", "ars5", "employment_status", "age"]
    missing_persons_cols = [c for c in required_persons_cols if c not in persons_kreis.columns]
    if missing_persons_cols:
        raise KeyError(
            f"realised_universe_participation: persons_kreis is missing required column(s) "
            f"{missing_persons_cols} (has {list(persons_kreis.columns)}).")
    if "person_id" not in trips.columns:
        raise KeyError(
            f"realised_universe_participation: trips is missing required column 'person_id' "
            f"(has {list(trips.columns)}).")

    n_persons_total = len(persons_kreis)
    participants = _purpose_leg_person_ids(trips, ("work", "education"))

    # Item 2(c): the empty-join trap. A person_id dtype mismatch between trips and
    # persons_kreis makes isin() match nothing, silently zeroing every "has_<purpose>_leg"
    # flag -- mirrors mid.participation.map_flag_from_plan_source's own documented defense.
    persons_kreis_ids = set(persons_kreis["person_id"])
    for purpose, ids in participants.items():
        n_participants = len(ids)
        if n_participants == 0:
            continue
        n_matched = len(ids & persons_kreis_ids)
        if n_matched == 0:
            LOGGER.warning(
                "realised_universe_participation: trips carry %d distinct person(s) with a "
                "'%s' leg, but NONE of them match any person_id in persons_kreis (dtype "
                "mismatch? trips person_id dtype %s vs persons_kreis person_id dtype %s); "
                "the resulting universe flag will be all-zero for this purpose.",
                n_participants, purpose, trips["person_id"].dtype, persons_kreis["person_id"].dtype)
        else:
            LOGGER.info(
                "realised_universe_participation: '%s' leg -- %d/%d (%.1f%%) trip "
                "participant(s) matched a person_id in persons_kreis.",
                purpose, n_matched, n_participants, 100.0 * n_matched / n_participants)

    ars5 = persons_kreis["ars5"].to_numpy()
    ages = pd.to_numeric(persons_kreis["age"], errors="coerce").to_numpy()

    # Item 2(a): a non-numeric/missing age is coerced to NaN, which compares False to
    # every ">="/"<=" bound below and so silently excludes that person from EVERY
    # universe -- log the count rather than letting the universe quietly shrink.
    n_bad_age = int(pd.isna(ages).sum())
    if n_bad_age:
        LOGGER.warning(
            "realised_universe_participation: %d/%d (%.1f%%) persons have a non-numeric or "
            "missing 'age' value (coerced to NaN) and are therefore excluded from EVERY "
            "control's universe; check the age column's dtype/values upstream.",
            n_bad_age, n_persons_total, 100.0 * n_bad_age / max(n_persons_total, 1))

    # Item 2(b): an employment_status value outside the recognised vocabulary is silently
    # classified NON-employed by isin() below -- the very failure attributes.py's own
    # module-level guard raises on for the CONSTANT (EMPLOYED_EMPLOYMENT_STATUS_CLASSES
    # must be a subset of EMPLOYMENT_STATUS_CATEGORIES); this is the equivalent guard on
    # the DATA side.
    recognised_employment_status = persons_kreis["employment_status"].isin(EMPLOYMENT_STATUS_CATEGORIES)
    n_bad_employment_status = int((~recognised_employment_status).sum())
    if n_bad_employment_status:
        bad_examples = sorted(
            {str(v) for v in persons_kreis.loc[~recognised_employment_status, "employment_status"]})[:5]
        LOGGER.warning(
            "realised_universe_participation: %d/%d (%.1f%%) persons carry an "
            "'employment_status' value outside the recognised %s classes (examples: %s); "
            "they are silently classified NON-employed by the work_by_employment employment "
            "test -- check for a label/vocabulary mismatch upstream.",
            n_bad_employment_status, n_persons_total,
            100.0 * n_bad_employment_status / max(n_persons_total, 1),
            EMPLOYMENT_STATUS_CATEGORIES, bad_examples)

    employed = persons_kreis["employment_status"].isin(EMPLOYED_EMPLOYMENT_STATUS_CLASSES).to_numpy()
    has_work_leg = persons_kreis["person_id"].isin(participants["work"]).to_numpy()
    has_education_leg = persons_kreis["person_id"].isin(participants["education"]).to_numpy()

    def _warn_if_empty(control_name: str, mask: np.ndarray) -> None:
        # Item 2(d): a control whose universe comes out empty must WARN, not silently
        # return zero rows for that (ars5, control) pair with no signal at all.
        if not mask.any():
            LOGGER.warning(
                "realised_universe_participation: control '%s' universe is EMPTY (0/%d "
                "persons in the provided persons_kreis frame); check the 'age' (and, for "
                "work_by_employment, 'employment_status') column's values/dtype.",
                control_name, n_persons_total)

    frames = []

    # work_by_employment: universe = age in [min_age, max_age], read from the REGISTRY
    # entry itself (fix round 1, item 3) rather than a separately-imported constant.
    ctl = _WORK_BY_EMPLOYMENT_REGISTRY_ENTRY
    in_universe = ages >= ctl.min_age
    if ctl.max_age is not None:
        in_universe = in_universe & (ages <= ctl.max_age)
    _warn_if_empty(ctl.name, in_universe)
    categories = tuple(label for label, _ in ctl.categories)
    category = np.where(
        employed[in_universe],
        np.where(has_work_leg[in_universe], "employed_work", "employed_nowork"),
        np.where(has_work_leg[in_universe], "nonemployed_work", "nonemployed_nowork"))
    frames.append(_category_shares(ars5[in_universe], category, ctl.name, categories))

    # education_0_5 / education_6_17 / education_18plus: each its OWN age-band universe,
    # again read from its own REGISTRY entry; max_age None means no upper bound.
    for ctl in _EDUCATION_REGISTRY_ENTRIES:
        in_band = ages >= ctl.min_age
        if ctl.max_age is not None:
            in_band = in_band & (ages <= ctl.max_age)
        _warn_if_empty(ctl.name, in_band)
        categories = tuple(label for label, _ in ctl.categories)
        edu_label, noedu_label = categories
        category = np.where(has_education_leg[in_band], edu_label, noedu_label)
        frames.append(_category_shares(ars5[in_band], category, ctl.name, categories))

    return pd.concat(frames, ignore_index=True)[
        ["ars5", "control", "category", "realised_share", "n_persons"]]


def load_universe_targets(targets_dir: Path) -> pd.DataFrame:
    """Load the four committed participation-UNIVERSE per-Kreis targets as one tidy frame.

    The filename and category columns for each control are read DIRECTLY off the
    matching ``kreis_attribute_control.REGISTRY`` entry's ``target_csv_relpath`` /
    ``categories`` fields (fix round 1, item 3) -- the SAME entries
    :func:`realised_universe_participation` reads its universe bounds from -- rather
    than re-derived as ``f"target2026_{control}_by_kreis.csv"``, a second path that
    could silently drift from the REGISTRY's actual filename. ``target_csv_relpath`` is
    relative to the DATA root (e.g. ``"braunschweig/targets/target2026_..._by_kreis.csv"``)
    while ``targets_dir`` here is already that targets directory, so only the relpath's
    filename component is used.

    Returns tidy columns ``ars5, control, category, target_share``.

    Raises ``FileNotFoundError`` naming the missing path if a required target file is
    absent, and ``KeyError`` if a required column is missing from a target file that IS
    present (no silent fallback to a guessed column name) -- mirrors
    :func:`load_participation_targets` exactly.
    """
    targets_dir = Path(targets_dir)
    frames = []

    def _load_one(ctl) -> None:
        filename = Path(ctl.target_csv_relpath).name
        path = targets_dir / filename
        categories = tuple(label for label, _ in ctl.categories)
        if not path.exists():
            raise FileNotFoundError(
                f"load_universe_targets: required target file {path} is missing.")
        target = pd.read_csv(path, comment="#", dtype={"ars5": str})
        missing_cols = [c for c in ("ars5", *categories) if c not in target.columns]
        if missing_cols:
            raise KeyError(
                f"load_universe_targets: {path} is missing required column(s) "
                f"{missing_cols} (has {list(target.columns)}).")
        for category in categories:
            frames.append(pd.DataFrame({
                "ars5": target["ars5"],
                "control": ctl.name,
                "category": category,
                "target_share": target[category],
            }))

    _load_one(_WORK_BY_EMPLOYMENT_REGISTRY_ENTRY)
    for ctl in _EDUCATION_REGISTRY_ENTRIES:
        _load_one(ctl)

    return pd.concat(frames, ignore_index=True)


def universe_participation_fit(trips: pd.DataFrame, persons_kreis: pd.DataFrame,
                               targets_dir: Path) -> pd.DataFrame:
    """Join realised universe-control participation (interface 1) to the committed
    universe targets (interface 2).

    Returns ``ars5, control, category, realised_share, n_persons, target_share,
    abs_error``.

    ``n_persons`` -- the size of THAT control's own universe in that Kreis, carried
    through unchanged from :func:`realised_universe_participation` -- is what makes an
    ``abs_error`` interpretable: an ``education_0_5`` universe of a handful of children in
    one Kreis and one of several thousand in another produce equally large deviations for
    entirely different reasons, and the run's headline "worst abs_error" line
    (``run_population_validation``) reports exactly such a single cell. The Plan-B design
    pinned a six-column output; this seventh column is a deliberate, reviewed extension of
    it (final fix wave, item 2).

    Realised (``ars5``, ``control``, ``category``) cells with no matching target row are
    logged (warning, with examples) and dropped -- mirroring :func:`participation_fit`'s
    (and ``control_validation.evaluate_control``'s) out-of-vocabulary handling -- rather
    than silently coercing them to NaN or 0.

    See the module docstring for the mandatory honesty caveat: a good fit here reflects
    raking convergence toward the committed target, not independent agreement with
    reality.
    """
    realised = realised_universe_participation(trips, persons_kreis)
    targets = load_universe_targets(targets_dir)

    merged = realised.merge(targets, on=["ars5", "control", "category"], how="left", indicator=True)
    missing = merged[merged["_merge"] == "left_only"]
    if not missing.empty:
        examples = list(zip(missing["ars5"], missing["control"], missing["category"]))[:5]
        LOGGER.warning(
            "universe_participation_fit: %d realised (ars5, control, category) cell(s) have "
            "no matching target and are excluded from the fit; examples: %s",
            len(missing), examples)
    merged = merged[merged["_merge"] == "both"].drop(columns="_merge")
    merged["abs_error"] = (merged["realised_share"] - merged["target_share"]).abs()
    return merged[["ars5", "control", "category", "realised_share", "n_persons",
                   "target_share", "abs_error"]].reset_index(drop=True)


def donor_neff(persons: pd.DataFrame, donor_id_col: str) -> dict:
    """Effective sample size implied by donor duplication in a synthetic population.

    Each synthetic person is a copy of some donor identified by
    ``donor_id_col`` (e.g. the MiD source person id the population carries).
    Kish's design-effect formula for equal-weight cluster duplication gives:

        N_eff = N**2 / sum(copies_per_donor**2)

    where ``copies_per_donor`` is the value_counts of ``donor_id_col`` and
    ``N`` is the total number of persons. N_eff equals N iff every donor is
    used exactly once (no duplication); it shrinks as donors are reused more.

    Returns a dict with:

    - ``n``: total number of persons (int)
    - ``n_eff``: the effective sample size (float)
    - ``n_eff_fraction``: ``n_eff / n`` (float in (0, 1])
    - ``max_copies_over_median``: the most-duplicated donor's copy count
      divided by the median copy count across donors, a simple concentration
      diagnostic (float)

    Raises ``KeyError`` if ``donor_id_col`` is absent from ``persons`` (no
    silent fallback to an assumed column name), and ``ValueError`` if
    ``persons`` is empty (N_eff is undefined for zero persons).
    """
    if donor_id_col not in persons.columns:
        raise KeyError(
            f"donor_neff: donor id column {donor_id_col!r} is absent from the persons frame "
            f"(has {list(persons.columns)}); cannot compute the donor-duplication N_eff.")
    n = int(len(persons))
    if n == 0:
        raise ValueError("donor_neff: persons frame is empty; N_eff is undefined for zero persons.")

    copies_per_donor = persons[donor_id_col].value_counts()
    sum_squared_copies = float((copies_per_donor ** 2).sum())
    n_eff = (n ** 2) / sum_squared_copies

    return {
        "n": n,
        "n_eff": n_eff,
        "n_eff_fraction": n_eff / n,
        "max_copies_over_median": float(copies_per_donor.max()) / float(copies_per_donor.median()),
    }
