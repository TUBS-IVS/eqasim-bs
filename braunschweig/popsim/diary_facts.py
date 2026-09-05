"""Per-donor-person facts about a MiD diary, derived ONCE from the Wege table.

Used by three consumers (spec 2026-09-05-plan-structure-fix-design.md): the diary plan match
(is the plan source realisable?), the trip_class seed (does the realised day need a synthetic
return-home trip?) and the rbW person attributes. Pure pandas; no synpp.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

HOME_ZWECK = frozenset({8, 9})
REQUIRED_WEGE_COLUMNS = ("W_ZWECK", "W_RBW", "W_SO1", "wegkm_imp")
#: MiD wegkm_imp codes >= this value are missing-value codes (9994 "unplausibel", 9999 ...); they
#: are counted as 0 km and reported.
WEGKM_CODE_MIN = 9994.0
FACT_COLUMNS = ("n_direct_legs", "n_rbw_legs", "rbw_distance_km", "first_so1",
                "first_direct_zweck", "last_direct_zweck", "ends_at_home", "starts_arriving_home")
_FILL = {"n_direct_legs": 0, "n_rbw_legs": 0, "rbw_distance_km": 0.0, "first_so1": -1,
         "first_direct_zweck": -1, "last_direct_zweck": -1, "ends_at_home": False,
         "starts_arriving_home": False}


def compute_diary_facts(wege, *, household_id="H_ID", person_id="P_ID", trip_id="W_ID"):
    """Derive per-person diary facts from a MiD Wege (trip) table.

    Args:
        wege: MiD Wege rows for any number of persons; must carry ``household_id``,
            ``person_id``, ``trip_id`` and ``REQUIRED_WEGE_COLUMNS``.
        household_id: Household id column name (default ``"H_ID"``).
        person_id: Person id column name (default ``"P_ID"``).
        trip_id: Trip id column name (default ``"W_ID"``); used only to order legs
            within a person so "first"/"last" are diary-chronological.

    Returns:
        A frame indexed by ``(household_id, person_id)`` for every person with
        >= 1 Wege row, columns ``FACT_COLUMNS``. See the module docstring's
        consumers for how these facts are used downstream.

    Raises:
        KeyError: naming the missing column(s) when any required column is absent.
    """
    missing = [c for c in (household_id, person_id, trip_id) + REQUIRED_WEGE_COLUMNS if c not in wege.columns]
    if missing:
        raise KeyError(f"compute_diary_facts: Wege frame lacks required column(s) {missing}")
    keys = [household_id, person_id]
    w = wege[keys + [trip_id] + list(REQUIRED_WEGE_COLUMNS)].sort_values(keys + [trip_id])
    is_rbw = w["W_RBW"] == 1
    km = pd.to_numeric(w["wegkm_imp"], errors="coerce")
    coded = km.isna() | (km >= WEGKM_CODE_MIN)
    n_coded_rbw = int((coded & is_rbw).sum())
    km = km.where(~coded, 0.0)
    rbw = w[is_rbw].assign(km=km[is_rbw])
    direct = w[~is_rbw]
    g = direct.groupby(keys, sort=True)
    facts = pd.DataFrame({
        "n_direct_legs": g.size(),
        "first_so1": g["W_SO1"].first().astype(int),
        "first_direct_zweck": g["W_ZWECK"].first().astype(int),
        "last_direct_zweck": g["W_ZWECK"].last().astype(int),
    })
    rb = rbw.groupby(keys, sort=True).agg(n_rbw_legs=("km", "size"), rbw_distance_km=("km", "sum"))
    facts = facts.join(rb, how="outer")
    for col, value in _FILL.items():
        if col in facts.columns:
            facts[col] = facts[col].fillna(value)
        else:
            facts[col] = value
    facts["n_direct_legs"] = facts["n_direct_legs"].astype(int)
    facts["n_rbw_legs"] = facts["n_rbw_legs"].astype(int)
    facts["first_so1"] = facts["first_so1"].astype(int)
    facts["first_direct_zweck"] = facts["first_direct_zweck"].astype(int)
    facts["last_direct_zweck"] = facts["last_direct_zweck"].astype(int)
    facts["ends_at_home"] = facts["last_direct_zweck"].isin(HOME_ZWECK) & (facts["n_direct_legs"] > 0)
    facts["starts_arriving_home"] = (facts["first_so1"] == 2) & facts["first_direct_zweck"].isin(HOME_ZWECK)
    facts = facts[list(FACT_COLUMNS)]
    n = len(facts)
    logger.info(
        "[diary_facts] %d donor persons with Wege: %d (%.1f%%) carry rbW legs (%d legs, %d coded km set to 0), "
        "%d (%.1f%%) do not end at home, %d (%.1f%%) start by arriving home, %d (%.1f%%) have only rbW legs",
        n, int((facts["n_rbw_legs"] > 0).sum()), 100.0 * (facts["n_rbw_legs"] > 0).mean(),
        int(facts["n_rbw_legs"].sum()), n_coded_rbw,
        int((~facts["ends_at_home"]).sum()), 100.0 * (~facts["ends_at_home"]).mean(),
        int(facts["starts_arriving_home"].sum()), 100.0 * facts["starts_arriving_home"].mean(),
        int((facts["n_direct_legs"] == 0).sum()), 100.0 * (facts["n_direct_legs"] == 0).mean())
    return facts


def attach_plan_source_facts(persons, facts, *, source_household_id="source_H_ID",
                             source_person_id="source_P_ID"):
    """Left-join diary facts onto a persons frame by its PLAN SOURCE keys.

    Unlike a join on the person's own ``(H_ID, P_ID)``, this follows
    ``source_household_id`` / ``source_person_id`` -- the donor a synthetic (or
    mirror-filled) person's plan is realised from -- so a filler person inherits
    its mirror donor's diary facts, matching the completion contract documented
    in ``mid/donor.py``.

    Args:
        persons: Synthetic/donor persons frame; must carry ``source_household_id``
            and ``source_person_id``.
        facts: Output of :func:`compute_diary_facts`.
        source_household_id: Column naming the plan-source household id
            (default ``"source_H_ID"``).
        source_person_id: Column naming the plan-source person id
            (default ``"source_P_ID"``).

    Returns:
        A copy of ``persons`` with columns ``src_<FACT_COLUMNS>`` attached.
        Persons whose source has no Wege row get the ``_FILL`` defaults
        (``n_* = 0``, ``rbw_distance_km = 0.0``, ``first_so1 = -1``,
        ``*_zweck = -1``, ``ends_at_home = False``, ``starts_arriving_home = False``).

    Raises:
        KeyError: naming the missing column when ``source_household_id`` or
            ``source_person_id`` is absent from ``persons``.
    """
    for col in (source_household_id, source_person_id):
        if col not in persons.columns:
            raise KeyError(f"attach_plan_source_facts: persons frame lacks {col!r}")
    idx = pd.MultiIndex.from_arrays([persons[source_household_id], persons[source_person_id]])
    aligned = facts.reindex(idx)
    out = persons.copy()
    for col in FACT_COLUMNS:
        values = aligned[col].to_numpy() if col in aligned.columns else np.full(len(out), _FILL[col])
        series = pd.Series(values, index=out.index)
        fill = _FILL[col]
        series = series.fillna(fill)
        if isinstance(fill, bool):
            series = series.astype(bool)
        elif isinstance(fill, int):
            series = series.astype(int)
        else:
            series = series.astype(float)
        out[f"src_{col}"] = series
    n_no_wege = int((out["src_n_direct_legs"] + out["src_n_rbw_legs"] == 0).sum())
    logger.info("[diary_facts] attached plan-source facts to %d persons; %d (%.2f%%) sources have no Wege row",
                len(out), n_no_wege, 100.0 * n_no_wege / max(len(out), 1))
    return out
