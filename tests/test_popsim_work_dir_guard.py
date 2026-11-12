"""Tests for the popsim work_dir stale-batch guard.

The PopulationSim work_dir persists across runs outside synpp's stage cache; the
batch runner skips a batch whose completion marker exists. On a config change the
re-assembled inputs no longer match the stale outputs, so they must be purged.
``purge_stale_batches_on_config_change`` enforces this coarsely via a signature file;
the per-batch decision (``braunschweig.popsim.batch.reconcile_batch_outputs``) is
driven through ``mid.run_popsim_mid`` and the real batch runner at the end of this file.
"""
import os
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from braunschweig.popsim import batch, folders, mid, stage


def _sig(**overrides):
    """Build a batch-config signature with sensible defaults, overriding named inputs.

    Covers the audit gap: the signature must reflect the CONTENT of the seed frames and
    the per-Kreis target table, not just the config-knob names.
    """
    base = dict(
        controls_df=pd.DataFrame({"target": ["total_households"], "importance": [1000]}),
        settings_text="geographies: [ZENSUS100m]\n",
        max_cells=1500,
        stratify_regiostar=False,
        source_name="mid",
        employment_grid_on=False,
        kreis_controls_map={"economic_status_very_low_KREIS": ("economic_status_very_low_KREIS",)},
        seed_day_filter=(1, 2, 3),
        seed_households=pd.DataFrame({"H_ID": [1, 2], "H_GEW": [1.0, 2.0]}),
        seed_persons=pd.DataFrame({"H_ID": [1, 2], "P_ID": [1, 2], "trip_class": [0, 3]}),
        kreis_table=pd.DataFrame({"ARS_kreis": ["03101"], "economic_status_very_low_KREIS": [100]}),
        active_entries=None,
        status_prior_n=0.0,
    )
    base.update(overrides)
    return stage.compute_batch_config_signature(**base)


def test_signature_is_deterministic():
    assert _sig() == _sig()


def test_signature_changes_when_kreis_target_values_change():
    # Editing a committed target2026_* CSV changes the per-Kreis count values; the
    # signature MUST change so stale batches are purged (audit finding, 2026-07-09).
    a = _sig(kreis_table=pd.DataFrame({"ARS_kreis": ["03101"], "economic_status_very_low_KREIS": [100]}))
    b = _sig(kreis_table=pd.DataFrame({"ARS_kreis": ["03101"], "economic_status_very_low_KREIS": [200]}))
    assert a != b


def test_signature_changes_when_kreis_controls_census_source_composition_changes():
    # Same control NAME (same dict key), but the underlying census_source column
    # composition changed (e.g. the catalog now feeds the control from a different
    # set of source columns). The old key-only hash was blind to this; a persistent
    # work_dir would silently reuse batches built against the OLD composition.
    a = _sig(kreis_controls_map={"economic_status_very_low_KREIS": ("economic_status_very_low_KREIS",)})
    b = _sig(kreis_controls_map={
        "economic_status_very_low_KREIS": ("economic_status_very_low_KREIS", "economic_status_low_KREIS"),
    })
    assert a != b


def test_signature_changes_when_seed_content_changes():
    # A seed toggle (weekend_plan_match / complete_members / ebike column) flows into the
    # seed VALUES; changing them MUST change the signature even at the same control names.
    a = _sig(seed_persons=pd.DataFrame({"H_ID": [1, 2], "P_ID": [1, 2], "trip_class": [0, 3]}))
    b = _sig(seed_persons=pd.DataFrame({"H_ID": [1, 2], "P_ID": [1, 2], "trip_class": [1, 2]}))
    assert a != b


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


# --------------------------------------------------------------------------- #
# Per-batch reuse decision: a completed batch is reused only for the inputs it was
# built from (audit finding P3-F2). The work-dir signature above does not see the
# per-cell control targets, so these tests change exactly those.
# --------------------------------------------------------------------------- #

_CELLS_100M = (
    "CRS3035RES100mN2689000E4337000",
    "CRS3035RES100mN2689100E4337000",
    "CRS3035RES100mN2690000E4341000",
    "CRS3035RES100mN2691000E4342000",
)
_PARENTS_1KM = (
    "CRS3035RES1000mN2689000E4337000",
    "CRS3035RES1000mN2689000E4337000",
    "CRS3035RES1000mN2690000E4341000",
    "CRS3035RES1000mN2691000E4342000",
)
# With max_cells=2 the first 1 km parent (two cells) fills batch_000 and the other two
# parents share batch_001, so the third 100 m cell belongs to batch_001 only.
_CELL_OF_BATCH_001 = _CELLS_100M[2]


def _cells(target_of_batch_001_cell=3.0):
    cells = folders.build_geo_crosswalk(
        pd.DataFrame({"GITTER_ID_100m": _CELLS_100M, "GITTER_ID_1km": _PARENTS_1KM})
    )
    cells["POP"] = [1.0, 2.0, target_of_batch_001_cell, 4.0]
    return cells


class _FakePopulationSim:
    """Stand-in for the PopulationSim process started by the real batch runner.

    Notes each folder it is started for and whether that folder's checkpoint store
    existed at that moment, then writes the completion marker from the 100 m control
    targets it finds in the folder, so the merged table shows which inputs produced it.
    """

    def __init__(self):
        self.started = []
        self.store_at_start = {}

    def __call__(self, command, **kwargs):
        folder = Path(command[command.index("-w") + 1])
        self.started.append(folder.name)
        self.store_at_start[folder.name] = (folder / batch.PIPELINE_STORE).exists()
        targets = pd.read_csv(folder / "data" / "control_totals_ZENSUS100m.csv")
        targets.assign(H_ID=1).to_csv(folder / batch.COMPLETION_MARKER, index=False)
        return SimpleNamespace(returncode=0, stdout="", stderr="")


def _run(work_dir, popsim, cells, *, logging_yaml="version: 1\n"):
    controls_df = pd.DataFrame(
        {"target": ["POP_ZENSUS100m_target"], "geography": ["ZENSUS100m"],
         "seed_table": ["persons"], "importance": [1000],
         "control_field": ["POP_ZENSUS100m"], "expression": ["(persons.P_GEW > 0)"]}
    )
    seed_hh = pd.DataFrame({"H_ID": [1], "H_GEW": [2.0], "STAAT": [1]})
    seed_p = pd.DataFrame({"H_ID": [1], "P_ID": [1], "STAAT": [1]})
    run_one = batch.make_populationsim_run_one(
        command_prefix=("populationsim",), subprocess_run=popsim, cleanup_pipeline_h5=False,
    )
    return mid.run_popsim_mid(
        cells, ["POP"], controls_df, seed_hh, seed_p,
        work_dir=work_dir, settings_yaml="x: 1\n", logging_yaml=logging_yaml,
        max_cells=2, run_one=run_one, num_workers=1,
    )


def test_batch_starts_again_exactly_when_its_inputs_change(tmp_path):
    _run(tmp_path, _FakePopulationSim(), _cells())

    # Same inputs; only PopulationSim's own logging setup differs: every batch is reused.
    rerun = _FakePopulationSim()
    _run(tmp_path, rerun, _cells(), logging_yaml="version: 1\nroot: {level: DEBUG}\n")
    assert rerun.started == []

    # Regression (P3-F2): one cell's control target changed. Before the fix no batch
    # started again and the merged table carried the old target 3.
    rerun = _FakePopulationSim()
    report = _run(tmp_path, rerun, _cells(target_of_batch_001_cell=7.0))
    assert rerun.started == ["batch_001"]
    merged_targets = report.combined.set_index("ZENSUS100m")["POP_ZENSUS100m"]
    assert merged_targets[_CELL_OF_BATCH_001] == 7
    assert merged_targets[_CELLS_100M[0]] == 1  # the reused batch_000 is still merged


def test_completed_batch_without_input_record_starts_again(tmp_path):
    # A work dir written before the per-batch record existed (or one whose record was
    # lost) cannot prove which inputs its output was built from.
    _run(tmp_path, _FakePopulationSim(), _cells())
    (tmp_path / "batch_000" / batch.INPUT_FINGERPRINT_FILE).unlink()

    rerun = _FakePopulationSim()
    _run(tmp_path, rerun, _cells())
    assert rerun.started == ["batch_000"]


def test_changed_inputs_discard_the_checkpoint_store_of_an_unfinished_batch(tmp_path):
    _run(tmp_path, _FakePopulationSim(), _cells())
    # Both batches were interrupted: no completion marker, a checkpoint store left behind.
    for name in ("batch_000", "batch_001"):
        (tmp_path / name / batch.COMPLETION_MARKER).unlink()
        (tmp_path / name / batch.PIPELINE_STORE).write_bytes(b"checkpoint")

    rerun = _FakePopulationSim()
    _run(tmp_path, rerun, _cells(target_of_batch_001_cell=7.0))
    # PopulationSim resumes from the store, so batch_001 must not see the one built
    # for its old targets; batch_000's inputs are unchanged, so its resume is kept.
    assert rerun.store_at_start == {"batch_000": True, "batch_001": False}


def test_batch_folder_of_an_earlier_larger_partition_is_removed(tmp_path):
    # Not merged, but summarize_integerizer_feasibility would still read its log.
    leftover = tmp_path / "batch_002"
    (leftover / "output").mkdir(parents=True)
    (leftover / "output" / "populationsim.log").write_text("OLD RUN", encoding="utf-8")

    _run(tmp_path, _FakePopulationSim(), _cells())
    assert not leftover.exists()
