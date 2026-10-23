#!/usr/bin/env python3
"""Isolated pipeline runs on the Linux server: set up, gate, launch and watch one run.

Sessions kept setting up runs on felix by hand, each with its own copy of the same scripts:
detached worktrees of eqasim-bs and eqasim-java-bs side by side, a symlink farm for the
gitignored eqasim-data, a physical copy of an earlier run's synpp cache, a throwaway overlay
with own paths, an explain/stale gate and a detached launch. This tool is that pattern, once.
Every subcommand works on one run directory:

    RUN_DIR/eqasim-bs        detached worktree of the shared ~/eqasim-bs at --bs
    RUN_DIR/eqasim-java-bs   detached worktree of the shared ~/eqasim-java-bs at --java; the base value
                             eqasim_source_path ../eqasim-java-bs resolves to it without a config change
    RUN_DIR/cache            synpp working_directory, a PHYSICAL copy (cp -a) of an earlier run's cache
    RUN_DIR/output           output_path
    RUN_DIR/logs             explain, input-check and run logs
    RUN_DIR/run.yml          the run's overlay, composed with configs/base_bs.yml at launch
    RUN_DIR/run.json         provenance: refs, commits, cache source, overlay source, gate, launches

    python scripts/isolated_run.py setup RUN_DIR --bs REF [--bs-bundle FILE] --java REF
                                         [--java-bundle FILE] [--cache-from DIR] [--dry-run]
    python scripts/isolated_run.py overlay RUN_DIR --from OVERLAY.yml [--prefix PREFIX]
    python scripts/isolated_run.py gate RUN_DIR [--write] [--allow-popsim]
    python scripts/isolated_run.py launch RUN_DIR [--java-home DIR] [--force-no-gate]
    python scripts/isolated_run.py status RUN_DIR

Exit codes: 0 done; 1 refused or failed, the message says why and what to do; 2 the gate refused
because braunschweig.popsim.stage would recompute. Run the tool with the eqasim conda env's
interpreter: `gate` runs the run's own scripts/explain_stale_stages.py and
scripts/verify_braunschweig_inputs.py with it. The shared checkouts are only fetched, never
switched, reset or pulled. Why each rule exists: docs/codebase/notes/isolated-server-runs.md.
"""
from __future__ import annotations

import argparse
import copy
import dataclasses
import datetime
import fnmatch
import hashlib
import json
import os
import re
import shlex
import shutil
import socket
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import yaml

# Run directory layout.
BS_DIR = "eqasim-bs"
JAVA_DIR = "eqasim-java-bs"
CACHE_DIR = "cache"
OUTPUT_DIR = "output"
LOGS_DIR = "logs"
RUN_YML = "run.yml"
RUN_JSON = "run.json"
RECORD_SCHEMA_VERSION = 1
# cp -a writes into <cache>.partial and renames on success, so an interrupted copy never looks complete.
PARTIAL_SUFFIX = ".partial"
# Bundle commits are fetched into refs no branch uses, so fetching them moves nothing else.
BUNDLE_REF_NAMESPACE = "refs/isolated-runs"

# Paths inside the run's eqasim-bs worktree (the launch directory).
BASE_CONFIG = "configs/base_bs.yml"
EXPLAIN_SCRIPT = "scripts/explain_stale_stages.py"
INPUTS_SCRIPT = "scripts/verify_braunschweig_inputs.py"
LAUNCHER_SCRIPT = "scripts/run_synpp.py"

# Launcher gate key read by scripts/run_synpp.py (explain_before_run).
GATE_KEY = "synpp_expected_stale_stages"
POPSIM_STAGE = "braunschweig.popsim.stage"
POPSIM_WORK_DIR_KEY = "braunschweig.population.popsim.work_dir"

# Paths written into run.yml, relative to RUN_DIR/eqasim-bs. Every isolated run carries the same
# strings, so a cache copied from another isolated run stays valid: output_path enters the synthesis
# stage hashes (synthesis.population.spatial.primary.candidates declares it), analysis_working_directory
# the hash of braunschweig.analysis.analysis_suite.
RUN_TOP_LEVEL_PATHS = {"working_directory": f"../{CACHE_DIR}"}
RUN_CONFIG_PATHS = {"output_path": f"../{OUTPUT_DIR}", "analysis_working_directory": f"../{CACHE_DIR}"}
# No stage reads these; they steer only the launcher's prime/export of eqasim-data/cache_shared, so
# switched off the run neither reads from nor writes into the shared store.
RUN_CONFIG_SWITCHES = {"cache_share_enabled": False, "cache_share_export": False}

# Top-level eqasim-data entries the symlink farm never links: run state belongs to each run.
FARM_EXCLUDE_TOP = ("cache_*", "output*", "popsim_work*", "matsim_archive", "analysis", "logs",
                    "*.yml", "*.yaml", "*.log", "*.pid")

DEFAULT_SHARED_BS = "~/eqasim-bs"
DEFAULT_SHARED_JAVA = "~/eqasim-java-bs"
DEFAULT_JAVA_HOME = "~/tools/jdk-25.0.3+9"
DEFAULT_CONDA_SH = "~/miniforge3/etc/profile.d/conda.sh"
DEFAULT_CONDA_ENV = "eqasim"
# Free space that must remain after the cache copy, for the run's own new stage outputs.
DEFAULT_RESERVE_GIB = 20.0
# A cache source written to more recently than this may still belong to a live run.
DEFAULT_ACTIVE_SOURCE_MINUTES = 15.0
# How long `launch` watches the new log for the launcher's [explain] verdict.
DEFAULT_WAIT_SECONDS = 120.0
POLL_SECONDS = 2.0
# Polls to wait after the [explain] summary for the launcher-gate abort line, which follows it.
GRACE_POLLS = 3

LOG_TAG = "[isolated-run]"
RUN_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
PREFIX_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")
GIB = 1024 ** 3
EXIT_OK, EXIT_REFUSED, EXIT_GATE = 0, 1, 2

# The explain report (braunschweig.synpp_fork.staleness.format_report) and the run log.
REPORT_LINE = re.compile(r"^(STALE-IF|STALE|RECOMPUTE|HIT|EPHEMERAL)\s+(\S+)")
REPORT_SUMMARY = re.compile(r"^# (\d+) stages: (\d+) stale for own reasons")
EXPLAIN_MARK = "[explain] "
EXECUTING = re.compile(r"Executing stage (\S+?)(?:__[0-9a-f]+)? \.\.\.")
START_LINE = re.compile(re.escape(LOG_TAG) + r" .*\bstart=")
EXIT_LINE = re.compile(re.escape(LOG_TAG) + r" .*\bexit=(-?\d+)\b")


class IsolatedRunError(Exception):
    """A refused or failed step; the message says what is wrong and what to do."""


def _run_command(argv, cwd=None, env=None) -> subprocess.CompletedProcess:
    return subprocess.run([str(item) for item in argv], cwd=None if cwd is None else str(cwd), env=env,
                          capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)


@dataclasses.dataclass
class Host:
    """Everything the tool asks of the machine, in one place so tests can substitute it."""

    run: Callable = _run_command
    popen: Callable = subprocess.Popen
    proc_root: Path = Path("/proc")
    disk_free: Callable = lambda path: shutil.disk_usage(path).free
    now: Callable = lambda: datetime.datetime.now().astimezone()
    sleep: Callable = time.sleep


# ---------------------------------------------------------------------------------------------
# small helpers

def _expand(value) -> Path:
    return Path(value).expanduser().resolve()


def _optional_path(value) -> Optional[Path]:
    return None if value is None else _expand(value)


def _run_name(run_dir: Path) -> str:
    if not RUN_NAME_PATTERN.match(run_dir.name):
        raise IsolatedRunError(f"run directory name {run_dir.name!r} must match {RUN_NAME_PATTERN.pattern}: it "
                               f"becomes part of a git ref ({BUNDLE_REF_NAMESPACE}/<name>/...) and of log lines")
    return run_dir.name


def _iso(host: Host) -> str:
    return host.now().isoformat(timespec="seconds")


def _stamp(host: Host) -> str:
    return host.now().strftime("%Y%m%dT%H%M%S")


def _gib(size_bytes: float) -> str:
    return f"{size_bytes / GIB:.1f}"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _tool_blob() -> str:
    """git blob id of this file as run: `git log --all --find-object=<id>` finds the tool version."""
    data = Path(__file__).read_bytes()
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def _inside(path, root) -> bool:
    """True when ``path`` lies in ``root`` after resolving symlinks (a farm link leaves the run)."""
    path, root = os.path.normcase(os.path.realpath(path)), os.path.normcase(os.path.realpath(root))
    try:
        return os.path.commonpath([path, root]) == root
    except ValueError:  # different drives on Windows
        return False


def _same_path(first, second) -> bool:
    return os.path.normcase(os.path.realpath(first)) == os.path.normcase(os.path.realpath(second))


def _existing_parent(path: Path) -> Path:
    for candidate in (path, *path.parents):
        if candidate.exists():
            return candidate
    return Path(path.anchor)


def _resolve_from_launch_dir(run_dir: Path, value) -> Path:
    """Where a run.yml path ends up: relative paths are relative to RUN_DIR/eqasim-bs (the launch cwd)."""
    return Path(os.path.realpath(run_dir / BS_DIR / str(value)))


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""


def _tail(text: str, lines: int = 15) -> str:
    return "\n".join(text.strip().splitlines()[-lines:])


def _git(host: Host, repository, *arguments) -> str:
    argv = ["git", "-C", str(repository), *[str(item) for item in arguments]]
    result = host.run(argv)
    if result.returncode != 0:
        raise IsolatedRunError(f"`{shlex.join(argv)}` failed (exit {result.returncode}): "
                               f"{(result.stderr or result.stdout or '').strip()}")
    return result.stdout


def _step(host: Host, dry_run: bool, argv, purpose: str) -> None:
    """Run one changing command, or in a dry run only print it."""
    argv = [str(item) for item in argv]
    shown = shlex.join(argv)
    if dry_run:
        print(f"  would run: {shown}\n      ({purpose})")
        return
    print(f"  run: {shown}")
    result = host.run(argv)
    if result.returncode != 0:
        raise IsolatedRunError(f"{purpose} failed (exit {result.returncode}): {shown}\n"
                               f"{(result.stderr or result.stdout or '').strip()}")


# ---------------------------------------------------------------------------------------------
# run.json and run.yml

def _load_record(run_dir: Path) -> dict:
    path = run_dir / RUN_JSON
    if not path.is_file():
        raise IsolatedRunError(f"{path} not found: {run_dir} is not a run directory made by `setup`")
    with open(path, encoding="utf-8") as handle:
        record = json.load(handle)
    if record.get("schema_version") != RECORD_SCHEMA_VERSION:
        raise IsolatedRunError(f"{path}: schema_version {record.get('schema_version')!r}, this tool writes "
                               f"{RECORD_SCHEMA_VERSION}")
    return record


def _save_record(run_dir: Path, record: dict) -> None:
    """Write run.json atomically and read it back: a write that silently did not happen fails here."""
    path = run_dir / RUN_JSON
    temporary = path.with_name(path.name + ".tmp")
    with open(temporary, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(record, handle, indent=2)
        handle.write("\n")
    os.replace(temporary, path)
    with open(path, encoding="utf-8") as handle:
        if json.load(handle) != record:
            raise IsolatedRunError(f"{path}: what was read back differs from what was written")


def _read_run_yml(path: Path) -> dict:
    if not path.is_file():
        raise IsolatedRunError(f"{path} not found: write it with `overlay` first")
    with open(path, encoding="utf-8") as handle:
        doc = yaml.safe_load(handle)
    if not isinstance(doc, dict):
        raise IsolatedRunError(f"{path} is not a YAML mapping")
    return doc


def _read_header(path: Path) -> List[str]:
    header = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            if not line.startswith("## "):
                break
            header.append(line[3:].rstrip("\n"))
    return header


def _write_run_yml(path: Path, doc: dict, header: List[str]) -> str:
    """Write run.yml (comment header + YAML), read it back, return its sha256."""
    text = "".join(f"## {line}\n" for line in header) + yaml.safe_dump(doc, sort_keys=False, allow_unicode=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    with open(path, encoding="utf-8") as handle:
        if yaml.safe_load(handle) != doc:
            raise IsolatedRunError(f"{path}: what was read back differs from what was written")
    return _sha256_file(path)


# ---------------------------------------------------------------------------------------------
# overlay

def transform_overlay(source: dict, prefix: Optional[str] = None) -> Tuple[dict, List[str]]:
    """Return the run overlay derived from ``source`` and a list of what changed.

    working_directory, output_path and analysis_working_directory point into the run directory,
    cache_share_enabled and cache_share_export are switched off, and a launcher gate list of the
    source run is dropped (this run gets its own from `gate --write`). output_prefix changes only
    when ``prefix`` is given: it enters the matsim.simulation.prepare stage hash. Every other key
    stays as it is, the popsim work_dir string included: it enters the popsim stage hash, so it
    must stay the source run's for PopulationSim to stay cached.
    """
    if not isinstance(source, dict):
        raise IsolatedRunError("the source overlay is not a YAML mapping")
    doc = copy.deepcopy(source)
    if doc.get("config") is None:
        doc["config"] = {}
    config = doc["config"]
    if not isinstance(config, dict):
        raise IsolatedRunError("the source overlay's `config` is not a mapping")
    changes = []

    def put(container, key, value, label):
        if key not in container:
            changes.append(f"{label}: (added) {value!r}")
        elif container[key] != value:
            changes.append(f"{label}: {container[key]!r} -> {value!r}")
        container[key] = value

    for key, value in RUN_TOP_LEVEL_PATHS.items():
        put(doc, key, value, key)
    for key, value in {**RUN_CONFIG_PATHS, **RUN_CONFIG_SWITCHES}.items():
        put(config, key, value, f"config.{key}")
    if prefix is not None:
        put(config, "output_prefix", prefix, "config.output_prefix")
    if GATE_KEY in config:
        changes.append(f"config.{GATE_KEY}: dropped (the source run's {len(config[GATE_KEY] or [])} stages)")
        del config[GATE_KEY]
    return doc, changes


def _popsim_work_dir_value(run_dir: Path, doc: dict) -> Optional[str]:
    """The popsim work_dir the run will use: run.yml's value, else the run commit's base value."""
    config = doc.get("config") or {}
    if POPSIM_WORK_DIR_KEY in config:
        return config[POPSIM_WORK_DIR_KEY]
    base = run_dir / BS_DIR / BASE_CONFIG
    if not base.is_file():
        return None
    with open(base, encoding="utf-8") as handle:
        return ((yaml.safe_load(handle) or {}).get("config") or {}).get(POPSIM_WORK_DIR_KEY)


def cmd_overlay(args, host: Host) -> int:
    run_dir = _expand(args.run_dir)
    name = _run_name(run_dir)
    record = _load_record(run_dir)
    if not (run_dir / BS_DIR).is_dir():
        raise IsolatedRunError(f"{run_dir / BS_DIR} not found: run `setup` first")
    if record.get("launches"):
        raise IsolatedRunError(f"{run_dir / RUN_YML} was launched already ({len(record['launches'])} launch(es) "
                               "in run.json): its provenance belongs to the cache it wrote; use a new run directory")
    if args.prefix is not None and not PREFIX_PATTERN.match(args.prefix):
        raise IsolatedRunError(f"--prefix {args.prefix!r} must match {PREFIX_PATTERN.pattern} (it starts file names)")
    source_path = _expand(args.source)
    if not source_path.is_file():
        raise IsolatedRunError(f"source overlay {source_path} not found")
    with open(source_path, encoding="utf-8") as handle:
        source = yaml.safe_load(handle) or {}
    doc, changes = transform_overlay(source, args.prefix)
    target = run_dir / RUN_YML
    if target.exists() and not args.force:
        raise IsolatedRunError(f"{target} exists; pass --force to replace it (the gate must then run again)")
    source_sha256 = _sha256_file(source_path)
    header = [
        f"Run overlay of {name}, written by scripts/isolated_run.py overlay on {_iso(host)}.",
        f"Source: {source_path} (sha256 {source_sha256}).",
        "Paths are relative to RUN_DIR/eqasim-bs, the launch directory, so every isolated run carries the",
        "same strings: output_path enters synthesis stage hashes, analysis_working_directory the analysis suite's.",
        "`launch` compares this file with its gate record: edit it only before `gate --write`.",
    ]
    digest = _write_run_yml(target, doc, header)
    record["overlay"] = {"time": _iso(host), "source": str(source_path), "source_sha256": source_sha256,
                         "prefix": args.prefix, "changes": changes, "run_yml_sha256": digest,
                         "tool_blob": _tool_blob()}
    record.pop("gate", None)
    _save_record(run_dir, record)

    print(f"{target} written from {source_path}")
    for change in changes:
        print(f"  {change}")
    config = doc["config"]
    if args.prefix is None:
        print(f"  kept: config.output_prefix {config.get('output_prefix')!r} (enters the matsim.simulation.prepare "
              "hash; --prefix changes it)")
    work_dir = _popsim_work_dir_value(run_dir, doc)
    if work_dir:
        resolved = _resolve_from_launch_dir(run_dir, work_dir)
        print(f"  kept: config.{POPSIM_WORK_DIR_KEY} {work_dir!r} (enters the popsim hash), resolves to {resolved}")
        if not resolved.exists():
            print(f"WARNING: {resolved} does not exist: harmless while PopulationSim stays cached (the gate checks "
                  "that), but stages that read the popsim work dir will not find it", file=sys.stderr)
    print(f"next: python scripts/isolated_run.py gate {run_dir} --write")
    return EXIT_OK


# ---------------------------------------------------------------------------------------------
# explain report and gate

@dataclasses.dataclass
class ExplainReport:
    """Stage statuses of one explain report (labels as staleness.format_report prints them)."""

    statuses: Dict[str, str]
    stale: List[str]
    summary: str


def parse_explain_report(text: str) -> ExplainReport:
    """Parse a report of scripts/explain_stale_stages.py; refuse one whose counts do not match.

    ``stale`` lists the stages stale for their own reasons (label ``STALE``), sorted. The summary
    line's stage and stale counts must equal the parsed lines: a report that does not add up was
    cut off or changed format, and a gate list built from it would let unlisted stages through.
    """
    statuses: Dict[str, str] = {}
    summary = None
    for line in text.splitlines():
        match = REPORT_LINE.match(line)
        if match:
            label, stage = match.groups()
            if stage in statuses:
                raise IsolatedRunError(f"stage {stage} is listed twice in the explain report")
            statuses[stage] = label
            continue
        match = REPORT_SUMMARY.match(line)
        if match:
            summary = (int(match.group(1)), int(match.group(2)), line.strip())
    if summary is None:
        raise IsolatedRunError("the explain report has no summary line ('# N stages: K stale for own reasons, "
                               "...'): it is incomplete or its format changed")
    total, stale_count, summary_line = summary
    stale = sorted(stage for stage, label in statuses.items() if label == "STALE")
    if len(statuses) != total or len(stale) != stale_count:
        raise IsolatedRunError(f"the explain report does not add up: {len(statuses)} stage lines and {len(stale)} "
                               f"STALE lines parsed, its summary says {total} and {stale_count}; its format may "
                               "have changed")
    return ExplainReport(statuses, stale, summary_line)


@dataclasses.dataclass
class PopsimVerdict:
    status: Optional[str]
    refused: bool
    lines: List[str]


def judge_popsim(report: ExplainReport, work_dir_value, run_dir: Path, allow_popsim: bool) -> PopsimVerdict:
    """Refuse a launch that would recompute PopulationSim unless allowed, and always one whose
    recompute would write outside the run (popsim purges stale batches in its work dir)."""
    status = report.statuses.get(POPSIM_STAGE)
    if status is None:
        return PopsimVerdict(None, False, [f"{POPSIM_STAGE}: not in this run's stage graph"])
    if status == "HIT":
        return PopsimVerdict(status, False, [f"{POPSIM_STAGE}: HIT, PopulationSim stays cached"])
    outside = None
    if work_dir_value:
        resolved = _resolve_from_launch_dir(run_dir, work_dir_value)
        if not _inside(resolved, run_dir):
            outside = resolved
    if status in ("STALE", "RECOMPUTE"):
        if not allow_popsim:
            return PopsimVerdict(status, True, [
                f"{POPSIM_STAGE} is {status}: PopulationSim would recompute (hours). Check that --cache-from was "
                "the cache of the run whose overlay went into `overlay` (the popsim work_dir string enters the "
                "popsim hash), or pass --allow-popsim to accept the recompute."])
        if outside is not None:
            return PopsimVerdict(status, True, [
                f"{POPSIM_STAGE} is {status} and would write its batches into {outside}, outside {run_dir}; its "
                "stale-batch purge would delete another run's batches there. Point "
                f"config.{POPSIM_WORK_DIR_KEY} in run.yml into the run (e.g. ../popsim_work) and gate again."])
        return PopsimVerdict(status, False, [f"WARNING: {POPSIM_STAGE} is {status}: PopulationSim WILL recompute "
                                             "(allowed by --allow-popsim)"])
    lines = [f"WARNING: {POPSIM_STAGE} is {status}: it recomputes only if a stale ancestor's output changes"]
    if outside is not None:
        lines.append(f"WARNING: if it does, it writes its batches into {outside}, outside {run_dir}")
    return PopsimVerdict(status, False, lines)


def _isolation_problems(run_dir: Path, doc: dict) -> List[str]:
    """What in run.yml would make the run read or write outside its own directory."""
    config = doc.get("config") or {}
    problems = []
    for label, value in (("working_directory", doc.get("working_directory")),
                         ("config.output_path", config.get("output_path")),
                         ("config.analysis_working_directory", config.get("analysis_working_directory"))):
        if not value:
            problems.append(f"{label} is not set")
        elif not _inside(_resolve_from_launch_dir(run_dir, value), run_dir):
            problems.append(f"{label} {value!r} resolves outside {run_dir}")
    for key in RUN_CONFIG_SWITCHES:
        if config.get(key) is not False:
            problems.append(f"config.{key} is {config.get(key)!r}, must be false")
    return problems


def cmd_gate(args, host: Host) -> int:
    run_dir = _expand(args.run_dir)
    record = _load_record(run_dir)
    bs_dir, run_yml, logs = run_dir / BS_DIR, run_dir / RUN_YML, run_dir / LOGS_DIR
    doc = _read_run_yml(run_yml)
    problems = _isolation_problems(run_dir, doc)
    if problems:
        raise IsolatedRunError("run.yml is not isolated: " + "; ".join(problems) + " (write it with `overlay`)")
    if args.write and find_run_processes(run_yml, host.proc_root):
        raise IsolatedRunError(f"{run_yml} is running: the gate list of a running launch stays as it is")
    logs.mkdir(exist_ok=True)
    stamp = _stamp(host)
    utf8 = dict(os.environ, PYTHONUTF8="1")

    explain_log = logs / f"explain_{stamp}.log"
    result = host.run([args.python, EXPLAIN_SCRIPT, BASE_CONFIG, str(run_yml)], cwd=bs_dir, env=utf8)
    explain_log.write_text(result.stdout + (result.stderr or ""), encoding="utf-8")
    if result.returncode != 0:
        raise IsolatedRunError(f"{EXPLAIN_SCRIPT} failed (exit {result.returncode}), log {explain_log}:\n"
                               f"{_tail(result.stdout + (result.stderr or ''))}")
    report = parse_explain_report(result.stdout)
    print(f"explain ({explain_log}): {report.summary}")
    for stage in report.stale:
        print(f"  STALE {stage}")

    inputs_log, inputs_ok = None, True
    if args.skip_input_check:
        print("WARNING: input check skipped (--skip-input-check)", file=sys.stderr)
    else:
        with_matsim = any(stage.startswith(("matsim.", "braunschweig.matsim.")) for stage in report.statuses)
        inputs_log = logs / f"inputs_{stamp}.log"
        result = host.run([args.python, INPUTS_SCRIPT] + (["--matsim"] if with_matsim else []), cwd=bs_dir, env=utf8)
        inputs_log.write_text(result.stdout + (result.stderr or ""), encoding="utf-8")
        inputs_ok = result.returncode == 0
        if inputs_ok:
            print(f"inputs: every required input present ({inputs_log})")
        else:
            print(f"ERROR: required inputs are missing (exit {result.returncode}), log {inputs_log}:\n"
                  f"{_tail(result.stdout + (result.stderr or ''))}", file=sys.stderr)

    verdict = judge_popsim(report, _popsim_work_dir_value(run_dir, doc), run_dir, args.allow_popsim)
    for line in verdict.lines:
        print(line, file=sys.stderr if verdict.refused or line.startswith("WARNING") else sys.stdout)
    if verdict.refused:
        return EXIT_GATE
    if not inputs_ok:
        return EXIT_REFUSED
    if not args.write:
        print("check only: run.yml unchanged (pass --write to store the gate list)")
        return EXIT_OK
    if not report.stale:
        raise IsolatedRunError("the explain report lists no STALE stage; an empty gate list would switch the "
                               "launcher gate off")
    doc["config"] = doc.get("config") or {}
    doc["config"][GATE_KEY] = report.stale
    header = _read_header(run_yml) + [f"Launcher gate list ({len(report.stale)} stages) written by `gate --write` "
                                      f"on {_iso(host)} from logs/{explain_log.name}."]
    digest = _write_run_yml(run_yml, doc, header)
    record["gate"] = {
        "time": _iso(host), "explain_log": str(explain_log),
        "inputs_log": None if inputs_log is None else str(inputs_log), "inputs_checked": not args.skip_input_check,
        "stale_stages": report.stale, "summary": report.summary, "popsim_status": verdict.status,
        "allow_popsim": args.allow_popsim,
        "commits": {BS_DIR: _git(host, bs_dir, "rev-parse", "HEAD").strip(),
                    JAVA_DIR: _git(host, run_dir / JAVA_DIR, "rev-parse", "HEAD").strip()},
        "run_yml_sha256": digest, "tool_blob": _tool_blob(),
    }
    _save_record(run_dir, record)
    print(f"gate list of {len(report.stale)} stages written to {run_yml}")
    print(f"next: python scripts/isolated_run.py launch {run_dir}")
    return EXIT_OK


# ---------------------------------------------------------------------------------------------
# symlink farm

@dataclasses.dataclass
class FarmPlan:
    links: List[Tuple[str, Path]]
    kept: List[str]
    excluded: List[str]


def scan_target_entries(target: Path) -> Dict[str, str]:
    """Map every entry under ``target`` (relative POSIX path) to 'dir', 'file' or 'link'; links are not followed."""
    entries: Dict[str, str] = {}
    if not target.is_dir():
        return entries
    for directory, names, files in os.walk(target):
        relative_directory = os.path.relpath(directory, target).replace(os.sep, "/")
        for name in names + files:
            path = os.path.join(directory, name)
            relative = name if relative_directory == "." else f"{relative_directory}/{name}"
            entries[relative] = "link" if os.path.islink(path) else "dir" if os.path.isdir(path) else "file"
    return entries


def entries_from_tracked_paths(paths, prefix: str = "eqasim-data/") -> Dict[str, str]:
    """The entries a worktree of a commit holds under ``prefix``, from `git ls-tree -r --name-only`."""
    entries: Dict[str, str] = {}
    for path in paths:
        if not path.startswith(prefix):
            continue
        parts = path[len(prefix):].split("/")
        for depth in range(1, len(parts)):
            entries.setdefault("/".join(parts[:depth]), "dir")
        entries["/".join(parts)] = "file"
    return entries


def plan_symlink_farm(shared: Path, entries: Dict[str, str], exclude_top=FARM_EXCLUDE_TOP) -> FarmPlan:
    """Plan the links that make ``shared``'s gitignored inputs reachable from a worktree.

    An entry the worktree lacks is linked (a directory as a whole); a directory present in both
    is descended into; anything else the worktree has is kept, so the run reads its OWN commit's
    tracked reference tables, not those of the shared checkout. Top-level run state (caches,
    outputs, popsim work dirs, resolved configs, logs) is never linked.
    """
    plan = FarmPlan([], [], [])

    def visit(relative_directory: str) -> None:
        directory = shared / relative_directory if relative_directory else shared
        for name in sorted(os.listdir(directory)):
            relative = f"{relative_directory}/{name}" if relative_directory else name
            if not relative_directory and any(fnmatch.fnmatchcase(name, pattern) for pattern in exclude_top):
                plan.excluded.append(relative)
                continue
            source = directory / name
            kind = entries.get(relative)
            if kind is None:
                plan.links.append((relative, source))
            elif kind == "dir" and source.is_dir():
                visit(relative)
            else:
                plan.kept.append(relative)

    visit("")
    return plan


def apply_symlink_farm(plan: FarmPlan, target: Path) -> int:
    for relative, source in plan.links:
        os.symlink(source, target / relative, target_is_directory=source.is_dir())
    dangling = [relative for relative, _ in plan.links if not (target / relative).exists()]
    if dangling:
        raise IsolatedRunError(f"{len(dangling)} farm links do not resolve: {', '.join(dangling[:5])}")
    return len(plan.links)


# ---------------------------------------------------------------------------------------------
# cache copy

@dataclasses.dataclass
class TreeStats:
    files: int
    links: int
    bytes: int
    newest_mtime: float


def tree_stats(top: Path) -> TreeStats:
    """Regular files, symlinks, bytes (each inode once, as du counts) and the newest mtime under ``top``."""
    files = links = size = 0
    newest = os.lstat(top).st_mtime
    seen = set()
    for directory, names, file_names in os.walk(top):
        for name in names + file_names:
            info = os.lstat(os.path.join(directory, name))
            newest = max(newest, info.st_mtime)
            if stat.S_ISLNK(info.st_mode):
                links += 1
            elif stat.S_ISREG(info.st_mode):
                files += 1
                key = (info.st_dev, info.st_ino) if info.st_ino else os.path.join(directory, name)
                if key not in seen:
                    seen.add(key)
                    size += info.st_size
    return TreeStats(files, links, size, newest)


@dataclasses.dataclass
class CacheCopyCheck:
    source: Path
    target: Path
    partial: Path
    stats: TreeStats
    free_bytes: int


def check_cache_copy(source: Path, target: Path, *, free_bytes: int, reserve_bytes: int, now: float,
                     allow_active_source: bool = False,
                     active_window_seconds: float = DEFAULT_ACTIVE_SOURCE_MINUTES * 60) -> CacheCopyCheck:
    """Refuse a cache copy that would overwrite, run out of space, or copy a cache still being written."""
    partial = target.with_name(target.name + PARTIAL_SUFFIX)
    if not source.is_dir():
        raise IsolatedRunError(f"cache source {source} is not a directory")
    if target.exists() or target.is_symlink():
        raise IsolatedRunError(f"{target} exists: the cache copy never overwrites; remove it deliberately or use a "
                               "new run directory")
    if partial.exists() or partial.is_symlink():
        raise IsolatedRunError(f"{partial} exists: an earlier copy did not finish; remove that path and set up again")
    stats = tree_stats(source)
    if free_bytes < stats.bytes + reserve_bytes:
        raise IsolatedRunError(f"not enough free space: the copy needs {_gib(stats.bytes)} GiB plus a reserve of "
                               f"{_gib(reserve_bytes)} GiB for the run's own outputs, {_gib(free_bytes)} GiB are free")
    age_seconds = now - stats.newest_mtime
    if age_seconds < active_window_seconds and not allow_active_source:
        raise IsolatedRunError(f"{source} was written {age_seconds / 60:.1f} min ago: a run may still be writing to "
                               "it, and a copy taken mid-write is no consistent cache; wait, or pass "
                               "--allow-active-source when nothing writes there")
    return CacheCopyCheck(source, target, partial, stats, free_bytes)


def copy_cache(check: CacheCopyCheck, host: Host, dry_run: bool = False) -> Optional[TreeStats]:
    """cp -a into <target>.partial, compare with the source, then rename to the target."""
    _step(host, dry_run, ["cp", "-a", "--", check.source, check.partial],
          "physical copy of the cache (never cp -al: a hardlinked copy writes through into the source run's cache)")
    if dry_run:
        return None
    copied = tree_stats(check.partial)
    expected = check.stats
    if (copied.files, copied.links, copied.bytes) != (expected.files, expected.links, expected.bytes):
        raise IsolatedRunError(f"the copy differs from its source ({copied.files} files, {copied.bytes} bytes against "
                               f"{expected.files}, {expected.bytes}); {check.partial} is left for inspection")
    os.rename(check.partial, check.target)
    print(f"  cache copied to {check.target}: {copied.files} files, {_gib(copied.bytes)} GiB")
    return copied


# ---------------------------------------------------------------------------------------------
# setup

@dataclasses.dataclass
class RepositoryPlan:
    name: str
    shared: Path
    ref: str
    bundle: Optional[Path]
    worktree: Path
    bundle_ref: Optional[str] = None
    namespaced_ref: Optional[str] = None
    commit: Optional[str] = None

    def as_record(self) -> dict:
        return {"shared_checkout": str(self.shared), "ref": self.ref,
                "bundle": None if self.bundle is None else str(self.bundle),
                "bundle_sha256": None if self.bundle is None else _sha256_file(self.bundle),
                "bundle_ref": self.bundle_ref, "namespaced_ref": self.namespaced_ref,
                "commit": self.commit, "worktree": str(self.worktree)}


def shared_checkout_state(host: Host, repository: Path) -> dict:
    """Branch, HEAD and dirty-entry count of a shared checkout (read-only, no optional index lock)."""
    branch = _git(host, repository, "branch", "--show-current").strip()
    head = _git(host, repository, "rev-parse", "HEAD").strip()
    argv = ["git", "--no-optional-locks", "-C", str(repository), "status", "--porcelain"]
    result = host.run(argv)
    if result.returncode != 0:
        raise IsolatedRunError(f"`{shlex.join(argv)}` failed: {(result.stderr or '').strip()}")
    dirty = sum(1 for line in result.stdout.splitlines() if line.strip())
    return {"branch": branch or "(detached)", "head": head, "dirty": dirty}


def _print_states(title: str, states: dict) -> None:
    print(title + ":")
    for name, state in states.items():
        print(f"  {name}: branch {state['branch']}, HEAD {state['head'][:12]}, {state['dirty']} dirty entries")


def _bundle_heads(text: str) -> Dict[str, str]:
    heads = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2:
            heads[parts[1]] = parts[0]
    if not heads:
        raise IsolatedRunError("the bundle lists no refs")
    return heads


def _pick_bundle_ref(heads: Dict[str, str], wanted: str, bundle: Path) -> str:
    for candidate in (wanted, f"refs/heads/{wanted}", f"refs/tags/{wanted}", f"refs/remotes/{wanted}"):
        if candidate in heads:
            return candidate
    raise IsolatedRunError(f"{bundle} holds no ref {wanted!r}; it holds {', '.join(sorted(heads))}")


def _resolve_commit(host: Host, repository: RepositoryPlan, run_name: str, dry_run: bool) -> None:
    if repository.bundle is None:
        repository.commit = _git(host, repository.shared, "rev-parse", "--verify", f"{repository.ref}^{{commit}}").strip()
        note = " (as of now; the real setup fetches first and may resolve newer)" if dry_run else ""
        print(f"  {repository.name}: {repository.ref} -> {repository.commit}{note}")
        return
    verify = host.run(["git", "-C", str(repository.shared), "bundle", "verify", str(repository.bundle)])
    if verify.returncode != 0:
        message = (f"bundle {repository.bundle} does not verify against {repository.shared}: "
                   f"{(verify.stderr or verify.stdout or '').strip()}")
        if not dry_run:
            raise IsolatedRunError(message)
        print(f"WARNING: {message} (a dry run does not fetch origin, which may bring the prerequisite commits)",
              file=sys.stderr)
    heads = _bundle_heads(_git(host, repository.shared, "bundle", "list-heads", repository.bundle))
    repository.bundle_ref = _pick_bundle_ref(heads, repository.ref, repository.bundle)
    repository.namespaced_ref = f"{BUNDLE_REF_NAMESPACE}/{run_name}/{repository.name}"
    _step(host, dry_run, ["git", "-C", repository.shared, "fetch", "--no-tags", repository.bundle,
                          f"+{repository.bundle_ref}:{repository.namespaced_ref}"],
          f"fetch {repository.bundle_ref} from the bundle into {repository.namespaced_ref}")
    expected = heads[repository.bundle_ref]
    if not dry_run:
        fetched = _git(host, repository.shared, "rev-parse", "--verify", f"{repository.namespaced_ref}^{{commit}}").strip()
        if fetched != expected:
            raise IsolatedRunError(f"{repository.namespaced_ref} is {fetched}, the bundle lists {expected}")
    repository.commit = expected
    print(f"  {repository.name}: {repository.bundle_ref} of {repository.bundle.name} -> {repository.commit}")


def _create_worktree(host: Host, repository: RepositoryPlan, dry_run: bool) -> None:
    target = repository.worktree
    if target.exists():
        top = host.run(["git", "-C", str(target), "rev-parse", "--show-toplevel"])
        if top.returncode != 0 or not _same_path(top.stdout.strip(), target):
            raise IsolatedRunError(f"{target} exists but is no git worktree; remove it or use a new run directory")
        head = _git(host, target, "rev-parse", "HEAD").strip()
        if head != repository.commit:
            raise IsolatedRunError(f"{target} exists at {head[:12]}, not at {repository.commit[:12]}; remove it with "
                                   f"`git -C {repository.shared} worktree remove {target}` or use a new run directory")
        print(f"  {target} is a worktree at {head[:12]} already: kept")
        return
    _step(host, dry_run, ["git", "-C", repository.shared, "worktree", "add", "--detach", target, repository.commit],
          f"detached worktree of {repository.name} at {repository.commit[:12]}")
    if not dry_run:
        head = _git(host, target, "rev-parse", "HEAD").strip()
        if head != repository.commit:
            raise IsolatedRunError(f"the new worktree {target} is at {head}, expected {repository.commit}")


def _symlink_farm(host: Host, repository: RepositoryPlan, shared_data: Path, dry_run: bool) -> Optional[dict]:
    target = repository.worktree / "eqasim-data"
    if dry_run:
        if repository.bundle is not None and host.run(
                ["git", "-C", str(repository.shared), "cat-file", "-e", f"{repository.commit}^{{commit}}"]).returncode:
            print(f"  symlink farm: planned after the bundle fetch ({repository.commit[:12]} is not in "
                  f"{repository.shared} yet)")
            return None
        listing = _git(host, repository.shared, "ls-tree", "-r", "--name-only", repository.commit, "--", "eqasim-data")
        entries = entries_from_tracked_paths(listing.splitlines())
    else:
        target.mkdir(exist_ok=True)
        entries = scan_target_entries(target)
    plan = plan_symlink_farm(shared_data, entries)
    print(f"  symlink farm {shared_data} -> {target}: {len(plan.links)} links, {len(plan.kept)} tracked entries "
          f"of the commit kept, run state excluded: {', '.join(plan.excluded) or '-'}")
    if not plan.links and "link" not in entries.values():
        print(f"WARNING: the symlink farm links nothing; the gitignored inputs of {shared_data} will be missing "
              "from the run (wrong --shared-data?)", file=sys.stderr)
    if not dry_run:
        apply_symlink_farm(plan, target)
    return {"shared_data": str(shared_data), "links": len(plan.links), "kept": len(plan.kept),
            "excluded": plan.excluded}


def cmd_setup(args, host: Host) -> int:
    run_dir = _expand(args.run_dir)
    name = _run_name(run_dir)
    dry_run = args.dry_run
    shared_bs, shared_java = _expand(args.shared_bs), _expand(args.shared_java)
    shared_data = _expand(args.shared_data) if args.shared_data else shared_bs / "eqasim-data"
    repositories = [RepositoryPlan(BS_DIR, shared_bs, args.bs, _optional_path(args.bs_bundle), run_dir / BS_DIR),
                    RepositoryPlan(JAVA_DIR, shared_java, args.java, _optional_path(args.java_bundle), run_dir / JAVA_DIR)]
    if (run_dir / RUN_JSON).exists():
        raise IsolatedRunError(f"{run_dir / RUN_JSON} exists: {run_dir} is set up already; a new run needs a new "
                               "run directory")
    for repository in repositories:
        if _inside(run_dir, repository.shared):
            raise IsolatedRunError(f"{run_dir} lies inside the shared checkout {repository.shared}; put run "
                                   "directories next to it")
        _git(host, repository.shared, "rev-parse", "--git-dir")
        if repository.bundle is not None and not repository.bundle.is_file():
            raise IsolatedRunError(f"bundle {repository.bundle} not found")
    if not shared_data.is_dir():
        raise IsolatedRunError(f"shared data directory {shared_data} not found (--shared-data)")

    print(f"setup {name} in {run_dir}" + (" -- DRY RUN: nothing is changed, nothing is fetched" if dry_run else ""))
    before = {repository.name: shared_checkout_state(host, repository.shared) for repository in repositories}
    _print_states("shared checkouts before (they must stay as they are)", before)
    for repository in repositories:
        _step(host, dry_run, ["git", "-C", repository.shared, "fetch", "-q", "origin"],
              f"fetch origin into the shared {repository.name} repository (its branch and files stay as they are)")
    for repository in repositories:
        _resolve_commit(host, repository, name, dry_run)

    cache_check = None
    if args.cache_from:
        cache_check = check_cache_copy(
            _expand(args.cache_from), run_dir / CACHE_DIR, free_bytes=host.disk_free(_existing_parent(run_dir)),
            reserve_bytes=int(args.reserve_gib * GIB), now=host.now().timestamp(),
            allow_active_source=args.allow_active_source, active_window_seconds=args.active_source_minutes * 60)
        print(f"  cache source {cache_check.source}: {cache_check.stats.files} files, "
              f"{_gib(cache_check.stats.bytes)} GiB; {_gib(cache_check.free_bytes)} GiB free")
    else:
        print("  no --cache-from: the run starts from an empty cache and computes every stage, PopulationSim included")

    if not dry_run:
        run_dir.mkdir(parents=True, exist_ok=True)
    for repository in repositories:
        _create_worktree(host, repository, dry_run)
    farm = _symlink_farm(host, repositories[0], shared_data, dry_run)
    copied = None if cache_check is None else copy_cache(cache_check, host, dry_run)
    if dry_run:
        print("dry run complete: nothing was changed")
        return EXIT_OK

    (run_dir / LOGS_DIR).mkdir(exist_ok=True)
    after = {repository.name: shared_checkout_state(host, repository.shared) for repository in repositories}
    record = {
        "schema_version": RECORD_SCHEMA_VERSION,
        "run_name": name,
        "run_dir": str(run_dir),
        "setup": {
            "time": _iso(host), "host": socket.gethostname(), "tool_blob": _tool_blob(),
            "repositories": {repository.name: repository.as_record() for repository in repositories},
            "symlink_farm": farm,
            "cache": None if cache_check is None else {"source": str(cache_check.source), "files": copied.files,
                                                       "links": copied.links, "bytes": copied.bytes},
            "shared_checkouts": {"before": before, "after": after, "unchanged": after == before},
        },
    }
    _save_record(run_dir, record)
    _print_states("shared checkouts after", after)
    if after != before:
        print("ERROR: a shared checkout changed during setup (see above); this tool only fetched, so another "
              "session is probably working there: look before you rely on it", file=sys.stderr)
        return EXIT_REFUSED
    print(f"run directory ready: {run_dir}")
    print(f"next: python scripts/isolated_run.py overlay {run_dir} --from <overlay of the run whose cache this is>")
    return EXIT_OK


# ---------------------------------------------------------------------------------------------
# processes, run log, launch and status

def find_run_processes(run_yml: Path, proc_root: Path) -> List[Tuple[int, str]]:
    """Processes running scripts/run_synpp.py on ``run_yml`` (the launch shell, the launcher, its workers)."""
    if not proc_root.is_dir():
        raise IsolatedRunError(f"process table {proc_root} not found: launch and status need Linux /proc")
    needle = str(run_yml)
    found = []
    for entry in proc_root.iterdir():
        if not entry.name.isdigit() or int(entry.name) == os.getpid():
            continue
        try:
            raw = (entry / "cmdline").read_bytes()
        except OSError:  # the process ended between the listing and the read
            continue
        cmdline = raw.replace(b"\0", b" ").decode("utf-8", "replace").strip()
        if "run_synpp.py" in cmdline and needle in cmdline:
            found.append((int(entry.name), cmdline))
    return sorted(found)


@dataclasses.dataclass
class RunLogSummary:
    started: Optional[str] = None
    exit_line: Optional[str] = None
    exit_code: Optional[int] = None
    last_executing_stage: Optional[str] = None
    traceback_count: int = 0
    explain: Optional[ExplainReport] = None
    explain_error: Optional[str] = None
    gate_aborted: Optional[str] = None
    lines_after_explain: int = 0


def summarise_run_log(text: str) -> RunLogSummary:
    """Read a launch log: start and exit lines, the launcher's [explain] block, stages, tracebacks."""
    summary = RunLogSummary()
    explain_lines: List[str] = []
    explain_done = False
    for line in text.splitlines():
        position = line.find(EXPLAIN_MARK)
        if position >= 0:
            body = line[position + len(EXPLAIN_MARK):]
            if body.startswith("aborting:"):
                summary.gate_aborted = body
            elif not explain_done:
                explain_lines.append(body)
                explain_done = bool(REPORT_SUMMARY.match(body))
            continue
        if explain_done and line.strip():
            summary.lines_after_explain += 1
        if START_LINE.search(line):
            summary.started = line.strip()
        match = EXIT_LINE.search(line)
        if match:
            summary.exit_line, summary.exit_code = line.strip(), int(match.group(1))
        match = EXECUTING.search(line)
        if match:
            summary.last_executing_stage = match.group(1)
        if "Traceback (most recent call last):" in line:
            summary.traceback_count += 1
    if explain_done:
        try:
            summary.explain = parse_explain_report("\n".join(explain_lines))
        except IsolatedRunError as error:
            summary.explain_error = str(error)
    return summary


def build_launch_script(run_name: str, bs_dir: Path, run_yml: Path, java_home: Path, conda_sh: Path,
                        conda_env: str) -> str:
    """The detached shell script of one launch: environment, start line, launcher, exit line."""
    quote = shlex.quote
    tag = f"{LOG_TAG} run={run_name}"
    return "\n".join([
        f'cd {quote(str(bs_dir))} || {{ echo "{tag} exit=96 end=$(date -Is) cd failed"; exit 96; }}',
        f"if ! {{ source {quote(str(conda_sh))} && conda activate {quote(conda_env)}; }}; then",
        f'  echo "{tag} exit=97 end=$(date -Is) conda activation failed"',
        "  exit 97",
        "fi",
        f"export JAVA_HOME={quote(str(java_home))}",
        'export PATH="$JAVA_HOME/bin:$HOME/.local/bin:$PATH"',
        "export PYTHONUTF8=1",
        "BS_COMMIT=$(git rev-parse HEAD)",
        f"JAVA_COMMIT=$(git -C ../{JAVA_DIR} rev-parse HEAD)",
        f'echo "{tag} eqasim-bs=$BS_COMMIT eqasim-java-bs=$JAVA_COMMIT start=$(date -Is)"',
        f"python {LAUNCHER_SCRIPT} {BASE_CONFIG} {quote(str(run_yml))}",
        "rc=$?",
        f'echo "{tag} eqasim-bs=$BS_COMMIT eqasim-java-bs=$JAVA_COMMIT exit=$rc end=$(date -Is)"',
        "exit $rc",
    ]) + "\n"


def _gate_problem(record: dict, doc: dict, run_yml: Path) -> Optional[str]:
    if not (doc.get("config") or {}).get(GATE_KEY):
        return f"run.yml has no {GATE_KEY} list, so the launcher would not gate the run"
    gate = record.get("gate")
    if not gate:
        return "run.json holds no gate record for this run.yml"
    if gate.get("run_yml_sha256") != _sha256_file(run_yml):
        return "run.yml changed after `gate --write`"
    return None


def _explain_lines(summary: RunLogSummary) -> List[str]:
    popsim = summary.explain.statuses.get(POPSIM_STAGE)
    lines = [f"launcher [explain] block complete, no gate abort: {summary.explain.summary}",
             f"{POPSIM_STAGE}: {popsim or 'not in the stage graph'}"]
    if popsim in ("STALE", "RECOMPUTE"):
        lines.append("WARNING: PopulationSim recomputes in this run")
    return lines


def _await_launch_check(log_path: Path, process, host: Host, wait_seconds: float) -> Tuple[bool, List[str]]:
    """Watch the new log until the launcher's [explain] verdict is in, the run ends, or time is up."""
    grace = None
    for _ in range(max(1, int(round(wait_seconds / POLL_SECONDS)))):
        summary = summarise_run_log(_read_text(log_path))
        returncode = process.poll()
        if summary.gate_aborted:
            return False, [f"the launcher gate ABORTED the run: {summary.gate_aborted}"]
        if summary.explain_error:
            return False, [f"the launcher's [explain] block cannot be read: {summary.explain_error}"]
        if returncode is not None:
            return False, [f"the run ended within the check (exit {returncode}, {summary.traceback_count} "
                           f"traceback(s)); see {log_path}:\n{_tail(_read_text(log_path))}"]
        if summary.explain is not None:
            if summary.lines_after_explain or grace == 0:
                return True, _explain_lines(summary)
            grace = GRACE_POLLS if grace is None else grace - 1
        host.sleep(POLL_SECONDS)
    return False, [f"the run is alive, but its [explain] block did not finish within {wait_seconds:.0f} s; "
                   "check `status` again in a minute"]


def cmd_launch(args, host: Host) -> int:
    run_dir = _expand(args.run_dir)
    name = _run_name(run_dir)
    record = _load_record(run_dir)
    bs_dir, run_yml = run_dir / BS_DIR, run_dir / RUN_YML
    for directory in (bs_dir, run_dir / JAVA_DIR):
        if not directory.is_dir():
            raise IsolatedRunError(f"{directory} not found: run `setup` first")
    running = find_run_processes(run_yml, host.proc_root)
    if running:
        raise IsolatedRunError(f"{run_yml} is running already (pids {', '.join(str(pid) for pid, _ in running)}); "
                               "a second start would write into the same cache")
    doc = _read_run_yml(run_yml)
    problems = _isolation_problems(run_dir, doc)
    if problems:
        raise IsolatedRunError("run.yml is not isolated: " + "; ".join(problems) + " (write it with `overlay`)")
    gate_problem = _gate_problem(record, doc, run_yml)
    if gate_problem and not args.force_no_gate:
        raise IsolatedRunError(f"{gate_problem}: run `gate {run_dir} --write`, or pass --force-no-gate to launch "
                               "without the launcher gate")
    if gate_problem:
        print(f"WARNING: launching WITHOUT a launcher gate ({gate_problem})", file=sys.stderr)
    java_home, conda_sh = _expand(args.java_home), _expand(args.conda_sh)
    if not (java_home / "bin" / "java").is_file():
        raise IsolatedRunError(f"{java_home}/bin/java not found (--java-home)")
    if not conda_sh.is_file():
        raise IsolatedRunError(f"{conda_sh} not found (--conda-sh)")
    logs = run_dir / LOGS_DIR
    logs.mkdir(exist_ok=True)
    log_path = logs / f"run_{_stamp(host)}.log"
    if log_path.exists():
        raise IsolatedRunError(f"{log_path} exists already; start again in a second")

    script = build_launch_script(name, bs_dir, run_yml, java_home, conda_sh, args.conda_env)
    with open(log_path, "ab") as log_handle:
        process = host.popen(["setsid", "nohup", "bash", "-c", script], cwd=str(bs_dir), stdin=subprocess.DEVNULL,
                             stdout=log_handle, stderr=subprocess.STDOUT, close_fds=True)
    entry = {"time": _iso(host), "pid": process.pid, "log": str(log_path), "run_yml_sha256": _sha256_file(run_yml),
             "gate": "forced-off" if gate_problem else "gated", "java_home": str(java_home),
             "conda_env": args.conda_env,
             "commits": {BS_DIR: _git(host, bs_dir, "rev-parse", "HEAD").strip(),
                         JAVA_DIR: _git(host, run_dir / JAVA_DIR, "rev-parse", "HEAD").strip()},
             "tool_blob": _tool_blob()}
    record.setdefault("launches", []).append(entry)
    _save_record(run_dir, record)
    print(f"launched {name}: pid {process.pid}, log {log_path}")
    if args.wait_seconds <= 0:
        print("no launch check (--wait-seconds 0): run `status` within a minute")
        return EXIT_OK
    passed, lines = _await_launch_check(log_path, process, host, args.wait_seconds)
    entry["check"] = {"passed": passed, "lines": lines}
    _save_record(run_dir, record)
    for line in lines:
        print(line, file=sys.stdout if passed and not line.startswith("WARNING") else sys.stderr)
    return EXIT_OK if passed else EXIT_REFUSED


def cmd_status(args, host: Host) -> int:
    run_dir = _expand(args.run_dir)
    record = _load_record(run_dir)
    run_yml = run_dir / RUN_YML
    print(f"run {run_dir.name} ({run_dir})")
    gate = record.get("gate")
    if gate:
        current = run_yml.is_file() and _sha256_file(run_yml) == gate.get("run_yml_sha256")
        print(f"gate: {len(gate['stale_stages'])} expected stale stages, written {gate['time']}"
              + ("" if current else "; run.yml CHANGED since"))
    else:
        print("gate: none")
    processes = find_run_processes(run_yml, host.proc_root)
    if processes:
        pids = ", ".join(str(pid) for pid, _ in processes[:8]) + (" ..." if len(processes) > 8 else "")
        print(f"RUNNING: {len(processes)} process(es), pids {pids}")
    else:
        print("not running")
    launches = record.get("launches") or []
    if not launches:
        print("no launch recorded")
        return EXIT_OK
    last = launches[-1]
    log_path = Path(last["log"])
    print(f"launches: {len(launches)}; latest {last['time']}, pid {last['pid']}, log {log_path}")
    if log_path.is_file():
        age_minutes = (host.now().timestamp() - log_path.stat().st_mtime) / 60
        print(f"log written {age_minutes:.1f} min ago (silent phases can be long: judge a stall by CPU, not log age)")
    summary = summarise_run_log(_read_text(log_path))
    if summary.gate_aborted:
        print(f"launcher gate ABORTED: {summary.gate_aborted}")
    elif summary.explain_error:
        print(f"[explain] block unreadable: {summary.explain_error}")
    elif summary.explain is not None:
        for line in _explain_lines(summary):
            print(line)
    else:
        print("[explain] block not complete yet")
    print(f"last stage started: {summary.last_executing_stage or '-'}")
    print(f"tracebacks in the log: {summary.traceback_count}")
    print(f"exit: {summary.exit_line or '-'}")
    if processes or summary.exit_code == 0:
        return EXIT_OK
    if summary.exit_code is None:
        print("no exit line and no process: the run was killed or the machine restarted", file=sys.stderr)
    return EXIT_REFUSED


# ---------------------------------------------------------------------------------------------
# command line

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="isolated_run.py", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)

    setup = commands.add_parser("setup", help="worktrees, symlink farm, cache copy, run.json")
    setup.add_argument("run_dir", metavar="RUN_DIR")
    setup.add_argument("--bs", required=True, metavar="REF",
                       help="eqasim-bs commit, branch or tag; with --bs-bundle a ref inside the bundle")
    setup.add_argument("--bs-bundle", metavar="FILE",
                       help=f"git bundle with unpushed eqasim-bs work, fetched into {BUNDLE_REF_NAMESPACE}/<run>/eqasim-bs")
    setup.add_argument("--java", required=True, metavar="REF",
                       help="eqasim-java-bs commit, branch or tag; with --java-bundle a ref inside the bundle")
    setup.add_argument("--java-bundle", metavar="FILE", help="git bundle with unpushed eqasim-java-bs work")
    setup.add_argument("--cache-from", metavar="DIR",
                       help="synpp working directory of an earlier run, copied physically into RUN_DIR/cache")
    setup.add_argument("--dry-run", action="store_true", help="print the plan and change nothing (no fetch either)")
    setup.add_argument("--shared-bs", default=DEFAULT_SHARED_BS, help="shared eqasim-bs checkout (default %(default)s)")
    setup.add_argument("--shared-java", default=DEFAULT_SHARED_JAVA,
                       help="shared eqasim-java-bs checkout (default %(default)s)")
    setup.add_argument("--shared-data", help="eqasim-data with the gitignored inputs (default <shared-bs>/eqasim-data)")
    setup.add_argument("--reserve-gib", type=float, default=DEFAULT_RESERVE_GIB,
                       help="GiB that must stay free after the cache copy (default %(default)s)")
    setup.add_argument("--active-source-minutes", type=float, default=DEFAULT_ACTIVE_SOURCE_MINUTES,
                       help="refuse a cache source written to within this many minutes (default %(default)s)")
    setup.add_argument("--allow-active-source", action="store_true",
                       help="copy a recently written cache source anyway (nothing writes there any more)")
    setup.set_defaults(handler=cmd_setup)

    overlay = commands.add_parser("overlay", help="write RUN_DIR/run.yml from an overlay")
    overlay.add_argument("run_dir", metavar="RUN_DIR")
    overlay.add_argument("--from", dest="source", required=True, metavar="OVERLAY.yml",
                         help="overlay of the run whose cache was copied (its popsim work_dir string is kept)")
    overlay.add_argument("--prefix", help="new output_prefix (default: keep it; it enters the prepare stage hash)")
    overlay.add_argument("--force", action="store_true", help="replace an existing run.yml (never after a launch)")
    overlay.set_defaults(handler=cmd_overlay)

    gate = commands.add_parser("gate", help="explain + input check; --write stores the launcher gate list")
    gate.add_argument("run_dir", metavar="RUN_DIR")
    gate.add_argument("--write", action="store_true", help=f"store the STALE stages as config.{GATE_KEY}")
    gate.add_argument("--allow-popsim", action="store_true", help="accept a PopulationSim recompute")
    gate.add_argument("--skip-input-check", action="store_true", help=f"do not run {INPUTS_SCRIPT}")
    gate.add_argument("--python", default=sys.executable, help="interpreter of the eqasim env (default: this one)")
    gate.set_defaults(handler=cmd_gate)

    launch = commands.add_parser("launch", help="start the gated run detached (setsid nohup)")
    launch.add_argument("run_dir", metavar="RUN_DIR")
    launch.add_argument("--java-home", default=DEFAULT_JAVA_HOME, help="JDK for MATSim (default %(default)s)")
    launch.add_argument("--conda-sh", default=DEFAULT_CONDA_SH, help="conda hook script (default %(default)s)")
    launch.add_argument("--conda-env", default=DEFAULT_CONDA_ENV, help="conda env (default %(default)s)")
    launch.add_argument("--force-no-gate", action="store_true", help="launch without a launcher gate list")
    launch.add_argument("--wait-seconds", type=float, default=DEFAULT_WAIT_SECONDS,
                        help="watch the log this long for the launcher's [explain] verdict; 0 skips (default %(default)s)")
    launch.set_defaults(handler=cmd_launch)

    status = commands.add_parser("status", help="running or not, gate verdict, last stage, tracebacks, exit")
    status.add_argument("run_dir", metavar="RUN_DIR")
    status.set_defaults(handler=cmd_status)
    return parser


def main(argv=None, host: Optional[Host] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.handler(args, host or Host())
    except IsolatedRunError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return EXIT_REFUSED


if __name__ == "__main__":
    sys.exit(main())
