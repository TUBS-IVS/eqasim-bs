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
