"""Render the human-readable views under docs/generated/ from the registries.

Every page is DERIVED: the registries (docs/registry/**), ADR records
(docs/decisions/), run manifests (docs/runs/) and DAG snapshots
(docs/registry/dag/) are the maintained sources; these files are rebuilt by
``python -m braunschweig.documentation build`` and verified fresh by check
D1-generated. Editing them by hand is how the old STATUS matrix drifted --
never do it.

Rendering is deterministic (sorted inputs, no timestamps), so a rebuild on an
unchanged repository is byte-identical and CI can diff it.
"""
from __future__ import annotations

import os
from typing import Dict, List

from braunschweig.documentation.schema import MODEL_AREAS

GENERATED_DIRECTORY = os.path.join("docs", "generated")

BANNER = ("<!-- THIS FILE IS GENERATED. DO NOT EDIT MANUALLY.\n"
          "     Rebuild: python -m braunschweig.documentation build\n"
          "     Sources: docs/registry/** + docs/decisions/ + docs/runs/ -->\n\n")

_LIFECYCLE_SYMBOL = {"active": "active", "supported": "supported",
                     "experimental": "EXPERIMENTAL", "parked": "PARKED",
                     "retired": "retired"}
_PIPELINE_SHORT = {"active": "A", "supported": "s", "inactive": "i", "not_used": "-"}

AREA_TITLES = {
    "population": "Population synthesis", "attributes": "Person & household attributes",
    "behavior": "Travel / activity behavior", "fleet": "Vehicle fleet",
    "home": "Home locations", "work": "Work locations", "education": "Education",
    "secondary": "Secondary locations", "cordon": "Cordon / external demand",
    "freight": "Freight", "matsim": "MATSim", "analysis": "Analysis",
    "validation": "Validation", "infrastructure": "Infrastructure",
    "spatial": "Spatial base data",
}


def _pipeline_cell(pipelines: dict) -> str:
    return "/".join(_PIPELINE_SHORT[pipelines[p]]
                    for p in ("popsim_mid", "popsim_open", "simple_ipf_open"))


def _issue_link(ref) -> str:
    if not ref:
        return ""
    number = str(ref).lstrip("#")
    return f"[#{number}](https://github.com/TUBS-IVS/eqasim-bs/issues/{number})"


def render_status(context) -> str:
    lines = [
        "# Model status (generated)", "",
        "Grouped by model area; every cell is derived from the registries and the",
        "resolved canonical production configuration "
        "(`configs/base_bs.yml` + `configs/overlays/test_100pct.yml`).",
        "Pipeline cells: popsim_mid/popsim_open/simple_ipf_open with",
        "A=active, s=supported, i=inactive (wired, off), -=not used.", "",
    ]
    values = context.config_values or {}
    method = values.get("braunschweig.population.method", "unknown")
    mode_choice = values.get("mode_choice", "unknown")
    lines += [
        f"- Production population method (resolved config): `{method}`",
        f"- `mode_choice` in the resolved production config: `{mode_choice}` -- no "
        "calibrated modal split exists; run mode shares are not behaviourally "
        "validated, and mode-share convergence is stability, not validation.",
        f"- Features: {len(context.features)} | stages: {len(context.stages)} | "
        f"datasets: {len(context.datasets)} | ADRs: {len(context.adrs)} | run "
        f"manifests: {len(context.manifests)}", "",
    ]
    features_by_area: Dict[str, List[dict]] = {}
    for feature in context.features:
        features_by_area.setdefault(feature["area"], []).append(feature)
    stages_by_layer: Dict[str, List[dict]] = {}
    for stage in context.stages:
        stages_by_layer.setdefault(stage["layer"], []).append(stage)

    for area in MODEL_AREAS:
        features = sorted(features_by_area.get(area, []), key=lambda r: r["feature"])
        stages = sorted(stages_by_layer.get(area, []), key=lambda r: r["stage"])
        if not features and not stages:
            continue
        lines.append(f"## {AREA_TITLES.get(area, area)}")
        lines.append("")
        if stages:
            production = sum(1 for s in stages if s["production"])
            datasets = sorted({d for s in stages for d in (s.get("inputs") or [])})
            lines.append(f"{len(stages)} stage(s), {production} in the production "
                         f"DAG. Datasets: "
                         f"{', '.join(f'`{d}`' for d in datasets) if datasets else '--'}")
            lines.append("")
        if features:
            lines.append("| Feature | Lifecycle | Prod | Pipelines | Validation | Issue |")
            lines.append("|---|---|---|---|---|---|")
            for feature in features:
                validation = feature["validation"]
                runs = ", ".join(f"`{r}`" for r in validation["runs"])
                validation_cell = validation["state"] + (f" ({runs})" if runs else "")
                lines.append(
                    f"| [{feature['title']}](../registry/features/{feature['feature']}.yml) "
                    f"| {_LIFECYCLE_SYMBOL[feature['lifecycle']]} "
                    f"| {'ON' if feature['production']['enabled'] else 'off'} "
                    f"| {_pipeline_cell(feature['pipelines'])} "
                    f"| {validation_cell} "
                    f"| {_issue_link((feature.get('introduced') or {}).get('issue'))} |")
            lines.append("")
    return BANNER + "\n".join(lines).rstrip() + "\n"


def render_features(context) -> str:
    lines = [
        "# Feature registry (generated)", "",
        "One row per declaration in `docs/registry/features/`. Evidence columns",
        "report what the checker RESOLVES, not scientific quality; `validation`",
        "may only name run manifests (no run, no claim).", "",
        "| Feature | Area | Lifecycle | Prod | mid/open/ipf | Tests | OFF byte-id "
        "| Fallback | Reference | Validation | Assessment |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for feature in sorted(context.features, key=lambda r: r["feature"]):
        evidence = feature["evidence"]
        byte_identity = str(evidence["off_path_byte_identical"].get("claimed", "")).lower()
        byte_cell = {"true": "proven", "false": "not claimed",
                     "not_applicable": "--"}.get(byte_identity, "?")
        fallback = ("marker" if evidence["fallback_rate"].get("instrumented")
                    else "none")
        reference = evidence["reference"].get("kind")
        assessment = feature.get("assessment") or {}
        if str(assessment.get("status", "")).strip() == "pending":
            assessment_cell = "pending"
        elif assessment.get("by"):
            assessment_cell = f"{assessment.get('by')}, {assessment.get('date')}"
        else:
            assessment_cell = "none"
        validation = feature["validation"]
        runs = ", ".join(f"`{r}`" for r in validation["runs"])
        lines.append(
            f"| [{feature['feature']}](../registry/features/{feature['feature']}.yml) "
            f"| {feature['area']} | {feature['lifecycle']} "
            f"| {'ON' if feature['production']['enabled'] else 'off'} "
            f"| {_pipeline_cell(feature['pipelines'])} "
            f"| {len(evidence['tests'])} | {byte_cell} | {fallback} | {reference} "
            f"| {validation['state']}{f' ({runs})' if runs else ''} "
            f"| {assessment_cell} |")
    return BANNER + "\n".join(lines).rstrip() + "\n"


def render_stages(context) -> str:
    lines = [
        "# Stage registry (generated)", "",
        "One row per synpp stage record in `docs/registry/stages/` (stage ids are",
        "the DAG node names; aliased seams list their per-workflow resolution).", "",
        "| Stage | Layer | Lineage | Prod | mid/open/ipf | Resolves to | Features |",
        "|---|---|---|---|---|---|---|",
    ]
    for stage in sorted(context.stages, key=lambda r: r["stage"]):
        resolves = stage.get("resolves_to") or {}
        resolve_cell = "<br>".join(
            f"{pipeline}: `{module}`" for pipeline, module in sorted(resolves.items()))
        features = ", ".join(sorted(stage.get("features") or []))
        lines.append(
            f"| [{stage['stage']}](../registry/stages/{stage['stage']}.yml) "
            f"| {stage['layer']} | {stage['lineage']['type']} "
            f"| {'x' if stage['production'] else '--'} "
            f"| {_pipeline_cell(stage['pipelines'])} "
            f"| {resolve_cell or '--'} | {features or '--'} |")
    return BANNER + "\n".join(lines).rstrip() + "\n"


def render_data(context) -> str:
    lines = [
        "# Data registry (generated)", "",
        "One row per dataset record in `docs/registry/data/`. `Required for` uses",
        "S=synthesis, M=matsim, P=production (r=required, o=optional, -=not needed).",
        "Restricted datasets are never committed or redistributed.", "",
        "| Dataset | Roles | Acquisition | Destination (eqasim-data/...) | "
        "Required S/M/P | Restricted |",
        "|---|---|---|---|---|---|",
    ]
    short = {"required": "r", "optional": "o", "not_needed": "-"}
    for dataset in sorted(context.datasets, key=lambda r: r["dataset"]):
        requirements = dataset["requirements"]
        required = "/".join(short[requirements[k]]
                            for k in ("synthesis", "matsim", "production"))
        acquisition = dataset["acquisition"]
        how = acquisition["method"]
        if acquisition.get("script"):
            how += f" (`{acquisition['script']}`)"
        path = dataset["storage"]["expected_path"].replace("eqasim-data/", "")
        lines.append(
            f"| [{dataset['dataset']}](../registry/data/{dataset['dataset']}.yml) "
            f"| {', '.join(dataset['roles'])} | {how} | `{path}` | {required} "
            f"| {'YES' if dataset['licensing']['restricted'] else 'no'} |")
    return BANNER + "\n".join(lines).rstrip() + "\n"


def render_pipeline(context) -> str:
    production = context.snapshots.get("production")
    lines = [
        "# Production pipeline (generated)", "",
        "Extracted from the ACTUAL synpp dependency graph "
        "(`docs/registry/dag/production.json`,",
        "`synpp.run(dryrun=True)` over `configs/base_bs.yml` + "
        "`configs/overlays/test_100pct.yml`).", "",
    ]
    if not production:
        return BANNER + "\n".join(lines) + "\nNo production DAG snapshot present.\n"
    layer_of = {stage["stage"]: stage["layer"] for stage in context.stages}
    node_set = set(production["nodes"])
    lines += [f"Run targets: {', '.join(f'`{t}`' for t in production['targets'])};"
              f" {len(production['nodes'])} stages, {len(production['edges'])} "
              "dependencies.", "",
              "## Model-area flow (condensed)", "",
              "One edge per dependency between model areas (self-edges dropped):", "",
              "```mermaid", "flowchart LR"]
    layer_edges = set()
    for source, target in production["edges"]:
        source_layer = layer_of.get(source, "spatial")
        target_layer = layer_of.get(target, "spatial")
        if source_layer != target_layer:
            layer_edges.add((source_layer, target_layer))
    for layer in sorted({layer_of.get(node, "spatial") for node in node_set}):
        lines.append(f'    {layer}["{AREA_TITLES.get(layer, layer)}"]')
    for source_layer, target_layer in sorted(layer_edges):
        lines.append(f"    {source_layer} --> {target_layer}")
    lines += ["```", "", "## Stages per pipeline (reachability)", "",
              "| Stage | Layer | production | popsim_open | simple_ipf_open |",
              "|---|---|---|---|---|"]
    for node in sorted(context.dag_union):
        marks = ["x" if node in context.pipeline_nodes[p] else "--"
                 for p in ("popsim_mid", "popsim_open", "simple_ipf_open")]
        lines.append(f"| `{node}` | {layer_of.get(node, '?')} "
                     f"| {marks[0]} | {marks[1]} | {marks[2]} |")
    return BANNER + "\n".join(lines).rstrip() + "\n"


def render_lineage(context) -> str:
    lines = [
        "# Bavaria -> Braunschweig lineage (generated)", "",
        "How the current model relates to its `eqasim-org/eqasim-bavaria` baseline",
        "(fork point `b20fbe6`, 2025-10-06 -- ADR-0000; delta record: "
        "`docs/UPSTREAM_DELTA.md`;",
        "upstream eqasim-france fix sweeps: `docs/UPSTREAM_FIX_SWEEP.md`).", "",
        "Lineage classes: `inherited` (upstream code, possibly relocated),",
        "`configured` (upstream code, regional inputs/config only), `extended`",
        "(upstream mechanism with added behavior), `overridden` (upstream stage",
        "name resolved to a regional implementation via the config alias table),",
        "`braunschweig_new` (built for this model), `upstream_port` (mechanism",
        "ported from another eqasim project), `retired`.", "",
    ]
    by_type: Dict[str, List[dict]] = {}
    for stage in context.stages:
        by_type.setdefault(stage["lineage"]["type"], []).append(stage)
    lines.append("| Lineage | Stages |")
    lines.append("|---|---|")
    for lineage_type in ("inherited", "configured", "extended", "overridden",
                         "braunschweig_new", "upstream_port", "retired"):
        count = len(by_type.get(lineage_type, []))
        lines.append(f"| {lineage_type} | {count} |")
    lines.append("")
    lines.append("## Override seams (upstream stage -> regional implementation)")
    lines.append("")
    lines.append("| Upstream stage name | popsim_mid | popsim_open | simple_ipf_open |")
    lines.append("|---|---|---|---|")
    for stage in sorted(by_type.get("overridden", []), key=lambda r: r["stage"]):
        resolves = stage.get("resolves_to") or {}
        lines.append(f"| `{stage['stage']}` "
                     f"| `{resolves.get('popsim_mid', '=')}` "
                     f"| `{resolves.get('popsim_open', '=')}` "
                     f"| `{resolves.get('simple_ipf_open', '=')}` |")
    lines.append("")
    lines.append("## Braunschweig-new stages per model area")
    lines.append("")
    new_by_layer: Dict[str, List[str]] = {}
    for stage in by_type.get("braunschweig_new", []):
        new_by_layer.setdefault(stage["layer"], []).append(stage["stage"])
    for layer in sorted(new_by_layer):
        stages = ", ".join(f"`{s}`" for s in sorted(new_by_layer[layer]))
        lines.append(f"- **{AREA_TITLES.get(layer, layer)}**: {stages}")
    return BANNER + "\n".join(lines).rstrip() + "\n"


def render_decisions(context) -> str:
    lines = [
        "# ADR index (generated)", "",
        "One row per record under `docs/decisions/` (numbering notes:",
        "`docs/decisions/README.md`; ADR-0051 is reserved).", "",
        "| ADR | Date | Status | Title |",
        "|---|---|---|---|",
    ]
    for record in context.adrs:
        title = record.title.replace("|", "\\|")
        lines.append(f"| [{record.id}](../decisions/{os.path.basename(record.path)}) "
                     f"| {record.date or 'n/a'} | {record.status or '--'} | {title} |")
    return BANNER + "\n".join(lines).rstrip() + "\n"


def render_runs(context) -> str:
    lines = [
        "# Run manifests (generated)", "",
        "One row per manifest under `docs/runs/`. Classification says what a run",
        "WAS; a completed run is not validation and convergence is not validation.",
        "", "| Run | Date | Classification | Sampling | Execution | First validation "
        "entry |", "|---|---|---|---|---|---|",
    ]
    def sort_key(manifest):
        return (str(manifest.get("date")), manifest["id"])
    for manifest in sorted(context.manifests, key=sort_key, reverse=True):
        validation = manifest.get("validation") or []
        first = ""
        if validation:
            entry = validation[0]
            first = f"{entry.get('metric', '')}: {str(entry.get('result', ''))[:80]}"
        first = first.replace("|", "\\|")
        lines.append(
            f"| [{manifest['id']}](../runs/{manifest['id']}.yml) "
            f"| {manifest.get('date')} | {', '.join(manifest['classification'])} "
            f"| {str((manifest.get('sampling') or {}).get('rate', ''))[:36]} "
            f"| {manifest['status']['execution']} | {first} |")
    return BANNER + "\n".join(lines).rstrip() + "\n"


def render_all(context) -> Dict[str, str]:
    """Render every generated view; returns {filename: text} (LF newlines)."""
    return {
        "STATUS.md": render_status(context),
        "PIPELINE.md": render_pipeline(context),
        "STAGES.md": render_stages(context),
        "FEATURES.md": render_features(context),
        "DATA.md": render_data(context),
        "LINEAGE.md": render_lineage(context),
        "DECISIONS.md": render_decisions(context),
        "RUNS.md": render_runs(context),
    }


def write_all(context, repo_root: str) -> List[str]:
    directory = os.path.join(repo_root, GENERATED_DIRECTORY)
    os.makedirs(directory, exist_ok=True)
    written = []
    for name, text in render_all(context).items():
        path = os.path.join(directory, name)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        written.append(path)
    return written
