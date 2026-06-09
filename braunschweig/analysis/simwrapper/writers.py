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
        card["legendTitles"] = legend_titles
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
    card = {"type": "csv", "title": title, "dataset": dataset,
            "width": width, "enableFilter": False, "showAllrows": True}
    if description:
        card["description"] = description
    return card


def card_tile(title: str, dataset: str, *, width: int = 1) -> dict[str, Any]:
    """Build a SimWrapper tile (single-value) card dict."""
    return {"type": "tile", "title": title, "dataset": dataset, "width": width}


def card_xytime(title: str, file: str, *, value_label: str = "",
                radius: int = 6, width: int = 2,
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
        description: Optional description shown below the card title.
    """
    card: dict[str, Any] = {
        "type": "xytime",
        "title": title,
        "file": file,
        "radius": radius,
        "width": width,
    }
    if value_label:
        card["valueLabel"] = value_label
    if description:
        card["description"] = description
    return card


def card_choropleth(title: str, geojson_file: str, dataset_file: str, *,
                    value_col: str, join: str = "ars5",
                    color_ramp: str = "Viridis", width: int = 2,
                    description: str = "") -> dict[str, Any]:
    """Build a SimWrapper shapefiles-plugin choropleth card dict.

    Follows the verified SimWrapper shapefiles-plugin schema::

        shapes:   { file: x.geojson, join: id }
        datasets: { agg: { file: data.csv } }
        display:  { fill: { dataset: agg, join: id, columnName: col,
                            colorRamp: { ramp: Viridis, steps: 7 } } }

    Args:
        title: Card title.
        geojson_file: Relative path to the GeoJSON polygon file (EPSG:4326).
            Must contain a property matching ``join``.
        dataset_file: Relative path to the CSV with ``join`` + ``value_col``.
        value_col: Column name in ``dataset_file`` to use for fill colour.
        join: Shared key between the GeoJSON ``join`` property and the CSV
            (default ``ars5``).
        color_ramp: SimWrapper colour ramp name (default ``Viridis``).
        width: Dashboard column width (default 2).
        description: Optional description.
    """
    card: dict[str, Any] = {
        "type": "map",
        "title": title,
        "width": width,
        "shapes": {"file": geojson_file, "join": join},
        "datasets": {"agg": {"file": dataset_file}},
        "display": {
            "fill": {
                "dataset": "agg",
                "join": join,
                "columnName": value_col,
                "colorRamp": {"ramp": color_ramp, "steps": 7},
            }
        },
    }
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
