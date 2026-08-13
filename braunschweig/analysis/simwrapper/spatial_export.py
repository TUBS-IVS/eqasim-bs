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
                 ``emit_behaviour`` below (still defined in this facade) via
                 this sibling's re-export.

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

The remaining tabs (commuters, student commuters) and ``export_spatial``
itself are still defined directly below; a later task of the same split will
extract them into a further sibling.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pandas as pd

if TYPE_CHECKING:
    import geopandas as gpd

from braunschweig.analysis.simwrapper import writers as w

LOGGER = logging.getLogger("braunschweig.analysis.simwrapper.spatial")

# ---------------------------------------------------------------------------
# Package submodules (extracted layer sections). Every name is re-exported
# here so external consumers (export.main(), tests) keep importing from this
# facade module path unchanged. This split is incremental (issue #267);
# further sibling modules will be added here by later tasks.
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


# ---------------------------------------------------------------------------
# export_spatial -- registry-based driver, wired into export.main()
# ---------------------------------------------------------------------------

def _load_commutes(run_output_dir: "str | None") -> "gpd.GeoDataFrame | None":
    """Load the synthesis home->work commute LineStrings (``*commutes.gpkg``).

    Returns None (logged) when the run dir or the file is absent, so the
    commuter tab can fall back / skip without a silent failure.
    """
    if run_output_dir is None:
        return None
    import geopandas as gpd
    path = next(Path(run_output_dir).glob("*commutes.gpkg"), None)
    if path is None:
        LOGGER.info("[commuters] no *commutes.gpkg in %s", run_output_dir)
        return None
    return gpd.read_file(path)


def emit_commuters(
    run_output_dir: "str | None",
    record: "dict[str, Any] | None",
    folder: Path,
) -> "dict[str, Any] | None":
    """Commuter (Pendler) tab: in-/out-/internal commuters per Kreis + top relations.

    Source of the work commute Kreis x Kreis matrix, in order of preference:
    1. **MATSim realised** work trips (``record["matsim"]["od_matrix"]``).
    2. **Synthesis** home->work assignment (``*commutes.gpkg``) -- works even
       without a MATSim run.
    The active source is named in the tab title so the two are never confused.
    Returns None (logged) when neither source is available.
    """
    from braunschweig.analysis.simwrapper import commuters as cm
    from braunschweig.analysis import spatial

    zm = cm.commute_matrix_from_record(record, "work") if record else None
    source = "MATSim realised work trips"
    if zm is None:
        commutes = _load_commutes(run_output_dir)
        if commutes is None:
            LOGGER.warning(
                "[commuters] neither MATSim work OD nor synthesis commutes.gpkg "
                "available -- commuter tab skipped")
            return None
        # commutes.gpkg loses its CRS metadata after clean_gpkg(); the synthesis
        # always writes in EPSG:25832, so set it explicitly for the spatial join.
        if commutes.crs is None:
            commutes = commutes.set_crs("EPSG:25832")
        kreise = spatial.load_kreise(commutes.crs)
        zm = cm.commute_matrix_from_synthesis(commutes, kreise)
        source = "synthesis home->work assignment (pre-MATSim)"
    zones, matrix = zm

    balance = cm.commuter_balance(zones, matrix)
    top = cm.top_relations(zones, matrix, n=12)
    LOGGER.info("[commuters] %d Kreise; source: %s", len(balance), source)

    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    w.write_csv(folder, "commuter_balance.csv", balance)
    w.write_csv(folder, "commuter_top_relations.csv", top)

    rows: dict[str, list[dict[str, Any]]] = {
        "bars": [w.card_bar(
            "In- / out- / internal commuters by Kreis",
            "commuter_balance.csv", x="ars5",
            columns=["einpendler_gesamt", "auspendler", "binnen"],
            legend_titles=["Einpendler (in)", "Auspendler (out)", "Binnen (internal)"],
            y_axis_name="commuters", width=2,
            description=f"Work commuters per Kreis. Source: {source}.")],
        "table": [w.card_table(
            "Top commuter relations (Kreis -> Kreis)",
            "commuter_top_relations.csv", width=2)],
    }

    # Net-balance choropleth (Einpendler - Auspendler), VG250 polygons in 4326.
    try:
        kreise4326 = spatial.load_kreise("EPSG:25832").to_crs(4326)[["ars5", "geometry"]]
        geo = kreise4326.merge(balance, on="ars5", how="left")
        geo.to_file(folder / "kreis_commuters.geojson", driver="GeoJSON")
        # Own full-width row so the map renders large.
        rows["choropleth"] = [w.card_choropleth(
            "Net commuter balance by Kreis (Einpendler - Auspendler)",
            "kreis_commuters.geojson",
            value_col="netto", join="ars5", color_ramp="RdYlGn",
            height=13,
            description=f"Positive = net in-commuting Kreis. Source: {source}.")]
        LOGGER.info("[commuters] wrote kreis_commuters.geojson (%d Kreise)", len(geo))
    except Exception as exc:
        LOGGER.warning("[commuters] net-balance choropleth skipped: %s", exc)

    return w.dashboard("Commuters", f"Commuters / Pendler ({source})", rows)


def emit_student_commuters(
    persons: "pd.DataFrame | None",
    locations: "gpd.GeoDataFrame | None",
    folder: Path,
) -> "dict[str, Any] | None":
    """Student in-commuter (#140) OD-flow + distance tab.

    Unlike :func:`emit_commuters` (which reads a POST-HOC disk artifact, since
    the SvB in-commuters never carry a fine external-Kreis breakdown on disk),
    the student in-commuter frames are passed in directly by the caller from
    the LIVE ``braunschweig.synthesis.student_incommuters`` stage output (see
    ``braunschweig.analysis.simwrapper_export``): that stage is the only place
    the per-agent ``orig_ars5`` / ``dest_commune`` columns exist, because
    in-commuters bypass ``synthesis.output`` (which only exports the resident
    population) and the MATSim-realised OD (``metrics_od_matrix``) collapses
    every external origin into one coarse ``"external"`` zone.

    Args:
        persons: The student stage's ``persons`` frame (must carry
            ``orig_ars5`` / ``dest_commune``, attached by
            ``student_incommuters._inject``), or ``None``/empty when the
            feature is off, skipped, or the caller did not pass it.
        locations: The student stage's ``locations`` GeoDataFrame (3 rows per
            agent: activity_index 0/2 = home, 1 = education), used to derive
            the per-agent straight-line origin->campus distance.
        folder: SimWrapper dashboard output folder.

    Returns:
        Dashboard dict, or ``None`` (logged) when there are no student
        in-commuters to report -- writes nothing in that case.
    """
    from braunschweig.analysis.simwrapper import student_commuters as sc
    from braunschweig.data.cordon.plans import straight_line_distance_km

    if persons is None or len(persons) == 0:
        LOGGER.info(
            "[student_commuters] no student in-commuters (feature off, "
            "zero-count run, or frames not supplied) -- tab skipped"
        )
        return None
    if locations is None or len(locations) == 0:
        LOGGER.warning(
            "[student_commuters] %d student in-commuter persons but an empty "
            "locations frame -- tab skipped (should never happen for an "
            "active student in-commuter stage)", len(persons),
        )
        return None

    persons = persons[["person_id", "orig_ars5", "dest_commune"]].copy()
    home = (locations[locations["activity_index"] == 0]
            .set_index("person_id").loc[persons["person_id"]])
    education = (locations[locations["activity_index"] == 1]
                .set_index("person_id").loc[persons["person_id"]])
    straight_line_km = pd.Series(
        straight_line_distance_km(
            home.geometry.x.to_numpy(), home.geometry.y.to_numpy(),
            education.geometry.x.to_numpy(), education.geometry.y.to_numpy(),
        ),
        index=persons.index, name="straight_line_km",
    )

    folder = Path(folder)
    sc.write_outputs(persons[["orig_ars5", "dest_commune"]], straight_line_km, str(folder))
    LOGGER.info(
        "[student_commuters] wrote student_commuter_od.csv / "
        "_top_relations.csv / _distance.csv for %d student in-commuters "
        "(mean straight-line distance %.1f km)",
        len(persons), float(straight_line_km.mean()),
    )

    return w.dashboard(
        "Student commuters",
        "Student in-commuters (#140): origin-Kreis -> campus OD + distance",
        {
            "table": [w.card_table(
                "Student OD flows (origin Kreis -> destination university commune)",
                "student_commuter_od.csv", width=2,
            )],
            "top": [w.card_table(
                "Top student in-commuter relations",
                "student_commuter_top_relations.csv", width=1,
            )],
            "distance": [w.card_bar(
                "Student in-commuter distance distribution",
                "student_commute_distance.csv",
                x="band", columns=["count"],
                x_axis_name="straight-line distance band (km)",
                y_axis_name="students", width=1,
            )],
        },
        description=(
            "Cross-cordon student in-commuter OD flows and origin->campus "
            "straight-line distances (braunschweig.synthesis.student_incommuters). "
            "Model output, not compared to a committed reference."
        ),
    )


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
