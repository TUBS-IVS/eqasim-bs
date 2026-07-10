# Run-management GUI (`braunschweig.runcontrol`)

A FastAPI + HTMX GUI with a background queue daemon that catalogs, monitors
(synpp stages / MATSim iteration / vitals / live log), composes (curated
config flags) and launches/stops eqasim-bs pipeline runs, locally or on the
run server via SSH + tmux.

- **Issue:** [#119](https://github.com/TUBS-IVS/eqasim-bs/issues/119)
- **Design spec:** `docs/superpowers/specs/2026-07-08-runcontrol-gui-design.md`
- **Implementation plan:** `docs/superpowers/plans/2026-07-08-runcontrol-gui.md`
- **Source:** `braunschweig/runcontrol/`
- **Tests:** `tests/runcontrol/` (69 tests, unit + local E2E; see "Verification
  status" below)

## Starting the GUI

```powershell
conda run -n eqasim python -m braunschweig.runcontrol serve --config runcontrol.toml
```

or, with the environment's Python directly (equivalent, no `conda run` wrapper
overhead):

```powershell
C:/Users/bienzeisler/AppData/Local/miniforge3/envs/eqasim/python.exe -m braunschweig.runcontrol serve
```

This starts one process: a `uvicorn` server bound to `runcontrol.toml`'s
`host:port`, plus a background thread running the `QueueWorker` (the daemon
that reconciles run state and advances the queue). Open
`http://<host>:<port>/` (default `http://127.0.0.1:8099/`).

A headless alternative for a quick DB check without starting the server:

```powershell
conda run -n eqasim python -m braunschweig.runcontrol status --config runcontrol.toml
```

## `runcontrol.toml` reference

Committed example at the repo root (`runcontrol.toml`); copy and adjust
`repo` paths per machine. All keys are top-level except the per-target
`[target.<name>]` tables.

| Key | Default | Meaning |
|---|---|---|
| `db_path` | `runcontrol_data/runs.db` | SQLite file holding runs/queue/events. |
| `host` | `127.0.0.1` | Bind address for the web server. Kept localhost-only by design (see "SSH-tunnel access"). |
| `port` | `8099` | Bind port. |
| `poll_seconds` | `3.0` | Daemon tick interval (reconcile-then-advance loop). |

Per target, in a `[target.<name>]` table (at least one target is required):

| Key | Default | Meaning |
|---|---|---|
| `kind` | *(required)* | `"local"` or `"ssh"`. |
| `repo` | *(required)* | Repository root on that host (working dir for launches). |
| `host` | *(required for `kind = "ssh"`)* | SSH alias (e.g. `felix`), must already work key-auth. |
| `runner` | `scripts/run_synpp.py` (local) / `scripts/run_pipeline.sh` (ssh) | Launch script, relative to `repo`. |
| `data_dir` | `eqasim-data` | Where the catalog looks for `output_*` / `cache_*` directories. |
| `logs_dir` | `logs` | Where run logs, exit markers and manifests are written. |

Loading fails early (`FileNotFoundError` / `ValueError`) on a missing file, an
unknown `kind`, a missing `host` for an ssh target, or zero targets -- a GUI
that silently mis-targets a run (e.g. launching a "100%" config against the
wrong host) is not acceptable for this project.

## Target kinds and launch mechanics

Both kinds implement the same `ExecutionTarget` interface
(`braunschweig/runcontrol/targets/base.py`): `launch`, `is_alive`,
`exit_code`, `stop`, plus filesystem primitives (`read_text`, `exists`,
`listdir`, `write_text`, `manifest_glob`). Nothing else in the app talks to a
process or a filesystem directly.

**`local` (`targets/local.py`)** -- a detached subprocess on this machine:

```
python local_runner.py <runner> <config> <log> <exit_marker>
```

`local_runner.py` is a small, import-free wrapper that runs the configured
runner script, tees its output into the log file, and writes the runner's
exit code into the marker file. Detachment uses
`CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS` on Windows and
`start_new_session` on POSIX, so the run survives a GUI restart. Liveness is a
PID check (`tasklist` / `os.kill(pid, 0)`); `stop()` sends `taskkill /T` (or
`SIGINT` to the process group on POSIX), escalating to a forced kill after
`stop_escalate_seconds` (30s) if the process has not exited.

**`ssh` (`targets/ssh.py`)** -- no new service on the server; the launch is:

```
ssh <alias> "cd <repo> && mkdir -p <logs_dir> && tmux new-session -d -s rc_<run_id> \
    'bash <runner> <config> <log>; echo $? > <exit_marker>'"
```

Liveness is `tmux has-session`; reading the log/exit-marker is `cat` /
`tail -c`; `stop()` sends `tmux send-keys C-c` to the run's own session,
escalating to `tmux kill-session` after 30s. Stopping never kills by process
image (`pkill java` is forbidden project-wide) -- only the run's own
session/process group is touched.

Both targets write a `RunManifest` (`rc_<run_id>.manifest.json`, next to the
log, on the execution host) **before** the process starts. The manifest is
the durable per-run truth: SQLite is a cache/index, the manifest on disk is
what survives a daemon restart or the app being closed (see "Queue
limitation" below).

## Curated flags (`registry.py`)

Decision (spec 2026-07-08): the launch/config-studio UI can only edit a
**curated allow-list** of flags, not arbitrary YAML keys. Anything not in the
registry is edited in the template YAML files directly. Rationale: research
run configs are scientifically load-bearing (sampling rate, random seed, tier
flags, ...); a free-text config editor in a GUI would make silent,
untraceable changes to a run's provenance trivial. Every `Flag` entry in
`braunschweig/runcontrol/registry.py` declares `key`, `group`, `type`
(`bool`/`int`/`float`/`str`/`choice`), `unit`, `description`, and (where
applicable) `choices` or a `min`/`max` range; keys must match the
`config_*.yml` key verbatim.

To add a new curated flag: append a `Flag(...)` entry to `FLAGS` in
`registry.py` under the appropriate group (add a new group name to
`_GROUP_ORDER` if needed) and it appears automatically in the Config Studio
inspector/editor and in `configwriter.compose()`'s validated override path --
no other file needs to change. Uncurated keys already present in a template
are still shown (as a count, e.g. "12 uncurated keys"), just not editable.

## Run catalog (`/catalog`)

`collectors/catalog.py::scan()` merges three origins into one table -- runs with a
`RunManifest` (authoritative), runs known to the SQLite DB without a manifest yet
(queued), and legacy `output_*`/`cache_*` directories that predate runcontrol (fields
"unknown", flagged `no_manifest`, sampling rate a directory-name *hint* only). It is
surfaced on the `/catalog?target=<name>` page (and `GET /api/catalog?target=<name>`
for the raw JSON), reachable from the "Catalog" link in the topbar next to "+ New run".
The page performs the scan (a `manifest_glob()` + a `listdir()` over the target's
`data_dir`) only when explicitly visited or the target switcher is clicked -- there is
no HTMX auto-polling on this page, unlike the home page's hero, because the scan does
real target I/O that should not run in the background unattended.

## Catalog v2 (legacy-run enrichment, issue #119)

Catalog v2 adds an opt-in enrichment layer on top of the V1 scan above,
scoped to legacy `output_*`/`cache_*` directories that predate runcontrol
(`origin == "legacy_dir"`, `kind` `"output"`/`"cache"`) -- runs with a
`RunManifest` already carry their real config and need no reconstruction.

- **Effective config is a reconstruction, always labelled partial.**
  `collectors/enrich.py::enrich_artifact()` builds the "effective config" as
  the UNION of the per-stage `config` dicts recorded in the synpp
  `pipeline.json` at the artifact's cache root; only stages still cached
  contribute. It is never presented as the run's real config file -- the
  details drawer (`_catalog_details.html`) always renders it under the label
  "(from N cached stages -- partial)", and `merge_stage_configs()` records
  which keys disagree across stages as `config_conflict:<key>` flags rather
  than silently picking one value.
- **Timeline is derived, not measured.** `timeline_from_pipeline()` turns
  consecutive `updated` epochs in `pipeline.json` into a per-stage
  `approx_duration_s` (the delta to the previous stage's completion); the
  first stage's duration is always `None` (no predecessor to delta against).
  This is presented as "derived from cache completion timestamps", never as
  a measured runtime.
- **Config-diff flow.** Checking exactly two legacy rows' checkboxes on
  `/catalog` enables "Compare configs", which navigates to
  `/catalog/diff?target=&a=&b=`; `GET /api/catalog/{target}/diff?a=&b=`
  reconstructs both artifacts' effective configs and returns only the keys
  whose values differ (`registry.by_key()` supplies the group, else
  `"other"`). Equal keys are omitted so the diff table stays focused on what
  actually differs between the two runs being compared.
- **Legacy log viewer (`/logs`).** `GET /api/logs?target=` lists log-like
  files in the target's `logs_dir` and `GET /api/logs/{name}/view` streams
  one back as plain text (tail-bounded, `_safe_relname`-guarded). The listing
  is restricted to the documented naming scheme -- `run_*.log` / `rc_*.log`
  (written by `LocalTarget`/`SshTarget`) or `*_stage_runtime.csv` (written by
  the synpp stage-timing collector) -- rather than any `*.log` file or any
  name merely containing `_stage_runtime`, so unrelated logs that happen to
  live in the same directory are not surfaced as if they were pipeline runs.
  The `/logs` page is read-only and view-only: there is no delete action.
- **Stale-cache chip (display only, no delete).** The catalog table flags a
  legacy `cache_*` row `stale?` when its directory mtime is older than
  `stale_age_days` (default 30, configurable in `runcontrol.toml`). This is
  purely an age-based hint always available from the directory listing; it
  is a suggestion to review for manual cleanup, not an automated or
  size-aware judgement. A size-based threshold (`stale_size_gb`, default 5)
  is applied only in the details drawer once a size has actually been
  fetched via the on-demand "size" button (`POST
  /api/catalog/{target}/{name}/size`) -- size is never inferred or
  estimated, and the GUI never deletes anything itself.
- **On-demand, SQLite-cached, mtime-invalidated.** Every enrichment/size/
  diff/log call happens only on an explicit user action (row expand,
  "Enrich all", "Compare configs", opening a log) -- there is no
  auto-polling in catalog v2, consistent with the V1 catalog scan. Computed
  payloads are cached in the `enrichment` SQLite table keyed by
  `"<target>:<name>"`; `Database.get_enrichment()` returns the cached payload
  only when the stored `dir_mtime` still matches the artifact directory's
  current mtime, so a payload is recomputed exactly when the underlying
  directory has actually changed.

## V1 queue limitation

The `QueueWorker` advances the queue only while the `serve` process is
running (`tick()` is driven by the background thread's poll loop). If the
process is stopped, queued runs simply wait -- they are not lost (SQLite
persists the queue), but nothing launches until `serve` runs again.
**Workaround:** for a chain of runs that must execute unattended, either keep
`serve` running (e.g. via the systemd unit below) or launch runs one at a
time and re-invoke `serve` before each subsequent submission.

A related, separate mechanic is the daemon's **blocking-UNKNOWN semantics**:
if an active run's process handle cannot be verified (the process is gone but
no exit marker was found, or an active run has neither a handle nor an
on-host manifest to recover one from), its status becomes `unknown` and it
**blocks the queue** -- no new run is launched on top of a possibly-still-live
process. This is a deliberate conservative bias (see `daemon.py`'s module
docstring): guessing `done`/`failed` for an unverifiable process would risk
running two simulations against the same output directory concurrently. An
`unknown` run is resolved by a human via "stop" (`stop_run`), which marks it
`stopped` and unblocks the queue; it does not attempt to kill anything if the
process cannot be found.

## Honesty rules (no invented values)

Consistent with the project-wide "no invented reference values / no silent
fallbacks" rules:

- **Unknown fields are `"unknown"` / `None`, never guessed.** Legacy
  `output_*`/`cache_*` directories predating runcontrol have no manifest, so
  their `git_commit`, `config_path`, and `status` are the literal string
  `"unknown"`, flagged `no_manifest` in the catalog entry (see
  `collectors/catalog.py`). A `bare`-format log line (the old
  `%(levelname)s:%(name)s:%(message)s` default logging format) carries no
  timestamp at all, so per-stage `duration_s`/`active_since_iso` are `None`
  for it -- an honest information loss, not a bug (see
  `collectors/synpp_progress.py`).
- **ETAs are always `estimated: true`.** The MATSim-iteration ETA
  (`collectors/matsim_progress.py`) is `mean observed iteration duration x
  remaining iterations`; it is a projection, never presented as a
  measurement, and is `None` when zero iterations have been observed yet.
- **`meta_inconsistent` flags a known data issue, it does not fix it.** When a
  legacy run directory's name implies one sampling rate (e.g. `..._25pct...`)
  but its `*_meta.json` records a different `sampling_rate`, the enrichment for
  that artifact (`collectors/enrich.py::enrich_artifact`) adds
  `flags: ["meta_inconsistent"]` (a known server-side issue, see `RUNS.md`)
  rather than silently trusting either source. This check is deferred to the
  lazy per-artifact enrichment (not the bulk `catalog.py::scan()` list) because
  it needs `meta.json`'s contents, which enrichment already reads for other
  reasons -- see #119, where doing this eagerly per legacy directory during
  `scan()` cost one extra ssh round-trip per directory and made switching the
  catalog to the `server` target take multiple seconds.
- **Log-format degradation is surfaced, not hidden.** `StageProgress.log_format`
  is one of `iso` / `console` / `bare` / `unknown`; only the `iso` format (the
  file-log timestamp written by `braunschweig.logging_setup`) carries a full
  date, so only `iso`-parsed stages have a directly comparable `end`
  timestamp. Consumers can see which format a log was in and interpret
  missing fields accordingly, instead of a UI that quietly shows a blank.
- **Vitals collection failures are visible, not hidden.** If `df`/process
  introspection fails for a target (e.g. a Windows local target has no
  `/proc`, or an ssh command errors), the vitals dict carries `source:
  "error:<exc>"` and `None` fields rather than a stale or fabricated number
  (`app.py::_collect_vitals`).

### Adopt running run

The "Adopt" feature monitors an externally-started pipeline run from its
catalog directory into runcontrol as a monitor-only ("external") run without
killing or controlling the external process. This is useful for observing runs
that were launched outside the GUI.

**How adoption works:** A legacy `cache_*` or `output_*` directory can be
adopted from the catalog page by clicking "Adopt". The adoption records the
newest top-level-child activity mtime of the artifact's cache/output dir pair
(see below) and begins polling for changes. The run shows on the home hero
with an `adopted` badge, remains `running` while that activity signal keeps
advancing, and transitions to `ENDED` when it stops advancing for
`adopt_alive_window_s` (default 1800 seconds, configurable in
`runcontrol.toml`). The adoption searches for a fresh `logs/run_*.log` matching
the artifact's mtime (within the window) and links it if found; if no such log
exists, the progress display shows only the cache timeline without live
iteration/stage metadata.

**Liveness is inferred from directory activity, not the directory's own mtime.**
A directory entry's own mtime (as reported when listing its parent) only
changes when its DIRECT children are added or removed -- not while files deep
inside it grow, which is exactly what happens for minutes at a time during a
single synpp stage or MATSim iteration. Watching that entry mtime alone
previously caused a genuinely running 100% run to be falsely marked `ENDED`
(issue #119). Instead the daemon watches
`enrich.newest_activity_mtime(target, name)`: the MAX top-level-child mtime
across both `cache_<name>` (a fresh `*.cache` file and updated `pipeline.json`
per completed synpp stage) and the paired `output_<name>` (fresh per-iteration
files written into the output dir root while MATSim runs) -- two cheap
`listdir` calls, never a recursive scan. Advancing since its previously-stored
value = still running; no advance for `adopt_alive_window_s` on the daemon's
own clock = `ENDED`. This approach is clock-skew-free: both the current and
stored values come from the server's filesystem, and the "stale-check"
interval uses only the daemon's local clock. The exit code of the external
process is not queried and remains unknown; the terminal status is always
`ENDED`, never `DONE` or `FAILED`.

**Queue blocking and monitor-only semantics:** An adopted running run blocks
the daemon queue, preventing new launches on the same target until the adopted
run ends. The "Stop" button marks the run as `ENDED` and stops monitoring -- it
does **not** kill the external process. The external process continues running
unaffected. If the process completes and the directory becomes active again
(e.g. a restart or retry of the same run), the run can be re-adopted from the
catalog and the daemon resumes monitoring.

**Limitation:** Without a matching `logs/run_*.log`, the GUI cannot infer
per-stage timing or iteration counts from a live log stream. The cache timeline
(derived from `pipeline.json` stage completion timestamps) remains the only
progress indicator. This is an honest limitation: the run may have started
before runcontrol was running, or the log may be on a different host or in a
non-standard location, so inferring its path is not reliable.

### Auto-detect active runs

The daemon continuously scans each target's data directory for fresh
directories matching configurable glob patterns and automatically creates
monitored external runs for them, eliminating the need to manually adopt runs
launched outside the GUI. This is particularly useful for background pipeline
runs that start while the GUI is not running.

**How auto-detection works:** Every `autodetect_interval_s` (default 60,
configurable in `runcontrol.toml`), the daemon scans the target's data
directory once using a bounded `newest_files()` query (a single `find` or
`os.walk` at most 4 levels deep, sorted by mtime, limited to 200 files).
Freshness is skew-free: a directory's newest file is compared against the
newest file found anywhere on the target (both from the server's clock),
and if that file is within the `adopt_alive_window_s` (the same window used
for adoption), a matching directory is considered active and auto-created
as an external run. Directory names are matched against `active_run_globs`
(default `["output_*", "cache_*", "popsim_work_*"]`, configurable in
`runcontrol.toml`).

**The `detected` badge:** Auto-detected runs are displayed on the home hero
and run detail pages with a `detected` badge and an honest note: "auto-detected
from filesystem activity; not launched by runcontrol". This distinguishes
them from manually adopted runs (which show an `adopted` badge). Both are
external runs (monitor-only, never killed by the GUI).

**Depth-aware liveness:** Like adoptions, auto-detected runs track liveness
by watching the newest descendant mtime (not the directory entry's own mtime)
across the entire monitored tree, up to 3 levels deep and 50 most-recent
files per scan. This ensures that deep batch writes (e.g. `popsim_work_*/
batch_N/output/populationsim.log`) keep the run marked `RUNNING` until they
stop advancing for `adopt_alive_window_s`, at which point the run transitions
to `ENDED`.

**Configuration:** Tune auto-detection in `runcontrol.toml`:

```toml
active_run_globs = ["output_*", "cache_*", "popsim_work_*"]  # Directory name patterns to detect
autodetect_interval_s = 60                                    # Daemon scan interval (seconds)
adopt_alive_window_s = 1800                                   # Window for freshness; auto-detected runs become ENDED after this silence
```

All three settings are optional and use sensible defaults. Set
`active_run_globs = []` to disable auto-detection entirely.

## SSH-tunnel access

The server binds `host = 127.0.0.1` by design -- there is no built-in
authentication in V1 (see `app.py`'s `require_write()` no-op hook, kept so a
token check or reverse proxy can be added later without touching endpoints).
To reach it from a laptop while it runs on the server, tunnel instead of
opening the port:

```bash
ssh -L 8099:localhost:8099 felix
```

then browse to `http://127.0.0.1:8099/` locally.

## Optional systemd user unit (server deployment)

To keep `serve` running unattended on the server (also sidesteps the V1 queue
limitation above), install an optional systemd user unit:

```ini
# ~/.config/systemd/user/runcontrol.service (server deployment, optional)
[Unit]
Description=eqasim-bs runcontrol GUI
[Service]
WorkingDirectory=%h/eqasim-bs
ExecStart=%h/miniforge3/envs/eqasim/bin/python -m braunschweig.runcontrol serve
Restart=on-failure
[Install]
WantedBy=default.target
```

Enable with `systemctl --user enable --now runcontrol.service`. This is
optional infrastructure, not required to use the GUI interactively.

## Verification status

- **Unit tests:** all 69 tests in `tests/runcontrol/` pass under the `eqasim`
  conda environment (`settings`, `db`, `local_runner`/`LocalTarget`,
  `SshTarget` with an injected fake command runner, `registry`,
  `config_inspect`/`configwriter`, `synpp_progress`/`matsim_progress`/
  `vitals`/`catalog` collectors, `daemon` reconciliation/queue-blocking, the
  FastAPI JSON API and HTML pages, and the CLI).
- **Local E2E lifecycle:** `tests/runcontrol/test_e2e_smoke.py` drives the
  real path submit -> launch -> running -> done through `QueueWorker` +
  `LocalTarget` + `Database` together, with a genuine detached subprocess (no
  mocks) and asserts the on-disk artifacts a human would look for (log,
  manifest, exit marker) exist afterwards.
- **OPEN follow-up: real ssh/tmux server smoke.** The manual verification
  described in the implementation plan (Task 13, Step 3 -- launching a real
  mini run on the `server` ssh target, checking the tmux session, log
  tailing, stop-button interruption, and daemon-restart reconciliation
  against a live remote process) has **not** been executed: the run server
  was busy with another job at the time this feature was built, and per
  explicit instruction this session did not touch it. The `SshTarget` unit
  tests use an injected fake command runner and therefore validate the
  command construction and response parsing, but not a real `ssh`/`tmux`
  round-trip. This should be run once the server is free, before relying on
  the `ssh` target for an unattended production run.
