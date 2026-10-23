"""Gate, posting and cost record of the automatic Claude PR review (scripts/claude_review.py).

Defects caught: a review that runs (and costs) on a docs-only or generated-file PR, a
critical-path change that is skipped, an unrelated label event that re-triggers a paid
review, rename/binary numstat lines that crash or count wrongly, a missing cost record
reported as a silent zero, a finding off the diff that breaks the whole PR review (GitHub
rejects inline comments outside the diff) or silently disappears, a failed run posted as
an empty review, a prompt placeholder left unfilled, and an unbounded review run.
"""
import importlib.util
import json
import pathlib
import sys

import pytest
import yaml

_REPO = pathlib.Path(__file__).resolve().parents[1]
_PATH = _REPO / "scripts" / "claude_review.py"


def _load():
    spec = importlib.util.spec_from_file_location("claude_review_mod", _PATH)
    module = importlib.util.module_from_spec(spec)
    # dataclasses resolves annotations through sys.modules, so the module must be registered first.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


cr = _load()
# The committed gate config, so a threshold or path edit that breaks the intent fails here.
CONFIG = yaml.safe_load((_REPO / ".github" / "claude-review.yml").read_text(encoding="utf-8"))


@pytest.mark.parametrize("entries, branch, labels, draft, action, label, tier", [
    # small change outside critical paths -> no review
    ([("README.md", 20)], "chore/x", [], False, "", "", "none"),
    # generated views and lock files never count, however large
    ([("docs/generated/STAGES.md", 5000), ("environments/conda-lock.yml", 9000)], "chore/x", [], False, "", "", "none"),
    # docs branch without a critical path -> skipped despite its size
    ([("docs/adr/0150.md", 900)], "docs/adr-0150", [], False, "", "", "none"),
    # docs branch that touches a critical path is still reviewed
    ([("configs/base_bs.yml", 10)], "docs/adr-0150", [], False, "", "", "light"),
    # small critical-path change -> light
    ([("braunschweig/population/x.py", 40)], "fix/i1-x", [], False, "", "", "light"),
    # large critical-path change -> full
    ([("braunschweig/population/x.py", 250), ("tests/test_x.py", 60)], "feature/i1-x", [], False, "", "", "full"),
    # large change anywhere -> full
    ([("scripts/report_x.py", 500), ("tests/test_report_x.py", 300)], "chore/x", [], False, "", "", "full"),
    # many small files outside critical paths -> light
    ([(f"docs/codebase/notes/n{i}.md", 3) for i in range(8)], "chore/x", [], False, "", "", "light"),
    # draft PRs wait, unless the force label is added
    ([("braunschweig/x.py", 400)], "feature/i1-x", [], True, "", "", "none"),
    ([("README.md", 1)], "chore/x", ["claude-review"], True, "labeled", "claude-review", "full"),
    # a force label left on the PR does not bill every later push
    ([("README.md", 1)], "chore/x", ["claude-review"], False, "synchronize", "", "none"),
    # skip label beats everything
    ([("braunschweig/x.py", 4000)], "feature/i1-x", ["skip-claude-review", "claude-review"], False, "", "", "none"),
    # adding an unrelated label must not start a paid review; adding the force label must
    ([("braunschweig/x.py", 400)], "feature/i1-x", ["automerge"], False, "labeled", "automerge", "none"),
    ([("README.md", 1)], "chore/x", ["claude-review"], False, "labeled", "claude-review", "full"),
])
def test_classify_decides_tier_from_committed_config(entries, branch, labels, draft, action, label, tier):
    decision = cr.classify(CONFIG, entries, branch, labels, draft, action, label)
    assert decision.tier == tier, decision.reason
    assert decision.run == (tier != "none")
    assert decision.model == (CONFIG["tiers"][tier]["model"] if tier != "none" else "")
    assert decision.reason


def test_parse_numstat_handles_renames_and_binaries():
    text = "10\t2\tbraunschweig/a.py\n-\t-\tdocs/fig.png\n3\t1\tbraunschweig/{old => new}/b.py\n0\t0\tx.py => y.py\n"
    assert cr.parse_numstat(text) == [
        ("braunschweig/a.py", 12), ("docs/fig.png", 0), ("braunschweig/new/b.py", 4), ("y.py", 0)]
    with pytest.raises(ValueError):
        cr.parse_numstat("garbage line\n")


def test_classify_cli_writes_github_output(tmp_path, monkeypatch, capsys):
    numstat = tmp_path / "diff.tsv"
    numstat.write_text("40\t5\tconfigs/base_bs.yml\n", encoding="utf-8")
    output, summary = tmp_path / "out.txt", tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    rc = cr.main(["classify", "--config", str(_REPO / ".github" / "claude-review.yml"),
                  "--numstat", str(numstat), "--branch", "fix/i1-x", "--labels", "step:generation"])
    assert rc == 0
    values = dict(line.split("=", 1) for line in output.read_text(encoding="utf-8").splitlines())
    assert values["run"] == "true" and values["tier"] == "light"
    assert values["model"] == CONFIG["tiers"]["light"]["model"]
    assert int(values["max_turns"]) == CONFIG["tiers"]["light"]["max_turns"]
    assert "configs/base_bs.yml" in values["reason"]
    assert "light" in summary.read_text(encoding="utf-8")


def test_cost_reads_result_record_and_fails_loudly_without_one(tmp_path, monkeypatch, capsys):
    execution = tmp_path / "exec.json"
    execution.write_text(json.dumps([
        {"type": "system", "subtype": "init"},
        {"type": "assistant", "message": {}},
        {"type": "result", "subtype": "error_max_turns", "is_error": False, "total_cost_usd": 0.4213, "num_turns": 7, "duration_ms": 81500,
         "usage": {"input_tokens": 120, "output_tokens": 3400,
                   "cache_creation_input_tokens": 40000, "cache_read_input_tokens": 150000}},
    ]), encoding="utf-8")
    out = tmp_path / "cost.json"
    assert cr.main(["cost", "--execution-file", str(execution), "--tier", "full", "--json", str(out)]) == 0
    summary = json.loads(out.read_text(encoding="utf-8"))
    assert summary["total_cost_usd"] == pytest.approx(0.4213)
    assert (summary["num_turns"], summary["duration_s"], summary["output_tokens"]) == (7, 81.5, 3400)
    assert summary["cache_read_input_tokens"] == 150000 and summary["tier"] == "full"
    # a review cut off by --max-turns is reported as partial, not as a normal review
    assert summary["subtype"] == "error_max_turns"
    assert "::warning::" in capsys.readouterr().out

    # an API error on the first call: the SDK says subtype "success" with is_error; the error
    # text must reach the log, and the status must not read as a successful review
    execution.write_text(json.dumps([{"type": "result", "subtype": "success", "is_error": True,
                                      "total_cost_usd": 0, "num_turns": 1,
                                      "result": "API Error: 400 model not supported"}]), encoding="utf-8")
    assert cr.main(["cost", "--execution-file", str(execution), "--json", str(out)]) == 0
    printed = capsys.readouterr().out
    assert "[claude-review] error:" in printed and "::error::Claude run error: API Error: 400" in printed
    assert json.loads(out.read_text(encoding="utf-8"))["error_text"].startswith("API Error: 400")

    execution.write_text(json.dumps([{"type": "assistant"}]), encoding="utf-8")
    step_summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(step_summary))
    assert cr.main(["cost", "--execution-file", str(execution)]) == 1
    assert "No cost record" in step_summary.read_text(encoding="utf-8")
    # an empty path (the action set no output) is named as such, not read as the cwd directory
    assert cr.main(["cost", "--execution-file", ""]) == 1
    assert "empty execution file path" in step_summary.read_text(encoding="utf-8")


_DIFF = """diff --git a/braunschweig/a.py b/braunschweig/a.py
index 1111111..2222222 100644
--- a/braunschweig/a.py
+++ b/braunschweig/a.py
@@ -10,4 +10,5 @@ def f():
     keep = 1
-    old = 2
+    new = 2
+    added = 3
     tail = 4
@@ -40,2 +41,2 @@ def g():
-    x = 1
+    x = 2
     y = 3
diff --git a/docs/gone.md b/docs/gone.md
deleted file mode 100644
--- a/docs/gone.md
+++ /dev/null
@@ -1,2 +0,0 @@
-a
-b
"""


def test_post_dry_run_places_findings_on_diff_lines_and_keeps_the_rest_in_the_summary(tmp_path, capsys):
    # New-side lines in the diff: 10 (context), 11, 12 (added), 13 (context); 41 (added), 42 (context).
    assert cr.commentable_lines(_DIFF) == {"braunschweig/a.py": {10, 11, 12, 13, 41, 42}}
    result = tmp_path / "result.json"
    result.write_text(json.dumps({
        "type": "result", "subtype": "success", "is_error": False, "total_cost_usd": 0.81, "num_turns": 9,
        "structured_output": {"summary": "One bug.", "findings": [
            {"severity": "low", "path": "braunschweig/a.py", "line": 13, "title": "Context line", "body": "b1"},
            {"severity": "high", "path": "braunschweig/a.py", "line": 41, "title": "Wrong x", "body": "b2"},
            # line 30 lies between the hunks: GitHub would reject it inline
            {"severity": "medium", "path": "braunschweig/a.py", "line": 30, "title": "Off diff", "body": "b3"},
        ]}}), encoding="utf-8")
    diff = tmp_path / "pr.diff"
    diff.write_text(_DIFF, encoding="utf-8")
    rc = cr.main(["post", "--result", str(result), "--repo", "o/r", "--pr", "5", "--tier", "full",
                  "--sha", "abcdef1234567890", "--diff-file", str(diff), "--dry-run"])
    assert rc == 0
    out = capsys.readouterr().out
    review = json.loads(out.split("Review payload:\n", 1)[1].split("\n[claude-review] Summary comment:", 1)[0])
    # high before low; the off-diff finding is not an inline comment
    assert [(c["path"], c["line"]) for c in review["comments"]] == [("braunschweig/a.py", 41), ("braunschweig/a.py", 13)]
    assert review["commit_id"] == "abcdef1234567890" and review["event"] == "COMMENT"
    summary = out.split("[claude-review] Summary comment:\n", 1)[1]
    assert cr.SUMMARY_MARKER in summary and "Off diff" in summary.split("Not on a changed line", 1)[1]
    assert "inline 2, summary-only 1" in out and "0.8100 USD" in summary


def test_post_refuses_a_failed_run(tmp_path, capsys):
    result = tmp_path / "result.json"
    result.write_text(json.dumps({"type": "result", "subtype": "success", "is_error": True,
                                  "result": "Credit balance is too low", "total_cost_usd": 0}), encoding="utf-8")
    assert cr.main(["post", "--result", str(result), "--repo", "o/r", "--pr", "5", "--tier", "full",
                    "--sha", "abc", "--diff-file", str(result), "--dry-run"]) == 1
    assert "Credit balance is too low" in capsys.readouterr().out


def test_committed_prompt_and_tiers_give_a_bounded_read_only_run():
    review_cfg = CONFIG["review"]
    prompt = cr.build_prompt((_REPO / review_cfg["prompt_file"]).read_text(encoding="utf-8"),
                             "o/r", 7, "light", "abc123")
    assert "PR NUMBER: 7" in prompt and "{{" not in prompt
    with pytest.raises(ValueError):
        cr.build_prompt("{{TYPO}}", "o/r", 7, "light", "abc")
    for tier in ("light", "full"):
        command = cr.claude_command("claude", CONFIG["tiers"][tier], review_cfg["allowed_tools"])
        value = {flag: command[i + 1] for i, flag in enumerate(command[:-1]) if flag.startswith("--")}
        assert value["--model"] == CONFIG["tiers"][tier]["model"]
        assert float(value["--max-budget-usd"]) > 0 and int(value["--max-turns"]) > 0
        # the variadic tool list must come last, and the model must have no write or shell access
        assert command[-2] == "--allowedTools"
        assert not {"Write", "Edit", "Bash"} & set(command[-1].split(","))
