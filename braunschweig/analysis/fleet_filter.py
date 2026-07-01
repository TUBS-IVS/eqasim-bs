"""Single source of truth for isolating the household FLEET vehicles.

The synthetic ``vehicles.csv`` carries two distinct car-mode vehicle sets:

- household **FLEET** vehicles: ``vehicle_id`` ``"<hh>:car:<idx>"``, keyed by
  ``household_id``, carrying the fleet-model attributes (brand, powertrain,
  euro_class, segment, hbefa_*, economic_status, kreis_ags5, age).
- eqasim per-person **ROUTING** vehicles: ``vehicle_id`` ``"<person>:car"``,
  keyed by ``owner_id``, with NO fleet attributes (all ``nan``). MATSim needs
  them for routing, so they legitimately exist in the scenario.

Both have ``mode == "car"``. A fleet analysis that filters only on
``mode == "car"`` therefore silently mixes the routing vehicles into its
aggregates, producing ~49% misleading ``nan`` in brand/powertrain/etc. This
helper is the one place that defines "the fleet subset" so every
vehicle-consuming analysis filters identically and the fleet-vs-routing split
is logged (never silent).
"""
from __future__ import annotations

import logging

LOGGER = logging.getLogger("braunschweig.analysis.fleet_filter")


def fleet_vehicles(vehicles, *, context: str = ""):
    """Return the household-fleet car subset of a vehicles frame, logging the split.

    Fleet vehicles are ``mode == "car"`` AND carry a non-null ``household_id``
    (the fleet model keys on ``household_id``; routing vehicles carry only
    ``owner_id``). The fleet-vs-routing counts are logged at INFO so the
    filtering is observable, per the project's no-silent-fallback rule.

    The function is idempotent (applying it to an already-filtered frame returns
    the same rows) and defensive: a ``None``/empty frame is returned unchanged,
    and a missing ``mode`` or ``household_id`` column is logged and that filter
    step is skipped rather than raising.

    Parameters
    ----------
    vehicles:
        A vehicles DataFrame (as read from ``*_vehicles.csv``) or ``None``.
    context:
        Short label identifying the calling analysis, included in the log line.
    """
    if vehicles is None or len(vehicles) == 0:
        return vehicles

    tag = f" {context}" if context else ""
    total = len(vehicles)
    df = vehicles

    if "mode" in df.columns:
        df = df[df["mode"] == "car"]
    else:
        LOGGER.warning("[fleet_filter]%s 'mode' column absent; skipping mode=='car' filter", tag)
    cars = len(df)

    if "household_id" in df.columns:
        fleet = df[df["household_id"].notna()].copy()
    else:
        LOGGER.warning("[fleet_filter]%s 'household_id' column absent; cannot exclude "
                       "routing vehicles -- returning all car rows", tag)
        fleet = df.copy()

    routing = cars - len(fleet)
    LOGGER.info(
        "[fleet_filter]%s fleet %d / cars %d (routing excluded %d, %.1f%%; total rows %d)",
        tag, len(fleet), cars, routing,
        (100.0 * routing / cars) if cars else 0.0, total,
    )
    return fleet
