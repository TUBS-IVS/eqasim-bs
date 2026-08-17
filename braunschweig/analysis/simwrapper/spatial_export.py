"""SimWrapper spatial dashboard export -- facade module.

Emits the SimWrapper spatial layers of a run: fleet map, trip-origin/
destination hexagons ("spatial demand"), household socio-economic points,
purpose-to-mode behaviour (sankey + scatter), and commuter (Pendler) flows.
``export_spatial`` (near the bottom of this file) is the registry-based
driver wired into :func:`braunschweig.analysis.simwrapper.export.main`.

Module layout (issue #267 split; formerly one ~1600-line module): this file
is being extracted, one layer at a time, into SIBLING modules inside the
already-existing ``braunschweig.analysis.simwrapper`` package -- a sibling
split, not a package conversion (this file keeps its module path, so no
consumer import changes are needed). Every name a sibling defines is
re-exported here (``# noqa: F401  (re-exports)`` blocks below) so external
imports of ``braunschweig.analysis.simwrapper.spatial_export`` keep working
unchanged. Submodules extracted so far:

    fleet        Fleet map tab: vehicle geolocation, BEV/brand/powertrain mix
                 by Kreis (``load_fleet``, ``fleet_by_kreis``,
                 ``_brand_mix_by_kreis``, ``_powertrain_mix_by_kreis``,
                 ``emit_fleet``).

    geo_layers   Generic geometry-aware writers shared across tabs:
                 ``write_xyt_csv`` (xytime point-cloud CSV) and
                 ``write_kreis_choropleth_geojson`` (Kreis choropleth
                 GeoJSON). Task 1 had to place these temporarily inside
                 ``fleet`` (no dedicated sibling existed yet and leaving them
                 here would have forced a facade-import cycle); Task 2
                 relocated them into this dedicated sibling. ``emit_socio``
                 (moved to the ``socio`` sibling below) calls ``write_xyt_csv``
                 directly from ``geo_layers``, not via this facade.

    trip_demand  Spatial demand tab: ``_trips_xy`` (hexagon-map OD
                 coordinates), ``_purpose_to_mode`` (purpose->mode trip
                 counts), and ``emit_fleet``-style tab emitter
                 ``emit_spatial_demand``. ``_purpose_to_mode`` is consumed by
                 ``emit_behaviour``, which lives in the ``behaviour`` sibling
                 below and imports it directly from ``trip_demand``, not via
                 this facade.

    socio        Socio tab: ``_socio_by_kreis`` (per-Kreis income/car/status
                 aggregation), ``_economic_status_ordinal`` (category ->
                 ordinal 1..5 mapping, with ``ECONOMIC_STATUS_ORDER`` /
                 ``_ECONOMIC_STATUS_CODE``), and tab emitter ``emit_socio``.
                 Calls ``write_xyt_csv`` directly from the ``geo_layers``
                 sibling (not via this facade), per the no-back-import rule.

    behaviour    Behaviour tab: purpose->mode sankey + per-Kreis car-share
                 scatter, tab emitter ``emit_behaviour``. Imports
                 ``_purpose_to_mode`` directly from the ``trip_demand``
                 sibling (not via this facade), per the no-back-import rule.
                 ``drop_freight_agents`` (freight_filter import) moved with
                 it, since ``emit_behaviour`` was its only user in this
                 facade; ``trip_demand`` keeps its own separate import of the
                 same helper for ``emit_spatial_demand``. The facade's own
                 ``drop_freight_agents`` name (part of the frozen 34-name
                 namespace-parity baseline) is kept alive by re-exporting it
                 from ``behaviour`` below, rather than importing
                 ``freight_filter`` directly here again.

    commuter_tabs  Commuter (Pendler) tab (``_load_commutes``,
                 ``emit_commuters``) + student in-commuter tab
                 (``emit_student_commuters``, #140). Deliberately NOT named
                 ``commuters`` or ``student_commuters`` -- those two names are
                 already taken by the pure analysis/aggregation libraries this
                 module calls into (``braunschweig.analysis.simwrapper.commuters``,
                 ``braunschweig.analysis.simwrapper.student_commuters``); see
                 this module's docstring for the full distinction.

This split is now complete (issue #267): this file holds only its docstring,
imports, the facade re-export blocks above, and ``export_spatial`` (the
registry-based driver) below.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any  # noqa: F401  (TYPE_CHECKING kept for namespace parity)

import pandas as pd  # noqa: F401  (kept for namespace parity; unused directly in this facade)

from braunschweig.analysis.simwrapper import writers as w

LOGGER = logging.getLogger("braunschweig.analysis.simwrapper.spatial")

# ---------------------------------------------------------------------------
# Package submodules (extracted layer sections). Every name is re-exported
# here so external consumers (export.main(), tests) keep importing from this
# facade module path unchanged. This split (issue #267) is now complete; see
# the module docstring above for the full list of extracted siblings.
# ---------------------------------------------------------------------------

from . import fleet  # noqa: F401  (submodule re-export)
from .fleet import (  # noqa: F401  (re-exports)
    BEV_POWERTRAIN_VALUE,
    _MIN_BRAND_COVERAGE,
    _REQUIRED_FLEET_COLS,
    _brand_mix_by_kreis,
    _powertrain_mix_by_kreis,
    emit_fleet,
    fleet_by_kreis,
    load_fleet,
)
from . import geo_layers  # noqa: F401  (submodule re-export)
from .geo_layers import (  # noqa: F401  (re-exports)
    MAX_XYT_POINTS,
    _XYT_SAMPLE_SEED,
    write_kreis_choropleth_geojson,
    write_xyt_csv,
)
from . import trip_demand  # noqa: F401  (submodule re-export)
from .trip_demand import (  # noqa: F401  (re-exports)
    _purpose_to_mode,
    _trips_xy,
    emit_spatial_demand,
)
from . import socio  # noqa: F401  (submodule re-export)
from .socio import (  # noqa: F401  (re-exports)
    ECONOMIC_STATUS_ORDER,
    _ECONOMIC_STATUS_CODE,
    _economic_status_ordinal,
    _socio_by_kreis,
    emit_socio,
)
from . import behaviour  # noqa: F401  (submodule re-export)
from .behaviour import drop_freight_agents, emit_behaviour  # noqa: F401  (re-exports)
from . import commuter_tabs  # noqa: F401  (submodule re-export)
from .commuter_tabs import (  # noqa: F401  (re-exports)
    _load_commutes,
    emit_commuters,
    emit_student_commuters,
)


# ---------------------------------------------------------------------------
# export_spatial -- registry-based driver, wired into export.main()
# ---------------------------------------------------------------------------

def export_spatial(
    target_dir: str | Path,
    run_output_dir: str | None = None,
    sim_cache: str | None = None,
    record: "dict[str, Any] | None" = None,
    start_index: int = 9,
    student_frames: "dict[str, Any] | None" = None,
) -> list[Path]:
    """Write spatial dashboard tabs (fleet, spatial-demand, socio, behaviour).

    Iterates over a registry of ``(name, emit_fn)`` pairs, writes each
    returned dashboard dict as ``dashboard-{idx}-{name}.yaml``, and skips
    boards that return ``None`` (always logging the skip so there are no
    silent fallbacks).

    Tab order (sequential indices starting at ``start_index``):
    1. fleet -- vehicle fleet map (all-features runs only).
    2. spatial-demand -- trip origin/destination hexagons.
    3. socio -- home points coloured by income / economic status.
    4. behaviour -- purpose->mode sankey + per-Kreis car-share scatter.
    5. commuters -- SvB in/out/internal commuters per Kreis + top relations.
    6. student-commuters -- student in-commuter OD flows + distance (#140);
       only produced when ``student_frames`` is supplied and non-empty (the
       caller must pull it from the LIVE
       ``braunschweig.synthesis.student_incommuters`` stage -- see
       :func:`emit_student_commuters` for why this differs from the other,
       disk-based tabs).

    Args:
        target_dir: SimWrapper dashboard output folder (created if absent).
        run_output_dir: eqasim run output directory.
        sim_cache: synpp cache directory.
        record: Run record dict from ``assemble_run_record`` (needed for the
            behaviour scatter; skipped when ``None``).
        start_index: Starting dashboard index (default 9 so it follows the 8
            core tabs produced by ``export_run``).
        student_frames: The ``braunschweig.synthesis.student_incommuters``
            stage output dict (keys ``persons``, ``locations``, ...), or
            ``None`` when the caller has no live pipeline context (e.g. the
            standalone CLI) or the feature is off -- the student-commuters tab
            is then skipped (logged, not silently ignored).

    Returns:
        List of written YAML :class:`pathlib.Path` objects.
    """
    if run_output_dir is None and sim_cache is None and not student_frames:
        LOGGER.warning(
            "[spatial_export] neither run_output_dir, sim_cache, nor "
            "student_frames provided -- all spatial tabs skipped"
        )
        return []

    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    # Resolve the fleet / socio source directory (run_output preferred).
    source_dir = run_output_dir if run_output_dir is not None else sim_cache

    # Resolve the MATSim simulation_output/ for trips-based tabs.
    sim_output_dir: Path | None = None
    if sim_cache is not None:
        from braunschweig.analysis.dashboard.build_dashboard import _find_sim_output
        sim_output_dir = _find_sim_output(Path(sim_cache))
        if sim_output_dir is None:
            LOGGER.info(
                "[spatial_export] no matsim.simulation.run__*.cache found in %s "
                "-- trips-based tabs (spatial-demand, behaviour-sankey) will be skipped",
                sim_cache,
            )

    _SPATIAL_REGISTRY: list[tuple[str, Any]] = [
        # Tab: fleet
        ("fleet", lambda f: emit_fleet(source_dir, f)),
        # Tab: spatial-demand (hexagons from eqasim_trips.csv)
        ("spatial-demand", lambda f: emit_spatial_demand(sim_output_dir, f)),
        # Tab: socio (home xytime from population)
        ("socio", lambda f: emit_socio(source_dir, f) if source_dir else None),
        # Tab: behaviour (sankey + scatter)
        ("behaviour", lambda f: emit_behaviour(sim_output_dir, record, f)),
        # Tab: commuters (Pendler in/out/internal + top relations); works in
        # both modes (MATSim work OD, else synthesis commutes.gpkg).
        ("commuters", lambda f: emit_commuters(source_dir, record, f)),
        # Tab: student-commuters (#140 OD flows + distance); requires the
        # LIVE student_incommuters stage frames (see emit_student_commuters),
        # so it is a no-op (None) unless the caller supplied student_frames.
        ("student-commuters", lambda f: emit_student_commuters(
            (student_frames or {}).get("persons"),
            (student_frames or {}).get("locations"), f)),
    ]

    written: list[Path] = []
    for idx, (name, fn) in enumerate(_SPATIAL_REGISTRY, start=start_index):
        try:
            board = fn(target_dir)
        except Exception as exc:
            LOGGER.warning(
                "[spatial_export] tab '%s' skipped due to error: %s", name, exc
            )
            board = None
        if board is None:
            LOGGER.info(
                "[spatial_export] tab '%s' has no data, skipped", name
            )
            continue
        path = w.write_yaml(target_dir, f"dashboard-{idx}-{name}.yaml", board)
        written.append(path)
        LOGGER.info("[spatial_export] wrote %s", path.name)

    LOGGER.info(
        "[spatial_export] wrote %d spatial dashboard tab(s) to %s",
        len(written), target_dir,
    )
    return written
