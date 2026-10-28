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
    # the agent rules are critical: a small CLAUDE.md or rule edit is reviewed, also on a docs branch
    ([("CLAUDE.md", 6)], "chore/x", [], False, "", "", "light"),
    ([(".claude/rules/tests.md", 4)], "docs/adr-x", [], False, "", "", "light"),
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
@@ -10,3 +10,4 @@ def f():
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
    record_path = tmp_path / "findings.json"
    rc = cr.main(["post", "--result", str(result), "--repo", "o/r", "--pr", "5", "--tier", "full",
                  "--sha", "abcdef1234567890", "--diff-file", str(diff), "--dry-run", "--record", str(record_path)])
    assert rc == 0
    out = capsys.readouterr().out
    review = json.loads(out.split("Review payload:\n", 1)[1].split("\n[claude-review] Summary comment:", 1)[0])
    # high before low; the off-diff finding is not an inline comment
    assert [(c["path"], c["line"]) for c in review["comments"]] == [("braunschweig/a.py", 41), ("braunschweig/a.py", 13)]
    assert review["commit_id"] == "abcdef1234567890" and review["event"] == "COMMENT"
    summary = out.split("[claude-review] Summary comment:\n", 1)[1]
    assert cr.SUMMARY_MARKER in summary and "Off diff" in summary.split("Not on a changed line", 1)[1]
    assert "inline 2, summary-only 1" in out and "0.8100 USD" in summary
    # the record the session retro learns from: every finding, its placement, no ids in a dry run
    record = json.loads(record_path.read_text(encoding="utf-8"))
    assert (record["repo"], record["pr"], record["head_sha"], record["cost_usd"]) == ("o/r", 5, "abcdef1234567890", 0.81)
    assert [(f["title"], f["placement"], f["comment_id"]) for f in record["findings"]] == [
        ("Wrong x", "inline", None), ("Off diff", "summary", None), ("Context line", "inline", None)]


def test_match_comment_ids_pairs_posted_comments_with_planned_ones():
    # The retro reads a finding's outcome from its thread, so the id must belong to the same finding,
    # also when two findings share a line, and a comment GitHub did not return stays unmatched.
    planned = [{"path": "a.py", "line": 3, "body": "**HIGH: x**\n\nb"},
               {"path": "a.py", "line": 3, "body": "**LOW: y**\n\nc"},
               {"path": "b.py", "line": 9, "body": "**LOW: z**\n\nd"}]
    posted = [{"id": 22, "path": "a.py", "line": 3, "body": "**LOW: y**\n\nc"},
              {"id": 11, "path": "a.py", "line": 3, "body": "**HIGH: x**\n\nb"}]
    assert cr.match_comment_ids(planned, posted) == [11, 22, None]


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
                             "o/r", 7, "light", "abc123", ".claude/review-pr.diff")
    assert "PR NUMBER: 7" in prompt and ".claude/review-pr.diff" in prompt and "{{" not in prompt
    with pytest.raises(ValueError):
        cr.build_prompt("{{TYPO}}", "o/r", 7, "light", "abc", "d")
    for tier in ("light", "full"):
        command = cr.claude_command("claude", CONFIG["tiers"][tier], review_cfg["allowed_tools"])
        value = {flag: command[i + 1] for i, flag in enumerate(command[:-1]) if flag.startswith("--")}
        assert value["--model"] == CONFIG["tiers"][tier]["model"]
        assert float(value["--max-budget-usd"]) > 0 and int(value["--max-turns"]) > 0
        # --bare: billed strictly to ANTHROPIC_API_KEY, never to a Claude plan login
        assert "--bare" in command
        # the variadic tool list must come last, and the model must have no write or shell access
        assert command[-2] == "--allowedTools"
        assert set(command[-1].split(",")) <= {"Read", "Grep", "Glob"}


def test_filter_diff_drops_ignored_files_for_the_model():
    # A generated view or lock refresh must not reach the model: it would spend the light
    # tier's turns and budget and starve the review of the real change (PR #497 finding).
    diff = _DIFF + (
        "diff --git a/docs/generated/STAGES.md b/docs/generated/STAGES.md\n"
        "--- a/docs/generated/STAGES.md\n+++ b/docs/generated/STAGES.md\n@@ -1 +1 @@\n-x\n+y\n"
        "diff --git a/environments/conda-lock.yml b/environments/conda-lock.yml\n"
        "--- a/environments/conda-lock.yml\n+++ b/environments/conda-lock.yml\n@@ -1 +1 @@\n-a\n+b\n")
    kept, dropped = cr.filter_diff(diff, CONFIG["ignored_paths"])
    assert dropped == ["docs/generated/STAGES.md", "environments/conda-lock.yml"]
    assert kept == _DIFF


def test_commentable_lines_reads_an_added_plus_plus_line_as_content_not_as_a_header():
    # "++ x" added inside a hunk is "+++ x" in the diff; taking it for a file header would drop the
    # rest of the file from the commentable lines and push its findings off the diff.
    diff = ("diff --git a/notes.md b/notes.md\n--- a/notes.md\n+++ b/notes.md\n@@ -1,2 +1,3 @@\n"
            " first\n+++ counter\n last\n"
            "diff --git a/b.py b/b.py\n--- a/b.py\n+++ b/b.py\n@@ -5 +5 @@\n-x\n+y\n")
    assert cr.commentable_lines(diff) == {"notes.md": {1, 2, 3}, "b.py": {5}}


def test_upsert_summary_leaves_one_comment_when_a_parallel_run_also_created_one(monkeypatch):
    # A forced and a push review can post at the same time; both find no summary and create one.
    store = {"comments": []}
    calls = []

    def fake_gh_api(args, payload=None):
        calls.append((args[0], args[2] if len(args) > 2 and args[1] == "--method" else "GET"))
        path = args[0]
        if path.endswith("/issues/5/comments") and "--method" not in args:
            return "\n".join(str(c) for c in store["comments"])
        if path.endswith("/issues/5/comments") and "POST" in args:
            store["comments"] += [101, 102]          # the parallel run's comment lands first
            return json.dumps({"id": 102})
        if "DELETE" in args:
            store["comments"].remove(int(path.rsplit("/", 1)[1]))
        return "{}"

    monkeypatch.setattr(cr, "_gh_api", fake_gh_api)
    cr.upsert_summary("o/r", 5, "github-actions[bot]", "body")
    assert store["comments"] == [101]
    assert ("repos/o/r/issues/comments/101", "PATCH") in calls     # the kept comment carries this run's body


def test_number_diff_gives_the_model_new_file_line_numbers_that_post_accepts():
    # The first real runs cited line numbers of the diff FILE (723 in a 650-line script), so every
    # finding fell off the diff; each number the model sees must be a commentable line.
    numbered = cr.number_diff(_DIFF)
    cited = {}
    for line in numbered.splitlines():
        column, _, raw = line.partition("|")
        if column.strip():
            cited.setdefault(raw, int(column))
    assert cited["+    added = 3"] == 12 and cited["     tail = 4"] == 13 and cited["+    x = 2"] == 41
    assert set(cited.values()) <= cr.commentable_lines(_DIFF)["braunschweig/a.py"]
    assert "      |-    old = 2" in numbered               # deleted lines carry no number


def test_a_line_separator_inside_a_source_line_does_not_shift_the_hunk_counts():
    # str.splitlines() would split "+a b" into two lines and push every later line off by one.
    diff = ("diff --git a/x.md b/x.md\n--- a/x.md\n+++ b/x.md\n@@ -1,1 +1,3 @@\n"
            " keep\n+a b\n+last\n")
    assert cr.commentable_lines(diff) == {"x.md": {1, 2, 3}}


def test_a_stale_review_leaves_the_newer_summary_alone(monkeypatch):
    calls = []

    def fake_gh_api(args, payload=None):
        calls.append(args)
        return "101" if args[0].endswith("/issues/5/comments") else "{}"

    monkeypatch.setattr(cr, "_gh_api", fake_gh_api)
    cr.upsert_summary("o/r", 5, "github-actions[bot]", "older review", stale=True)
    assert not any("PATCH" in call or "POST" in call for call in calls)


def test_state_block_round_trips_through_the_summary_comment_and_escapes_the_comment_end():
    state = cr.empty_state()
    state["findings"].append({"id": "abcdef123456-0", "path": "a.py", "line": 3, "severity": "high",
                              "scope": "introduced", "title": "x --> y breaks", "body": "b", "author_note": "",
                              "comment_id": 11, "status": "open"})
    body = "## Claude review\n\ntext\n" + cr.encode_state(state)
    assert "-->" not in cr.encode_state(state)[len(cr.STATE_MARKER):-len(cr.STATE_END)]
    assert cr.decode_state(body) == state
    assert cr.strip_state(body) == "## Claude review\n\ntext\n"
    assert cr.decode_state("## Claude review\n\nno block") is None
    assert cr.decode_state(cr.STATE_MARKER + "{not json" + cr.STATE_END) is None


def test_refresh_state_dismisses_what_the_author_closed_and_keeps_the_bot_resolution():
    state = cr.empty_state()
    mk = lambda i, status="open": {"id": f"s-{i}", "path": "a.py", "line": i, "severity": "low", "scope": "introduced",
                                   "title": f"t{i}", "body": "b", "author_note": "", "comment_id": i, "status": status}
    state["findings"] = [mk(1), mk(2), mk(3), mk(4, "resolved"), mk(5)]
    threads = {1: {"thread_id": "T1", "resolved": False, "replies": ["**Rejected:** the fallback is the API's."]},
               2: {"thread_id": "T2", "resolved": True, "replies": []},                       # resolved by a human
               3: {"thread_id": "T3", "resolved": False, "replies": ["Fixed in 1a2b3c: guarded the join"]},
               4: {"thread_id": "T4", "resolved": True, "replies": ["Re-review at 1a2b3c4d5e6f: no longer present."]},
               5: {"thread_id": "T5", "resolved": False, "replies": ["Not changed: pre-existing, out of scope"]}}
    out = cr.refresh_state(state, threads)
    assert [f["status"] for f in out["findings"]] == ["dismissed", "dismissed", "open", "resolved", "dismissed"]
    assert out["findings"][2]["author_note"] == "Fixed in 1a2b3c: guarded the join"
    assert [f["id"] for f in cr.open_findings(out)] == ["s-3"]


def test_state_cli_reads_the_summary_and_logs_the_fallback_for_a_summary_without_a_block(tmp_path, monkeypatch, capsys):
    bodies = {"101": "## Claude review (full tier, commit 1234)\n\nold-format summary\n" + cr.SUMMARY_MARKER}

    def fake_gh_api(args, payload=None):
        if args[0].endswith("/issues/5/comments"):
            return "101"
        if args[0].endswith("/issues/comments/101"):
            return json.dumps({"body": bodies["101"]})
        raise AssertionError(args)

    monkeypatch.setattr(cr, "_gh_api", fake_gh_api)
    monkeypatch.setattr(cr, "_fetch_threads", lambda repo, pr: {})
    out = tmp_path / "state.json"
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    assert cr.main(["state", "--repo", "o/r", "--pr", "5", "--out", str(out)]) == 0
    assert json.loads(out.read_text(encoding="utf-8")) == cr.empty_state()
    printed = capsys.readouterr().out
    assert "fallback: no state block" in printed and "no state block" in summary.read_text(encoding="utf-8")
    # a hand-edited (corrupt) state block is the same fallback, never a crash of the gate
    bodies["101"] += cr.STATE_MARKER + "{not json" + cr.STATE_END
    assert cr.main(["state", "--repo", "o/r", "--pr", "5", "--out", str(out)]) == 0
    assert json.loads(out.read_text(encoding="utf-8")) == cr.empty_state()
    assert "fallback: no state block" in capsys.readouterr().out


def test_merge_state_keys_rounds_by_commit_and_run_and_findings_by_id():
    existing = {"schema": 1, "rounds": [{"round": 1, "sha": "a" * 40, "run_id": "1", "automatic": True, "complete": True,
                                          "tier": "full", "cost_usd": 1.0, "findings": 1}],
                "findings": [{"id": "aaaaaaaaaaaa-0", "status": "open", "body": "b", "author_note": "n", "path": "a.py",
                              "line": 1, "severity": "high", "scope": "introduced", "title": "t", "comment_id": 7}]}
    delta = {"schema": 1, "rounds": [{"round": 2, "sha": "b" * 40, "run_id": "2", "automatic": True, "complete": False,
                                       "tier": "light", "cost_usd": 0.2, "findings": 0}],
             "findings": [{"id": "aaaaaaaaaaaa-0", "status": "resolved", "body": "b", "author_note": "n", "path": "a.py",
                           "line": 1, "severity": "high", "scope": "introduced", "title": "t", "comment_id": 7}]}
    merged = cr.merge_state(existing, delta)
    assert [r["round"] for r in merged["rounds"]] == [1, 2]
    assert merged["findings"][0]["status"] == "resolved" and "body" not in merged["findings"][0]
    assert cr.last_complete_sha(merged) == "a" * 40 and cr.automatic_rounds(merged) == 1
    # the same round posted twice (a retry) is one round
    assert len(cr.merge_state(merged, delta)["rounds"]) == 2


_THEN = """diff --git a/braunschweig/a.py b/braunschweig/a.py
index 1111111..2222222 100644
--- a/braunschweig/a.py
+++ b/braunschweig/a.py
@@ -10,3 +10,4 @@ def f():
     keep = 1
-    old = 2
+    new = 2
+    added = 3
     tail = 4
diff --git a/braunschweig/b.py b/braunschweig/b.py
--- a/braunschweig/b.py
+++ b/braunschweig/b.py
@@ -1 +1 @@
-x
+y
"""


def test_interdiff_ignores_shifted_hunks_and_reports_changed_new_and_reverted_files():
    # a merge of main added 7 lines above the hunk of a.py and changed nothing of the PR's own change;
    # b.py was reverted to main; c.py is new; a.py's second hunk is edited
    now = (_THEN.split("diff --git a/braunschweig/b.py")[0]
           .replace("@@ -10,3 +10,4 @@", "@@ -17,3 +17,4 @@").replace("index 1111111..2222222", "index 1111111..3333333")
           + "diff --git a/braunschweig/c.py b/braunschweig/c.py\n--- /dev/null\n+++ b/braunschweig/c.py\n@@ -0,0 +1 @@\n+z\n")
    unchanged = cr.interdiff(_THEN, now)
    assert (unchanged.changed, unchanged.unchanged, unchanged.reverted) == (["braunschweig/c.py"], ["braunschweig/a.py"], ["braunschweig/b.py"])
    assert unchanged.diff_text.startswith("diff --git a/braunschweig/c.py") and "a.py" not in unchanged.diff_text
    edited = cr.interdiff(_THEN, now.replace("+    added = 3", "+    added = 4"))
    assert edited.changed == ["braunschweig/a.py", "braunschweig/c.py"]
    assert cr.commentable_lines(edited.diff_text)["braunschweig/a.py"] == {17, 18, 19, 20}   # head line numbers
    # a renamed file is a changed new path and a reverted old one, never a crash
    renamed = cr.interdiff(_THEN, _THEN.replace("diff --git a/braunschweig/b.py b/braunschweig/b.py",
                                                "diff --git a/braunschweig/b.py b/braunschweig/b2.py\nsimilarity index 90%\nrename from braunschweig/b.py\nrename to braunschweig/b2.py"))
    assert "braunschweig/b2.py" in renamed.changed and "braunschweig/b.py" in renamed.reverted


def test_interdiff_cli_falls_back_to_the_whole_pr_when_the_reviewed_commit_is_not_an_ancestor(tmp_path, monkeypatch, capsys):
    state = cr.empty_state()
    state["rounds"].append({"round": 1, "sha": "c" * 40, "tier": "full", "run_id": "1", "automatic": True,
                            "complete": True, "cost_usd": 1.0, "findings": 0})
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    calls = []

    def fake_git(args):
        calls.append(args)
        if args[0] == "diff" and "--numstat" in args:
            return "3\t1\tbraunschweig/a.py\n"
        return _THEN

    monkeypatch.setattr(cr, "_git", fake_git)
    monkeypatch.setattr(cr, "_is_ancestor", lambda older, newer: False)
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    out_dir = tmp_path / "idiff"
    assert cr.main(["interdiff", "--config", str(_REPO / ".github" / "claude-review.yml"), "--state", str(state_path),
                    "--base-sha", "b" * 40, "--head-sha", "d" * 40, "--out-dir", str(out_dir)]) == 0
    meta = json.loads((out_dir / "interdiff.json").read_text(encoding="utf-8"))
    assert meta["last_sha"] == "c" * 40 and "not an ancestor" in meta["fallback"]
    assert meta["changed"] == ["braunschweig/a.py", "braunschweig/b.py"] and meta["reverted"] == []
    assert (out_dir / "interdiff.diff").read_text(encoding="utf-8") == _THEN
    assert "fallback" in capsys.readouterr().out and "not an ancestor" in summary.read_text(encoding="utf-8")
    # no previous round: the whole PR, no fallback, numstat of base...head
    state_path.write_text(json.dumps(cr.empty_state()), encoding="utf-8")
    assert cr.main(["interdiff", "--config", str(_REPO / ".github" / "claude-review.yml"), "--state", str(state_path),
                    "--base-sha", "b" * 40, "--head-sha", "d" * 40, "--out-dir", str(out_dir)]) == 0
    meta = json.loads((out_dir / "interdiff.json").read_text(encoding="utf-8"))
    assert meta["last_sha"] == "" and meta["fallback"] == ""
