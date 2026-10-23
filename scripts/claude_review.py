"""Gate and cost record for the automatic Claude PR review (.github/workflows/claude-review.yml).

    python scripts/claude_review.py classify --config .github/claude-review.yml --numstat diff.tsv \
        --branch feature/i1-x [--labels a,b] [--draft] [--event-action labeled --event-label x]
    python scripts/claude_review.py cost --execution-file <path> [--tier full] [--model m] [--json out.json]

classify decides WITHOUT calling a model whether the review runs and at which tier
("light" or "full"), from the `git diff --numstat` of the PR, its branch kind, labels and
draft state, against the thresholds in the config. It prints the decision with its reason
and, under GitHub Actions, writes run/tier/model/max_turns/reason to $GITHUB_OUTPUT and a
line to $GITHUB_STEP_SUMMARY, so a skipped review is never silent.

cost reads the Claude Code execution file the action leaves behind and reports the API
cost in USD, the token counts, turns and duration as the action itself recorded them.
A missing or unparsable record is an error (exit 1), not a silent zero.

See docs/codebase/notes/claude-pr-review.md.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class Decision:
    run: bool
    tier: str  # "light", "full" or "none"
    model: str
    max_turns: int
    reason: str
    changed_lines: int
    changed_files: int
    critical_files: list


def glob_to_regex(pattern: str) -> re.Pattern:
    """Translate a path glob to a regex: "**" crosses directories, "*" and "?" do not."""
    out = []
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out) + r"\Z")


def matches_any(path: str, patterns) -> bool:
    return any(glob_to_regex(pattern).match(path) for pattern in patterns)


def parse_numstat(text: str) -> list:
    """Parse `git diff --numstat` lines into (path, changed_lines); binary files count 0 lines."""
    entries = []
    for line in text.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 3:
            raise ValueError(f"not a numstat line: {line!r}")
        added, deleted, path = parts[0], parts[1], parts[-1]
        # A rename is reported as "old => new" or "dir/{old => new}/file"; the new path counts.
        if " => " in path:
            path = re.sub(r"\{[^{}]* => ([^{}]*)\}", r"\1", path)
            path = path.split(" => ")[-1].replace("//", "/")
        lines = 0 if added == "-" else int(added) + int(deleted)
        entries.append((path, lines))
    return entries


def classify(config: dict, entries: list, branch: str, labels: list, draft: bool,
             event_action: str = "", event_label: str = "") -> Decision:
    label_cfg = config["labels"]
    tiers = config["tiers"]
    limits = config["thresholds"]

    counted = [(path, lines) for path, lines in entries if not matches_any(path, config.get("ignored_paths", []))]
    changed_lines = sum(lines for _, lines in counted)
    changed_files = len(counted)
    critical = sorted(path for path, _ in counted if matches_any(path, config.get("critical_paths", [])))
    kind = branch.split("/", 1)[0] if "/" in branch else ""
    size = f"{changed_lines} changed lines in {changed_files} files"
    if critical:
        size += f", critical: {', '.join(critical[:5])}" + (f" (+{len(critical) - 5} more)" if len(critical) > 5 else "")

    def decide(tier: str, why: str) -> Decision:
        model = tiers[tier]["model"] if tier != "none" else ""
        max_turns = int(tiers[tier]["max_turns"]) if tier != "none" else 0
        return Decision(tier != "none", tier, model, max_turns, f"{why} ({size})",
                        changed_lines, changed_files, critical)

    if label_cfg["skip"] in labels:
        return decide("none", f"skipped: label '{label_cfg['skip']}' is set")
    if event_action == "labeled":
        # Any label change re-triggers the workflow; only adding the force label starts a review,
        # once. Later pushes are gated normally, so a forgotten force label cannot bill every push.
        if event_label == label_cfg["force"]:
            return decide("full", f"forced: label '{label_cfg['force']}' was added")
        return decide("none", f"skipped: label event for '{event_label}', not '{label_cfg['force']}'")
    if draft and config.get("skip_drafts", True):
        return decide("none", "skipped: draft PR")
    if kind in config.get("skip_kinds", []) and not critical:
        return decide("none", f"skipped: branch kind '{kind}/' touches no critical path")
    if changed_lines >= limits["full_min_lines"]:
        return decide("full", f"full: >= {limits['full_min_lines']} changed lines")
    if critical and changed_lines >= limits["full_min_lines_critical"]:
        return decide("full", f"full: critical path and >= {limits['full_min_lines_critical']} changed lines")
    if critical:
        return decide("light", "light: critical path touched")
    if changed_lines >= limits["light_min_lines"]:
        return decide("light", f"light: >= {limits['light_min_lines']} changed lines")
    if changed_files >= limits["light_min_files"]:
        return decide("light", f"light: >= {limits['light_min_files']} changed files")
    return decide("none", "skipped: below every threshold, no critical path")


def read_result_record(execution_file: Path) -> dict:
    """Return the final "result" message of a Claude Code execution file (JSON array or JSON lines)."""
    text = execution_file.read_text(encoding="utf-8")
    try:
        data = json.loads(text)
        messages = data if isinstance(data, list) else [data]
    except json.JSONDecodeError:
        messages = [json.loads(line) for line in text.splitlines() if line.strip()]
    results = [m for m in messages if isinstance(m, dict) and m.get("type") == "result"]
    if not results:
        raise ValueError(f"no 'result' message in {execution_file}")
    return results[-1]


def cost_summary(result: dict) -> dict:
    if "total_cost_usd" not in result:
        raise ValueError("result message carries no total_cost_usd")
    usage = result.get("usage") or {}
    return {
        "total_cost_usd": float(result["total_cost_usd"]),
        "input_tokens": int(usage.get("input_tokens", 0)),
        "output_tokens": int(usage.get("output_tokens", 0)),
        "cache_creation_input_tokens": int(usage.get("cache_creation_input_tokens", 0)),
        "cache_read_input_tokens": int(usage.get("cache_read_input_tokens", 0)),
        "num_turns": int(result.get("num_turns", 0)),
        "duration_s": round(float(result.get("duration_ms", 0)) / 1000.0, 1),
        "is_error": bool(result.get("is_error", False)),
        "subtype": str(result.get("subtype", "unknown")),
        # On a failed run the result text carries the error (e.g. the API's message), which the
        # action itself hides in its log; keep it so the failure is diagnosable.
        "error_text": str(result.get("result", ""))[:1000] if result.get("is_error") else "",
        "model_usage": result.get("modelUsage", {}),
    }


def _append(env_var: str, text: str) -> None:
    target = os.environ.get(env_var)
    if target:
        with open(target, "a", encoding="utf-8") as handle:
            handle.write(text)


def _cmd_classify(args) -> int:
    import yaml  # only classify needs it; the cost step runs on a bare runner Python

    config = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    entries = parse_numstat(Path(args.numstat).read_text(encoding="utf-8"))
    labels = [label.strip() for label in (args.labels or "").split(",") if label.strip()]
    decision = classify(config, entries, args.branch, labels, args.draft,
                        args.event_action or "", args.event_label or "")
    print(f"[claude-review] {decision.reason}")
    print(f"[claude-review] run={str(decision.run).lower()} tier={decision.tier} model={decision.model or '-'}")
    _append("GITHUB_OUTPUT", f"run={str(decision.run).lower()}\ntier={decision.tier}\nmodel={decision.model}\n"
                             f"max_turns={decision.max_turns}\nreason={decision.reason}\n")
    _append("GITHUB_STEP_SUMMARY", f"### Claude review gate\n\n- Decision: **{decision.tier}**\n"
                                   f"- Reason: {decision.reason}\n")
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(asdict(decision), indent=2), encoding="utf-8")
    return 0


def _cmd_cost(args) -> int:
    try:
        if not args.execution_file:
            raise ValueError("empty execution file path (the action set no output)")
        summary = cost_summary(read_result_record(Path(args.execution_file)))
    except (OSError, ValueError) as error:
        print(f"error: no cost record: {error}", file=sys.stderr)
        _append("GITHUB_STEP_SUMMARY", f"### Claude review cost\n\n**No cost record:** {error}\n")
        return 1
    summary.update({"tier": args.tier, "model": args.model})
    # A run cut off by --max-turns or an API error still costs money but is not a complete review.
    # The SDK can report subtype "success" together with is_error (an API error on the first call).
    if summary["is_error"]:
        status = "error" if summary["subtype"] == "success" else summary["subtype"]
    else:
        status = "complete" if summary["subtype"] == "success" else summary["subtype"]
    line = (f"[claude-review] {status}: cost ${summary['total_cost_usd']:.4f} USD, tier {args.tier or '-'}, "
            f"model {args.model or '-'}, {summary['num_turns']} turns, {summary['duration_s']} s, "
            f"tokens in {summary['input_tokens']} / out {summary['output_tokens']} / "
            f"cache write {summary['cache_creation_input_tokens']} / cache read {summary['cache_read_input_tokens']}")
    print(line)
    if status != "complete":
        print(f"::warning::Claude review did not complete ({status}); the cost is real, the review is partial.")
    if summary["error_text"]:
        # One line, so the workflow command is not cut at the first newline.
        print(f"::error::Claude run error: {' '.join(summary['error_text'].split())}")
    _append("GITHUB_STEP_SUMMARY",
            "### Claude review cost\n\n| Status | Cost (USD) | Tier | Model | Turns | Duration (s) | Input | Output "
            "| Cache write | Cache read |\n|---|---|---|---|---|---|---|---|---|---|\n"
            f"| {status} | {summary['total_cost_usd']:.4f} | {args.tier or '-'} | {args.model or '-'} | {summary['num_turns']} "
            f"| {summary['duration_s']} | {summary['input_tokens']} | {summary['output_tokens']} "
            f"| {summary['cache_creation_input_tokens']} | {summary['cache_read_input_tokens']} |\n\n"
            "Source: the `result` record of the Claude Code execution file (as reported by the API).\n"
            + (f"\n**Run error:** `{' '.join(summary['error_text'].split())}`\n" if summary["error_text"] else ""))
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p_classify = sub.add_parser("classify", help="decide whether and how the review runs")
    p_classify.add_argument("--config", required=True)
    p_classify.add_argument("--numstat", required=True, help="file holding `git diff --numstat base...head`")
    p_classify.add_argument("--branch", required=True, help="PR head branch, e.g. feature/i1-slug")
    p_classify.add_argument("--labels", default="", help="comma-separated PR labels")
    p_classify.add_argument("--draft", action="store_true")
    p_classify.add_argument("--event-action", default="")
    p_classify.add_argument("--event-label", default="")
    p_classify.add_argument("--json", dest="json_out")
    p_classify.set_defaults(func=_cmd_classify)

    p_cost = sub.add_parser("cost", help="report the API cost of a finished review")
    p_cost.add_argument("--execution-file", required=True)
    p_cost.add_argument("--tier", default="")
    p_cost.add_argument("--model", default="")
    p_cost.add_argument("--json", dest="json_out")
    p_cost.set_defaults(func=_cmd_cost)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
