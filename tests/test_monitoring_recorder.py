"""Background recording of the resource series during a pipeline run (issue #350).

The recorder runs alongside every pipeline invocation, so two properties matter
more than any feature: it must never be able to kill the run it is measuring, and
a run that dies must still leave its measurement behind -- a killed run is exactly
the one whose resource record is wanted.

The series lands in the run's own working directory, next to the outputs a run
manifest already references.
"""
from __future__ import annotations

import json
import logging

import pytest
import yaml

from braunschweig.monitoring import recorder


class _CannedSampler:
    """Sampler stand-in: returns prepared rows, optionally raising on some calls."""

    def __init__(self, rows=None, raise_on=(), stop_after=None, owner=None):
        self.rows = list(rows or [])
        self.raise_on = set(raise_on)
        self.stop_after = stop_after
        self.owner = owner
        self.calls = 0

    def sample(self):
        self.calls += 1
        if self.stop_after is not None and self.calls >= self.stop_after:
            self.owner.request_stop()
        if self.calls in self.raise_on:
            raise OSError("/proc read failed")
        if self.rows:
            return self.rows.pop(0)
        return {"sample_index": self.calls - 1, "unix_time": float(self.calls),
                "timestamp": "2026-08-24T07:00:0%d" % self.calls, "stage": "stage.a",
                "tree_cpu_seconds": float(self.calls), "process_count": 1,
                "cpu_count": 64, "source": "proc"}


def test_every_sample_is_appended_as_one_json_line(tmp_path):
    series = tmp_path / "series.jsonl"
    resource_recorder = recorder.ResourceRecorder(str(series), _CannedSampler())

    resource_recorder.sample_once()
    resource_recorder.sample_once()

    lines = series.read_text(encoding="utf-8").strip().splitlines()
    assert [json.loads(line)["sample_index"] for line in lines] == [0, 1]
    assert resource_recorder.written_sample_count == 2


def test_the_output_directory_is_created_explicitly(tmp_path):
    series = tmp_path / "monitoring" / "nested" / "series.jsonl"

    recorder.ResourceRecorder(str(series), _CannedSampler()).sample_once()

    assert series.exists()


def test_a_failing_sample_is_counted_and_the_recording_continues(tmp_path, caplog):
    """A /proc read racing with process exit must not end the recording."""
    series = tmp_path / "series.jsonl"
    resource_recorder = recorder.ResourceRecorder(str(series),
                                                 _CannedSampler(raise_on=(1,)))

    with caplog.at_level(logging.WARNING):
        assert resource_recorder.sample_once() is None
        resource_recorder.sample_once()

    assert (resource_recorder.failed_sample_count,
            resource_recorder.written_sample_count) == (1, 1)
    assert any("monitoring" in record.message for record in caplog.records)


def test_the_sampling_loop_stops_when_a_stop_is_requested(tmp_path):
    series = tmp_path / "series.jsonl"
    sampler = _CannedSampler(stop_after=3)
    resource_recorder = recorder.ResourceRecorder(str(series), sampler,
                                                  interval_seconds=0.0)
    sampler.owner = resource_recorder

    resource_recorder.run_until_stopped()

    assert resource_recorder.written_sample_count == 3


def test_background_recording_stops_its_thread_and_writes_the_summary(tmp_path):
    series = tmp_path / "series.jsonl"

    with recorder.record_in_background(str(series), _CannedSampler(),
                                       interval_seconds=0.0) as handle:
        pass

    assert not handle.thread.is_alive()
    assert (tmp_path / "series.summary.json").exists()
    assert (tmp_path / "series.summary.md").exists()


def test_a_run_that_raises_still_leaves_its_measurement_behind(tmp_path):
    """The killed-run case: the summary must exist even though the run failed."""
    series = tmp_path / "series.jsonl"

    with pytest.raises(RuntimeError):
        with recorder.record_in_background(str(series), _CannedSampler(),
                                           interval_seconds=0.0):
            raise RuntimeError("stage failed")

    assert (tmp_path / "series.summary.json").exists()


def test_the_summary_states_how_many_samples_failed(tmp_path):
    series = tmp_path / "series.jsonl"
    resource_recorder = recorder.ResourceRecorder(str(series),
                                                 _CannedSampler(raise_on=(1,)))
    resource_recorder.sample_once()
    resource_recorder.sample_once()

    record = resource_recorder.write_summary()

    assert record["failed_sample_count"] == 1
    assert record["sample_count"] == 1


def _write_config(tmp_path, working_directory, **monitoring_keys):
    config = {
        "working_directory": str(working_directory),
        "config": dict(monitoring_keys),
    }
    path = tmp_path / "config.yml"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    return str(path)


def test_a_run_records_into_the_working_directory_by_default(tmp_path):
    working_directory = tmp_path / "work"
    config_path = _write_config(tmp_path, working_directory)

    with recorder.record_from_config(config_path, interval_seconds=0.0) as handle:
        handle.sample_once()

    series_files = list((working_directory / "monitoring").glob("resource_series_*.jsonl"))
    assert len(series_files) == 1
    assert series_files[0].read_text(encoding="utf-8").strip()


def test_monitoring_can_be_switched_off_and_is_then_a_pure_no_op(tmp_path):
    working_directory = tmp_path / "work"
    config_path = _write_config(tmp_path, working_directory, monitoring_enabled=False)

    with recorder.record_from_config(config_path) as handle:
        assert handle is None

    assert not (working_directory / "monitoring").exists()


def test_the_configured_sampling_interval_is_used(tmp_path):
    config_path = _write_config(tmp_path, tmp_path / "work",
                               monitoring_interval_seconds=17.5)

    with recorder.record_from_config(config_path) as handle:
        assert handle.interval_seconds == pytest.approx(17.5)


def test_the_recorded_filesystems_include_the_working_directory_and_the_output_path(tmp_path):
    working_directory = tmp_path / "work"
    output_path = tmp_path / "out"
    output_path.mkdir()
    config_path = _write_config(tmp_path, working_directory,
                                output_path=str(output_path))

    with recorder.record_from_config(config_path, interval_seconds=0.0) as handle:
        row = handle.sample_once()

    paths = [filesystem["path"] for filesystem in row["filesystems"]]
    assert str(working_directory) in paths and str(output_path) in paths


def test_the_populationsim_working_directory_is_watched_for_free_space_too(tmp_path):
    """Its per-batch pipeline.h5 files are ~9 GB each and may sit on another disk."""
    popsim_work = tmp_path / "popsim_work"
    config_path = _write_config(
        tmp_path, tmp_path / "work",
        **{"braunschweig.population.popsim.work_dir": str(popsim_work)})

    with recorder.record_from_config(config_path, interval_seconds=0.0) as handle:
        row = handle.sample_once()

    assert str(popsim_work) in [filesystem["path"] for filesystem in row["filesystems"]]


def test_a_path_configured_twice_is_recorded_once(tmp_path):
    working_directory = tmp_path / "work"
    config_path = _write_config(tmp_path, working_directory,
                                output_path=str(working_directory))

    with recorder.record_from_config(config_path, interval_seconds=0.0) as handle:
        row = handle.sample_once()

    assert [filesystem["path"] for filesystem in row["filesystems"]] == \
        [str(working_directory)]
