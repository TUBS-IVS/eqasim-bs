"""Behavioural tests for ``scripts/isolated_run.py`` (isolated pipeline runs on the server).

No real git, conda, server or pipeline: everything the tool asks of the machine goes through
``isolated_run.Host``, which these tests replace with a fake that answers like git and the
run's helper scripts and records every call. The tests therefore run on Linux and Windows; only
the steps that need POSIX symlinks or ``cp`` skip where those are missing.
"""
from __future__ import annotations

import datetime
import json
import os
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from scripts import isolated_run as tool

SHA_BS = "1" * 40
SHA_JAVA = "2" * 40
SHA_BUNDLE = "3" * 40
SHA_SHARED_HEAD = "4" * 40
TRACKED = ("eqasim-data/DOWNLOAD_CHECKLIST_BS.md", "eqasim-data/data/committed.csv")
# commands that write; a dry run must never ask for one of them
MUTATING = {"fetch", "worktree", "update-ref", "checkout", "switch", "reset", "pull", "merge", "cp"}
NOW = datetime.datetime(2026, 10, 23, 12, 0, tzinfo=datetime.timezone.utc)

# The overlay of an earlier isolated run (shaped like the WP-E smoke's i465_wpe.yml).
SOURCE_OVERLAY = {
    "working_directory": "/home/felix/wt-old/cache_old",
    "run": ["synthesis.output", "matsim.output"],
    "config": {
        "sampling_rate": 0.01,
        "output_path": "/home/felix/wt-old/output_old",
        "analysis_working_directory": "/home/felix/wt-old/cache_old",
        "output_prefix": "braunschweig_1pct_old_",
        "matsim_last_iteration": 100,
        "braunschweig.population.popsim.work_dir": "/home/felix/wt-old/popsim_work",
        "braunschweig.population.popsim.num_workers": 1,
        "cache_share_enabled": True,
        "cache_share_export": True,
        "cache_share_recompute": [],
        "braunschweig.outside_tour_modes.enabled": True,
        "synpp_expected_stale_stages": ["matsim.output"],
    },
}
STALE_WITHOUT_POPSIM = ["braunschweig.freight.extraction", "braunschweig.synthesis.incommuters", "matsim.output"]
EXECUTING_LINE = "12:00:10 │ INFO    │ synpp            │ Executing stage matsim.output__0123abcd ..."


def explain_report(popsim_label=None, summary_stale=None):
    """An explain report in the format of braunschweig.synpp_fork.staleness.format_report."""
    lines = [
        "WARNING! A Java JDK of at least version 11 is recommended.",
        "HIT      braunschweig.data.alkis",
        "STALE    braunschweig.freight.extraction  [module_hash_changed]",
        "STALE    braunschweig.synthesis.incommuters  [not_in_meta, cache_missing; config vs nearest variant: "
        "output_path: '/home/felix/wt-old/output_old' -> '../output']",
        "STALE-IF braunschweig.freight.trips  [if output of braunschweig.freight.extraction changes]",
        "RECOMPUTE matsim.simulation.prepare  [ancestor braunschweig.synthesis.incommuters is stale; "
        "no recorded output hash cuts the cascade off]",
        "EPHEMERAL synthesis.population.sampled  [not cached; synpp recomputes it only if a descendant runs]",
        "STALE    matsim.output  [required]",
        "         legacy timestamp rule for parents: data.spatial.iris",
    ]
    total, stale = 7, 3
    if popsim_label:
        lines.append(f"{popsim_label:<8} {tool.POPSIM_STAGE}" + ("  [module_hash_changed]" if popsim_label == "STALE" else ""))
        total += 1
        stale += popsim_label == "STALE"
    lines.append("# WARNING: 1 ephemeral stage(s) in the registry; the fork disables early cutoff for such runs (spec 4.4)")
    lines.append(f"# {total} stages: {stale if summary_stale is None else summary_stale} stale for own reasons, "
                 "1 will recompute (stale ancestor, no recorded output hash cuts it off), "
                 "1 may recompute (stale ancestor, early cutoff decides), 2 hits, 1 uncached ephemeral")
    return "\n".join(lines) + "\n"


def run_log(explain_text, *tail):
    """A launch log: start line, the launcher's [explain] block, then ``tail``."""
    lines = [f"{tool.LOG_TAG} run=r1 eqasim-bs={SHA_BS} eqasim-java-bs={SHA_JAVA} start=2026-10-23T12:00:01+02:00"]
    lines += [f"12:00:09 │ INFO    │ braunschweig     │ [explain] {line}" for line in explain_text.splitlines()]
    return "\n".join(lines + list(tail)) + "\n"


def split_command(argv):
    """(repository, subcommand, rest) of a git call; (None, argv[0], argv[1:]) of anything else."""
    if argv[0] != "git":
        return None, argv[0], argv[1:]
    arguments, repository = argv[1:], None
    while arguments[0].startswith("-"):
        if arguments[0] == "-C":
            repository, arguments = arguments[1], arguments[2:]
        else:
            arguments = arguments[1:]
    return repository, arguments[0], arguments[1:]


class FakeProcess:
    pid = 4242

    def __init__(self, returncode=None):
        self.returncode = returncode

    def poll(self):
        return self.returncode


class FakeMachine:
    """Answers the tool's git, cp and helper-script calls like the server would; records every call."""

    def __init__(self, explain_text=None, inputs_returncode=0, launch_log=None, launch_returncode=None):
        self.calls = []
        self.popen_calls = []
        self.explain_text = explain_report("HIT") if explain_text is None else explain_text
        self.inputs_returncode = inputs_returncode
        self.launch_log = launch_log
        self.launch_returncode = launch_returncode
        self.worktrees = {}

    def host(self, root, free_bytes=10 ** 15):
        proc_root = root / "proc"
        proc_root.mkdir(exist_ok=True)
        return tool.Host(run=self.run, popen=self.popen, proc_root=proc_root, disk_free=lambda path: free_bytes,
                         now=lambda: NOW, sleep=lambda seconds: None)

    def run(self, argv, cwd=None, env=None):
        argv = [str(item) for item in argv]
        self.calls.append(argv)
        if argv[0] == "git":
            return self._git(argv)
        if argv[0] == "cp":
            assert argv[1] == "-a"
            shutil.copytree(argv[-2], argv[-1], symlinks=True)
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv[1].endswith("explain_stale_stages.py"):
            return subprocess.CompletedProcess(argv, 0, self.explain_text, "")
        if argv[1].endswith("verify_braunschweig_inputs.py"):
            return subprocess.CompletedProcess(argv, self.inputs_returncode, "inputs checked\n", "")
        raise AssertionError(f"unexpected command {argv}")

    def _git(self, argv):
        repository, command, rest = split_command(argv)
        java = repository is not None and Path(repository).name.endswith("eqasim-java-bs")
        output, returncode = "", 0
        if command == "rev-parse" and rest == ["--git-dir"]:
            output = ".git\n"
        elif command == "rev-parse" and rest == ["--show-toplevel"]:
            output = repository + "\n"
        elif command == "rev-parse" and rest == ["HEAD"]:
            output = self.worktrees.get(repository, SHA_SHARED_HEAD) + "\n"
        elif command == "rev-parse" and rest[0] == "--verify":
            reference = rest[1].replace("^{commit}", "")
            output = (SHA_BUNDLE if reference.startswith("refs/isolated-runs/")
                      else SHA_JAVA if java else SHA_BS) + "\n"
        elif command == "branch":
            output = "someone-elses-branch\n"
        elif command == "status":
            output = " M scripts/x.py\n?? scratch.txt\n"
        elif command == "bundle" and rest[0] == "list-heads":
            output = f"{SHA_BUNDLE} refs/heads/fix/x\n"
        elif command == "cat-file":
            returncode = 1  # the bundle's commit is not in the shared repository before its fetch
        elif command == "ls-tree":
            output = "\n".join(TRACKED) + "\n"
        elif command == "worktree" and rest[:2] == ["add", "--detach"]:
            target = Path(rest[2])
            for tracked in TRACKED if not java else ("pom.xml",):
                (target / tracked).parent.mkdir(parents=True, exist_ok=True)
                (target / tracked).write_text("tracked\n", encoding="utf-8")
            self.worktrees[str(target)] = rest[3]
        elif command not in ("fetch", "bundle"):
            raise AssertionError(f"unexpected git call {argv}")
        return subprocess.CompletedProcess(argv, returncode, output, "")

    def popen(self, argv, **kwargs):
        self.popen_calls.append((argv, kwargs))
        if self.launch_log is not None:
            kwargs["stdout"].write(self.launch_log.encode("utf-8"))
            kwargs["stdout"].flush()
        return FakeProcess(self.launch_returncode)


@pytest.fixture
def root(tmp_path):
    """``tmp_path`` with Windows 8.3 short names expanded, as in the tool's resolved paths."""
    return tmp_path.resolve()


def symlinks_supported(directory):
    try:
        os.symlink(directory, directory / "probe-link", target_is_directory=True)
    except (OSError, NotImplementedError):
        return False
    os.unlink(directory / "probe-link")
    return True


def age_tree(top, seconds):
    """Backdate every entry under ``top`` so a cache source does not look like a live run's."""
    stamp = NOW.timestamp() - seconds
    for directory, names, files in os.walk(top):
        for name in names + files:
            os.utime(os.path.join(directory, name), (stamp, stamp))
        os.utime(directory, (stamp, stamp))


def make_run_dir(root, name="r1"):
    """A run directory as `setup` leaves it, reduced to what overlay, gate and launch read."""
    run_dir = root / name
    (run_dir / tool.BS_DIR / "configs").mkdir(parents=True)
    (run_dir / tool.JAVA_DIR).mkdir()
    (run_dir / tool.BS_DIR / "configs" / "base_bs.yml").write_text(
        yaml.safe_dump({"config": {tool.POPSIM_WORK_DIR_KEY: "eqasim-data/popsim_work_base"}}), encoding="utf-8")
    (run_dir / tool.RUN_JSON).write_text(json.dumps({"schema_version": tool.RECORD_SCHEMA_VERSION, "run_name": name}),
                                         encoding="utf-8")
    source = root / "source_overlay.yml"
    source.write_text(yaml.safe_dump(SOURCE_OVERLAY, sort_keys=False), encoding="utf-8")
    return run_dir, source


def load_yaml(path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def load_record(run_dir):
    return json.loads((run_dir / tool.RUN_JSON).read_text(encoding="utf-8"))


@pytest.mark.parametrize("prefix", [None, "braunschweig_1pct_new_"])
def test_overlay_points_run_state_into_the_run_and_keeps_every_scientific_key(root, prefix):
    run_dir, source = make_run_dir(root)
    machine = FakeMachine()
    argv = ["overlay", str(run_dir), "--from", str(source)] + (["--prefix", prefix] if prefix else [])

    assert tool.main(argv, host=machine.host(root)) == 0

    doc = load_yaml(run_dir / tool.RUN_YML)
    config = doc["config"]
    launch_directory = run_dir / tool.BS_DIR
    # the run's state lands in RUN_DIR/cache and RUN_DIR/output, resolved from the launch directory
    assert (launch_directory / doc["working_directory"]).resolve() == run_dir / "cache"
    assert (launch_directory / config["analysis_working_directory"]).resolve() == run_dir / "cache"
    assert (launch_directory / config["output_path"]).resolve() == run_dir / "output"
    assert config["cache_share_enabled"] is False and config["cache_share_export"] is False
    assert tool.GATE_KEY not in config, "the source run's gate list must not gate this run"
    assert config["output_prefix"] == (prefix or SOURCE_OVERLAY["config"]["output_prefix"])
    # every other key, the popsim work_dir string (part of the popsim hash) included, is the source's
    changed = {"output_path", "analysis_working_directory", "output_prefix", "cache_share_enabled",
               "cache_share_export", tool.GATE_KEY}
    assert {k: v for k, v in config.items() if k not in changed} == \
        {k: v for k, v in SOURCE_OVERLAY["config"].items() if k not in changed}
    assert doc["run"] == SOURCE_OVERLAY["run"]
    assert load_record(run_dir)["overlay"]["source"] == str(source)


def test_explain_report_that_does_not_add_up_is_refused():
    # a parser that silently missed STALE lines would write a gate list that lets those stages through
    with pytest.raises(tool.IsolatedRunError, match="does not add up"):
        tool.parse_explain_report(explain_report("HIT", summary_stale=4))
    with pytest.raises(tool.IsolatedRunError, match="summary"):
        tool.parse_explain_report("HIT      braunschweig.data.alkis\n")


@pytest.mark.parametrize("label, allow, work_dir, expected_exit", [
    pytest.param("HIT", False, None, 0, id="popsim-hit"),
    pytest.param(None, False, None, 0, id="popsim-not-in-dag"),
    pytest.param("STALE-IF", False, None, 0, id="popsim-early-cutoff-decides"),
    pytest.param("STALE", False, None, 2, id="popsim-stale-refused"),
    pytest.param("RECOMPUTE", False, None, 2, id="popsim-recompute-refused"),
    pytest.param("STALE", True, "../popsim_work", 0, id="popsim-stale-allowed-run-local-work-dir"),
    pytest.param("STALE", True, None, 2, id="popsim-allowed-but-writes-outside-the-run"),
])
def test_gate_writes_the_stale_list_and_refuses_a_popsim_recompute(root, label, allow, work_dir, expected_exit):
    run_dir, source = make_run_dir(root)
    machine = FakeMachine(explain_text=explain_report(label))
    host = machine.host(root)
    assert tool.main(["overlay", str(run_dir), "--from", str(source)], host=host) == 0
    if work_dir:
        doc = load_yaml(run_dir / tool.RUN_YML)
        doc["config"][tool.POPSIM_WORK_DIR_KEY] = work_dir
        (run_dir / tool.RUN_YML).write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    before = (run_dir / tool.RUN_YML).read_bytes()

    exit_code = tool.main(["gate", str(run_dir), "--write"] + (["--allow-popsim"] if allow else []), host=host)

    assert exit_code == expected_exit
    assert list((run_dir / "logs").glob("explain_*.log")), "the explain report is kept in RUN_DIR/logs"
    if expected_exit:
        assert (run_dir / tool.RUN_YML).read_bytes() == before
        assert "gate" not in load_record(run_dir)
        return
    expected = sorted(STALE_WITHOUT_POPSIM + ([tool.POPSIM_STAGE] if label == "STALE" else []))
    assert load_yaml(run_dir / tool.RUN_YML)["config"][tool.GATE_KEY] == expected
    assert load_record(run_dir)["gate"]["stale_stages"] == expected
    verify_call = next(call for call in machine.calls if call[1].endswith("verify_braunschweig_inputs.py"))
    assert "--matsim" in verify_call, "the run includes MATSim stages, so MATSim inputs are checked"


def test_gate_refuses_missing_inputs(root):
    run_dir, source = make_run_dir(root)
    host = FakeMachine(inputs_returncode=1).host(root)
    assert tool.main(["overlay", str(run_dir), "--from", str(source)], host=host) == 0

    assert tool.main(["gate", str(run_dir), "--write"], host=host) == 1
    assert tool.GATE_KEY not in load_yaml(run_dir / tool.RUN_YML)["config"]
    assert tool.main(["gate", str(run_dir), "--write", "--skip-input-check"], host=host) == 0


def gated_run(root, machine):
    run_dir, source = make_run_dir(root)
    host = machine.host(root)
    assert tool.main(["overlay", str(run_dir), "--from", str(source)], host=host) == 0
    assert tool.main(["gate", str(run_dir), "--write"], host=host) == 0
    java_home = root / "jdk"
    (java_home / "bin").mkdir(parents=True)
    (java_home / "bin" / "java").write_text("", encoding="utf-8")
    conda_sh = root / "conda.sh"
    conda_sh.write_text("", encoding="utf-8")
    launch = ["launch", str(run_dir), "--java-home", str(java_home), "--conda-sh", str(conda_sh)]
    return run_dir, source, host, launch, java_home


@pytest.mark.parametrize("log_tail, returncode, expected_exit", [
    pytest.param([EXECUTING_LINE], None, 0, id="gate-passed"),
    pytest.param(["12:00:10 │ ERROR   │ braunschweig     │ [explain] aborting: stale stages not in "
                  "synpp_expected_stale_stages: braunschweig.popsim.stage"], 2, 1, id="gate-aborted"),
])
def test_launch_starts_one_detached_gated_run(root, log_tail, returncode, expected_exit):
    machine = FakeMachine(launch_log=run_log(explain_report("HIT"), *log_tail), launch_returncode=returncode)
    run_dir, source, host, launch, java_home = gated_run(root, machine)

    assert tool.main(launch, host=host) == expected_exit

    [(argv, kwargs)] = machine.popen_calls
    assert argv[:4] == ["setsid", "nohup", "bash", "-c"]
    script = argv[4]
    assert "conda activate eqasim" in script
    assert f"export JAVA_HOME={shlex.quote(str(java_home))}" in script
    assert "export PYTHONUTF8=1" in script
    assert f"python scripts/run_synpp.py configs/base_bs.yml {shlex.quote(str(run_dir / tool.RUN_YML))}" in script
    assert "exit=$rc" in script, "the exit line records the launcher's exit status"
    assert kwargs["cwd"] == str(run_dir / tool.BS_DIR)
    assert kwargs["stdin"] is subprocess.DEVNULL
    [log] = (run_dir / "logs").glob("run_*.log")
    [launch_record] = load_record(run_dir)["launches"]
    assert launch_record["pid"] == FakeProcess.pid and launch_record["log"] == str(log)
    # a launched run.yml is never replaced: its provenance belongs to the cache it wrote
    assert tool.main(["overlay", str(run_dir), "--from", str(source), "--force"], host=host) == 1


@pytest.mark.parametrize("problem", ["already-running", "no-gate", "edited-after-gate", "forced-without-gate"])
def test_launch_refuses_a_second_start_and_an_ungated_run(root, problem):
    machine = FakeMachine(launch_log=run_log(explain_report("HIT"), EXECUTING_LINE))
    run_dir, source, host, launch, java_home = gated_run(root, machine)
    run_yml = run_dir / tool.RUN_YML
    if problem == "already-running":
        (host.proc_root / "777").mkdir()
        (host.proc_root / "777" / "cmdline").write_bytes(
            b"\0".join([b"python", b"scripts/run_synpp.py", b"configs/base_bs.yml", str(run_yml).encode()]) + b"\0")
    elif problem in ("no-gate", "forced-without-gate"):
        assert tool.main(["overlay", str(run_dir), "--from", str(source), "--force"], host=host) == 0
    elif problem == "edited-after-gate":
        run_yml.write_text(run_yml.read_text(encoding="utf-8") + "dryrun: false\n", encoding="utf-8")
    if problem == "forced-without-gate":
        launch.append("--force-no-gate")

    exit_code = tool.main(launch, host=host)

    if problem == "forced-without-gate":
        assert exit_code == 0 and len(machine.popen_calls) == 1
    else:
        assert exit_code == 1 and machine.popen_calls == []
        assert "launches" not in load_record(run_dir)


def test_setup_dry_run_plans_every_step_and_changes_nothing(root, capsys):
    shared_bs, shared_java = root / "shared" / "eqasim-bs", root / "shared" / "eqasim-java-bs"
    (shared_bs / "eqasim-data" / "data").mkdir(parents=True)
    (shared_bs / "eqasim-data" / "data" / "raw.csv").write_text("x", encoding="utf-8")
    shared_java.mkdir()
    bundle = root / "bs.bundle"
    bundle.write_bytes(b"bundle")
    cache = root / "old_cache"
    (cache / "stage.cache").mkdir(parents=True)
    (cache / "stage.p").write_bytes(b"12345")
    age_tree(cache, 3600)
    run_dir = root / "runs" / "i999_smoke"
    machine = FakeMachine()

    exit_code = tool.main(["setup", str(run_dir), "--bs", "fix/x", "--bs-bundle", str(bundle), "--java", "origin/main",
                           "--cache-from", str(cache), "--dry-run", "--shared-bs", str(shared_bs),
                           "--shared-java", str(shared_java)], host=machine.host(root))

    assert exit_code == 0
    assert not run_dir.exists(), "a dry run creates nothing"
    assert [call for call in machine.calls if split_command(call)[1] in MUTATING] == [], "a dry run only reads"
    printed = capsys.readouterr().out
    for planned in (
        ["git", "-C", shared_bs, "fetch", "-q", "origin"],
        ["git", "-C", shared_java, "fetch", "-q", "origin"],
        ["git", "-C", shared_bs, "fetch", "--no-tags", bundle, "+refs/heads/fix/x:refs/isolated-runs/i999_smoke/eqasim-bs"],
        ["git", "-C", shared_bs, "worktree", "add", "--detach", run_dir / "eqasim-bs", SHA_BUNDLE],
        ["git", "-C", shared_java, "worktree", "add", "--detach", run_dir / "eqasim-java-bs", SHA_JAVA],
        ["cp", "-a", "--", cache, run_dir / "cache.partial"],
    ):
        assert shlex.join(str(item) for item in planned) in printed


@pytest.mark.parametrize("target_view", ["worktree", "ls-tree"])
def test_symlink_farm_links_only_what_the_commit_lacks(root, target_view):
    shared = root / "shared"
    for relative in ("DOWNLOAD_CHECKLIST_BS.md", "data/committed.csv", "data/raw.csv", "data/gtfs/feed.zip",
                     "cache_bs_1pct/stage.p", "output_bs_1pct/x.csv", "popsim_work_allfeat/batch_001/h.csv",
                     "i465_wpe.yml", "run.log", "logs/run.log", "analysis/a.csv", "matsim_archive/m.xml"):
        (shared / relative).parent.mkdir(parents=True, exist_ok=True)
        (shared / relative).write_text("shared\n", encoding="utf-8")
    if target_view == "worktree":
        target = root / "worktree" / "eqasim-data"
        for tracked in TRACKED:
            path = target / tracked.split("/", 1)[1]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("tracked\n", encoding="utf-8")
        entries = tool.scan_target_entries(target)
    else:
        entries = tool.entries_from_tracked_paths(TRACKED)

    plan = tool.plan_symlink_farm(shared, entries)

    assert sorted(relative for relative, _ in plan.links) == ["data/gtfs", "data/raw.csv"]
    assert sorted(plan.kept) == ["DOWNLOAD_CHECKLIST_BS.md", "data/committed.csv"]
    assert sorted(plan.excluded) == ["analysis", "cache_bs_1pct", "i465_wpe.yml", "logs", "matsim_archive",
                                     "output_bs_1pct", "popsim_work_allfeat", "run.log"]


@pytest.mark.parametrize("situation", ["ready", "target-exists", "partial-left", "no-space", "source-active"])
def test_cache_copy_preconditions(root, situation):
    source = root / "old_cache"
    (source / "stage.cache").mkdir(parents=True)
    (source / "stage.p").write_bytes(b"123")
    (source / "stage.cache" / "data.csv").write_bytes(b"12345")
    age_tree(source, 0 if situation == "source-active" else 3600)
    target = root / "run" / "cache"
    target.parent.mkdir()
    if situation == "target-exists":
        target.mkdir()
    if situation == "partial-left":
        (root / "run" / "cache.partial").mkdir()
    free = 10 * tool.GIB + 7 if situation == "no-space" else 10 ** 15

    def check():
        return tool.check_cache_copy(source, target, free_bytes=free, reserve_bytes=10 * tool.GIB,
                                     now=NOW.timestamp())

    if situation == "ready":
        assert check().stats.bytes == 8
        return
    with pytest.raises(tool.IsolatedRunError):
        check()


@pytest.mark.skipif(os.name == "nt" or shutil.which("cp") is None, reason="cp -a copies on the Linux server")
def test_cache_copy_is_physical(root):
    source = root / "old_cache"
    (source / "stage.cache").mkdir(parents=True)
    (source / "stage.cache" / "data.csv").write_text("source\n", encoding="utf-8")
    age_tree(source, 3600)
    check = tool.check_cache_copy(source, root / "cache", free_bytes=10 ** 15, reserve_bytes=0, now=NOW.timestamp())

    tool.copy_cache(check, tool.Host())
    (root / "cache" / "stage.cache" / "data.csv").write_text("changed by the run\n", encoding="utf-8")

    # a hardlinked copy (cp -al) would have written the run's change through into the source cache
    assert (source / "stage.cache" / "data.csv").read_text(encoding="utf-8") == "source\n"
    assert not (root / "cache.partial").exists()


def test_run_log_summary_reports_gate_progress_and_exit():
    log = run_log(explain_report("HIT"),
                  "12:00:10 │ INFO    │ synpp            │ Executing stage "
                  "braunschweig.synthesis.incommuters__0123abcd ...",
                  "12:40:00 │ INFO    │ synpp            │ Executing stage matsim.output__89abcdef ...",
                  "Traceback (most recent call last):",
                  '  File "run_synpp.py", line 1, in <module>',
                  "RuntimeError: boom",
                  f"{tool.LOG_TAG} run=r1 eqasim-bs={SHA_BS} eqasim-java-bs={SHA_JAVA} exit=1 end=2026-10-23T12:41:00+02:00")

    summary = tool.summarise_run_log(log)

    assert summary.exit_code == 1
    assert summary.last_executing_stage == "matsim.output"
    assert summary.traceback_count == 1
    assert summary.explain.statuses[tool.POPSIM_STAGE] == "HIT"
    assert summary.explain.stale == STALE_WITHOUT_POPSIM
    assert not summary.gate_aborted


def test_setup_creates_worktrees_farm_cache_and_record(root):
    if not symlinks_supported(root):
        pytest.skip("the symlink farm needs symlinks (Linux server; Windows only with developer mode)")
    shared_bs, shared_java = root / "shared" / "eqasim-bs", root / "shared" / "eqasim-java-bs"
    (shared_bs / "eqasim-data" / "data").mkdir(parents=True)
    (shared_bs / "eqasim-data" / "data" / "raw.csv").write_text("raw\n", encoding="utf-8")
    (shared_bs / "eqasim-data" / "data" / "committed.csv").write_text("stale shared copy\n", encoding="utf-8")
    shared_java.mkdir()
    cache = root / "old_cache"
    (cache / "stage.cache").mkdir(parents=True)
    (cache / "stage.cache" / "data.csv").write_text("cached\n", encoding="utf-8")
    age_tree(cache, 3600)
    run_dir = root / "runs" / "i999_smoke"
    machine = FakeMachine()
    setup = ["setup", str(run_dir), "--bs", "origin/main", "--java", "origin/main", "--shared-bs", str(shared_bs),
             "--shared-java", str(shared_java)]

    assert tool.main(setup + ["--cache-from", str(cache)], host=machine.host(root)) == 0

    data = run_dir / "eqasim-bs" / "eqasim-data" / "data"
    assert (data / "raw.csv").is_symlink(), "a gitignored input reaches the worktree through the farm"
    assert (data / "committed.csv").read_text(encoding="utf-8") == "tracked\n", "the commit's own table is kept"
    assert (run_dir / "cache" / "stage.cache" / "data.csv").read_text(encoding="utf-8") == "cached\n"
    assert not (run_dir / "cache.partial").exists()
    record = load_record(run_dir)["setup"]
    assert record["repositories"]["eqasim-bs"]["commit"] == SHA_BS
    assert record["repositories"]["eqasim-java-bs"]["commit"] == SHA_JAVA
    assert record["cache"]["source"] == str(cache)
    assert record["shared_checkouts"]["unchanged"] is True
    # a second setup into the same directory is refused: run.json is the run's provenance
    assert tool.main(setup, host=machine.host(root)) == 1
