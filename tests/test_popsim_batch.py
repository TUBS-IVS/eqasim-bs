"""Tests for the PopulationSim batching module (``braunschweig.popsim.batch``).

These tests use only tiny synthetic cell mappings -- no real Zensus data and no
PopulationSim subprocess. The runner is exercised with an injected fake
``run_one`` callable so the real subprocess never runs.
"""

from __future__ import annotations

import json
import threading
import time

import pytest

from braunschweig.popsim import batch


# A 1 km id is atomic; here we only need distinct keys and child-count control.
def _make_groups(sizes: dict[str, int]) -> dict[str, list[str]]:
    """Build a {1km_id: [100m_ids]} mapping with the requested child counts.

    The ids are synthetic but unique per (parent, child) so hashing/counting is
    well defined; the parent ids are ordered so that sorted order is predictable.
    """
    groups: dict[str, list[str]] = {}
    for parent, n in sizes.items():
        groups[parent] = [f"{parent}_c{i:03d}" for i in range(n)]
    return groups


# --------------------------------------------------------------------------- #
# partition_by_1km
# --------------------------------------------------------------------------- #


def test_single_oversize_parent_gets_its_own_partition():
    # A single 1 km parent with more children than max_cells must stay whole.
    groups = _make_groups({"km_A": 150})
    parts = batch.partition_by_1km(groups, max_cells=100)
    assert parts == [["km_A"]]


def test_oversize_parent_is_never_split_across_partitions():
    # Even mixed with small parents, the over-size parent is one whole partition.
    groups = _make_groups({"km_A": 30, "km_B": 150, "km_C": 30})
    parts = batch.partition_by_1km(groups, max_cells=100)
    # km_A (30) fits; adding km_B (150) would exceed -> new partition with the
    # whole km_B; km_C then starts a third partition.
    assert parts == [["km_A"], ["km_B"], ["km_C"]]
    # No partition splits km_B's children: km_B appears exactly once, whole.
    flat = [p for part in parts for p in part]
    assert flat.count("km_B") == 1


def test_greedy_packing_groups_small_parents_up_to_max():
    # 4 parents of 30 each, max 100 -> [A,B,C] (90) then [D] (next 30 -> 120>100).
    groups = _make_groups({"km_A": 30, "km_B": 30, "km_C": 30, "km_D": 30})
    parts = batch.partition_by_1km(groups, max_cells=100)
    assert parts == [["km_A", "km_B", "km_C"], ["km_D"]]


def test_partition_is_in_sorted_key_order_and_deterministic():
    # Insertion order is deliberately not sorted; output must be sorted + stable.
    groups = {
        "km_C": ["km_C_c000"],
        "km_A": ["km_A_c000"],
        "km_B": ["km_B_c000"],
    }
    parts_first = batch.partition_by_1km(groups, max_cells=100)
    parts_second = batch.partition_by_1km(groups, max_cells=100)
    assert parts_first == parts_second  # determinism
    assert parts_first == [["km_A", "km_B", "km_C"]]  # sorted, all fit


def test_partition_exact_fit_does_not_start_new_partition():
    # Two parents of 50 each with max 100 fit exactly into one partition.
    groups = _make_groups({"km_A": 50, "km_B": 50})
    parts = batch.partition_by_1km(groups, max_cells=100)
    assert parts == [["km_A", "km_B"]]


def test_partition_rejects_nonpositive_max_cells():
    groups = _make_groups({"km_A": 1})
    with pytest.raises(ValueError):
        batch.partition_by_1km(groups, max_cells=0)


# --------------------------------------------------------------------------- #
# count_100m_cells
# --------------------------------------------------------------------------- #


def test_count_100m_cells_sums_children():
    groups = _make_groups({"km_A": 3, "km_B": 7})
    assert batch.count_100m_cells(groups) == 10


def test_count_100m_cells_empty():
    assert batch.count_100m_cells({}) == 0


# --------------------------------------------------------------------------- #
# sha1_of_cells
# --------------------------------------------------------------------------- #


def test_sha1_of_cells_is_order_independent():
    cells_a = ["c2", "c1", "c3"]
    cells_b = ["c3", "c2", "c1"]
    assert batch.sha1_of_cells(cells_a) == batch.sha1_of_cells(cells_b)


def test_sha1_of_cells_is_deterministic_and_value_sensitive():
    base = batch.sha1_of_cells(["c1", "c2"])
    assert base == batch.sha1_of_cells(["c1", "c2"])
    assert base != batch.sha1_of_cells(["c1", "c3"])
    # Hex digest of sha1 is 40 chars.
    assert len(base) == 40


# --------------------------------------------------------------------------- #
# build_manifest / SplitManifest
# --------------------------------------------------------------------------- #


def test_build_manifest_computes_counts_and_sorts_km_cells():
    groups = _make_groups({"km_B": 2, "km_A": 3})
    manifest = batch.build_manifest(
        source_folder="run/source",
        max_cells=100,
        split_index=0,
        km_cells=["km_B", "km_A"],  # unsorted on purpose
        cell_groups=groups,
    )
    assert manifest.schema_version == 1
    assert manifest.km_cells == ("km_A", "km_B")  # stored sorted
    assert manifest.num_1km == 2
    assert manifest.num_100m == 5
    assert manifest.source_folder == "run/source"
    assert manifest.split_index == 0
    # sha matches the hash of all 100 m ids across the two km cells.
    all_100m = groups["km_A"] + groups["km_B"]
    assert manifest.cells_100m_sha1 == batch.sha1_of_cells(all_100m)


def test_build_manifest_rejects_unknown_km_cell():
    groups = _make_groups({"km_A": 1})
    with pytest.raises(ValueError):
        batch.build_manifest(
            source_folder="src",
            max_cells=100,
            split_index=0,
            km_cells=["km_A", "km_MISSING"],
            cell_groups=groups,
        )


# --------------------------------------------------------------------------- #
# manifests_equivalent
# --------------------------------------------------------------------------- #


def test_manifests_equivalent_true_for_identical_split_any_km_order():
    # Same source, index, max_cells and cells (km order differs only on input).
    groups = _make_groups({"km_A": 3, "km_B": 2})
    a = batch.build_manifest("src", 100, 0, ["km_A", "km_B"], groups)
    b = batch.build_manifest("src", 100, 0, ["km_B", "km_A"], groups)
    assert batch.manifests_equivalent(a, b) is True


def test_manifests_equivalent_false_when_source_or_index_differs():
    # Faithful to popsimprep batch_run_popsim.py: source_folder and split_index
    # are part of the split identity (reuse-or-rebuild is per split folder).
    groups = _make_groups({"km_A": 3, "km_B": 2})
    a = batch.build_manifest("srcA", 100, 0, ["km_A", "km_B"], groups)
    assert batch.manifests_equivalent(
        a, batch.build_manifest("srcB", 100, 0, ["km_A", "km_B"], groups)
    ) is False
    assert batch.manifests_equivalent(
        a, batch.build_manifest("srcA", 100, 9, ["km_A", "km_B"], groups)
    ) is False


def test_manifests_equivalent_false_when_a_cell_differs():
    groups_a = _make_groups({"km_A": 3, "km_B": 2})
    groups_b = _make_groups({"km_A": 3, "km_C": 2})
    a = batch.build_manifest("src", 100, 0, ["km_A", "km_B"], groups_a)
    b = batch.build_manifest("src", 100, 0, ["km_A", "km_C"], groups_b)
    assert batch.manifests_equivalent(a, b) is False


def test_manifests_equivalent_false_when_max_cells_differs():
    groups = _make_groups({"km_A": 3})
    a = batch.build_manifest("src", 100, 0, ["km_A"], groups)
    b = batch.build_manifest("src", 200, 0, ["km_A"], groups)
    assert batch.manifests_equivalent(a, b) is False


# --------------------------------------------------------------------------- #
# write_manifest / read_manifest round-trip
# --------------------------------------------------------------------------- #


def test_write_read_manifest_round_trip(tmp_path):
    groups = _make_groups({"km_A": 3, "km_B": 2})
    manifest = batch.build_manifest("src", 100, 1, ["km_A", "km_B"], groups)
    path = tmp_path / "split_manifest.json"
    batch.write_manifest(manifest, path)
    assert path.exists()
    # File is valid JSON.
    json.loads(path.read_text(encoding="utf-8"))
    loaded = batch.read_manifest(path)
    assert loaded == manifest
    assert batch.manifests_equivalent(loaded, manifest) is True


# --------------------------------------------------------------------------- #
# is_completed
# --------------------------------------------------------------------------- #


def test_is_completed_false_without_marker(tmp_path):
    assert batch.is_completed(tmp_path) is False


def test_is_completed_true_with_marker(tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    (output / "final_expanded_household_ids.csv").write_text("x", encoding="utf-8")
    assert batch.is_completed(tmp_path) is True


# --------------------------------------------------------------------------- #
# run_batches with an injected fake run_one
# --------------------------------------------------------------------------- #


def test_run_batches_classifies_succeeded_failed_skipped():
    def fake_run_one(folder: str) -> batch.BatchResult:
        if folder == "ok":
            return batch.BatchResult(folder, "succeeded", "done", 0.1)
        if folder == "bad":
            return batch.BatchResult(folder, "failed", "boom", 0.2)
        return batch.BatchResult(folder, "skipped", "already done", None)

    summary = batch.run_batches(["ok", "bad", "skip"], fake_run_one, num_workers=2)
    assert summary.n_total == 3
    assert summary.n_succeeded == 1
    assert summary.n_failed == 1
    assert summary.n_skipped == 1
    statuses = {r.folder: r.status for r in summary.results}
    assert statuses == {"ok": "succeeded", "bad": "failed", "skip": "skipped"}


def test_run_batches_respects_num_workers_bound():
    # A fake run_one that records the max number of concurrently active calls;
    # with num_workers=2 the observed concurrency must never exceed 2.
    lock = threading.Lock()
    state = {"active": 0, "max_active": 0}

    def fake_run_one(folder: str) -> batch.BatchResult:
        with lock:
            state["active"] += 1
            state["max_active"] = max(state["max_active"], state["active"])
        time.sleep(0.02)
        with lock:
            state["active"] -= 1
        return batch.BatchResult(folder, "succeeded", "ok", 0.02)

    folders = [f"f{i}" for i in range(8)]
    summary = batch.run_batches(folders, fake_run_one, num_workers=2)
    assert summary.n_succeeded == 8
    assert state["max_active"] <= 2


def test_run_batches_unknown_status_is_rejected():
    def fake_run_one(folder: str) -> batch.BatchResult:
        return batch.BatchResult(folder, "weird", "?", None)

    with pytest.raises(ValueError):
        batch.BatchResult("f", "weird", "?", None)

    # Also surfaced if a result with an invalid status reaches the summary.
    with pytest.raises(ValueError):
        batch.run_batches(["f"], fake_run_one, num_workers=1)


# --------------------------------------------------------------------------- #
# make_populationsim_run_one (concrete subprocess runner, fake-injected)
# --------------------------------------------------------------------------- #


class _FakeCompleted:
    def __init__(self, returncode):
        self.returncode = returncode
        self.stdout = ""
        self.stderr = ""


def _completed_folder(tmp_path, name="batch"):
    folder = tmp_path / name
    (folder / "output").mkdir(parents=True)
    return folder


def test_subprocess_run_one_skips_completed_without_calling_subprocess(tmp_path):
    folder = _completed_folder(tmp_path)
    (folder / "output" / "final_expanded_household_ids.csv").write_text("x")
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return _FakeCompleted(0)

    run_one = batch.make_populationsim_run_one(subprocess_run=fake_run)
    result = run_one(str(folder))
    assert result.status == "skipped"
    assert calls == []  # a completed batch must not spawn the subprocess


def test_subprocess_run_one_succeeds_when_output_created(tmp_path):
    folder = _completed_folder(tmp_path)

    def fake_run(cmd, **kwargs):
        (folder / "output" / "final_expanded_household_ids.csv").write_text("x")
        return _FakeCompleted(0)

    run_one = batch.make_populationsim_run_one(subprocess_run=fake_run)
    assert run_one(str(folder)).status == "succeeded"


def test_subprocess_run_one_fails_on_nonzero_exit(tmp_path):
    folder = _completed_folder(tmp_path)
    run_one = batch.make_populationsim_run_one(
        subprocess_run=lambda cmd, **kwargs: _FakeCompleted(2)
    )
    result = run_one(str(folder))
    assert result.status == "failed"
    assert "2" in result.message


def test_subprocess_run_one_fails_when_no_output_despite_success(tmp_path):
    folder = _completed_folder(tmp_path)
    run_one = batch.make_populationsim_run_one(
        subprocess_run=lambda cmd, **kwargs: _FakeCompleted(0)
    )
    result = run_one(str(folder))
    assert result.status == "failed"
    assert "output" in result.message.lower()


def test_subprocess_run_one_fails_on_timeout(tmp_path):
    import subprocess

    folder = _completed_folder(tmp_path)

    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, 3600)

    run_one = batch.make_populationsim_run_one(subprocess_run=fake_run)
    result = run_one(str(folder))
    assert result.status == "failed"
    assert "timeout" in result.message.lower()


def test_subprocess_run_one_builds_expected_command_and_cwd(tmp_path):
    folder = _completed_folder(tmp_path)
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["cwd"] = kwargs.get("cwd")
        (folder / "output" / "final_expanded_household_ids.csv").write_text("x")
        return _FakeCompleted(0)

    run_one = batch.make_populationsim_run_one(
        command_prefix=("uv", "run", "populationsim"), subprocess_run=fake_run
    )
    run_one(str(folder))
    assert captured["cmd"][:3] == ["uv", "run", "populationsim"]
    assert "-w" in captured["cmd"]
    assert str(folder) in captured["cmd"]
    # Default cwd is the folder's parent (matches batch_run_popsim.py).
    assert captured["cwd"] == str(folder.parent)
