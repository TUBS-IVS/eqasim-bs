"""matsim.simulation.run materialises the symlinks the Java run leaves in simulation_output (#465 WP-E smoke).

The eqasim-java-bs SimWrapperHouseholdsInputLinker links ``<prefix>households.xml.gz`` to MATSim's
``output_households.xml.gz`` inside the output directory. The synpp fork hashes every executed stage's cache
directory and refuses symlinks (ADR-0149, ``braunschweig.synpp_fork.output_hash``), so the first full run on
main failed AFTER the whole simulation. The stage now replaces such links by copies of their targets.
"""
import os

import pytest

from braunschweig.synpp_fork import output_hash
from matsim.simulation import run


def _symlink_or_skip(target, link):
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError) as error:  # Windows without the symlink privilege
        pytest.skip(f"cannot create a symlink here: {error}")


def test_relative_symlink_is_replaced_by_a_copy_of_its_target(tmp_path):
    out = tmp_path / "simulation_output"
    out.mkdir()
    (out / "output_households.xml.gz").write_bytes(b"households")
    _symlink_or_skip("output_households.xml.gz", out / "p_households.xml.gz")

    assert run.materialise_output_symlinks(str(out)) == 1

    link = out / "p_households.xml.gz"
    assert not link.is_symlink()
    assert link.read_bytes() == b"households"
    assert (out / "output_households.xml.gz").read_bytes() == b"households"


def test_cache_directory_hashes_after_materialising(tmp_path):
    cache = tmp_path / "stage.cache"
    out = cache / "simulation_output"
    out.mkdir(parents=True)
    (tmp_path / "stage.p").write_bytes(b"pickle")
    (out / "output_households.xml.gz").write_bytes(b"households")
    _symlink_or_skip("output_households.xml.gz", out / "p_households.xml.gz")
    with pytest.raises(OSError, match="symlink"):
        output_hash.hash_stage_output(str(tmp_path), "stage", ())

    run.materialise_output_symlinks(str(out))

    assert output_hash.hash_stage_output(str(tmp_path), "stage", ())


def test_symlink_pointing_outside_the_directory_raises(tmp_path):
    outside = tmp_path / "elsewhere.xml.gz"
    outside.write_bytes(b"x")
    out = tmp_path / "simulation_output"
    out.mkdir()
    _symlink_or_skip(str(outside), out / "p_households.xml.gz")

    with pytest.raises(ValueError, match="outside"):
        run.materialise_output_symlinks(str(out))


def test_directory_without_symlinks_is_left_alone(tmp_path):
    out = tmp_path / "simulation_output"
    (out / "ITERS").mkdir(parents=True)
    (out / "output_events.xml.gz").write_bytes(b"e")

    assert run.materialise_output_symlinks(str(out)) == 0
    assert (out / "output_events.xml.gz").read_bytes() == b"e"
