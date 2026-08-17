"""Per-batch ``pipeline.h5`` cleanup after verified completion (issue #153).

At full donor pool each completed PopulationSim batch leaves a ~15 GB
``output/pipeline.h5`` checkpoint store behind that no downstream consumer ever
reads (the merge reads only ``final_expanded_household_ids.csv``). 30 batches
would overflow the run server's disk, so the batch runner deletes the store
once the completion marker is verified present. The file must be KEPT for
incomplete batches because PopulationSim needs it to resume an aborted run.

Tests use the injected fake ``subprocess_run`` (no real PopulationSim).
"""

from __future__ import annotations

import logging

from braunschweig.popsim import batch, stage


class _FakeCompleted:
    def __init__(self, returncode=0):
        self.returncode = returncode
        self.stdout = ""
        self.stderr = ""


def _batch_folder(tmp_path, name="batch_000"):
    folder = tmp_path / name
    (folder / "output").mkdir(parents=True)
    return folder


def _write_marker(folder):
    (folder / batch.COMPLETION_MARKER).write_text("x", encoding="utf-8")


def _write_h5(folder, size_bytes=1024):
    h5 = folder / batch.PIPELINE_STORE
    h5.write_bytes(b"\0" * size_bytes)
    return h5


# --------------------------------------------------------------------------- #
# config key + default (stage wiring)
# --------------------------------------------------------------------------- #


def test_cleanup_config_key_name():
    assert stage.KEY_CLEANUP_H5 == "braunschweig.population.popsim.cleanup_batch_pipeline"


def test_configure_registers_cleanup_default_on():
    seen = {}

    class FakeContext:
        def config(self, key, default=None):
            seen[key] = default
            return default

        def stage(self, *a, **k):
            return None

    stage.configure(FakeContext())
    assert seen[stage.KEY_CLEANUP_H5] is True


# --------------------------------------------------------------------------- #
# run_one cleanup behaviour
# --------------------------------------------------------------------------- #


def test_run_one_deletes_pipeline_h5_after_verified_success(tmp_path):
    folder = _batch_folder(tmp_path)

    def fake_run(cmd, **kwargs):
        # PopulationSim writes both the completion marker and the checkpoint store.
        _write_marker(folder)
        _write_h5(folder)
        return _FakeCompleted(0)

    run_one = batch.make_populationsim_run_one(
        subprocess_run=fake_run, cleanup_pipeline_h5=True
    )
    result = run_one(str(folder))
    assert result.status == "succeeded"
    assert not (folder / batch.PIPELINE_STORE).exists()
    # The completion marker and other outputs are untouched.
    assert (folder / batch.COMPLETION_MARKER).is_file()


def test_run_one_keeps_pipeline_h5_when_cleanup_disabled(tmp_path):
    folder = _batch_folder(tmp_path)

    def fake_run(cmd, **kwargs):
        _write_marker(folder)
        _write_h5(folder)
        return _FakeCompleted(0)

    run_one = batch.make_populationsim_run_one(
        subprocess_run=fake_run, cleanup_pipeline_h5=False
    )
    assert run_one(str(folder)).status == "succeeded"
    assert (folder / batch.PIPELINE_STORE).is_file()


def test_run_one_keeps_pipeline_h5_for_incomplete_batch(tmp_path):
    # PopulationSim exited 0 but wrote no completion marker -> "failed".
    # The checkpoint store must survive so a rerun can RESUME the batch.
    folder = _batch_folder(tmp_path)

    def fake_run(cmd, **kwargs):
        _write_h5(folder)
        return _FakeCompleted(0)

    run_one = batch.make_populationsim_run_one(
        subprocess_run=fake_run, cleanup_pipeline_h5=True
    )
    assert run_one(str(folder)).status == "failed"
    assert (folder / batch.PIPELINE_STORE).is_file()


def test_run_one_keeps_pipeline_h5_on_nonzero_exit(tmp_path):
    folder = _batch_folder(tmp_path)
    _write_h5(folder)
    run_one = batch.make_populationsim_run_one(
        subprocess_run=lambda cmd, **kwargs: _FakeCompleted(2),
        cleanup_pipeline_h5=True,
    )
    assert run_one(str(folder)).status == "failed"
    assert (folder / batch.PIPELINE_STORE).is_file()


def test_run_one_deletes_leftover_h5_on_skipped_completed_batch(tmp_path):
    # A batch completed by a PREVIOUS run (with cleanup off / the interim watcher
    # not running) is skipped -- its leftover store is equally dead and removed.
    folder = _batch_folder(tmp_path)
    _write_marker(folder)
    _write_h5(folder)
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return _FakeCompleted(0)

    run_one = batch.make_populationsim_run_one(
        subprocess_run=fake_run, cleanup_pipeline_h5=True
    )
    assert run_one(str(folder)).status == "skipped"
    assert calls == []
    assert not (folder / batch.PIPELINE_STORE).exists()


def test_run_one_success_without_h5_is_not_an_error(tmp_path):
    # Nothing to delete (e.g. the watcher already removed it) -> still succeeds.
    folder = _batch_folder(tmp_path)

    def fake_run(cmd, **kwargs):
        _write_marker(folder)
        return _FakeCompleted(0)

    run_one = batch.make_populationsim_run_one(
        subprocess_run=fake_run, cleanup_pipeline_h5=True
    )
    assert run_one(str(folder)).status == "succeeded"


def test_cleanup_logs_freed_size(tmp_path, caplog):
    # Fallback-transparency style: each deletion is logged with the freed size.
    folder = _batch_folder(tmp_path)

    def fake_run(cmd, **kwargs):
        _write_marker(folder)
        _write_h5(folder, size_bytes=2048)
        return _FakeCompleted(0)

    run_one = batch.make_populationsim_run_one(
        subprocess_run=fake_run, cleanup_pipeline_h5=True
    )
    with caplog.at_level(logging.INFO, logger="braunschweig.popsim.batch"):
        run_one(str(folder))
    messages = " ".join(r.getMessage() for r in caplog.records)
    assert "pipeline.h5" in messages
    assert "2048" in messages or "2.0" in messages  # freed size surfaced
