# ADR-0193 · 2026-11-12 · A PopulationSim batch output is reused only for the input files it was built from (repository audit P3-F2)

- **Status:** active
- **Context:** The PopulationSim work dir (`braunschweig.population.popsim.work_dir`) lives
  outside the synpp stage cache and is shared between runs (ADR-0037). The batch runner
  (`braunschweig.popsim.batch.make_populationsim_run_one`) skips a folder whose completion marker
  exists. Whether the folders of an earlier run may be reused was decided once per work dir, by a
  signature that lists the batch inputs by hand
  (`braunschweig.popsim.stage.batch_cache.compute_batch_config_signature`). That list was extended
  twice after an input had been found missing (the seed content and the per-Kreis targets; the
  `census_source` composition of a Kreis control: see the docstring and the comments of that
  function). The repository audit of 2026-11-11 found a third gap (finding P3-F2): the per-cell
  control targets, that is the `cells` frame with the content of the cell file, the
  employment-grid and ownership-grid columns and the Kreis filter, are not in the list. A code
  change to the functions that build the batch inputs does not move the signature either: it
  re-executes `braunschweig.popsim.stage`, which then skips every completed batch. In both cases
  the output of an old batch is merged into the population without a signal. No run is known in
  which this happened; the exposure was not measured.

## Decision

1. The reuse decision is taken per batch, from the files the batch reads.
   `braunschweig.popsim.mid.batch_folders.run_popsim_mid` writes the input files of every batch
   folder on every run. After all folders of the run are assembled,
   `braunschweig.popsim.batch.reconcile_batch_outputs` compares a sha256 fingerprint of those
   files with the fingerprint recorded in the folder (`batch.INPUT_FINGERPRINT_FILE`). When they
   differ, the folder's whole output directory is discarded (the completion marker and the
   checkpoint store of an unfinished batch), the new fingerprint is recorded, and the runner
   executes the batch.
2. Output without a recorded fingerprint is not trusted and is discarded. This covers every work
   dir written before this change.
3. Batch folders in the work dir that are not part of the run are removed. A folder of an
   earlier, larger partition is not merged, but
   `braunschweig.popsim.mid.batch_folders.summarize_integerizer_feasibility` would still count
   its log.
4. `logging.yaml` is not part of the fingerprint: it configures PopulationSim's logging only.
5. The work-dir signature stays as it is. It purges the whole work dir when one of the inputs it
   lists changes; it is no longer what protects a single batch.
6. One log line per run states how many batch outputs were kept, how many were discarded because
   the inputs changed, how many because no fingerprint was recorded, how many folders were new
   and how many were removed. It is a warning when anything was discarded or removed.

## Rejected

- **Add the `cells` frame to the hand-written signature.** About ten lines, and enough for the
  finding as written. Rejected because it repeats the pattern that has now failed three times (an
  input reaches the batch folders and the list is not updated) and because it cannot see a code
  change to the batch-input builders.

## Rationale

Apart from the PopulationSim installation itself, the files in a batch folder are everything a
batch reads. A fingerprint over them cannot miss an input that reaches a batch, whoever adds it.
It also tells the batches apart: a change to the targets of some cells re-runs the batches that
hold those cells and no others.

## Consequences

- The first run after this change in an existing work dir executes every PopulationSim batch once
  (decision 2). `braunschweig.popsim.stage` re-executes once as well, because its token hashes
  the two edited helper modules. The duration was not measured for this change.
- A change of the PopulationSim installation (the package version in the `popsimprep`
  environment) is still not detected: neither guard reads it.
- Two runs that use one work dir with different inputs discard each other's batch outputs. That
  costs compute, not correctness.

## Evidence

- Code and tests on the implementing branch: `tests/test_popsim_work_dir_guard.py` drives
  `run_popsim_mid` with the real batch runner and a stand-in for the PopulationSim process, on
  the unstratified and the RegioStaR-stratified branch.
- The shared work dir and its first guard: ADR-0037, feature record
  `docs/registry/features/tier_ab_caching.yml`.
