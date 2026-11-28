"""Decide per MiD diary owner whether a day that does not end at home stays open.

Today every synthetic day whose MiD diary ends away from home gets an invented
return-home trip. For persons who were on an overnight trip on the reporting day
(MiD ``P_STREISE``) the diary end is real: they sleep away from home. This module
is the single place that decides, from the MiD overnight answer and the diary facts
of ``braunschweig.popsim.diary_facts.compute_diary_facts``, whether the open end is
kept.

Assumptions (documented, none is a validated reference value):

- ``P_STREISE`` codes 2 and 3 mean an overnight trip on the reporting day; codes 1
  and 609 mean none; codes 9 and 610 are "no usable answer" and close the day
  (counted and logged); code 402 means a child who was not asked.
- A child (402) inherits the flag from any member of the same MiD household, because
  all household members share the reporting day.
- A middle day of a trip (the diary starts away and its first leg does not go home)
  stays closed: the model rebases such a day at the synthetic home anyway.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Union

import numpy as np
import pandas as pd

from braunschweig.popsim.plan_validation import KEEPS_OPEN_END_COLUMN

logger = logging.getLogger(__name__)

OVERNIGHT_TRIP_CODES = frozenset({2, 3})
CHILD_NOT_ASKED_CODE = 402
NOT_OVERNIGHT_CODES = frozenset({1, 609})
NO_USABLE_ANSWER_CODES = frozenset({9, 610})
OVERNIGHT_TRIP_COLUMN = "overnight_trip"
#: ``DataFrame.attrs`` / ``Series.attrs`` keys. The answers frame carries the warn threshold from
#: ``load_overnight_answers`` to ``overnight_trip_flags``; the flags Series carries, per person, why
#: it is (not) flagged (one of the ``STATUS_*`` values) and the threshold on to ``_decide``.
MAX_NO_ANSWER_SHARE_ATTR = "overnight_max_no_answer_share"
ANSWER_STATUS_ATTR = "overnight_answer_status"
#: Summary of one decision, set on the Series returned by ``keeps_open_end`` / ``_decide``.
DECISION_SUMMARY_ATTR = "overnight_decision_summary"
STATUS_OWN = "own"
STATUS_INHERITED = "inherited"
STATUS_NO_ANSWER = "no_usable_answer"
STATUS_CHILD_UNFLAGGED = "child_without_flagged_member"
STATUS_NOT_OVERNIGHT = "not_overnight"

_KNOWN_CODES = (
    OVERNIGHT_TRIP_CODES
    | NOT_OVERNIGHT_CODES
    | NO_USABLE_ANSWER_CODES
    | {CHILD_NOT_ASKED_CODE}
)
_FACT_COLUMNS = ("ends_at_home", "n_direct_legs", "first_so1", "starts_arriving_home")
_SRC_FACT_COLUMNS = tuple(f"src_{c}" for c in _FACT_COLUMNS)
_FIRST_SO1_AWAY_START_CODE = 2  # MiD start-of-day code: diary starts away from home


def load_overnight_answers(mid_dir: Union[str, Path], *, max_no_answer_share: float) -> pd.DataFrame:
    """Read ``H_ID``, ``P_ID`` and ``P_STREISE`` (int) from ``MiD2023_Personen.csv``.

    ``max_no_answer_share`` (config key ``overnight_open_end_max_no_answer_share``, a share in
    [0, 1]) is the warn threshold for the share of open-end diaries whose owner gave no usable
    answer; it travels with the frame in ``attrs`` to :func:`overnight_trip_flags`. It has no
    default here: the single default lives in ``braunschweig.popsim.stage.config_keys``.
    """
    _check_share(max_no_answer_share)
    # Imported here, not at module level: the mid package is not part of the decision itself,
    # and a module-level import would pull it into every stage closure that reaches this module.
    from braunschweig.popsim.mid.csv_format import detect_csv_separator
    persons_path = Path(mid_dir) / "MiD2023_Personen.csv"
    if not persons_path.is_file():
        raise FileNotFoundError(f"[popsim.overnight_open_end] MiD persons file not found: {persons_path}")
    columns = ["H_ID", "P_ID", "P_STREISE"]
    header = pd.read_csv(persons_path, sep=detect_csv_separator(persons_path), nrows=0).columns
    missing = [c for c in columns if c not in header]
    if missing:
        raise KeyError(f"[popsim.overnight_open_end] {persons_path.name} lacks required column(s) {missing}")
    answers = pd.read_csv(persons_path, usecols=columns, sep=detect_csv_separator(persons_path))
    if answers[columns].isna().any().any():
        raise ValueError(f"[popsim.overnight_open_end] {persons_path.name} has missing values in {columns}")
    out = answers[columns].astype("int64")
    out.attrs[MAX_NO_ANSWER_SHARE_ATTR] = float(max_no_answer_share)
    return out


def overnight_trip_flags(answers: pd.DataFrame, *, max_no_answer_share: float | None = None) -> pd.Series:
    """Per-person overnight flag, bool, MultiIndex ``(H_ID, P_ID)``, name ``overnight_trip``.

    The Series carries in ``attrs`` the per-person answer status and the warn threshold
    (``max_no_answer_share``, or the one ``load_overnight_answers`` put on ``answers``) that the
    decision functions use to count and warn on the OPEN-END universe. A missing threshold raises:
    a silent default would hide a mis-wired config.
    """
    missing = [c for c in ("H_ID", "P_ID", "P_STREISE") if c not in answers.columns]
    if missing:
        raise KeyError(f"overnight_trip_flags: answers frame lacks required column(s) {missing}")
    duplicated = answers.duplicated(subset=["H_ID", "P_ID"], keep=False)
    if duplicated.any():
        raise ValueError(
            f"[popsim.overnight_open_end] {int(duplicated.sum())} answer rows share a duplicated (H_ID, P_ID) key"
        )
    if max_no_answer_share is None:
        max_no_answer_share = answers.attrs.get(MAX_NO_ANSWER_SHARE_ATTR)
    if max_no_answer_share is None:
        raise ValueError(
            "[popsim.overnight_open_end] no warn threshold for the no-usable-answer share: pass "
            "max_no_answer_share or build the answers with load_overnight_answers(max_no_answer_share=...)"
        )
    _check_share(max_no_answer_share)
    code = answers["P_STREISE"].astype("int64")
    unknown = code[~code.isin(_KNOWN_CODES)]
    if len(unknown):
        counts = unknown.value_counts().sort_index()
        detail = ", ".join(f"{int(k)}: {int(v)}" for k, v in counts.items())
        raise ValueError(
            f"[popsim.overnight_open_end] unknown P_STREISE code(s) in {len(unknown)} rows "
            f"(code: count) {detail}; known codes are {sorted(_KNOWN_CODES)}"
        )
    own = code.isin(OVERNIGHT_TRIP_CODES)
    is_child = code == CHILD_NOT_ASKED_CODE
    household_flagged = own.groupby(answers["H_ID"]).transform("any")
    inherited = is_child & household_flagged
    flag = own | inherited
    n_no_answer = int(code.isin(NO_USABLE_ANSWER_CODES).sum())
    n_child_unflagged = int((is_child & ~household_flagged).sum())
    total = len(answers)
    logger.info(
        "[popsim.overnight_open_end] answers %d persons: own overnight %d (%.2f%%), inherited %d (%.2f%%), "
        "no usable answer %d (%.2f%%), child without flagged member %d (%.2f%%)",
        total, int(own.sum()), _pct(own.sum(), total), int(inherited.sum()), _pct(inherited.sum(), total),
        n_no_answer, _pct(n_no_answer, total), n_child_unflagged, _pct(n_child_unflagged, total),
    )
    index = pd.MultiIndex.from_arrays([answers["H_ID"].to_numpy(), answers["P_ID"].to_numpy()],
                                      names=["H_ID", "P_ID"])
    status = np.select(
        [own.to_numpy(), inherited.to_numpy(), code.isin(NO_USABLE_ANSWER_CODES).to_numpy(),
         (is_child & ~household_flagged).to_numpy()],
        [STATUS_OWN, STATUS_INHERITED, STATUS_NO_ANSWER, STATUS_CHILD_UNFLAGGED],
        default=STATUS_NOT_OVERNIGHT,
    )
    out = pd.Series(flag.to_numpy(), index=index, name=OVERNIGHT_TRIP_COLUMN)
    out.attrs[ANSWER_STATUS_ATTR] = pd.Series(status, index=index, name="answer_status")
    out.attrs[MAX_NO_ANSWER_SHARE_ATTR] = float(max_no_answer_share)
    return out


def keeps_open_end(facts: pd.DataFrame, overnight: pd.Series) -> pd.Series:
    """Decide per diary owner whether the open day end is kept.

    ``facts`` is indexed ``(H_ID, P_ID)`` (output of ``compute_diary_facts``). A fact row
    without an overnight answer raises ``KeyError``; an answer without facts is unused.
    """
    missing_cols = [c for c in _FACT_COLUMNS if c not in facts.columns]
    if missing_cols:
        raise KeyError(f"keeps_open_end: facts frame lacks required column(s) {missing_cols}")
    _reject_nulls(facts, _FACT_COLUMNS)
    absent = facts.index.difference(overnight.index)
    if len(absent):
        raise KeyError(
            f"[popsim.overnight_open_end] {len(absent)} of {len(facts)} diary-fact rows have no overnight "
            f"answer by (H_ID, P_ID), e.g. {list(absent[:3])}"
        )
    flag = overnight.reindex(facts.index).astype(bool).to_numpy()
    return _decide(
        facts["ends_at_home"].to_numpy(dtype=bool), facts["n_direct_legs"].to_numpy(),
        facts["first_so1"].to_numpy(), facts["starts_arriving_home"].to_numpy(dtype=bool),
        flag, facts.index, "diaries", status=_aligned_status(overnight, facts.index),
        max_no_answer_share=overnight.attrs.get(MAX_NO_ANSWER_SHARE_ATTR),
    )


def attach_src_keeps_open_end(
    persons: pd.DataFrame,
    overnight: pd.Series,
    *,
    source_household_id: str = "source_H_ID",
    source_person_id: str = "source_P_ID",
) -> pd.DataFrame:
    """Return a copy of ``persons`` with ``src_keeps_open_end`` from the ``src_*`` diary facts."""
    required = [source_household_id, source_person_id, *_SRC_FACT_COLUMNS]
    missing = [c for c in required if c not in persons.columns]
    if missing:
        raise KeyError(f"attach_src_keeps_open_end: persons frame lacks required column(s) {missing}")
    _reject_nulls(persons, _SRC_FACT_COLUMNS)
    keys = pd.MultiIndex.from_arrays(
        [persons[source_household_id].to_numpy(), persons[source_person_id].to_numpy()],
        names=["H_ID", "P_ID"],
    )
    absent = keys.difference(overnight.index)
    if len(absent):
        raise KeyError(
            f"[popsim.overnight_open_end] {len(absent)} distinct plan sources have no overnight answer "
            f"by (H_ID, P_ID), e.g. {list(absent[:3])}; source key dtypes "
            f"({persons[source_household_id].dtype}, {persons[source_person_id].dtype}), "
            f"answer index dtypes {[str(d) for d in overnight.index.dtypes]}"
        )
    flag = overnight.reindex(keys).astype(bool).to_numpy()
    decision = _decide(
        persons["src_ends_at_home"].to_numpy(dtype=bool), persons["src_n_direct_legs"].to_numpy(),
        persons["src_first_so1"].to_numpy(), persons["src_starts_arriving_home"].to_numpy(dtype=bool),
        flag, persons.index, "persons", note=" (donor rows, not distinct plan sources)",
        status=_aligned_status(overnight, keys),
        max_no_answer_share=overnight.attrs.get(MAX_NO_ANSWER_SHARE_ATTR),
    )
    out = persons.copy()
    out["src_" + KEEPS_OPEN_END_COLUMN] = decision.to_numpy()
    return out


def _decide(ends_at_home, n_direct_legs, first_so1, starts_arriving_home, flag, index, unit, note="",
            status=None, max_no_answer_share=None):
    """Shared rule: ``not ends_at_home and n_direct_legs > 0 and overnight and not middle_day``.

    ``status`` (per-row ``STATUS_*`` values, from the flags Series) lets the OPEN-END universe be
    split into own-flag / inherited / no usable answer / child without flagged member; it is None
    only for a flags Series not built by :func:`overnight_trip_flags` (then the split is reported
    as unavailable and no share warning can fire). This function runs only when the feature is
    ON, so "nothing kept open" is always a warning (fallback transparency: a broken answer join
    would otherwise silently close every day again).
    """
    open_end = ~ends_at_home & (n_direct_legs > 0)
    middle_day = (first_so1 == _FIRST_SO1_AWAY_START_CODE) & ~starts_arriving_home
    keep = open_end & flag & ~middle_day
    n_open = int(open_end.sum())
    n_kept = int(keep.sum())
    n_middle = int((open_end & flag & middle_day).sum())
    logger.info(
        "[popsim.overnight_open_end] decisions over %d open-end %s%s: kept open %d (%.2f%%), closed %d (%.2f%%), "
        "of which middle day %d (%.2f%%; counted among the overnight-flagged open-end %s)",
        n_open, unit, note, n_kept, _pct(n_kept, n_open), n_open - n_kept, _pct(n_open - n_kept, n_open),
        n_middle, _pct(n_middle, n_open), unit,
    )
    summary = {"n_open_end": n_open, "n_kept": n_kept}
    if status is None:
        logger.info(
            "[popsim.overnight_open_end] answer status unavailable for these %s (the overnight flags were "
            "not built by overnight_trip_flags): own/inherited split and no-answer share are not reported", unit)
    else:
        status = np.asarray(status)
        n_kept_own = int((keep & (status == STATUS_OWN)).sum())
        n_kept_inherited = int((keep & (status == STATUS_INHERITED)).sum())
        n_no_answer = int((open_end & (status == STATUS_NO_ANSWER)).sum())
        n_child_unflagged = int((open_end & (status == STATUS_CHILD_UNFLAGGED)).sum())
        summary.update(n_kept_own=n_kept_own, n_kept_inherited=n_kept_inherited,
                       n_no_answer=n_no_answer, n_child_without_flagged_member=n_child_unflagged)
        logger.info(
            "[popsim.overnight_open_end] open-end %s: kept open own %d (%.2f%%), inherited from household %d "
            "(%.2f%%); no usable answer %d (%.2f%%, closed); child without flagged household member %d "
            "(%.2f%%, closed)",
            unit, n_kept_own, _pct(n_kept_own, n_open), n_kept_inherited, _pct(n_kept_inherited, n_open),
            n_no_answer, _pct(n_no_answer, n_open), n_child_unflagged, _pct(n_child_unflagged, n_open),
        )
        if max_no_answer_share is not None and n_open and n_no_answer / n_open > max_no_answer_share:
            logger.warning(
                "[popsim.overnight_open_end] %d of %d open-end %s (%.1f%%) have no usable P_STREISE answer, above "
                "the %.1f%% threshold (overnight_open_end_max_no_answer_share): the overnight answer is probably "
                "mis-read or mis-joined and these days are closed by default",
                n_no_answer, n_open, unit, _pct(n_no_answer, n_open), 100.0 * max_no_answer_share,
            )
    if n_kept == 0:
        logger.warning(
            "[popsim.overnight_open_end] keep_overnight_open_ends is ON but 0 of %d open-end %s are kept open: "
            "every day is closed as before; check the P_STREISE answers and the (H_ID, P_ID) join",
            n_open, unit,
        )
    out = pd.Series(keep, index=index, name=KEEPS_OPEN_END_COLUMN)
    out.attrs[DECISION_SUMMARY_ATTR] = summary
    return out


def _aligned_status(overnight, keys):
    """Per-row answer status of ``keys`` (an index), or None for flags not built by ``overnight_trip_flags``."""
    status = overnight.attrs.get(ANSWER_STATUS_ATTR)
    return None if status is None else status.reindex(keys).to_numpy()


def _check_share(value) -> None:
    if not 0.0 <= float(value) <= 1.0:
        raise ValueError(
            f"[popsim.overnight_open_end] overnight_open_end_max_no_answer_share must be a share in [0, 1], got {value!r}"
        )


def _reject_nulls(frame, columns):
    for column in columns:
        n_null = int(frame[column].isna().sum())
        if n_null:
            raise ValueError(
                f"[popsim.overnight_open_end] column {column!r} has {n_null} null value(s) in {len(frame)} rows"
            )


def _pct(part, whole) -> float:
    return 100.0 * float(part) / float(whole) if whole else 0.0
