"""Exercise shared-cache reuse through synpp's actual invalidation rules."""
import importlib
import json
import sys
from copy import deepcopy

import pytest
import synpp

from braunschweig import cache_share


@pytest.fixture
def pipeline(tmp_path, monkeypatch):
    module_name = "shared_cache_probe"
    (tmp_path / (module_name + ".py")).write_text(
        "from pathlib import Path\n"
        "def configure(context):\n"
        "    context.config('trace')\n"
        "    context.config('value')\n"
        "    context.config('token_file')\n"
        "def validate(context):\n"
        "    return Path(context.config('token_file')).read_text()\n"
        "def execute(context):\n"
        "    with open(context.config('trace'), 'a') as f: f.write('executed\\n')\n"
        "    return context.config('value')\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delitem(sys.modules, module_name, raising=False)
    importlib.invalidate_caches()
    trace = tmp_path / "trace.txt"
    token = tmp_path / "token.txt"
    token.write_text("original", encoding="utf-8")
    config = {"trace": str(trace), "token_file": str(token), "value": 17}

    def run(directory, value=17):
        directory.mkdir(exist_ok=True)
        with cache_share.track_run(str(directory)):
            return synpp.run([{"descriptor": module_name}], {**config, "value": value},
                             working_directory=str(directory), rerun_required=False)

    return module_name, run, trace, token


def test_export_prime_reuses_real_synpp_result(tmp_path, pipeline):
    module, run, trace, _ = pipeline
    source, store, target = (tmp_path / name for name in ("source", "store", "target"))
    expected = run(source)
    cache_share.export(str(source), [module], str(store))
    cache_share.prime(str(target), [module], str(store), [])
    assert run(target) == expected
    assert trace.read_text().splitlines() == ["executed"]


def test_untracked_native_entries_cannot_gain_invented_creation_provenance(tmp_path, pipeline):
    module, _, trace, token = pipeline
    source, store, target = (tmp_path / name for name in ("source", "store", "target"))
    config = {"trace": str(trace), "token_file": str(token), "value": 17}
    source.mkdir()
    synpp.run([{"descriptor": module}], config, working_directory=str(source), rerun_required=False)
    cache_share.export(str(source), [module], str(store))
    cache_share.prime(str(target), [module], str(store), [])
    synpp.run([{"descriptor": module}], config, working_directory=str(target), rerun_required=False)
    assert trace.read_text().splitlines() == ["executed", "executed"]


@pytest.mark.parametrize("difference", ["system", "packages"])
def test_runtime_mismatch_is_a_real_synpp_miss(tmp_path, pipeline, monkeypatch, difference):
    module, run, trace, _ = pipeline
    source, store, target = (tmp_path / name for name in ("source", "store", "target"))
    expected = run(source)
    cache_share.export(str(source), [module], str(store))
    environment = deepcopy(cache_share._runtime_fingerprint())
    if difference == "packages":
        environment["packages"]["numpy"] = "different-version"
    else:
        environment["system"] = "different-operating-system"
    monkeypatch.setattr(cache_share, "_runtime_fingerprint", lambda: environment)
    cache_share.prime(str(target), [module], str(store), [])
    assert run(target) == expected
    assert trace.read_text().splitlines() == ["executed", "executed"]


def test_reexport_preserves_known_creation_environment(tmp_path, pipeline, monkeypatch):
    module, run, _, _ = pipeline
    source, store = tmp_path / "source", tmp_path / "store"
    run(source)
    entry = cache_share.find_stage_entries(str(source), module)[0]
    original = json.loads((source / (entry + ".metadata.json")).read_text())
    monkeypatch.setattr(cache_share, "_runtime_fingerprint", lambda: {"system": "different"})
    cache_share.export(str(source), [module], str(store))
    assert json.loads((store / (entry + ".metadata.json")).read_text()) == original


def test_tracking_unchanged_unknown_hits_does_not_relabel_them(tmp_path, pipeline):
    module, run, trace, _ = pipeline
    source = tmp_path / "source"
    run(source)
    entry = cache_share.find_stage_entries(str(source), module)[0]
    sidecar = source / (entry + ".metadata.json")
    sidecar.unlink()
    run(source)
    assert trace.read_text().splitlines() == ["executed"]
    assert not sidecar.exists()


@pytest.mark.parametrize("change", ["config", "validation_token", "module_hash"])
def test_shared_cache_keeps_synpp_invalidation(tmp_path, pipeline, change):
    module, run, trace, token = pipeline
    source, store, target = (tmp_path / name for name in ("source", "store", "target"))
    run(source)
    if change == "module_hash":
        path = source / "pipeline.json"
        metadata = json.loads(path.read_text())
        for record in metadata.values():
            record["module_hash"] = "previous-implementation"
        path.write_text(json.dumps(metadata), encoding="utf-8")
        # Model a tracked result produced by that previous implementation.
        for entry, record in metadata.items():
            sidecar = source / (entry + ".metadata.json")
            envelope = json.loads(sidecar.read_text())
            envelope["metadata"] = record
            sidecar.write_text(json.dumps(envelope), encoding="utf-8")
    cache_share.export(str(source), [module], str(store))
    cache_share.prime(str(target), [module], str(store), [])
    if change == "validation_token":
        token.write_text("changed", encoding="utf-8")
    run(target, value=18 if change == "config" else 17)
    assert trace.read_text().splitlines() == ["executed", "executed"]


@pytest.mark.parametrize("share_metadata", [False, True])
def test_legacy_store_remains_a_safe_miss(tmp_path, pipeline, share_metadata):
    module, run, trace, _ = pipeline
    source, store, target = (tmp_path / name for name in ("source", "store", "target"))
    expected = run(source)
    cache_share.export(str(source), [module], str(store), share_metadata=False)
    cache_share.prime(str(target), [module], str(store), [], share_metadata=share_metadata)
    assert run(target) == expected
    assert trace.read_text().splitlines() == ["executed", "executed"]


def test_off_path_ignores_store_metadata(tmp_path, pipeline):
    module, run, trace, _ = pipeline
    source, store, target = (tmp_path / name for name in ("source", "store", "target"))
    expected = run(source)
    cache_share.export(str(source), [module], str(store))
    cache_share.prime(str(target), [module], str(store), [], share_metadata=False)
    assert not (target / "pipeline.json").exists()
    assert run(target) == expected
    assert trace.read_text().splitlines() == ["executed", "executed"]


def test_existing_payload_never_gets_foreign_metadata(tmp_path, pipeline):
    module, run, _, _ = pipeline
    source, store, target = (tmp_path / name for name in ("source", "store", "target"))
    run(source)
    cache_share.export(str(source), [module], str(store))
    cache_share.prime(str(target), [module], str(store), [], share_metadata=False)
    report = cache_share.prime(str(target), [module], str(store), [])
    assert report["skipped_present"]
    assert not (target / "pipeline.json").exists()


def test_failed_copy_cannot_leave_a_validating_old_record(tmp_path, pipeline, monkeypatch):
    module, run, _, _ = pipeline
    source, store, target = (tmp_path / name for name in ("source", "store", "target"))
    run(source)
    cache_share.export(str(source), [module], str(store))
    target.mkdir()
    (target / "pipeline.json").write_bytes((source / "pipeline.json").read_bytes())

    def fail_copy(*args, **kwargs):
        raise OSError("simulated interrupted directory copy")

    monkeypatch.setattr(cache_share.shutil, "copytree", fail_copy)
    with pytest.raises(OSError, match="simulated interrupted"):
        cache_share.prime(str(target), [module], str(store), [])
    assert json.loads((target / "pipeline.json").read_text()) == {}


def test_merge_retains_existing_unrelated_metadata(tmp_path, pipeline):
    module, run, trace, _ = pipeline
    source, store, target = (tmp_path / name for name in ("source", "store", "target"))
    run(source)
    cache_share.export(str(source), [module], str(store))
    target.mkdir()
    (target / "pipeline.json").write_text('{"unrelated": {"sentinel": 1}}')
    cache_share.prime(str(target), [module], str(store), [])
    assert json.loads((target / "pipeline.json").read_text())["unrelated"] == {"sentinel": 1}
    run(target)
    assert trace.read_text().splitlines() == ["executed"]


def _age_recorded_runs(directory, seconds=1.0):
    """Move a working directory's recorded synpp timestamps strictly into the past.

    The two cross-run scenarios below need the target's entries to come from a
    DIFFERENT run than the store's, but they expressed that only through wall-clock
    order: two sub-millisecond synpp runs a few statements apart. synpp stamps
    ``updated`` with ``datetime.now(timezone.utc).timestamp()``, whose resolution on
    Windows is the ~15.6 ms system tick, so both runs land on the IDENTICAL float
    often enough to matter (measured: 2 failures in 120 runs of
    ``older_target_parent``).

    On such a collision the scenario silently loses its precondition:
    ``cache_share._drop_incoherent_metadata`` requires EXACT timestamp equality to
    judge a primed child coherent with the target's parent -- deliberately, see its
    docstring -- so an equal pair keeps the primed record instead of dropping it,
    and synpp's ``parent.updated > child.dependencies[parent]`` is False for equal
    values. The child is then served from cache rather than re-executed, and the
    test fails although nothing is wrong with the code under test.

    Ageing the recorded timestamps states the precondition instead of hoping for it,
    and removes the clock from the test. ``updated`` and the dependency snapshots
    are shifted by the SAME amount so the directory's own entries stay internally
    coherent; only recorded metadata is touched, never a cached payload, so what
    synpp is asked to decide is unchanged.
    """
    path = directory / "pipeline.json"
    metadata = json.loads(path.read_text(encoding="utf-8"))
    for record in metadata.values():
        record["updated"] -= seconds
        record["dependencies"] = {
            dependency: timestamp - seconds
            for dependency, timestamp in record.get("dependencies", {}).items()
        }
    path.write_text(json.dumps(metadata), encoding="utf-8")


def _recorded_timestamps(directory):
    metadata = json.loads((directory / "pipeline.json").read_text(encoding="utf-8"))
    stamps = set()
    for record in metadata.values():
        stamps.add(record["updated"])
        stamps.update(record.get("dependencies", {}).values())
    return stamps


def _assert_runs_are_distinguishable(target, source):
    """The precondition of the two cross-run scenarios, asserted instead of assumed.

    Both need the target's entries to be recognisably from a different run than the
    store's. Coherence is decided by EXACT timestamp equality, so a single shared
    value silently turns the scenario into a different one -- which is how this test
    used to fail about 2.5 % of the time on a coarse clock.

    What this assertion does and does NOT do, precisely: ``_age_recorded_runs`` is
    what makes the precondition hold, by shifting the target a whole second away so
    equality is impossible. This check does not re-establish that; verified by
    mutation, removing the ageing leaves this assertion passing on ~97.5 % of runs,
    because the clock usually does tick between the two runs. Its value is that on
    the remaining ~2.5 % the failure now names the cause -- shared timestamps, runs
    indistinguishable -- instead of surfacing as a confusing trace mismatch several
    statements later.
    """
    shared = _recorded_timestamps(target) & _recorded_timestamps(source)
    assert not shared, (
        f"target and source share recorded timestamps {sorted(shared)}; the scenario "
        f"cannot distinguish the two runs (see _age_recorded_runs)")


@pytest.mark.parametrize("scenario", ["complete", "missing_parent", "changed_parent", "forced_parent",
                                    "older_target_parent", "newer_target_child"])
def test_shared_dependency_chain(tmp_path, pipeline, monkeypatch, scenario):
    module, _, trace, token = pipeline
    child = "shared_cache_probe_child"
    monkeypatch.delitem(sys.modules, child, raising=False)
    (tmp_path / (child + ".py")).write_text(
        "def configure(context):\n"
        f"    context.stage('{module}')\n"
        "    context.config('trace')\n"
        "def execute(context):\n"
        "    with open(context.config('trace'), 'a') as f: f.write('child\\n')\n"
        f"    return context.stage('{module}') + 1\n",
        encoding="utf-8",
    )
    importlib.invalidate_caches()
    config = {"trace": str(trace), "token_file": str(token), "value": 17}

    def run(directory):
        directory.mkdir(exist_ok=True)
        with cache_share.track_run(str(directory)):
            return synpp.run([{"descriptor": child}], config, working_directory=str(directory),
                             rerun_required=False)

    source, store, target = (tmp_path / name for name in ("source", "store", "target"))
    if scenario == "older_target_parent":
        target.mkdir()
        synpp.run([{"descriptor": module}], config, working_directory=str(target),
                  rerun_required=False)
        # Make "older" true by construction rather than by clock tick.
        _age_recorded_runs(target)
    expected = run(source)
    if scenario == "newer_target_child":
        assert run(target) == expected
        # Same reason: the target's run must be distinguishable from the store's.
        _age_recorded_runs(target)
        for entry in cache_share.find_stage_entries(str(target), module):
            (target / (entry + ".p")).unlink()
    if scenario in ("older_target_parent", "newer_target_child"):
        _assert_runs_are_distinguishable(target, source)
    modules = [child] if scenario == "missing_parent" else [module, child]
    cache_share.export(str(source), modules, str(store))
    forced = [module] if scenario == "forced_parent" else []
    cache_share.prime(str(target), modules, str(store), forced)
    if scenario == "changed_parent":
        token.write_text("changed", encoding="utf-8")
    assert run(target) == expected
    executions = ["executed", "child"] * (1 if scenario == "complete" else 2)
    if scenario == "older_target_parent":
        executions = ["executed", "executed", "child", "child"]
    if scenario == "newer_target_child":
        executions = ["executed", "child", "executed", "child", "child"]
    assert trace.read_text().splitlines() == executions
