"""Committed, traceable outputs for the distance-fit diagnostic."""
from __future__ import annotations

import json
import logging
import os
import subprocess

logger = logging.getLogger(__name__)


def git_hash():
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"],
                                       stderr=subprocess.DEVNULL).decode().strip()
    except Exception:
        return "unknown"


def write_fit_csv(fit_df, output_dir, filename):
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, filename)
    fit_df.to_csv(path, index=False)
    logger.info("[distance-fit] wrote %s", path)
    return path


def write_summary(summaries_by_activity, provenance, output_dir):
    os.makedirs(output_dir, exist_ok=True)
    payload = {"activities": summaries_by_activity, "provenance": provenance}
    json_path = os.path.join(output_dir, "distance_fit_summary.json")
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, default=str)
    md_path = os.path.join(output_dir, "distance_fit_summary.md")
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write("# Distance-fit diagnostic summary\n\n")
        for key, val in provenance.items():
            fh.write(f"- **{key}:** {val}\n")
        fh.write("\n| activity | aggregate | subpop-weighted mean | worst key | worst | validation? |\n")
        fh.write("|---|---|---|---|---|---|\n")
        for act, s in summaries_by_activity.items():
            fh.write(f"| {act} | {s.get('aggregate')} | {s.get('subpop_weighted_mean')} | "
                     f"{s.get('worst_key')} | {s.get('worst_value')} | {s.get('is_validation')} |\n")
    logger.info("[distance-fit] wrote %s and %s", json_path, md_path)
    return json_path
