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

    Fleet vehicles are ``mode == "car"`` rows identified STRUCTURALLY: a
    non-null fleet attribute (``segment``), falling back to the vehicle_id
    shape (``<hh>:car:<idx>`` vs the routing ``<person>:car``), falling back to
    the legacy ``household_id.notna()`` test. Nullability alone is NOT a safe
    discriminator: the 2026-07 kreis5 run wrote routing vehicles with a
    constant filler household_id, which polluted every fleet aggregate (41.8%
    attribute-less rows). The fleet-vs-routing counts and the discriminator
    used are logged so the filtering is observable, and a defeated
    discriminator (kept rows without ``brand``) is warned loudly, per the
    project's no-silent-fallback rule.

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

    # Discriminate STRUCTURALLY, not by household_id nullability: the 2026-07
    # kreis5 run showed routing vehicles can carry a constant FILLER
    # household_id (287972.5) that defeats a notna() test, polluting every
    # fleet aggregate with 41.8% attribute-less rows ('nan' top brand).
    # Layered discriminators, most robust first:
    #   1. a fleet-model attribute ('segment') is non-null only on fleet rows;
    #   2. the vehicle_id shape: fleet '<hh>:car:<idx>' vs routing '<person>:car';
    #   3. legacy fallback: household_id.notna() (pre-filler runs), logged.
    if "segment" in df.columns and df["segment"].notna().any():
        fleet = df[df["segment"].notna()].copy()
        discriminator = "segment.notna"
    elif "vehicle_id" in df.columns:
        fleet = df[df["vehicle_id"].astype(str).str.count(":") >= 2].copy()
        discriminator = "vehicle_id '<hh>:car:<idx>' shape"
        LOGGER.warning(
            "[fleet_filter]%s no usable 'segment' column; discriminating by "
            "vehicle_id shape instead", tag)
    elif "household_id" in df.columns:
        fleet = df[df["household_id"].notna()].copy()
        discriminator = "household_id.notna (legacy)"
        LOGGER.warning(
            "[fleet_filter]%s neither 'segment' nor 'vehicle_id' available; "
            "falling back to household_id.notna -- this CANNOT exclude routing "
            "vehicles that carry a filler household_id", tag)
    else:
        fleet = df.copy()
        discriminator = "none"
        LOGGER.warning("[fleet_filter]%s no discriminating column available; "
                       "returning all car rows UNFILTERED", tag)

    routing = cars - len(fleet)
    LOGGER.info(
        "[fleet_filter]%s fleet %d / cars %d (routing excluded %d, %.1f%%; "
        "total rows %d; discriminator: %s)",
        tag, len(fleet), cars, routing,
        (100.0 * routing / cars) if cars else 0.0, total, discriminator,
    )
    # Integrity signal: fleet rows must carry fleet attributes. A non-trivial
    # NaN share means the discriminator was defeated (the exact 2026-07 failure).
    if "brand" in fleet.columns and len(fleet) > 0:
        nan_share = float(fleet["brand"].isna().mean())
        if nan_share > 0.001:
            LOGGER.warning(
                "[fleet_filter]%s %.1f%% of the kept fleet rows have no 'brand' "
                "-- the routing/fleet discriminator looks defeated; fleet "
                "aggregates are NOT trustworthy", tag, 100.0 * nan_share)
    return fleet
