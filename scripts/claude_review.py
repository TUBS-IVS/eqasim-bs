"""Gate, review run, posting and cost record of the automatic Claude PR review
(.github/workflows/claude-review.yml).

    python scripts/claude_review.py classify --config .github/claude-review.yml --numstat diff.tsv \
        --branch feature/i1-x [--labels a,b] [--draft] [--event-action labeled --event-label x]
    python scripts/claude_review.py review --config .github/claude-review.yml --tier full \
        --repo OWNER/NAME --pr 12 --sha <head sha> --out result.json [--diff-out .claude/review-pr.diff]
    python scripts/claude_review.py post --result result.json --repo OWNER/NAME --pr 12 \
        --tier full --sha <head sha> [--diff-file pr.diff] [--dry-run]
    python scripts/claude_review.py cost --execution-file result.json [--tier full] [--model m] [--json out.json]

classify decides WITHOUT calling a model whether the review runs and at which tier
("light" or "full"), from the `git diff --numstat` of the PR, its branch kind, labels and
draft state, against the thresholds in the config. It prints the decision with its reason
and, under GitHub Actions, writes run/tier/model/max_turns/reason to $GITHUB_OUTPUT and a
line to $GITHUB_STEP_SUMMARY, so a skipped review is never silent.

review runs `claude -p` (Claude Code in print mode) with the API key in ANTHROPIC_API_KEY,
the tier's model, turn and budget limits, read-only tools and a JSON schema for the findings,
and writes the CLI's JSON result to --out. It deliberately does not use
anthropics/claude-code-action: runs started by that action count as Claude Code usage, which
the monthly API credits of Claude Team/Max plans do not cover, whereas `claude -p` run with an
API key of the linked Console organization is covered (Claude Help Center article 17154008,
"Monthly API credits for Max and Team plans"). The model only reads; posting is done here.

post turns the findings into one PR review with inline comments on changed lines, and keeps
one summary comment per PR up to date (edited in place). A finding whose line is not part of
the diff cannot be an inline comment; it is listed in the summary instead, and the
inline/summary-only counts are logged. --dry-run prints the payloads and posts nothing.

cost reads the result record (the `claude -p` JSON result, or an Agent SDK message list) and
reports the API cost in USD, the token counts, turns and duration as the CLI recorded them.
A missing or unparsable record is an error (exit 1), not a silent zero.

See docs/codebase/notes/claude-pr-review.md.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

SUMMARY_MARKER = "<!-- claude-pr-review:summary -->"
SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}

# Structured output the model must return (claude -p --json-schema).
FINDINGS_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "description": "two or three sentences on the change and the review"},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "severity": {"type": "string", "enum": ["high", "medium", "low"]},
                    "path": {"type": "string", "description": "repository-relative path of the changed file"},
                    "line": {"type": "integer", "description": "line number in the NEW version of the file"},
                    "title": {"type": "string", "description": "the defect in one short sentence"},
                    "body": {"type": "string", "description": "concrete failure scenario and, if obvious, the fix"},
                },
                "required": ["severity", "path", "line", "title", "body"],
            },
        },
    },
    "required": ["summary", "findings"],
}


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


def build_prompt(template: str, repo: str, pr: int, tier: str, sha: str, diff_file: str) -> str:
    """Fill the {{REPO}}, {{PR}}, {{TIER}}, {{SHA}} and {{DIFF_FILE}} placeholders of the prompt file."""
    values = {"REPO": repo, "PR": str(pr), "TIER": tier, "SHA": sha, "DIFF_FILE": diff_file}
    prompt = template
    for key, value in values.items():
        prompt = prompt.replace("{{" + key + "}}", value)
    left = re.findall(r"\{\{[A-Z_]+\}\}", prompt)
    if left:
        raise ValueError(f"unknown placeholders in the prompt file: {sorted(set(left))}")
    return prompt


def claude_command(claude_bin: str, tier_cfg: dict, allowed_tools: list) -> list:
    return [
        claude_bin, "-p",
        # --bare: authenticate strictly with ANTHROPIC_API_KEY (never a Claude plan login, so the
        # run bills the API key), and load no user hooks, plugins, memory or CLAUDE.md on its own.
        "--bare",
        "--model", str(tier_cfg["model"]),
        "--max-turns", str(int(tier_cfg["max_turns"])),
        "--max-budget-usd", str(float(tier_cfg["max_budget_usd"])),
        "--output-format", "json",
        "--json-schema", json.dumps(FINDINGS_SCHEMA),
        # dontAsk denies every tool that is not pre-allowed; there is nobody to ask in CI.
        "--permission-mode", "dontAsk",
        # Last, because the option takes a variadic list.
        "--allowedTools", ",".join(allowed_tools),
    ]


def commentable_lines(diff_text: str) -> dict:
    """Map each file of a unified diff to the NEW-side line numbers GitHub accepts inline comments on
    (added and context lines inside hunks)."""
    lines_by_path: dict = {}
    path = None
    new_line = old_left = new_left = 0
    for raw in diff_text.splitlines():
        # A hunk ends when the line counts of its header are used up. Only then are header lines
        # read: an added line "++i;" appears as "+++i;" and must not be taken for a "+++ b/file" header
        # (Claude review finding on PR #497).
        if old_left > 0 or new_left > 0:
            # A context line of an empty source line may arrive as "" when trailing blanks are stripped.
            if path is not None and (raw.startswith("+") or raw.startswith(" ") or raw == ""):
                lines_by_path.setdefault(path, set()).add(new_line)
            if raw.startswith("+"):
                new_line, new_left = new_line + 1, new_left - 1
            elif raw.startswith(" ") or raw == "":
                new_line, new_left, old_left = new_line + 1, new_left - 1, old_left - 1
            elif raw.startswith("-"):
                old_left -= 1
            # "\ No newline at end of file" is no line on either side.
        elif raw.startswith("diff --git "):
            path = None
        elif raw.startswith("+++ "):
            target = raw[4:].strip()
            path = None if target == "/dev/null" else (target[2:] if target.startswith("b/") else target)
        elif raw.startswith("@@"):
            match = re.match(r"@@ -\d+(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", raw)
            if not match:
                raise ValueError(f"unparsable hunk header: {raw!r}")
            old_left = int(match.group(1)) if match.group(1) is not None else 1
            new_line = int(match.group(2))
            new_left = int(match.group(3)) if match.group(3) is not None else 1
    return lines_by_path


def read_findings(result: dict) -> dict:
    """Return the structured findings of a `claude -p --json-schema` result."""
    if result.get("is_error"):
        raise ValueError(f"the review run failed: {' '.join(str(result.get('result', '')).split())[:500]}")
    structured = result.get("structured_output")
    if structured is None:
        # Older CLI versions return the JSON only as the result text.
        try:
            structured = json.loads(result.get("result", ""))
        except (TypeError, json.JSONDecodeError):
            raise ValueError("the result carries no structured_output and no JSON result text") from None
    if not isinstance(structured, dict) or not isinstance(structured.get("findings"), list):
        raise ValueError("the structured output has no 'findings' list")
    return structured


def plan_comments(findings: list, commentable: dict) -> tuple:
    """Split findings into inline review comments and summary-only ones (line not in the diff)."""
    inline, summary_only = [], []
    for finding in sorted(findings, key=lambda f: SEVERITY_ORDER.get(f.get("severity"), 9)):
        path, line = finding.get("path"), finding.get("line")
        if isinstance(line, int) and line in commentable.get(path, set()):
            inline.append({"path": path, "line": line, "side": "RIGHT",
                           "body": f"**{finding['severity'].upper()}: {finding['title']}**\n\n{finding['body']}"})
        else:
            summary_only.append(finding)
    return inline, summary_only


def match_comment_ids(inline: list, posted: list) -> list:
    """Return the GitHub id of each planned inline comment (None where no posted comment matches)."""
    by_key: dict = {}
    for comment in posted:
        key = (comment.get("path"), comment.get("line"), (comment.get("body") or "").strip())
        by_key.setdefault(key, []).append(comment.get("id"))
    ids = []
    for comment in inline:
        candidates = by_key.get((comment["path"], comment["line"], comment["body"].strip()), [])
        ids.append(candidates.pop(0) if candidates else None)
    return ids


def findings_record(structured: dict, inline: list, comment_ids: list, args, result: dict) -> dict:
    """The machine-readable record of one review run that the session retro learns from
    (~/.claude/retro, retro_reviews.py): every finding with its placement and, when inline,
    its comment id, so the retro can read whether the finding was fixed or rejected."""
    placed = {(c["path"], c["line"], c["body"]): cid for c, cid in zip(inline, comment_ids)}
    findings = []
    for index, finding in enumerate(sorted(structured["findings"],
                                           key=lambda f: SEVERITY_ORDER.get(f.get("severity"), 9))):
        body = f"**{finding['severity'].upper()}: {finding['title']}**\n\n{finding['body']}"
        key = (finding.get("path"), finding.get("line"), body)
        findings.append({"index": index, "severity": finding.get("severity"), "path": finding.get("path"),
                         "line": finding.get("line"), "title": finding.get("title"),
                         "body": str(finding.get("body", ""))[:1500],
                         "placement": "inline" if key in placed else "summary",
                         "comment_id": placed.get(key)})
    return {"schema": 1, "repo": args.repo, "pr": args.pr, "head_sha": args.sha, "tier": args.tier,
            "run_id": os.environ.get("GITHUB_RUN_ID"), "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
            "created": datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat(),
            "cost_usd": result.get("total_cost_usd"), "num_turns": result.get("num_turns"),
            "summary": structured.get("summary", ""), "findings": findings}


def summary_markdown(structured: dict, inline: list, summary_only: list, tier: str, sha: str,
                     cost_line: str) -> str:
    findings = sorted(structured["findings"], key=lambda f: SEVERITY_ORDER.get(f.get("severity"), 9))
    parts = [SUMMARY_MARKER, f"## Claude review ({tier} tier, commit {sha[:12]})", "", structured["summary"], ""]
    if findings:
        parts += ["| Severity | Where | Finding |", "|---|---|---|"]
        parts += [f"| {f['severity']} | `{f['path']}:{f['line']}` | {f['title']} |" for f in findings]
    else:
        parts.append("No findings.")
    if summary_only:
        parts += ["", "**Not on a changed line** (could not be posted inline):", ""]
        parts += [f"- **{f['severity']}** `{f['path']}:{f['line']}`: {f['title']}. {f['body']}" for f in summary_only]
    parts += ["", f"<sub>{len(inline)} inline, {len(summary_only)} summary-only. {cost_line} "
                  "Advisory review, not a required check.</sub>"]
    return "\n".join(parts) + "\n"


def filter_diff(diff_text: str, ignored_patterns) -> tuple:
    """Drop the file sections of a unified diff whose path matches the gate's ignored_paths.

    Returns (kept diff text, dropped paths). The gate already leaves these files out of its
    line counts; handing them to the model as well would spend turns and budget on generated
    views and lock files (Claude review finding on PR #497)."""
    kept, dropped = [], []
    section: list = []
    section_path = None

    def flush():
        if section:
            (dropped.append(section_path) if section_path and matches_any(section_path, ignored_patterns)
             else kept.extend(section))

    for line in diff_text.splitlines(keepends=True):
        if line.startswith("diff --git "):
            flush()
            section = [line]
            match = re.match(r"diff --git a/(.*) b/(.*)$", line.rstrip("\r\n"))
            section_path = match.group(2) if match else None
        else:
            section.append(line)
    flush()
    return "".join(kept), dropped


def _pr_diff(repo: str, pr: int, base_sha: str = "", head_sha: str = "") -> str:
    # errors="replace": a diff can carry bytes that are not UTF-8 (a Latin-1 data file); a strict
    # decode would crash the whole review on such a PR. Only the replaced characters are lost.
    if base_sha:
        # Local git, as the gate does: no API diff size limit (gh pr diff fails on very large diffs).
        return subprocess.run(["git", "-c", "core.quotepath=false", "diff", f"{base_sha}...{head_sha}"],
                              check=True, capture_output=True, text=True, encoding="utf-8",
                              errors="replace").stdout
    return subprocess.run(["gh", "pr", "diff", str(pr), "--repo", repo],
                          check=True, capture_output=True, text=True, encoding="utf-8", errors="replace").stdout


def _gh_api(args: list, payload: dict | None = None) -> str:
    command = ["gh", "api", *args]
    if payload is None:
        return subprocess.run(command, check=True, capture_output=True, text=True).stdout
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as handle:
        json.dump(payload, handle)
        name = handle.name
    try:
        return subprocess.run([*command, "--input", name], check=True, capture_output=True, text=True).stdout
    finally:
        os.unlink(name)


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


def _cmd_review(args) -> int:
    import yaml

    config = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    tier_cfg = config["tiers"][args.tier]
    review_cfg = config["review"]
    # The model gets the diff as a file it pages through with Read, so it needs no shell at all
    # (a local run showed the model piping `gh pr diff` into grep/awk, which the read-only tool
    # list rightly denies, and the review then covered only part of the diff).
    diff_text, dropped = filter_diff(_pr_diff(args.repo, args.pr, args.base_sha, args.sha),
                                     config.get("ignored_paths", []))
    diff_path = Path(args.diff_out)
    diff_path.parent.mkdir(parents=True, exist_ok=True)
    diff_path.write_text(diff_text, encoding="utf-8")
    print(f"[claude-review] diff of PR {args.pr}: {len(diff_text.splitlines())} lines -> {diff_path.as_posix()}; "
          f"{len(dropped)} ignored file(s) left out" + (f": {', '.join(dropped[:10])}" if dropped else ""))
    if not diff_text.strip():
        print("::warning::Nothing left to review after dropping ignored paths; no model call.")
        Path(args.out).write_text(json.dumps({"type": "result", "subtype": "success", "is_error": False,
                                              "total_cost_usd": 0.0, "num_turns": 0, "structured_output": {
                                                  "summary": "Only ignored (generated or lock) files changed.",
                                                  "findings": []}}), encoding="utf-8")
        return 0
    prompt = build_prompt(Path(review_cfg["prompt_file"]).read_text(encoding="utf-8"),
                          args.repo, args.pr, args.tier, args.sha, diff_path.as_posix())
    claude_bin = shutil.which(args.claude_bin) or args.claude_bin
    if not os.environ.get("ANTHROPIC_API_KEY"):
        # claude --bare authenticates only with this key; fail here with a clear message instead.
        print("::error::ANTHROPIC_API_KEY is not set; the review needs a workspace API key.")
        return 1
    command = claude_command(claude_bin, tier_cfg, review_cfg["allowed_tools"])
    print(f"[claude-review] running claude -p: tier {args.tier}, model {tier_cfg['model']}, "
          f"max {tier_cfg['max_turns']} turns, budget {tier_cfg['max_budget_usd']} USD")
    # The prompt goes in on stdin: no argv length limit, and it is not echoed in process listings.
    completed = subprocess.run(command, input=prompt, capture_output=True, text=True, encoding="utf-8")
    Path(args.out).write_text(completed.stdout, encoding="utf-8")
    if completed.stderr.strip():
        print(completed.stderr.strip()[-2000:], file=sys.stderr)
    print(f"[claude-review] claude exited with {completed.returncode}; result written to {args.out}")
    return completed.returncode


def _cmd_post(args) -> int:
    result = json.loads(Path(args.result).read_text(encoding="utf-8"))
    try:
        structured = read_findings(result)
    except ValueError as error:
        print(f"::error::Nothing to post: {error}")
        return 1
    diff_text = Path(args.diff_file).read_text(encoding="utf-8") if args.diff_file else _pr_diff(args.repo, args.pr)
    inline, summary_only = plan_comments(structured["findings"], commentable_lines(diff_text))
    total = len(inline) + len(summary_only)
    print(f"[claude-review] findings {total}: inline {len(inline)}, summary-only {len(summary_only)} "
          "(line not in the diff)")
    if total and len(summary_only) == total:
        # Every finding off the diff usually means wrong paths or old-side line numbers, not a real pattern.
        print(f"::warning::All {total} findings point outside the diff; check path/line in the result.")
    cost_line = (f"Cost {float(result.get('total_cost_usd', 0)):.4f} USD, {result.get('num_turns', '?')} turns."
                 if "total_cost_usd" in result else "")
    body = summary_markdown(structured, inline, summary_only, args.tier, args.sha, cost_line)
    review = {"commit_id": args.sha, "event": "COMMENT",
              "body": "Claude review: inline findings below; the summary is in the Claude review comment.",
              "comments": inline}
    comment_ids = [None] * len(inline)
    if args.dry_run:
        print("[claude-review] dry run, nothing posted. Review payload:")
        print(json.dumps(review, indent=2))
        print("[claude-review] Summary comment:")
        print(body)
    elif inline:
        posted = json.loads(_gh_api([f"repos/{args.repo}/pulls/{args.pr}/reviews", "--method", "POST"], review))
        # One page of 100 (not --paginate, which would concatenate JSON arrays); a review posts far fewer.
        comments = json.loads(_gh_api([f"repos/{args.repo}/pulls/{args.pr}/reviews/{posted['id']}/comments"
                                       "?per_page=100"]) or "[]")
        comment_ids = match_comment_ids(inline, comments)
        matched = sum(1 for cid in comment_ids if cid is not None)
        print(f"[claude-review] inline comment ids matched {matched}/{len(inline)}")
        if matched < len(inline):
            # Without the id the retro cannot read the thread's outcome; the finding then stays "unknown".
            print(f"::warning::{len(inline) - matched} posted inline comment(s) could not be matched to a finding.")
    if args.record:
        record = findings_record(structured, inline, comment_ids, args, result)
        Path(args.record).write_text(json.dumps(record, indent=2), encoding="utf-8")
        print(f"[claude-review] findings record written to {args.record}")
    if args.dry_run:
        return 0
    upsert_summary(args.repo, args.pr, args.bot_login, body)
    return 0


def _summary_ids(repo: str, pr: int, bot_login: str) -> list:
    # Only a comment the workflow itself wrote counts as the summary, so nobody else's comment is edited.
    select = (f'.[] | select(.user.login == "{bot_login}" and ((.body // "") | contains("{SUMMARY_MARKER}")))'
              " | .id")
    return _gh_api([f"repos/{repo}/issues/{pr}/comments", "--paginate", "--jq", select]).split()


def upsert_summary(repo: str, pr: int, bot_login: str, body: str) -> None:
    """Keep exactly one summary comment: edit the oldest, or create one.

    A forced review (label event) and a push review run in different concurrency groups and can
    post at the same time; both may then find no summary and create one each (Claude review
    finding on PR #497). After writing, the oldest summary is kept with this body and any later
    duplicate is deleted, so whichever run finishes last leaves one current comment."""
    existing = _summary_ids(repo, pr, bot_login)
    if existing:
        _gh_api([f"repos/{repo}/issues/comments/{existing[0]}", "--method", "PATCH"], {"body": body})
        written = existing[0]
    else:
        written = str(json.loads(_gh_api([f"repos/{repo}/issues/{pr}/comments", "--method", "POST"],
                                         {"body": body}))["id"])
    now = _summary_ids(repo, pr, bot_login)
    if now and now[0] != written:
        _gh_api([f"repos/{repo}/issues/comments/{now[0]}", "--method", "PATCH"], {"body": body})
    for duplicate in now[1:]:
        try:
            _gh_api([f"repos/{repo}/issues/comments/{duplicate}", "--method", "DELETE"])
        except subprocess.CalledProcessError:
            pass  # the parallel run deleted it first
    print(f"[claude-review] summary comment {now[0] if now else written} up to date"
          + (f"; removed {len(now) - 1} duplicate(s) from a parallel run" if len(now) > 1 else ""))


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

    p_review = sub.add_parser("review", help="run claude -p and write its JSON result")
    p_review.add_argument("--config", required=True)
    p_review.add_argument("--tier", required=True, choices=["light", "full"])
    p_review.add_argument("--repo", required=True, help="OWNER/NAME")
    p_review.add_argument("--pr", required=True, type=int)
    p_review.add_argument("--sha", required=True, help="PR head commit")
    p_review.add_argument("--out", required=True)
    p_review.add_argument("--base-sha", default="",
                          help="PR base commit: diff with local git (needs full history) instead of gh pr diff")
    p_review.add_argument("--diff-out", default=".claude/review-pr.diff",
                          help="where the PR diff is written for the model (inside the checkout, so Read may open "
                               "it); pass the same file to post --diff-file")
    p_review.add_argument("--claude-bin", default="claude")
    p_review.set_defaults(func=_cmd_review)

    p_post = sub.add_parser("post", help="post the findings of a review result to the PR")
    p_post.add_argument("--result", required=True)
    p_post.add_argument("--repo", required=True)
    p_post.add_argument("--pr", required=True, type=int)
    p_post.add_argument("--tier", required=True)
    p_post.add_argument("--sha", required=True)
    p_post.add_argument("--diff-file", help="unified diff of the PR; default: `gh pr diff`")
    p_post.add_argument("--dry-run", action="store_true", help="print the payloads, post nothing")
    p_post.add_argument("--record", help="write the machine-readable findings record (for the session retro) here")
    p_post.add_argument("--bot-login", default="github-actions[bot]",
                        help="author of the summary comment to update (the workflow token's identity)")
    p_post.set_defaults(func=_cmd_post)

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
