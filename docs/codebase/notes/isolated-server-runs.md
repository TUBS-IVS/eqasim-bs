# Isolated server runs: `scripts/isolated_run.py`

A/B arms, smokes and reruns on felix all need the same thing: the pipeline at a chosen
commit of eqasim-bs and eqasim-java-bs, the shared input data, a copy of an earlier run's
synpp cache, its own output, and no write into anybody else's checkout, cache or work dir.
Sessions built that by hand for every run (the per-run scripts under
`docs/runs/artifacts/freight-boundary-links-ab-1pct-2026-10-16/`,
`.../i465-wpe-smoke-1pct-2026-10-23/` and the #468 runs). Those copies stay as the record of
the runs that used them; new runs use `scripts/isolated_run.py`, which does the same steps,
refuses the known mistakes, and writes down what it did.

## The run directory

| Path | What it is |
|---|---|
| `RUN_DIR/eqasim-bs` | detached worktree of the shared `~/eqasim-bs` at `--bs` |
| `RUN_DIR/eqasim-java-bs` | detached worktree of the shared `~/eqasim-java-bs` at `--java` |
| `RUN_DIR/cache` | synpp `working_directory`: a physical copy of an earlier run's cache |
| `RUN_DIR/output` | `output_path` |
| `RUN_DIR/logs` | explain report, input check, run logs |
| `RUN_DIR/run.yml` | the run's overlay, composed with `configs/base_bs.yml` at launch |
| `RUN_DIR/run.json` | provenance: refs and commits, bundles, cache source, overlay source, gate, launches |

The directory name becomes part of a git ref and of log lines, so it is restricted to
letters, digits, `.`, `_` and `-`.

## A run, step by step

Run the tool with the `eqasim` env's interpreter (`gate` runs the run's own
`scripts/explain_stale_stages.py` and `scripts/verify_braunschweig_inputs.py` with it):

```
python isolated_run.py setup ~/i999_smoke --bs origin/main --java origin/main \
    --cache-from ~/wt-i465-wpe/cache_wpe --dry-run          # read-only plan first
python isolated_run.py setup ~/i999_smoke --bs origin/main --java origin/main \
    --cache-from ~/wt-i465-wpe/cache_wpe
python isolated_run.py overlay ~/i999_smoke --from ~/wt-i465-wpe/i465_wpe.yml
python isolated_run.py gate ~/i999_smoke --write
python isolated_run.py launch ~/i999_smoke
python isolated_run.py status ~/i999_smoke
```

Unpushed work travels as a git bundle (`git bundle create x.bundle origin/main..<branch>`,
then `scp`): `setup --bs <branch> --bs-bundle x.bundle` verifies it and fetches it into
`refs/isolated-runs/<run>/eqasim-bs`, a ref no branch uses, so nothing has to be pushed and
nothing else moves. Exit codes: 0 done, 1 refused or failed (the message says what to do),
2 the gate refused a PopulationSim recompute.

## Rules, and why each exists

**The shared checkouts are only fetched.** `~/eqasim-bs` and `~/eqasim-java-bs` carry other
sessions' branches and uncommitted work. `setup` runs `git fetch`, `git bundle verify`, a fetch
into the run's own ref namespace and `git worktree add --detach`; none of these touches a
branch or a working tree. It prints each shared checkout's branch, HEAD and dirty-entry count
before and after (`git --no-optional-locks status`, so not even the index is refreshed) and
fails when they differ.

**Both repositories as detached worktrees, side by side.** The base value
`eqasim_source_path: ../eqasim-java-bs` then resolves to the run's own Java tree without a
config change, so no stage hash moves; a different Java tree devalidates only
`matsim.runtime.eqasim` and what depends on it.

**A symlink farm for the gitignored inputs, not a whole-directory link.** The farm links only
what the worktree lacks and keeps every file the commit tracks under `eqasim-data/`. A link to
the whole shared `eqasim-data` would replace the run commit's reference tables with the shared
checkout's copies, and that checkout lags `main` at times; run state of other runs (`cache_*`,
`output*`, `popsim_work*`, `analysis`, `matsim_archive`, `logs`, `*.yml`, `*.log`, `*.pid`) is
never linked. `scripts/setup_server_worktree.sh` (sparse checkout plus one whole-directory
link) remains the tool for a long-lived server worktree that follows `main`.

**The cache is copied physically, into `cache.partial` first.** `cp -a`, never `cp -al`: synpp
rewrites a re-executed stage in place, and through a hard link that write lands in the source
run's cache. The i409 smoke demonstrated the mechanism: a stage with an unchanged hash was
re-executed and its artifact rewritten through the hard link; only because that source was
itself a working copy did no production cache change
([run manifest](../../runs/i409-passive-joint-surrogate-smoke-03101-2026-09-17.yml), part 3).
The copy goes into `RUN_DIR/cache.partial`, is compared with the source (files, links, bytes)
and only then renamed, so an interrupted copy never looks complete. `setup` refuses an existing
target, too little free space (source size plus `--reserve-gib`, default 20, for the run's own
outputs), and a source written to within `--active-source-minutes` (default 15): a copy taken
while a run writes is no consistent cache.

**run.yml paths are relative to `RUN_DIR/eqasim-bs`.** `overlay` sets `working_directory` and
`analysis_working_directory` to `../cache` and `output_path` to `../output`. `output_path`
enters the synthesis stage hashes (`synthesis.population.spatial.primary.candidates` declares
it in `configure`, and synpp passes the option on to every stage downstream, see
[synpp-config-propagation](synpp-config-propagation.md)), and
`analysis_working_directory` the hash of `braunschweig.analysis.analysis_suite`. With the same
strings in every isolated run, a cache copied from one isolated run into the next keeps those
stages cached. The first run from a production cache, whose strings differ, recomputes them
once; the gate lists them.

**The popsim `work_dir` string and `output_prefix` stay as the source overlay has them.** The
work dir string enters the `braunschweig.popsim.stage` hash; changing it means hours of
PopulationSim. `output_prefix` is part of the configuration, and so of the hash, of
`matsim.simulation.prepare` and `matsim.simulation.run` (read from the stage registry of
`configs/overlays/test_1pct.yml` on 2026-10-23), so a new prefix reruns the MATSim preparation
and simulation; `--prefix` changes it on purpose. Outputs cannot collide anyway: they land in
`RUN_DIR/output`.

**The shared store is off.** `cache_share_enabled` and `cache_share_export` are false: no stage
reads them, they only steer `scripts/run_synpp.py`'s prime and export of
`eqasim-data/cache_shared`, so the run neither reads from nor writes into it.

**The gate is written per run, from the run's own commit.** `gate` runs the explain report in
`RUN_DIR/eqasim-bs` and keeps it in `RUN_DIR/logs`. The `STALE` stages (stale for their own
reasons, run targets included) become `config.synpp_expected_stale_stages`, which
`scripts/run_synpp.py` (`explain_before_run`) enforces before any stage runs. The parser
refuses a report whose summary counts do not match the lines it read, and `--write` refuses an
empty list, because an empty list means "no gate" to the launcher. An old gate list of the
source overlay is always dropped. After a failed launch, gate again before relaunching: the
stages the failed launch completed change their descendants' status.

**PopulationSim recomputes only on request, and never outside the run.** The gate exits 2 when
`braunschweig.popsim.stage` is `STALE` or `RECOMPUTE`, unless `--allow-popsim`. Even then it
refuses when the popsim work dir resolves outside `RUN_DIR`: a recomputing popsim purges
stale `batch_*` folders in its work dir (`braunschweig.popsim.stage.batch_cache`,
`purge_stale_batches_on_config_change`), which would delete another run's batches. `STALE-IF`
(early cutoff decides) is a warning.

**Inputs are checked before launch.** `gate` runs `scripts/verify_braunschweig_inputs.py` in
the worktree, with `--matsim` when MATSim stages are in the stage graph, and refuses missing
required inputs (`--skip-input-check` to override). Local-only inputs added on `main` do not
reach felix by themselves: the first launch of the #444 A/B died after about 40 minutes on a
missing `zensus2022_employed_by_age_kreis.csv`
([run manifest](../../runs/freight-boundary-links-ab-1pct-2026-10-16.yml)).

**One launch at a time, and only a gated run.yml.** `launch` refuses while any process runs
`run_synpp.py` on this run.yml (two launchers would write one cache), and refuses a run.yml
without a gate list or changed since `gate --write` (checksum in run.json), unless
`--force-no-gate`. It starts `setsid nohup bash -c ...` with the conda env, `JAVA_HOME`
(default `~/tools/jdk-25.0.3+9`) and `PYTHONUTF8=1`, and logs start and exit lines with both
commits to `RUN_DIR/logs/run_<timestamp>.log`. It then watches that log for up to
`--wait-seconds` (default 120) for the launcher's `[explain]` verdict and exits 1 when the gate
aborted, the run ended, or no verdict came: a launch is only done once its gate has passed and
PopulationSim is known not to recompute.

**`status` reads the process table and the log, not the log's age.** It reports running
processes (by run.yml path), the explain verdict, the last `Executing stage`, the traceback
count and the exit line. A silent phase can be long; judge a stall by CPU, not by how old the
log is.

**Everything is recorded.** `run.json` holds refs, commits, bundle checksums, the cache source
and its size, the shared checkouts before and after, the overlay source and its checksum, the
gate list and logs, and every launch with its PID, log and commits. Each entry carries the git
blob id of the tool file that wrote it (`git log --all --find-object=<id>` finds the version).
`run.json` and `run.yml` are written and read back, so a write that silently did not happen
fails on the spot. A launched run.yml is never replaced: a different overlay needs a new run
directory.

## Getting the tool onto the server

The tool is one file (standard library plus PyYAML). Take it from `origin/main` and check that
the copy is complete before using it:

```
git -C ~/eqasim-bs fetch -q origin
git -C ~/eqasim-bs show origin/main:scripts/isolated_run.py > ~/isolated_run.py
test "$(git -C ~/eqasim-bs hash-object ~/isolated_run.py)" = \
     "$(git -C ~/eqasim-bs rev-parse origin/main:scripts/isolated_run.py)" && echo complete
```

Once a run directory exists, `RUN_DIR/eqasim-bs/scripts/isolated_run.py` is the run commit's
own copy, if that commit has one.

## Cleaning up (by hand)

The tool never deletes. Remove the worktrees through git, which unlinks the farm's links
without following them into the shared data, then the rest:

```
git -C ~/eqasim-bs worktree remove --force RUN_DIR/eqasim-bs
git -C ~/eqasim-java-bs worktree remove --force RUN_DIR/eqasim-java-bs
git -C ~/eqasim-bs update-ref -d refs/isolated-runs/<run>/eqasim-bs    # only after a bundle setup
rm -rf RUN_DIR/cache RUN_DIR/output
```

Never point `rm -rf` at a path below `RUN_DIR/eqasim-bs/eqasim-data` with a trailing slash: a
farm entry is a link into the shared data, and `rm -rf link/` deletes what it points to.
