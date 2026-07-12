"""Tests for the popsim work_dir stale-batch guard.

The PopulationSim work_dir persists across runs outside synpp's stage cache; the
batch runner skips a batch whose completion marker exists. On a config change the
re-assembled inputs no longer match the stale outputs, so they must be purged.
``purge_stale_batches_on_config_change`` enforces this via a signature file.
"""
import os

import pandas as pd

from braunschweig.popsim import stage


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
