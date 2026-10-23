"""Gate and cost record of the automatic Claude PR review (scripts/claude_review.py).

Defects caught: a review that runs (and costs) on a docs-only or generated-file PR, a
critical-path change that is skipped, an unrelated label event that re-triggers a paid
review, rename/binary numstat lines that crash or count wrongly, and a missing cost
record that is reported as a silent zero instead of an error.
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

    execution.write_text(json.dumps([{"type": "assistant"}]), encoding="utf-8")
    step_summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(step_summary))
    assert cr.main(["cost", "--execution-file", str(execution)]) == 1
    assert "No cost record" in step_summary.read_text(encoding="utf-8")
