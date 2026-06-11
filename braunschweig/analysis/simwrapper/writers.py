"""Pure helpers that turn Python data into SimWrapper dashboard files.

A SimWrapper dashboard folder contains data files (CSV) plus one or more
``dashboard-*.yaml`` files. Each YAML has a ``header`` (tab/title/description)
and a ``layout`` of named rows; every row is a list of cards. A card has a
``type`` (bar/line/area/csv/tile/...), a ``dataset`` (relative CSV filename)
and type-specific keys. See https://docs.simwrapper.app/docs/guide-dashboards.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import yaml


def write_csv(folder: Path, name: str, df: pd.DataFrame) -> str:
    """Write ``df`` to ``folder/name`` (comma CSV, UTF-8, no index). Returns name."""
    folder.mkdir(parents=True, exist_ok=True)
    df.to_csv(folder / name, index=False, encoding="utf-8")
    return name


def write_yaml(folder: Path, name: str, obj: dict[str, Any]) -> Path:
    """Write a dashboard dict to ``folder/name`` as YAML (key order preserved)."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text(
        yaml.safe_dump(obj, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return path


def card_bar(title: str, dataset: str, *, x: str, columns: list[str],
             legend_titles: list[str] | None = None, stacked: bool = False,
             x_axis_name: str = "", y_axis_name: str = "", width: int = 1,
             description: str = "") -> dict[str, Any]:
    """Build a SimWrapper bar-chart card dict."""
    card: dict[str, Any] = {
        "type": "bar", "title": title, "width": width,
        "dataset": dataset, "x": x, "columns": columns,
        "stacked": stacked,
    }
    if description:
        card["description"] = description
    if legend_titles:
        # The SimWrapper Chart base class uses ``legendName`` (a list), not
        # ``legendTitles``.  The Python parameter keeps the descriptive name.
        card["legendName"] = legend_titles
    if x_axis_name:
        card["xAxisName"] = x_axis_name
    if y_axis_name:
        card["yAxisName"] = y_axis_name
    return card


def card_line(title: str, dataset: str, *, x: str, columns: list[str],
              legend_titles: list[str] | None = None, x_axis_name: str = "",
              y_axis_name: str = "", width: int = 1,
              description: str = "") -> dict[str, Any]:
    """Build a SimWrapper line-chart card dict."""
    card = card_bar(title, dataset, x=x, columns=columns,
                    legend_titles=legend_titles, x_axis_name=x_axis_name,
                    y_axis_name=y_axis_name, width=width, description=description)
    card["type"] = "line"
    card.pop("stacked", None)
    return card


def card_table(title: str, dataset: str, *, width: int = 1,
               description: str = "") -> dict[str, Any]:
    """Build a SimWrapper CSV-table card dict."""
    # SimWrapper Table uses ``showAllRows`` (capital R).
    card = {"type": "csv", "title": title, "dataset": dataset,
            "width": width, "enableFilter": False, "showAllRows": True}
    if description:
        card["description"] = description
    return card


def card_tile(title: str, dataset: str, *, width: int = 1) -> dict[str, Any]:
    """Build a SimWrapper tile (single-value) card dict."""
    return {"type": "tile", "title": title, "dataset": dataset, "width": width}


def card_xytime(title: str, file: str, *, value_label: str = "",
                radius: int = 6, width: int = 2, height: int = 5,
                description: str = "") -> dict[str, Any]:
    """Build a SimWrapper xytime point-cloud card dict.

    The ``file`` must be a SimWrapper xytime CSV (first line ``# EPSG:...``,
    header ``time,x,y,value``). The ``radius`` controls the rendered dot size.

    Args:
        title: Card title shown in the SimWrapper UI.
        file: Relative path to the xytime CSV (not ``dataset``).
        value_label: Optional label for the value legend.
        radius: Dot radius in pixels (default 6).
        width: Dashboard column width (1 or 2; default 2).
        height: Card height in SimWrapper grid units (default 5).
        description: Optional description shown below the card title.
    """
    card: dict[str, Any] = {
        "type": "xytime",
        "title": title,
        "file": file,
        "radius": radius,
        "width": width,
        "height": height,
    }
    if value_label:
        card["valueLabel"] = value_label
    if description:
        card["description"] = description
    return card


def card_choropleth(title: str, geojson_file: str, *,
                    value_col: str, join: str = "ars5",
                    color_ramp: str = "Viridis", height: int = 13,
                    width: int = 2, description: str = "") -> dict[str, Any]:
    """Build a SimWrapper shapefiles-plugin choropleth card dict.

    The value column is read **directly from the GeoJSON feature properties**
    (the callers merge the aggregated data into the GeoJSON before writing it).
    This avoids the SimWrapper type-coercion bug where a CSV ``ars5`` value such
    as ``"03101"`` is parsed as the integer ``3101``, making the join fail and
    producing a flat single colour.

    Emitted schema (MapPlot / shapefiles plugin, verified against SimWrapper source)::

        shapes:  { file: x.geojson, join: ars5 }
        display: { fill: { columnName: col,
                           colorRamp: { ramp: Viridis, steps: 7 } } }

    No ``datasets`` key and no ``fill.dataset``/``fill.join``: the colour is
    sourced entirely from the GeoJSON properties, bypassing the CSV join path.

    Args:
        title: Card title.
        geojson_file: Relative path to the GeoJSON polygon file (EPSG:4326).
            Must contain a feature property matching ``join`` AND ``value_col``.
        value_col: GeoJSON property name to use for fill colour.
        join: GeoJSON property used as the zone identifier (default ``ars5``).
        color_ramp: SimWrapper colour ramp name (default ``Viridis``).
        height: Card height in SimWrapper grid units (default 13 for big maps).
        width: Dashboard column width (default 2).
        description: Optional description.
    """
    card: dict[str, Any] = {
        "type": "map",
        "title": title,
        "height": height,
        "width": width,
        "shapes": {"file": geojson_file, "join": join},
        "display": {
            "fill": {
                "columnName": value_col,
                "colorRamp": {"ramp": color_ramp, "steps": 7},
            }
        },
    }
    if description:
        card["description"] = description
    return card


def card_hexagons(title: str, file: str, *,
                  from_x: str, from_y: str, to_x: str, to_y: str,
                  aggregation_name: str = "Trips",
                  from_title: str = "Origins", to_title: str = "Destinations",
                  radius: int = 150, projection: str = "EPSG:25832",
                  height: int = 13, width: int = 2,
                  description: str = "") -> dict[str, Any]:
    """Build a SimWrapper hexagons density-map card dict.

    The hexagons plugin counts points per hexagonal cell from the ``file`` CSV.
    Each aggregation entry is a **FROM-TO object** (verified against the
    SimWrapper Hexagons contrib source)::

        aggregations:
          <Name>:
            title: <Name>
            fromTitle: Origins
            fromX: origin_x
            fromY: origin_y
            toTitle: Destinations
            toX: destination_x
            toY: destination_y

    Args:
        title: Card title.
        file: Relative path to the CSV (must contain the x/y columns).
        from_x: Column name for origin x coordinate.
        from_y: Column name for origin y coordinate.
        to_x: Column name for destination x coordinate.
        to_y: Column name for destination y coordinate.
        aggregation_name: Top-level key for the aggregation group (default ``Trips``).
        from_title: Label for the origin layer (default ``Origins``).
        to_title: Label for the destination layer (default ``Destinations``).
        radius: Hexagon radius in map units (default 150).
        projection: CRS string (default ``EPSG:25832``).
        height: Card height in SimWrapper grid units (default 13 for big maps).
        width: Dashboard column width (default 2).
        description: Optional description.
    """
    aggregations = {
        aggregation_name: {
            "title": aggregation_name,
            "fromTitle": from_title,
            "fromX": from_x,
            "fromY": from_y,
            "toTitle": to_title,
            "toX": to_x,
            "toY": to_y,
        }
    }
    card: dict[str, Any] = {
        "type": "hexagons",
        "title": title,
        "file": file,
        "projection": projection,
        "radius": radius,
        "height": height,
        "aggregations": aggregations,
        "width": width,
    }
    if description:
        card["description"] = description
    return card


def card_sankey(title: str, csv: str, *, sort: bool = True,
                width: int = 2, description: str = "") -> dict[str, Any]:
    """Build a SimWrapper sankey flow card dict.

    The CSV must be semicolon-delimited with columns ``from``, ``to``,
    ``value``.

    Args:
        title: Card title.
        csv: Relative path to the semicolon-delimited CSV.
        sort: Whether SimWrapper should sort the flows (default True).
        width: Dashboard column width (default 2).
        description: Optional description.
    """
    card: dict[str, Any] = {
        "type": "sankey",
        "title": title,
        "csv": csv,
        "sort": sort,
        "width": width,
    }
    if description:
        card["description"] = description
    return card


def card_scatter(title: str, dataset: str, *, x: str, y: str,
                 x_axis_name: str = "", y_axis_name: str = "",
                 width: int = 1, description: str = "") -> dict[str, Any]:
    """Build a SimWrapper scatter plot card dict.

    Args:
        title: Card title.
        dataset: Relative path to the CSV.
        x: Column name for the x-axis.
        y: Column name for the y-axis.
        x_axis_name: Optional x-axis label.
        y_axis_name: Optional y-axis label.
        width: Dashboard column width (default 1).
        description: Optional description.
    """
    card: dict[str, Any] = {
        "type": "scatter",
        "title": title,
        "dataset": dataset,
        "x": x,
        "y": y,
        "width": width,
    }
    if x_axis_name:
        card["xAxisName"] = x_axis_name
    if y_axis_name:
        card["yAxisName"] = y_axis_name
    if description:
        card["description"] = description
    return card


def dashboard(tab: str, title: str, rows: dict[str, list[dict[str, Any]]],
              description: str = "") -> dict[str, Any]:
    """Build a top-level SimWrapper dashboard dict (header + layout).

    Args:
        tab: Short label shown on the tab selector in the SimWrapper UI.
        title: Full title shown at the top of the dashboard view.
        rows: Mapping of row-name to list of card dicts (the ``layout`` section).
        description: Optional description shown below the title.

    Returns:
        Dict ready to be serialised with :func:`write_yaml`.
    """
    header: dict[str, Any] = {"tab": tab, "title": title}
    if description:
        header["description"] = description
    return {"header": header, "layout": rows}
