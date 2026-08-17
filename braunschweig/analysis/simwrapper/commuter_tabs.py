"""Commuter (Pendler) and student in-commuter SimWrapper dashboard tabs (issue #267 split).

This module holds the SimWrapper *tab emitters* for the two commuter-related
boards: :func:`_load_commutes` (a small loader helper), :func:`emit_commuters`
(SvB in-/out-/internal commuters per Kreis + top relations) and
:func:`emit_student_commuters` (student in-commuter OD flows + distance,
#140). It is deliberately named differently from the two modules it
consumes, to avoid exactly the name confusion this split's naming audit is
meant to catch:

- :mod:`braunschweig.analysis.simwrapper.commuters` is a **pure analysis
  library**: OD-matrix extraction/aggregation (``commute_matrix_from_record``,
  ``commute_matrix_from_synthesis``, ``commuter_balance``, ``top_relations``).
  It performs no I/O and knows nothing about SimWrapper cards, folders, or
  YAML -- it operates purely on dicts / GeoDataFrames in, DataFrames out.
- :mod:`braunschweig.analysis.simwrapper.student_commuters` is likewise a
  **pure aggregation + plain-CSV-writing library** for the student
  in-commuter frames (``student_od``, ``write_outputs``); it writes raw CSVs
  but builds no dashboard cards and knows nothing about SimWrapper either.
- **This** module (``commuter_tabs``) is the presentation/orchestration layer
  on top of both: it resolves the data source (MATSim-realised OD vs.
  synthesis ``*commutes.gpkg``), calls into ``commuters`` /
  ``student_commuters`` for the actual numbers, writes the SimWrapper-specific
  GeoJSON choropleth, and assembles the ``w.dashboard(...)`` card layout
  returned to :func:`export_spatial`. Mirrors the fleet / trip_demand / socio
  / behaviour siblings extracted earlier in this split, which are likewise
  named after the SimWrapper *tab*, not the underlying data module.

``emit_commuters`` and ``emit_student_commuters`` import their respective
pure-library siblings directly (``braunschweig.analysis.simwrapper.commuters``,
``braunschweig.analysis.simwrapper.student_commuters``), not via the
``spatial_export`` facade -- a sibling must never import the facade back.
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
# Commuters (Pendler): in-/out-/internal per Kreis + top relations
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


# ---------------------------------------------------------------------------
# Student in-commuters (#140): OD flows + distance
# ---------------------------------------------------------------------------

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
