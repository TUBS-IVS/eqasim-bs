"""Tests for the popsim work_dir stale-batch guard.

The PopulationSim work_dir persists across runs outside synpp's stage cache; the
batch runner skips a batch whose completion marker exists. On a config change the
re-assembled inputs no longer match the stale outputs, so they must be purged.
``purge_stale_batches_on_config_change`` enforces this via a signature file.
"""
import os

from braunschweig.popsim import stage


def _make_batch(work_dir, name):
    out = os.path.join(work_dir, name, "output")
    os.makedirs(out, exist_ok=True)
    # the completion marker the batch runner keys on
    with open(os.path.join(out, "final_expanded_household_ids.csv"), "w", encoding="utf-8") as f:
        f.write("H_ID\n1\n")


def test_first_run_purges_preexisting_batches_and_writes_signature(tmp_path):
    wd = tmp_path / "popsim_work"
    _make_batch(str(wd), "batch_000")
    _make_batch(str(wd), "batch_001")
    purged = stage.purge_stale_batches_on_config_change(str(wd), "sigA")
    assert purged == 2
    assert not (wd / "batch_000").exists()
    assert (wd / stage.WORK_DIR_SIGNATURE_FILE).read_text(encoding="utf-8").strip() == "sigA"


def test_same_signature_keeps_batches(tmp_path):
    wd = tmp_path / "popsim_work"
    _make_batch(str(wd), "batch_000")
    stage.purge_stale_batches_on_config_change(str(wd), "sigA")  # writes sig, purges the 1
    _make_batch(str(wd), "batch_000")  # simulate a (resumed) completed batch under same config
    purged = stage.purge_stale_batches_on_config_change(str(wd), "sigA")
    assert purged == 0
    assert (wd / "batch_000").exists()  # resume: kept


def test_changed_signature_purges_stale_batches(tmp_path):
    wd = tmp_path / "popsim_work"
    _make_batch(str(wd), "batch_000")
    stage.purge_stale_batches_on_config_change(str(wd), "sigA")
    _make_batch(str(wd), "batch_000")
    _make_batch(str(wd), "batch_001")
    purged = stage.purge_stale_batches_on_config_change(str(wd), "sigB")  # config changed
    assert purged == 2
    assert not (wd / "batch_000").exists()
    assert (wd / stage.WORK_DIR_SIGNATURE_FILE).read_text(encoding="utf-8").strip() == "sigB"


def test_empty_work_dir_is_safe(tmp_path):
    wd = tmp_path / "popsim_work"
    os.makedirs(wd, exist_ok=True)
    purged = stage.purge_stale_batches_on_config_change(str(wd), "sigA")
    assert purged == 0
    assert (wd / stage.WORK_DIR_SIGNATURE_FILE).is_file()
