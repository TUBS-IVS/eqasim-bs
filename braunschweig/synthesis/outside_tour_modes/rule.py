"""The plausibility rule and the seeded substitute draw (eqasim-bs#453, ADR-0145 decision 3).

Per leg of an outside tour: purpose group (precedence: a leg touching an ``outside`` activity -> ``all``
with the top band; a leg into home -> the group of its origin purpose; else the group of its destination
purpose), band of its realised straight-line distance, and the donor mode is implausible when its SrV share
in that cell is below ``min_share``. Per tour with at least one implausible leg: ONE substitute drawn from
the cell of the tour's longest implausible leg, among the modes with share >= ``min_share`` that the person
may use, weighted by their shares; applied to every leg of the tour (same mode across the tour, so
``VehicleContinuity`` holds; plausible legs are swept along and counted).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from braunschweig.calibration.srv_mode_by_distance_band_table import ALL_GROUP
from braunschweig.synthesis.outside_tour_modes.config_keys import ALWAYS_AVAILABLE_MODES, HOME_PURPOSE, OUTSIDE_PURPOSE
from braunschweig.synthesis.outside_tour_modes.srv_table import ModeShareTable

GROUP_BY_PURPOSE = {"work": "work", "education": "education"}
SUBSTITUTE_COLUMNS = ["person_id", "tour", "cell", "donor_modes", "substitute", "n_legs", "n_legs_implausible",
                      "n_candidates", "n_removed_by_availability", "removed_modes"]
AVAILABILITY_MODE_COLUMNS = {"car": "can_car", "bicycle": "can_bicycle", "car_passenger": "can_car_passenger"}


def purpose_groups(trips: pd.DataFrame) -> pd.Series:
    """Purpose group per leg: portal legs -> ``all``; legs into home -> origin purpose; else destination purpose."""
    preceding = trips["preceding_purpose"].astype(str)
    following = trips["following_purpose"].astype(str)
    portal = preceding.eq(OUTSIDE_PURPOSE) | following.eq(OUTSIDE_PURPOSE)
    home_bound = following.eq(HOME_PURPOSE)
    by_destination = following.map(GROUP_BY_PURPOSE).fillna("other")
    by_origin = preceding.map(GROUP_BY_PURPOSE).fillna("other")
    groups = by_destination.where(~home_bound, by_origin)
    return groups.where(~portal, ALL_GROUP)


def leg_cells(groups: pd.Series, leg_km: np.ndarray, table: ModeShareTable, mask=None) -> list:
    """Per leg ``(group, band label, {mode: share})``; ``None`` where ``mask`` is False (no lookup needed)."""
    needed = np.ones(len(groups), dtype=bool) if mask is None else np.asarray(mask, dtype=bool)
    cells = []
    for group, km, wanted in zip(groups.to_numpy(), np.asarray(leg_km, dtype=float), needed):
        if not wanted:
            cells.append(None)
            continue
        label, shares = table.top_cell(ALL_GROUP) if group == ALL_GROUP else table.cell(group, float(km))
        cells.append((group, label, shares))
    return cells


def implausible_legs(trips: pd.DataFrame, cells: list, min_share: float) -> np.ndarray:
    """True where the donor mode's share in the leg's cell is below ``min_share``; False where no cell was looked up."""
    modes = trips["mode"].astype(str).to_numpy()
    return np.array([cell is not None and cell[2].get(mode, 0.0) < min_share for mode, cell in zip(modes, cells)],
                    dtype=bool)


def allowed_modes(availability: pd.DataFrame) -> dict:
    """Per person: the modes the person may use (always-available ones plus the availability flags)."""
    allowed = {}
    for person_id, row in availability.iterrows():
        modes = set(ALWAYS_AVAILABLE_MODES)
        modes.update(mode for mode, column in AVAILABILITY_MODE_COLUMNS.items() if bool(row[column]))
        allowed[person_id] = frozenset(modes)
    return allowed


def tour_substitutes(trips: pd.DataFrame, tour_ids: np.ndarray, outside: np.ndarray, implausible: np.ndarray,
                     leg_km: np.ndarray, cells: list, allowed: dict, min_share: float,
                     rng: np.random.RandomState) -> pd.DataFrame:
    """One substitute per outside tour with an implausible leg, in person_id, tour order.

    Only outside tours with at least one implausible leg are iterated (a vectorised pre-filter), so the Python
    loop runs over the tours that are re-moded, not over every tour of the population. An empty candidate set
    raises (no silent fallback to an always-available mode).
    """
    frame = pd.DataFrame({"person_id": trips["person_id"].to_numpy(), "tour": np.asarray(tour_ids),
                          "outside": np.asarray(outside, dtype=bool), "implausible": np.asarray(implausible, dtype=bool),
                          "km": np.asarray(leg_km, dtype=float), "mode": trips["mode"].astype(str).to_numpy(),
                          "position": np.arange(len(trips))})
    frame = frame[frame["outside"]]
    tour_has_bad_leg = frame.groupby(["person_id", "tour"], sort=False)["implausible"].transform("any")
    frame = frame[tour_has_bad_leg.to_numpy(dtype=bool)]
    rows = []
    for (person_id, tour), legs in frame.groupby(["person_id", "tour"], sort=True):
        bad = legs[legs["implausible"]]
        longest = bad.sort_values(["km", "position"], ascending=[False, True]).iloc[0]
        group, label, shares = cells[int(longest["position"])]
        plausible = ModeShareTable.plausible_modes(shares, min_share)
        person_allowed = allowed.get(person_id)
        if person_allowed is None:
            raise ValueError(f"[outside_tour_modes] person_id {person_id} has no availability row")
        candidates = tuple(mode for mode in plausible if mode in person_allowed)
        if not candidates:
            raise ValueError(f"[outside_tour_modes] no substitute candidate for person_id {person_id}, tour {tour}: "
                             f"plausible {plausible} in cell {group}/{label}, allowed {sorted(person_allowed)}")
        weights = np.array([shares[mode] for mode in candidates], dtype=float)
        substitute = str(rng.choice(np.array(candidates, dtype=object), p=weights / weights.sum()))
        removed = tuple(mode for mode in plausible if mode not in person_allowed)
        rows.append({"person_id": person_id, "tour": int(tour), "cell": f"{group}/{label}",
                     "donor_modes": ",".join(sorted(set(legs["mode"]))), "substitute": substitute,
                     "n_legs": int(len(legs)), "n_legs_implausible": int(len(bad)), "n_candidates": len(candidates),
                     "n_removed_by_availability": len(removed), "removed_modes": ",".join(removed)})
    return pd.DataFrame(rows, columns=SUBSTITUTE_COLUMNS)


def apply_tour_modes(trips: pd.DataFrame, tour_ids: np.ndarray, substitutes: pd.DataFrame,
                     implausible: np.ndarray) -> tuple:
    """A copy of ``trips`` with the substitute on every leg of each listed tour.

    Returns ``(frame, n_remoded, n_swept)``: legs whose mode changed, and affected legs whose donor mode was
    plausible (swept along for the one-mode-per-tour rule; counted, never hidden).
    """
    out = trips.copy()
    if substitutes.empty:
        return out, 0, 0
    key = pd.MultiIndex.from_arrays([trips["person_id"].to_numpy(), np.asarray(tour_ids)])
    lookup = substitutes.set_index(["person_id", "tour"])["substitute"]
    new_mode = lookup.reindex(key).to_numpy(dtype=object)
    affected = pd.notna(new_mode)
    changed = affected & (out["mode"].astype(str).to_numpy() != new_mode)
    out.loc[affected, "mode"] = new_mode[affected]
    n_swept = int((affected & ~np.asarray(implausible, dtype=bool)).sum())
    return out, int(changed.sum()), n_swept
