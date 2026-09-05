"""SrV primary-distance baseline plots -- bar-chart companion in the spirit of eqasim's
``documentation/plots/commute_distance.py``; this module does not use
``documentation.plotting.setup()``.

Reads the CSVs written by ``braunschweig.analysis.synthesis.commute_distance_by_kreis``
(function ``write_outputs``) and draws grouped distance-band-share bars, model vs SrV
2023, one panel per Kreis (plus the ZGB aggregate) for work, or per model education
level (ZGB aggregate only) for education. Distances are always ROUTED km (the model's
euclidean home->activity distance times the configured detour factor); the SrV target
share drawn as a bar is the SHRUNK (empirical-Bayes, toward the Kreis's RegioStaR-7
pool) share used for the EMD/gap decision, not the raw survey share -- the raw share,
where the CSV carries it, is overlaid as a black marker labelled "SrV 2023 (raw)" so
the amount of shrinkage stays visible rather than hidden behind the decision metric.
"""
import logging
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

from braunschweig.calibration import srv_distance_targets as T  # noqa: E402

logger = logging.getLogger(__name__)


def _format_band_label(label):
    """Render a distance-band label for an axis tick: ``"100_plus"`` -> ``"100+"``,
    ``"20_plus"`` -> ``"20+"``, any other ``"a_b"`` -> ``"a-b"``."""
    if label.endswith("_plus"):
        return label[: -len("_plus")] + "+"
    return label.replace("_", "-")


def _panel_title(value, row):
    """One panel's title: the group value (Kreis code or education level), the source
    when it is not the direct SrV survey (CRITICAL 1: e.g. Wolfsburg's RS7-72 proxy, or
    ``(no reference)`` when ``source == "none"`` -- the cell has no usable reference at
    all), a ``(no model)`` marker when ``classification == "no_model"`` (Task 14 minor:
    a real reference exists but zero model persons landed in this cell), the model and
    reference person counts, and the EMD/classification (NaN EMD renders as "n/a")."""
    label = str(value)
    if row is None:
        return f"{label}\n(no data)"
    source = str(row.get("source", "srv"))
    if source == "none":
        label += " (no reference)"
    elif source != "srv":
        label += f" (proxy: {source})"
    if str(row.get("classification", "")) == "no_model":
        label += " (no model)"
    emd = row["emd"]
    emd_str = "n/a" if pd.isna(emd) else f"{emd:.3f}"
    n_model = int(row["n_model"])
    n_reference = int(row["n_reference_persons"])
    return f"{label}\nn={n_model} model / {n_reference} ref\nEMD {emd_str} ({row['classification']})"


def _grouped_bars(cells, labels, out_png, group_col, group_values, suptitle, csv_path, selection_label):
    """Draw one grouped-bar panel per value in ``group_values``, model vs SrV 2023 band
    shares, and save the figure to ``out_png``.

    ``group_values`` is the FULL expected set of codes/levels (not merely the ones
    present in ``cells``, per the reachability fix) so a value with no matching row in
    ``cells`` still gets a "(no data)" panel instead of silently vanishing from the
    figure; such panels are counted and logged (IMPORTANT 3). Each panel's bars are the
    model share (blue) and the SHRUNK SrV target share (orange, "SrV 2023 (shrunk
    target)"); when ``cells`` carries ``target_share_raw_<label>`` columns for every
    label, the raw (un-shrunk) survey share is overlaid as black markers labelled
    "SrV 2023 (raw)" so the shrinkage applied is visible. Distances are always ROUTED km
    (euclidean times the detour factor) -- the x-axis label states this explicitly.
    ``csv_path`` and ``selection_label`` are used only for the panel-count log line, not
    for re-reading the CSV.
    """
    n = len(group_values)
    fig, axes = plt.subplots(1, n, figsize=(2.6 * n, 3.2), sharey=True, dpi=150)
    if n == 1:
        axes = [axes]
    x = list(range(len(labels)))
    xtick_labels = [_format_band_label(lbl) for lbl in labels]
    has_raw_target = all(f"target_share_raw_{lbl}" in cells.columns for lbl in labels)

    legend_handles_labels = None
    missing_values = []
    n_drawn = 0
    for ax, value in zip(axes, group_values):
        matched = cells[cells[group_col].astype(str) == str(value)]
        if matched.empty:
            missing_values.append(str(value))
            ax.set_title(_panel_title(value, None), fontsize=8)
        else:
            row = matched.iloc[0]
            model = [row[f"model_share_{lbl}"] for lbl in labels]
            target = [row[f"target_share_{lbl}"] for lbl in labels]
            ax.bar([i - 0.2 for i in x], model, width=0.4, label="model")
            ax.bar([i + 0.2 for i in x], target, width=0.4, label="SrV 2023 (shrunk target)")
            if has_raw_target:
                raw = [row[f"target_share_raw_{lbl}"] for lbl in labels]
                ax.scatter([i + 0.2 for i in x], raw, color="black", marker="_", s=80, zorder=3,
                           label="SrV 2023 (raw)")
            ax.set_title(_panel_title(value, row), fontsize=8)
            if legend_handles_labels is None:
                legend_handles_labels = ax.get_legend_handles_labels()
            n_drawn += 1
        ax.set_xticks(x)
        ax.set_xticklabels(xtick_labels, rotation=90, fontsize=7)
        ax.set_xlabel("distance band (routed km)", fontsize=7)
    axes[0].set_ylabel("share")
    if legend_handles_labels is not None:
        axes[0].legend(*legend_handles_labels, fontsize=7)
    fig.suptitle(suptitle, fontsize=9)
    fig.tight_layout()
    fig.savefig(out_png)
    plt.close(fig)

    logger.info(
        "[srv_commute_distance] %s (%s, from %s): %d/%d panels with data; missing: %s",
        os.path.basename(str(out_png)), selection_label, csv_path, n_drawn, n,
        ", ".join(missing_values) if missing_values else "none")
    return str(out_png)


def plot_work_bands(commute_by_kreis_csv, out_png, scope="all"):
    """Home->work distance-band-share plot: one panel per Kreis in
    :data:`braunschweig.calibration.srv_distance_targets.ZGB_KREISE` plus the ZGB
    aggregate, for the requested ``scope`` (one of ``"all"``, ``"inter"`` -- home
    Gemeinde != workplace Gemeinde --, or ``"intra"`` -- same Gemeinde).

    Reads ``commute_by_kreis_csv`` as written by
    ``braunschweig.analysis.synthesis.commute_distance_by_kreis.write_outputs``
    (``commute_by_kreis.csv``). Raises :class:`ValueError` naming the CSV path and the
    requested scope if no row in the CSV matches it at all (IMPORTANT 2) -- this is
    distinct from an individual Kreis having no row, which instead renders as a
    "(no data)" panel. Returns the path the figure was written to.
    """
    cells = pd.read_csv(commute_by_kreis_csv, dtype={"code": str})
    scoped = cells[cells["scope"] == scope]
    if scoped.empty:
        raise ValueError(
            f"No rows in {commute_by_kreis_csv} match scope={scope!r}; cannot draw the "
            "SrV work-distance-band plot for this scope")
    codes = list(T.ZGB_KREISE) + ["zgb"]
    suptitle = (
        f"Home -> work distance bands, scope={scope} "
        "(distances are routed km = euclidean distance x detour factor)")
    return _grouped_bars(scoped, T.WORK_BAND_LABELS, out_png, "code", codes, suptitle,
                         commute_by_kreis_csv, f"scope={scope!r}")


def plot_education_bands(education_csv, out_png):
    """Home->education distance-band-share plot for the ZGB AGGREGATE ONLY (no
    per-Kreis breakdown), one panel per comparable model education level in
    :data:`braunschweig.calibration.srv_distance_targets.COMPARABLE_LEVELS`
    (``kindergarten``, ``grundschule``, ``sekundar_1``, ``upper_secondary``,
    ``university``).

    Reads ``education_csv`` as written by
    ``braunschweig.analysis.synthesis.commute_distance_by_kreis.write_outputs``
    (``education_by_kreis_level.csv``); education cells always carry ``scope ==
    "education"``. Raises :class:`ValueError` naming the CSV path if no ``code ==
    "zgb"`` row exists at all (IMPORTANT 2). Returns the path the figure was written to.
    """
    cells = pd.read_csv(education_csv, dtype={"code": str})
    zgb = cells[cells["code"] == "zgb"].copy()
    if zgb.empty:
        raise ValueError(
            f"No code=='zgb' rows in {education_csv}; cannot draw the SrV education-"
            "distance-band plot")
    levels = list(T.COMPARABLE_LEVELS)
    suptitle = (
        "Home -> education distance bands, ZGB aggregate by level "
        "(distances are routed km = euclidean distance x detour factor)")
    return _grouped_bars(zgb, T.EDUCATION_BAND_LABELS, out_png, "education_level", levels, suptitle,
                         education_csv, "code=='zgb'")
