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
