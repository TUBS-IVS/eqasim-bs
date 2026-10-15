"""The plausibility rule and the seeded substitute draw (eqasim-bs#453, ADR-0145 decision 3).

Per leg of an outside tour: purpose group (precedence: a leg touching an ``outside`` activity -> ``all``
with the top band; a leg into home -> the group of its origin purpose; else the group of its destination
purpose), band of its realised straight-line distance, and the donor mode is implausible when its SrV share
in that cell is below ``min_share``. Per tour with at least one implausible leg: ONE substitute, applied to
every leg of the tour (same mode across the tour, so ``VehicleContinuity`` holds; plausible legs are swept
along and counted). The candidates are the modes that

* the person may use (availability),
* are plausible (share >= ``min_share``) in the cell of EVERY leg of the tour (ADR-0145, final-review fix R16),
* keep the mode class of the portal gate of the tour when it contains a portal leg: a pt stay sits at a rail
  exit station, every other mode at a road gate (ADR-0141, ADR-0145 R17).

The draw weights are the shares of the cell of the longest implausible leg of the tour. When no mode survives
all three conditions the plausibility condition is relaxed to the cell of the LONGEST leg of the tour (counted
per tour in ``relaxed_to_longest_leg``, never silent); when that is empty too the draw raises.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from braunschweig.calibration.srv_mode_by_distance_band_table import ALL_GROUP, MODES
from braunschweig.synthesis.outside_tour_modes.config_keys import ALWAYS_AVAILABLE_MODES, HOME_PURPOSE, OUTSIDE_PURPOSE
from braunschweig.synthesis.outside_tour_modes.srv_table import ModeShareTable

GROUP_BY_PURPOSE = {"work": "work", "education": "education"}
SUBSTITUTE_COLUMNS = ["person_id", "tour", "cell", "donor_modes", "substitute", "n_legs", "n_legs_implausible",
                      "n_candidates", "n_removed_by_availability", "removed_modes", "relaxed_to_longest_leg",
                      "portal_tour", "portal_class", "n_removed_by_portal_class"]
#: ``portal_class`` values: no portal leg in the tour, a rail gate (pt stay) or a road gate (every other mode).
PORTAL_CLASS_NONE = ""
PORTAL_CLASS_RAIL = "pt"
PORTAL_CLASS_ROAD = "road"
RAIL_GATE_MODE = "pt"
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


def require_plain_mode_column(trips: pd.DataFrame) -> None:
    """Fail early on a categorical ``mode`` column: assigning a substitute outside its categories fails or
    silently adds categories, so the caller must convert it to plain strings upstream (no silent conversion)."""
    dtype = trips["mode"].dtype
    if isinstance(dtype, pd.CategoricalDtype):
        raise ValueError(f"[outside_tour_modes] trips['mode'] has the categorical dtype {dtype}; convert it to plain "
                         "strings upstream, the substitute modes are assigned as strings")


def _portal_class(legs: pd.DataFrame, person_id, tour) -> str:
    """Mode class of the portal gate of the tour: rail (pt), road (any other mode) or none; raises on a mix."""
    portal_modes = set(legs.loc[legs["portal"], "mode"])
    if not portal_modes:
        return PORTAL_CLASS_NONE
    classes = {PORTAL_CLASS_RAIL if mode == RAIL_GATE_MODE else PORTAL_CLASS_ROAD for mode in portal_modes}
    if len(classes) > 1:
        raise ValueError(f"[outside_tour_modes] person_id {person_id}, tour {tour} has portal legs at both gate "
                         f"classes (portal leg modes {sorted(portal_modes)}); one mode per tour cannot serve a rail "
                         "gate and a road gate")
    return classes.pop()


def _admissible(pool: tuple, person_allowed: frozenset, portal_class: str) -> tuple:
    """``(candidates, modes removed by availability, number removed by the portal class)`` from ``pool``."""
    available = tuple(mode for mode in pool if mode in person_allowed)
    removed_by_availability = tuple(mode for mode in pool if mode not in person_allowed)
    if portal_class == PORTAL_CLASS_NONE:
        return available, removed_by_availability, 0
    wants_rail = portal_class == PORTAL_CLASS_RAIL
    candidates = tuple(mode for mode in available if (mode == RAIL_GATE_MODE) == wants_rail)
    return candidates, removed_by_availability, len(available) - len(candidates)


def tour_substitutes(trips: pd.DataFrame, tour_ids: np.ndarray, outside: np.ndarray, implausible: np.ndarray,
                     leg_km: np.ndarray, cells: list, allowed: dict, min_share: float,
                     rng: np.random.RandomState, portal_leg: np.ndarray | None = None) -> pd.DataFrame:
    """One substitute per outside tour with an implausible leg, in person_id, tour order.

    Candidates: modes the person may use that are plausible in the cell of every leg of the tour and keep the
    mode class of the portal gate of the tour (``portal_leg`` flags the portal legs; ``None`` = no portal legs;
    the gate class of a tour is rail when the current mode of its portal legs is pt, road otherwise). Weights:
    the SrV shares of the cell of the longest implausible leg. When the candidate set is empty the plausibility
    is judged in the cell of the longest leg of the tour only (``relaxed_to_longest_leg`` True, weights from
    that cell; the portal class and the availability still apply); when that is empty too this raises.

    Only outside tours with at least one implausible leg are iterated (a vectorised pre-filter), so the Python
    loop runs over the tours that are re-moded, not over every tour of the population. An empty candidate set
    raises (no silent fallback to an always-available mode).
    """
    if portal_leg is None:
        portal_leg = np.zeros(len(trips), dtype=bool)
    portal_leg = np.asarray(portal_leg, dtype=bool)
    if len(portal_leg) != len(trips):
        raise ValueError(f"[outside_tour_modes] portal_leg has {len(portal_leg)} entries for {len(trips)} legs")
    frame = pd.DataFrame({"person_id": trips["person_id"].to_numpy(), "tour": np.asarray(tour_ids),
                          "outside": np.asarray(outside, dtype=bool), "implausible": np.asarray(implausible, dtype=bool),
                          "km": np.asarray(leg_km, dtype=float), "mode": trips["mode"].astype(str).to_numpy(),
                          "portal": portal_leg, "position": np.arange(len(trips))})
    frame = frame[frame["outside"]]
    tour_has_bad_leg = frame.groupby(["person_id", "tour"], sort=False)["implausible"].transform("any")
    frame = frame[tour_has_bad_leg.to_numpy(dtype=bool)]
    plausible_by_cell = {}

    def plausible_in(position: int) -> tuple:
        shares = cells[position][2]
        if id(shares) not in plausible_by_cell:
            plausible_by_cell[id(shares)] = ModeShareTable.plausible_modes(shares, min_share)
        return plausible_by_cell[id(shares)]

    rows = []
    for (person_id, tour), legs in frame.groupby(["person_id", "tour"], sort=True):
        person_allowed = allowed.get(person_id)
        if person_allowed is None:
            raise ValueError(f"[outside_tour_modes] person_id {person_id} has no availability row")
        by_length = legs.sort_values(["km", "position"], ascending=[False, True])
        bad = legs[legs["implausible"]]
        longest_bad = int(bad.sort_values(["km", "position"], ascending=[False, True]).iloc[0]["position"])
        longest_leg = int(by_length.iloc[0]["position"])
        portal_class = _portal_class(legs, person_id, tour)

        positions = legs["position"].to_numpy()
        strict = set(plausible_in(int(positions[0])))
        for position in positions[1:]:
            strict &= set(plausible_in(int(position)))
        pool = tuple(mode for mode in MODES if mode in strict)
        candidates, removed, n_removed_by_portal_class = _admissible(pool, person_allowed, portal_class)
        weight_position, relaxed = longest_bad, False
        if not candidates:
            relaxed_pool = plausible_in(longest_leg)
            candidates, removed, n_removed_by_portal_class = _admissible(relaxed_pool, person_allowed, portal_class)
            weight_position, relaxed = longest_leg, True
            if not candidates:
                raise ValueError(
                    f"[outside_tour_modes] no substitute candidate for person_id {person_id}, tour {tour}: plausible "
                    f"on every leg {pool}, plausible in the cell of the longest leg {relaxed_pool}, allowed "
                    f"{sorted(person_allowed)}, portal gate class {portal_class or 'none'}")
        group, label, shares = cells[weight_position]
        weights = np.array([shares[mode] for mode in candidates], dtype=float)
        substitute = str(rng.choice(np.array(candidates, dtype=object), p=weights / weights.sum()))
        rows.append({"person_id": person_id, "tour": int(tour), "cell": f"{group}/{label}",
                     "donor_modes": ",".join(sorted(set(legs["mode"]))), "substitute": substitute,
                     "n_legs": int(len(legs)), "n_legs_implausible": int(len(bad)), "n_candidates": len(candidates),
                     "n_removed_by_availability": len(removed), "removed_modes": ",".join(removed),
                     "relaxed_to_longest_leg": relaxed, "portal_tour": portal_class != PORTAL_CLASS_NONE,
                     "portal_class": portal_class, "n_removed_by_portal_class": int(n_removed_by_portal_class)})
    return pd.DataFrame(rows, columns=SUBSTITUTE_COLUMNS)


def apply_tour_modes(trips: pd.DataFrame, tour_ids: np.ndarray, substitutes: pd.DataFrame,
                     implausible: np.ndarray) -> tuple:
    """A copy of ``trips`` with the substitute on every leg of each listed tour.

    Returns ``(frame, n_remoded, n_swept)``: legs whose mode changed, and affected legs whose donor mode was
    plausible (swept along for the one-mode-per-tour rule; counted, never hidden).
    """
    require_plain_mode_column(trips)
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
