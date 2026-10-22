"""Content-addressed validity in the fork's run() (spec 2026-10-16, section 4.4).

A parent that re-executes with a byte-identical output leaves its children cached (early cutoff).
The decision is taken per child from the output digest the CHILD recorded for that parent; a
legacy child, an unrecorded digest and every ephemeral registry fall back to upstream's rules,
and the fallback is logged. Every test runs real synpp stages in ``tmp_path``.
"""
import importlib
import json
import logging
import re
import shutil
import sys

import pytest

from tests.synpp_pristine import upstream_run

# A source stage: its output is the integer in <name>.payload; its validation token is the text of
# <name>.token plus the payload text. Editing the token alone re-executes the stage with an
# identical output; editing the payload re-executes it with a changed output. Neither is a config
# key, so no cache entry is ever renamed: only the run-time output rule can devalidate a child.
SOURCE = """
from pathlib import Path
def configure(context):
    context.config("trace")
    context.config("{name}_token")
    context.config("{name}_payload")
def validate(context):
    return (Path(context.config("{name}_token")).read_text() + "|"
            + Path(context.config("{name}_payload")).read_text())
def execute(context):
    with open(context.config("trace"), "a") as f:
        f.write("{name}\\n")
    return int(Path(context.config("{name}_payload")).read_text())
"""
# An inner stage: result = {result} over the sum of its parents' results.
INNER = """
def configure(context):
    context.config("trace")
{configure}
def execute(context):
    with open(context.config("trace"), "a") as f:
        f.write("{name}\\n")
    total = {total}
    return {result}
"""


class Pipeline:
    """Synthetic stages in ``root``; every execution appends the stage name to ``trace.txt``.

    ``graph`` maps a stage name to the tuple of its parents (in dependency order, the last entry is
    the run target); a stage without parents is a source stage. ``saturate`` lists inner stages
    whose result is ``int(total > 0)`` instead of ``total * 2``, so their output survives a changed
    parent output. ``ephemeral_edges`` lists ``(consumer, parent)`` pairs declared ephemeral.
    ``overrides`` replaces a stage's module source (``{name}`` formatted in) with a fixed text.
    """

    def __init__(self, root, monkeypatch, graph, *, payloads=None, saturate=(), ephemeral_edges=(),
                 overrides=None):
        from braunschweig import synpp_fork
        synpp_fork.install()
        from braunschweig.synpp_fork import pipeline as fork
        self.fork_run = fork.run
        self.root, self.monkeypatch, self.graph = root, monkeypatch, graph
        self.target = list(graph)[-1]
        self.working = root / "cache"
        self.working.mkdir()
        self.trace = root / "trace.txt"
        self.config = {"trace": str(self.trace)}
        monkeypatch.syspath_prepend(str(root))
        for name, parents in graph.items():
            if parents:
                stages = "\n".join('    context.stage("%s"%s)' % (
                    parent, ", ephemeral=True" if (name, parent) in ephemeral_edges else "")
                    for parent in parents)
                total = " + ".join('context.stage("%s")' % parent for parent in parents)
                result = "int(total > 0)" if name in saturate else "total * 2"
                source = INNER.format(name=name, configure=stages, total=total, result=result)
            else:
                source = SOURCE.format(name=name)
                for suffix, text in (("token", "v1"), ("payload", str((payloads or {}).get(name, 1)))):
                    path = root / ("%s.%s" % (name, suffix))
                    path.write_bytes(text.encode("utf-8"))
                    self.config["%s_%s" % (name, suffix)] = str(path)
            if overrides and name in overrides:
                source = overrides[name].format(name=name)
            (root / (name + ".py")).write_bytes(source.encode("utf-8"))

    def run(self, *, working=None, in_memory=False, run_function=None, rerun_required=False, targets=None):
        for name in self.graph:
            self.monkeypatch.delitem(sys.modules, name, raising=False)
        importlib.invalidate_caches()
        function = run_function or self.fork_run
        return function([{"descriptor": target} for target in (targets or [self.target])], self.config,
                        working_directory=None if in_memory else str(working or self.working),
                        rerun_required=rerun_required, verbose=True)

    def registry(self):
        import synpp.pipeline
        for name in self.graph:
            self.monkeypatch.delitem(sys.modules, name, raising=False)
        importlib.invalidate_caches()
        return synpp.pipeline.process_stages([{"descriptor": self.target}], self.config)

    def assess(self):
        from braunschweig.synpp_fork import metadata, staleness
        return staleness.assess(self.registry(), str(self.working), metadata.read_meta(str(self.working)),
                                rerun_required=False)

    def events(self):
        return self.trace.read_text(encoding="utf-8").splitlines() if self.trace.exists() else []

    def set_token(self, name, text):
        (self.root / (name + ".token")).write_bytes(text.encode("utf-8"))

    def set_payload(self, name, value):
        (self.root / (name + ".payload")).write_bytes(str(value).encode("utf-8"))

    def meta(self, working=None):
        return json.loads(((working or self.working) / "pipeline.json").read_text(encoding="utf-8"))

    def write_meta(self, meta):
        (self.working / "pipeline.json").write_text(json.dumps(meta), encoding="utf-8")

    def key(self, name, meta=None):
        return next(k for k in (meta or self.meta()) if k.split("__")[0] == name)

    def record(self, name):
        meta = self.meta()
        return meta[self.key(name, meta)]


TWO = {"ec_parent": (), "ec_child": ("ec_parent",)}


@pytest.fixture
def two_stage(tmp_path, monkeypatch):
    return Pipeline(tmp_path, monkeypatch, TWO, payloads={"ec_parent": 7})


def test_first_run_executes_both_and_records_hashes(two_stage):
    assert two_stage.run()["results"] == [14]
    assert two_stage.events() == ["ec_parent", "ec_child"]
    meta = two_stage.meta()
    parent_key = two_stage.key("ec_parent", meta)
    parent, child = meta[parent_key], meta[two_stage.key("ec_child", meta)]
    assert child["dependency_output_hashes"] == {parent_key: parent["output_hash"]["digest"]}
    assert parent["dependency_output_hashes"] == {}
    assert child["fork"] == parent["fork"] == {"format": 1}
    # an own reason is final: the parent's changed output on the very first run adds nothing to it
    assert child["stale_reasons"] == ["not_in_meta", "cache_missing"]
    assert parent["stale_reasons"] == ["not_in_meta", "cache_missing"]


def test_parent_token_change_with_identical_output_spares_the_child(two_stage):
    two_stage.run()
    two_stage.set_token("ec_parent", "v2")
    assert two_stage.run()["results"] == [14]
    assert two_stage.events() == ["ec_parent", "ec_child", "ec_parent"]


def test_parent_output_change_devalidates_the_child_without_renaming_it(two_stage):
    two_stage.run()
    keys_before = set(two_stage.meta())
    two_stage.set_payload("ec_parent", 8)           # token and output change, no config key does
    assert two_stage.run()["results"] == [16]
    assert two_stage.events() == ["ec_parent", "ec_child", "ec_parent", "ec_child"]
    assert set(two_stage.meta()) == keys_before     # same cache entries: the run-time rule decided
    assert two_stage.record("ec_child")["stale_reasons"] == ["parent_output_changed:ec_parent"]
    assert two_stage.record("ec_parent")["stale_reasons"] == ["token_changed:opaque"]


def test_run_devalidates_a_legacy_child_whose_parent_is_newer(two_stage, caplog):
    two_stage.run()
    meta = two_stage.meta()
    for record in meta.values():                    # strip the fork fields -> legacy entries
        for key in ("output_hash", "dependency_output_hashes", "fork", "stale_reasons"):
            record.pop(key, None)
    meta[two_stage.key("ec_parent", meta)]["updated"] += 10.0
    two_stage.write_meta(meta)
    with caplog.at_level(logging.INFO, logger="synpp"):
        two_stage.run()
    assert two_stage.events() == ["ec_parent", "ec_child", "ec_child"]
    assert "legacy timestamp rule" in caplog.text   # never a silent fallback


def test_spared_child_gets_its_parent_record_refreshed(two_stage):
    two_stage.run()
    two_stage.set_token("ec_parent", "v2")
    two_stage.run()
    assert two_stage.events() == ["ec_parent", "ec_child", "ec_parent"]     # the child was spared
    meta = two_stage.meta()
    parent_key = two_stage.key("ec_parent", meta)
    child = meta[two_stage.key("ec_child", meta)]
    assert child["dependencies"][parent_key] == meta[parent_key]["updated"]
    assert child["dependency_output_hashes"][parent_key] == meta[parent_key]["output_hash"]["digest"]
    executed = two_stage.events()
    two_stage.run()                                 # the refreshed record is a valid entry next time
    assert two_stage.events() == executed


def test_in_memory_run_executes_everything_and_returns_the_result(two_stage):
    result = two_stage.run(in_memory=True, rerun_required=True)
    assert result["results"] == [14]
    assert two_stage.events() == ["ec_parent", "ec_child"]
    assert len(result["stale"]) == 2 and not list(two_stage.working.iterdir())


JOIN = {"ec_pa": (), "ec_pb": (), "ec_join": ("ec_pa", "ec_pb")}


@pytest.mark.parametrize("pa_output_changes, pb_output_changes",
                         [(False, False), (True, False), (False, True), (True, True)])
def test_a_two_parent_child_executes_when_any_parent_output_changed(tmp_path, monkeypatch,
                                                                    pa_output_changes, pb_output_changes):
    pipe = Pipeline(tmp_path, monkeypatch, JOIN)
    pipe.run()
    for parent, changes in (("ec_pa", pa_output_changes), ("ec_pb", pb_output_changes)):
        if changes:
            pipe.set_payload(parent, 9)             # re-executes with a different output
        else:
            pipe.set_token(parent, "v2")            # re-executes with the same output
    pipe.run()
    events = pipe.events()
    assert events.count("ec_pa") == events.count("ec_pb") == 2
    assert events.count("ec_join") == (2 if pa_output_changes or pb_output_changes else 1)
    changed = {"parent_output_changed:" + name for name, flag in (("ec_pa", pa_output_changes),
                                                                  ("ec_pb", pb_output_changes)) if flag}
    if changed:
        assert set(pipe.record("ec_join")["stale_reasons"]) == changed


CHAIN = {"ec_a": (), "ec_b": ("ec_a",), "ec_c": ("ec_b",), "ec_d": ("ec_c",)}


def test_early_cutoff_keeps_a_whole_chain_cached_and_says_so(tmp_path, monkeypatch, caplog):
    pipe = Pipeline(tmp_path, monkeypatch, CHAIN)
    pipe.run()
    pipe.set_token("ec_a", "v2")
    with caplog.at_level(logging.INFO, logger="synpp"):
        assert pipe.run()["results"] == [8]
    assert pipe.events() == ["ec_a", "ec_b", "ec_c", "ec_d", "ec_a"]
    messages = [r.getMessage() for r in caplog.records]
    summary = next(i for i, m in enumerate(messages) if m.startswith("Executed 1 stages"))
    assert "early cutoff kept 3 stages cached" in messages[summary]
    assert "descendants of stale stages" in messages[summary]            # says what is counted
    progress = [m for m in messages if m.startswith("Pipeline progress:")]
    assert progress and all(re.fullmatch(r"Pipeline progress: \d+/\d+ known stale \(\d+\.\d\d%\)", m)
                            for m in progress)         # the total can still grow while the run goes on
    tail = "\n".join(messages[summary:])               # the end-of-run summary names both groups
    assert "ec_a" in tail and "token_changed:opaque" in tail
    assert all(name in tail for name in ("ec_b", "ec_c", "ec_d"))


def test_a_devalidated_middle_stage_with_unchanged_output_cuts_the_cascade_off(tmp_path, monkeypatch, caplog):
    pipe = Pipeline(tmp_path, monkeypatch, CHAIN, saturate=("ec_b",))
    pipe.run()
    pipe.set_payload("ec_a", 2)                        # a's output changes, b = int(a > 0) does not
    with caplog.at_level(logging.INFO, logger="synpp"):
        assert pipe.run()["results"] == [4]
    assert pipe.events() == ["ec_a", "ec_b", "ec_c", "ec_d", "ec_a", "ec_b"]
    assert "early cutoff kept 2 stages cached" in caplog.text


def test_a_required_stage_re_executes_every_run_but_its_unchanged_output_spares_the_stage_behind_it(
        tmp_path, monkeypatch):
    """The production mode (rerun_required): run targets execute on every run, upstream then also
    re-executed every stage between two targets; with an unchanged output the fork keeps it cached."""
    graph = {"ec_p": (), "ec_c": ("ec_p",), "ec_x": ("ec_c",)}
    pipe = Pipeline(tmp_path, monkeypatch, graph, payloads={"ec_p": 3})
    assert pipe.run(rerun_required=True, targets=["ec_p", "ec_x"])["results"] == [3, 12]
    assert pipe.run(rerun_required=True, targets=["ec_p", "ec_x"])["results"] == [3, 12]
    assert pipe.events() == ["ec_p", "ec_c", "ec_x", "ec_p", "ec_x"]          # ec_c was not re-executed
    assert pipe.record("ec_x")["stale_reasons"] == ["required"]
    # A changed output devalidates ec_c; ec_x (stale only for `required`) records the parent reason too.
    pipe.set_payload("ec_p", 4)
    assert pipe.run(rerun_required=True, targets=["ec_p", "ec_x"])["results"] == [4, 16]
    assert pipe.events()[5:] == ["ec_p", "ec_c", "ec_x"]
    assert pipe.record("ec_c")["stale_reasons"] == ["parent_output_changed:ec_p"]
    assert pipe.record("ec_x")["stale_reasons"] == ["required", "parent_output_changed:ec_c"]


def test_a_legacy_child_of_a_re_executed_parent_recomputes_and_is_logged(two_stage, caplog):
    two_stage.run()
    meta = two_stage.meta()
    child = meta[two_stage.key("ec_child", meta)]
    del child["dependency_output_hashes"]              # only the child is legacy; the parent has a hash
    two_stage.write_meta(meta)
    two_stage.set_token("ec_parent", "v2")             # parent re-executes with an identical output
    with caplog.at_level(logging.INFO, logger="synpp"):
        two_stage.run()
    assert two_stage.events() == ["ec_parent", "ec_child", "ec_parent", "ec_child"]
    reason = two_stage.record("ec_child")["stale_reasons"]
    assert reason == ["parent_output_unrecorded:ec_parent"]
    assert "parent_output_unrecorded:ec_parent" in caplog.text


@pytest.mark.parametrize("tampered, child_is_spared", [("child", False), ("parent", True)])
def test_the_child_s_own_recorded_digest_decides_not_the_parent_s_previous_one(two_stage, tampered,
                                                                               child_is_spared):
    """The child's record is the exact claim of what it was computed from (ruling R16-2).

    A child that recorded a different digest than the parent now has is never spared, even when
    the parent's re-executed output equals its own previous output; a child that recorded the
    parent's true digest is spared even when the parent's previous record is unrelated.
    """
    two_stage.run()
    meta = two_stage.meta()
    parent_key = two_stage.key("ec_parent", meta)
    child = meta[two_stage.key("ec_child", meta)]
    if tampered == "child":
        child["dependency_output_hashes"][parent_key] = "0" * 64
    else:
        meta[parent_key]["output_hash"]["digest"] = "0" * 64
    two_stage.write_meta(meta)
    two_stage.set_token("ec_parent", "v2")             # forces the re-execution, output is identical
    two_stage.run()
    expected = ["ec_parent", "ec_child", "ec_parent"] + ([] if child_is_spared else ["ec_child"])
    assert two_stage.events() == expected


EPHEMERAL = {"ec_g": (), "ec_e": ("ec_g",), "ec_c": ("ec_e",), "ec_x": ("ec_c", "ec_g")}


def test_an_ephemeral_registry_runs_upstream_s_eager_rule_for_the_whole_run(tmp_path, monkeypatch, caplog):
    """ec_e is an uncached ephemeral stage: early cutoff is off and the fork equals upstream exactly."""
    pipe = Pipeline(tmp_path, monkeypatch, EPHEMERAL, ephemeral_edges={("ec_c", "ec_e")})
    pipe.run(run_function=upstream_run())              # a legacy cache, the state upstream leaves
    shutil.copytree(pipe.working, tmp_path / "cache_fork")
    shutil.copytree(pipe.working, tmp_path / "cache_upstream")
    pipe.set_token("ec_g", "v2")                       # ec_g re-executes with an identical output
    trace_start = len(pipe.events())
    with caplog.at_level(logging.INFO, logger="synpp"):
        fork = pipe.run(working=tmp_path / "cache_fork")
    fork_events = pipe.events()[trace_start:]
    trace_start = len(pipe.events())
    upstream = pipe.run(working=tmp_path / "cache_upstream", run_function=upstream_run())
    upstream_events = pipe.events()[trace_start:]
    assert fork_events == upstream_events == ["ec_g", "ec_e", "ec_c", "ec_x"]
    assert fork["stale"] == upstream["stale"] and len(fork["stale"]) == 4
    assert "early cutoff is disabled" in caplog.text
    fork_meta = pipe.meta(tmp_path / "cache_fork")
    for name in ("ec_e", "ec_c", "ec_x"):
        assert fork_meta[pipe.key(name, fork_meta)]["stale_reasons"] == ["ancestor_stale (eager rule)"]


def test_an_in_memory_run_with_ephemeral_stages_does_not_claim_a_disabled_early_cutoff(tmp_path, monkeypatch,
                                                                                       caplog):
    """Nothing is cached without a working directory, so there is no early cutoff to disable."""
    pipe = Pipeline(tmp_path, monkeypatch, EPHEMERAL, ephemeral_edges={("ec_c", "ec_e")})
    with caplog.at_level(logging.INFO, logger="synpp"):
        assert pipe.run(in_memory=True)["results"] == [10]
    assert pipe.events() == ["ec_g", "ec_e", "ec_c", "ec_x"]
    assert "early cutoff is disabled" not in caplog.text
    assert "No working directory" in caplog.text


def test_an_ephemeral_registry_marks_every_stale_if_stage_a_certain_recompute_and_says_why(tmp_path, monkeypatch):
    """Records written by the fork carry digests, so without ephemeral stages ec_c and ec_x would be
    STALE-IF; the eager rule makes them certain, and assess predicts exactly what the fork executes."""
    from braunschweig.synpp_fork import staleness
    pipe = Pipeline(tmp_path, monkeypatch, EPHEMERAL, ephemeral_edges={("ec_c", "ec_e")})
    pipe.run()
    pipe.set_token("ec_g", "v2")
    assessments = pipe.assess()
    by_name = {a.stage: a for a in assessments.values()}
    assert by_name["ec_g"].stale
    for name in ("ec_e", "ec_c", "ec_x"):
        assert by_name[name].stale_if == ["ec_g"] and by_name[name].certain_recompute
    report = staleness.format_report(assessments)
    assert "RECOMPUTE ec_x" in report and "eager rule" in report and "ephemeral" in report
    predicted = {a.stage for a in assessments.values() if a.stale or a.certain_recompute}
    assert {h.split("__")[0] for h in pipe.run()["stale"]} == predicted == {"ec_g", "ec_e", "ec_c", "ec_x"}


def test_assess_and_run_agree_on_whether_the_eager_rule_applies(tmp_path, monkeypatch, caplog):
    """Only an UNCACHED ephemeral stage turns early cutoff off: run() flips a cached one to non-ephemeral."""
    pipe = Pipeline(tmp_path, monkeypatch, EPHEMERAL, ephemeral_edges={("ec_c", "ec_e")})
    pipe.run()
    with caplog.at_level(logging.WARNING, logger="synpp"):
        assert any(a.eager_rule for a in pipe.assess().values())               # ec_e was removed after use
        pipe.run()
    assert "early cutoff is disabled" in caplog.text
    caplog.clear()
    ephemeral_key = pipe.key("ec_e")
    (pipe.working / (ephemeral_key + ".p")).write_bytes(b"")                   # the cache of ec_e is back
    (pipe.working / (ephemeral_key + ".cache")).mkdir()
    with caplog.at_level(logging.WARNING, logger="synpp"):
        assert not any(a.eager_rule for a in pipe.assess().values())
        pipe.run()
    assert "early cutoff is disabled" not in caplog.text


def _child_with(declaration):
    """ec_child's module source with an extra module-level line (the output-exclusion declaration)."""
    return INNER.format(name="ec_child", configure='    context.stage("ec_parent")',
                        total='context.stage("ec_parent")', result="total") + "\n" + declaration + "\n"


def test_a_malformed_output_exclusion_fails_before_any_stage_executes(tmp_path, monkeypatch):
    graph = {"ec_parent": (), "ec_child": ("ec_parent",), "ec_leaf": ("ec_child",)}
    pipe = Pipeline(tmp_path, monkeypatch, graph,
                    overrides={"ec_child": _child_with('_OUTPUT_HASH_EXCLUDE = "*.log"')})   # a str, not a tuple
    with pytest.raises(TypeError, match="_OUTPUT_HASH_EXCLUDE"):
        pipe.run()
    assert pipe.events() == []                          # not even the healthy parent ran
    assert not [p for p in pipe.working.iterdir() if p.name.endswith((".cache", ".p"))]


def test_a_validate_that_raises_for_a_new_stage_aborts_the_run_before_any_stage_executes(tmp_path, monkeypatch):
    """Upstream step 4.8 validates EVERY cached stage before anything executes, new stages included.

    Many validate() functions raise on a missing input; a run with a new stage must fail at the start,
    not at that stage's turn after hours of upstream stages. The explainer fails the same way.
    """
    child = _child_with("def validate(context):\n    raise RuntimeError('missing input of ec_child')")
    pipe = Pipeline(tmp_path, monkeypatch, TWO, overrides={"ec_child": child})
    (tmp_path / "cache_upstream").mkdir()
    with pytest.raises(RuntimeError, match="missing input of ec_child"):
        pipe.run(working=tmp_path / "cache_upstream", run_function=upstream_run())
    assert pipe.events() == []                          # the contract: upstream executes nothing either
    with pytest.raises(RuntimeError, match="missing input of ec_child"):
        pipe.run()
    assert pipe.events() == []
    with pytest.raises(RuntimeError, match="missing input of ec_child"):
        pipe.assess()


def test_an_in_memory_run_validates_every_stage_before_anything_executes(tmp_path, monkeypatch):
    """Upstream step 4.8 also runs with working_directory None: validate() of the second stage must abort
    the run before the FIRST stage executes (cache path "None/<hash>.cache", as upstream builds it)."""
    child = _child_with("def validate(context):\n    raise RuntimeError('missing input of ec_child')")
    pipe = Pipeline(tmp_path, monkeypatch, TWO, overrides={"ec_child": child})
    with pytest.raises(RuntimeError, match="missing input of ec_child"):
        pipe.run(in_memory=True, run_function=upstream_run())
    assert pipe.events() == []                          # the contract: upstream executes nothing either
    with pytest.raises(RuntimeError, match="missing input of ec_child"):
        pipe.run(in_memory=True)
    assert pipe.events() == []


def test_a_malformed_exclusion_of_a_stage_devalidated_mid_run_fails_before_it_executes(tmp_path, monkeypatch):
    """ec_child is valid when the run starts, so only the check before its own execution can catch it."""
    helper = tmp_path / "ec_exclusions.py"
    helper.write_bytes(b"EXCLUDE = ()\n")
    child = _child_with("from ec_exclusions import EXCLUDE as _OUTPUT_HASH_EXCLUDE")
    pipe = Pipeline(tmp_path, monkeypatch, TWO, overrides={"ec_child": child})
    pipe.run()
    helper.write_bytes(b'EXCLUDE = "*.log"\n')          # not part of ec_child's own source
    monkeypatch.delitem(sys.modules, "ec_exclusions", raising=False)
    pipe.set_payload("ec_parent", 5)                    # the parent's output changes: the child is due
    with pytest.raises(TypeError, match="ec_child.*_OUTPUT_HASH_EXCLUDE"):
        pipe.run()
    assert pipe.events() == ["ec_parent", "ec_child", "ec_parent"]      # the child never executed again


LOGGING_SOURCE = SOURCE.replace("    return int(", """    import time
    from pathlib import Path as P
    P(context.path(), "run.log").write_text(str(time.time_ns()))
    return int(""")


@pytest.mark.parametrize("declared, child_is_spared", [(True, True), (False, False)])
def test_declared_output_exclusions_make_a_log_file_irrelevant_to_the_digest(tmp_path, monkeypatch, declared,
                                                                             child_is_spared):
    """A timestamp written into the cache directory changes the digest unless the stage excludes it."""
    source = LOGGING_SOURCE + ('\n_OUTPUT_HASH_EXCLUDE = ("*.log",)\n' if declared else "\n")
    pipe = Pipeline(tmp_path, monkeypatch, TWO, overrides={"ec_parent": source})
    pipe.run()
    pipe.set_token("ec_parent", "v2")
    pipe.run()
    assert pipe.events() == ["ec_parent", "ec_child", "ec_parent"] + ([] if child_is_spared else ["ec_child"])
