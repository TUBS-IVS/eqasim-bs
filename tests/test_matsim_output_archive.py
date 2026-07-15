"""Unit tests for the MATSim simulation_output archive helper.

Covers matsim/output.py::mirror_directory_tree in isolation (pure stdlib,
no synpp / Java / MATSim run needed), so this module runs in the fast gate.
"""

from __future__ import annotations

import os

from matsim.output import mirror_directory_tree


def _build_source_tree(root):
    """Create a small simulation_output-like tree under root."""
    os.makedirs(os.path.join(root, "ITERS", "it.0"))
    with open(os.path.join(root, "output_events.xml.gz"), "w") as fh:
        fh.write("events")
    with open(os.path.join(root, "logfile.log"), "w") as fh:
        fh.write("log")
    with open(os.path.join(root, "ITERS", "it.0", "0.plans.xml.gz"), "w") as fh:
        fh.write("plans")


def test_mirror_recreates_tree_and_hardlinks(tmp_path):
    source = tmp_path / "simulation_output"
    target = tmp_path / "matsim_output"
    _build_source_tree(str(source))

    hardlink_count, copy_count, file_count = mirror_directory_tree(
        str(source), str(target)
    )

    # Every file is present at the target, including nested ITERS files.
    assert (target / "output_events.xml.gz").is_file()
    assert (target / "logfile.log").is_file()
    assert (target / "ITERS" / "it.0" / "0.plans.xml.gz").is_file()

    # tmp_path is a single volume -> all files hardlinked, none copied.
    assert file_count == 3
    assert hardlink_count == 3
    assert copy_count == 0

    # A hardlink shares the inode with its source.
    src_ino = (source / "output_events.xml.gz").stat().st_ino
    dst_ino = (target / "output_events.xml.gz").stat().st_ino
    assert src_ino == dst_ino


def test_mirror_overwrites_existing_target(tmp_path):
    source = tmp_path / "simulation_output"
    target = tmp_path / "matsim_output"
    _build_source_tree(str(source))

    # Pre-existing archive with a stale file that is NOT in the source tree.
    os.makedirs(str(target))
    with open(os.path.join(str(target), "stale_old_run.txt"), "w") as fh:
        fh.write("stale")

    mirror_directory_tree(str(source), str(target))

    # Overwrite is clean: stale content gone, current tree present.
    assert not (target / "stale_old_run.txt").exists()
    assert (target / "output_events.xml.gz").is_file()


def test_mirror_falls_back_to_copy(tmp_path, monkeypatch):
    source = tmp_path / "simulation_output"
    target = tmp_path / "matsim_output"
    _build_source_tree(str(source))

    # Simulate a cross-volume filesystem where hardlinks are impossible.
    def _raise_oserror(src, dst):
        raise OSError("simulated cross-device link (EXDEV)")

    monkeypatch.setattr("matsim.output.os.link", _raise_oserror)

    hardlink_count, copy_count, file_count = mirror_directory_tree(
        str(source), str(target)
    )

    # All files still present, all via copy, none hardlinked.
    assert file_count == 3
    assert hardlink_count == 0
    assert copy_count == 3
    assert (target / "ITERS" / "it.0" / "0.plans.xml.gz").is_file()


def test_archive_writes_provenance_and_returns_counts(tmp_path):
    from matsim.output import archive_simulation_output
    run_path = tmp_path / "matsim.simulation.run__abc.cache"
    output_path = tmp_path / "output"
    os.makedirs(str(output_path))
    _build_source_tree(str(run_path / "simulation_output"))

    hardlink_count, copy_count, file_count = archive_simulation_output(
        str(run_path), str(output_path)
    )

    target = output_path / "matsim_output"
    assert (target / "output_events.xml.gz").is_file()
    assert (target / "ITERS" / "it.0" / "0.plans.xml.gz").is_file()
    assert file_count == 3

    import json as _json
    with open(str(target / "ARCHIVE_INFO.json")) as fh:
        info = _json.load(fh)
    assert info["source_hash_dir"] == str(run_path)
    assert info["file_count"] == 3
    assert info["hardlink_count"] == hardlink_count
    assert info["copy_count"] == copy_count
    assert "created" in info


def test_archive_raises_when_source_missing(tmp_path):
    from matsim.output import archive_simulation_output
    import pytest
    run_path = tmp_path / "matsim.simulation.run__missing.cache"  # no simulation_output/
    output_path = tmp_path / "output"
    os.makedirs(str(output_path))

    with pytest.raises(RuntimeError) as excinfo:
        archive_simulation_output(str(run_path), str(output_path))
    assert "156" in str(excinfo.value)
    # No half-written archive left behind.
    assert not (output_path / "matsim_output" / "ARCHIVE_INFO.json").exists()
