"""This repository's wiring of the central Claude PR review (TUBS-IVS/claude-pr-review, ADR-0176).

The review's code and its tests live in the central repository; this file checks only what this
repository owns. Defects caught: a caller left on a test branch, on `main` or on a commit of the
central workflow (it would run unreleased code with the API key), `secrets: inherit` (every secret
of this repository handed to the central code), token permissions too narrow for posting, a dropped
`paths-ignore` of the generated views (every bot PR of ADR-0168 would start a run that fails at
once), a label name copied into the workflow, and a prompt that lost the intent check against this
repository's PR template.
"""
import pathlib
import re

import yaml

_REPO = pathlib.Path(__file__).resolve().parents[1]
_CALLER = _REPO / ".github" / "workflows" / "claude-review.yml"
_CONFIG = yaml.safe_load((_REPO / ".github" / "claude-review.yml").read_text(encoding="utf-8"))


def test_the_caller_runs_a_released_central_review_with_only_the_api_key():
    text = _CALLER.read_text(encoding="utf-8")
    caller = yaml.safe_load(text)
    trigger = caller[True]["pull_request"]          # PyYAML reads the key `on` as true
    assert trigger["paths-ignore"] == ["docs/generated/**"]
    (job,) = caller["jobs"].values()
    # the moving major tag of a release, never a branch, `main` or a commit left over from a test
    assert re.fullmatch(r"TUBS-IVS/claude-pr-review/\.github/workflows/review\.yml@v\d+", job["uses"]), job["uses"]
    assert job["secrets"] == {"ANTHROPIC_API_KEY": "${{ secrets.ANTHROPIC_API_KEY }}"}
    # the called workflow can only narrow these: the review job posts comments and resolves threads
    assert job["permissions"] == {"contents": "read", "pull-requests": "write", "issues": "write"}
    # Label names stay in .github/claude-review.yml. Standalone words only: "claude-review" also
    # occurs inside the concurrency group and the file names.
    for label in (_CONFIG["labels"]["force"], _CONFIG["labels"]["skip"]):
        assert not re.search(rf"(?<![\w./-]){re.escape(label)}(?![\w./-])", text), label


def test_the_prompt_checks_the_diff_against_the_pr_template():
    prompt = (_REPO / _CONFIG["review"]["prompt_file"]).read_text(encoding="utf-8")
    template = (_REPO / ".github" / "pull_request_template.md").read_text(encoding="utf-8")
    for section in ("What & why", "Behaviour change?", "Validation evidence"):
        assert section in template and section in prompt, section
