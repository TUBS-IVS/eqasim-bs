"""FastAPI application: JSON API + SSE log tail + HTML pages (Task 11).

Binds to localhost by design (SSH tunnel = auth in V1). All mutating routes
depend on require_write(), a no-op hook kept so a token check / reverse
proxy can be added later without touching the endpoints."""
from __future__ import annotations

import asyncio
import json
import logging
import re
import shlex
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import yaml
from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import registry, targetstore
from .collectors import catalog, config_inspect, enrich, matsim_progress, synpp_progress, vitals
from .configwriter import compose
from .daemon import QueueWorker
from .db import Database
from .models import RunSpec
from .settings import Settings, TargetConfig
from .targets import get_target
from .targets.base import ExecutionTarget

logger = logging.getLogger(__name__)
_PKG = Path(__file__).parent


def require_write(request: Request) -> None:
    """V1: no-op (localhost + ssh tunnel is the auth). Multi-user hook point."""
    return None


def _run_id(label: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return f"{label}_{ts}"


# Single flat path segment: letters, digits, dot, underscore, hyphen only. This whitelist
# blocks BOTH path traversal (no "/", "\\", ".." segments survive) AND shell metacharacters
# (";", "`", "$", "|", "&", spaces, ...), because the name reaches the local filesystem AND
# -- on ssh targets -- remote shell commands (e.g. the /size route's `du`). Legitimate names
# in this project (config_*.yml, output_bs_25pct, cache_bs_100pct_allfeat_synth, run_*.log,
# rc_*.log, *_stage_runtime.csv) all match.
_SAFE_RELNAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")

# The launch label is not just a display string: _run_id(label) embeds it in the run id,
# which becomes the rc_<run_id>.log filename on disk. An unvalidated label with shell or
# JS metacharacters (e.g. "x';alert(1)//") would produce a filename that later executes
# when rendered into the log viewer. Restricting the label to the same safe alphabet as
# target/relative names keeps every derived run id and filename safe by construction.
_LABEL_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def _safe_relname(name: str) -> str:
    """Reject anything that could escape the repo root or reach a shell when used by name alone.

    Guards /api/templates/{name}/inspect, /api/launch's `template`, the catalog v2
    enrich/size routes, and the log viewer. The ".." check is kept explicit (belt and
    braces) even though the whitelist already excludes it.
    """
    if not name or ".." in name or not _SAFE_RELNAME_RE.match(name):
        raise HTTPException(422, "invalid template name")
    return name


def create_app(settings: Settings, db: Database, worker: QueueWorker,
               targets: dict[str, ExecutionTarget],
               config_target_names: set[str] | None = None) -> FastAPI:
    app = FastAPI(title="eqasim-bs runcontrol")
    app.mount("/static", StaticFiles(directory=_PKG / "static"), name="static")
    templates = Jinja2Templates(directory=_PKG / "templates")
    # Names seeded from runcontrol.toml (immutable); everything else in `targets` was
    # added at runtime through POST /api/targets and may be removed through DELETE.
    # Defaulting to set(targets) keeps every existing caller (tests, __main__ before
    # Task 14) working unchanged: with no dynamic targets ever added, "config" is correct.
    config_target_names = set(targets) if config_target_names is None else set(config_target_names)

    def _target(name: str) -> ExecutionTarget:
        if name not in targets:
            raise HTTPException(404, f"unknown target '{name}'")
        return targets[name]

    def _run_or_404(run_id: str) -> dict:
        row = db.get_run(run_id)
        if row is None:
            raise HTTPException(404, f"unknown run '{run_id}'")
        return row

    # ---- JSON API ---------------------------------------------------------
    def _collect_vitals(t: ExecutionTarget) -> dict:
        """Vitals per target; failures stay visible via source, never hidden."""
        try:
            if t.kind == "ssh":
                df_out = t.read_text_command("df -BG . | tail -1")
                return vitals.collect_from_proc(t, df_out).__dict__
            if Path("/proc").exists():                        # local Linux
                import subprocess
                df_out = subprocess.run(["df", "-BG", str(t.repo)], capture_output=True,
                                        text=True).stdout.splitlines()[-1]
                return vitals.collect_from_proc(t, df_out).__dict__
            return vitals.collect_local_windows(str(t.repo)).__dict__   # local Windows
        except Exception as exc:                              # degraded, flagged, not fatal
            return {"cpu_percent": None, "ram_avail_gb": None,
                    "disk_avail_gb": None, "source": f"error:{exc}"}

    @app.get("/api/status")
    def api_status():
        active = next((r for r in db.list_runs() if r["status"] in ("launching", "running")), None)
        if active is not None:
            active = _enrich(active)
        target_vitals = {name: _collect_vitals(t) for name, t in targets.items()}
        return {"active_run": active, "queue": db.queue_ids(), "vitals": target_vitals}

    def _enrich(row: dict) -> dict:
        """Attach live progress parsed from the run's log (best effort, flagged)."""
        out = dict(row)
        t = targets.get(row["target"])
        if t is None or not row.get("log_path"):
            return out
        try:
            log = t.read_text(row["log_path"], tail_bytes=512_000)
        except (FileNotFoundError, RuntimeError):
            out["progress_available"] = False
            return out
        out["progress_available"] = True
        out["stages"] = synpp_progress.parse(log, expected=None).__dict__
        last_it, output_path = None, None
        try:
            cfg = yaml.safe_load(t.read_text(row["config_path"])) or {}
            last_it = cfg.get("config", {}).get("matsim_last_iteration")
            output_path = cfg.get("config", {}).get("output_path")
        except (FileNotFoundError, RuntimeError, ValueError, yaml.YAMLError):
            pass
        out["matsim"] = matsim_progress.parse(log, last_it).__dict__
        out["output_path"] = output_path        # meta tab links/copy path; None -> "unknown"
        return out

    @app.get("/api/runs")
    def api_runs():
        return db.list_runs()

    @app.get("/api/runs/{run_id}")
    def api_run(run_id: str):
        return _enrich(_run_or_404(run_id))

    @app.get("/api/runs/{run_id}/log", response_class=PlainTextResponse)
    def api_run_log(run_id: str, tail_bytes: int = 65536):
        row = _run_or_404(run_id)
        if not row.get("log_path"):
            raise HTTPException(404, "run has no log yet")
        try:
            return targets[row["target"]].read_text(row["log_path"], tail_bytes=tail_bytes)
        except FileNotFoundError:
            raise HTTPException(404, "log file not found on target")

    @app.get("/api/runs/{run_id}/logstream")
    async def api_logstream(run_id: str):
        row = _run_or_404(run_id)
        if not row.get("log_path"):
            raise HTTPException(404, "run has no log yet")

        async def gen():
            sent = 0
            while True:
                try:
                    text = targets[row["target"]].read_text(row["log_path"], tail_bytes=65536)
                except (FileNotFoundError, RuntimeError):
                    text = ""
                if len(text) > sent:
                    chunk = text[sent:]
                    sent = len(text)
                    yield f"data: {json.dumps(chunk)}\n\n"
                current = db.get_run(run_id)
                if current and current["status"] in ("done", "failed", "stopped", "ended", "unknown"):
                    yield "event: end\ndata: {}\n\n"
                    return
                await asyncio.sleep(2.0)

        return StreamingResponse(gen(), media_type="text/event-stream")

    @app.get("/api/templates")
    def api_templates(target: str):
        t = _target(target)
        entries = [e for e in t.listdir(".") if e["name"].startswith("config_") and e["name"].endswith(".yml")]
        return sorted(({"name": e["name"], "mtime": e["mtime"]} for e in entries), key=lambda x: x["name"])

    @app.get("/api/templates/{name}/inspect")
    def api_template_inspect(name: str, target: str):
        name = _safe_relname(name)
        t = _target(target)
        try:
            res = config_inspect.inspect(t.read_text(name))
        except FileNotFoundError:
            raise HTTPException(404, f"template '{name}' not found on target '{target}'")
        return {"run_list": res.run_list, "groups": res.groups, "uncurated_count": res.uncurated_count}

    @app.post("/api/launch", dependencies=[Depends(require_write)])
    def api_launch(target: str = Form(...), template: str = Form(...),
                   label: str = Form(...), overrides: str = Form("{}")):
        if not _LABEL_RE.match(label):
            raise HTTPException(422, "label must be 1-64 chars of letters, digits, dot, underscore, hyphen")
        template = _safe_relname(template)
        t = _target(target)
        try:
            template_yaml = t.read_text(template)
        except FileNotFoundError:
            raise HTTPException(404, f"template '{template}' not found on target '{target}'")
        try:
            fname, text = compose(template_yaml, json.loads(overrides), label=label,
                                  template_name=template, git_commit=t.git_commit(),
                                  now_iso=datetime.now(timezone.utc).isoformat(timespec="seconds"))
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        t.write_text(fname, text)
        spec = RunSpec(run_id=_run_id(label), target=target, label=label, config_path=fname)
        worker.submit(spec)
        return {"run_id": spec.run_id, "config_path": fname}

    @app.post("/api/runs/{run_id}/stop", dependencies=[Depends(require_write)])
    def api_stop(run_id: str):
        _run_or_404(run_id)
        worker.stop_run(run_id)
        return {"ok": True}

    @app.post("/api/queue/reorder", dependencies=[Depends(require_write)])
    def api_reorder(ids: list[str]):
        try:
            db.reorder_queue(ids)
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        return {"ok": True}

    @app.get("/api/catalog")
    def api_catalog(target: str):
        res = catalog.scan(_target(target), db.list_runs())
        return {"runs": res.runs, "n_manifest": res.n_manifest, "n_legacy": res.n_legacy}

    # ---- catalog v2: legacy-artifact enrichment (issue #119) ---------------
    def _artifact_dir_mtime(t: ExecutionTarget, name: str) -> float:
        """mtime of the artifact's own directory entry, used as the enrichment cache key.

        Reusing the directory's own mtime (rather than a wall-clock timestamp) means the
        cached payload is invalidated exactly when the artifact directory itself changes.
        """
        for entry in t.listdir(t.cfg.data_dir):
            if entry["name"] == name:
                return float(entry["mtime"])
        return 0.0

    def _enrich_cached(t: ExecutionTarget, name: str, kind: str) -> dict:
        """Return the enrichment payload, serving from the SQLite cache when the artifact
        directory's mtime has not changed since it was last computed, else recompute and
        persist. Always a dict (never raises) -- enrich_artifact() itself is honest about
        partial/missing sources via the `sources` field."""
        key = f"{t.name}:{name}"
        mtime = _artifact_dir_mtime(t, name)
        cached = db.get_enrichment(key, mtime)
        if cached is not None:
            return cached
        payload = asdict(enrich.enrich_artifact(t, name, kind))
        db.put_enrichment(key, mtime, payload)
        return payload

    def _artifact_kind(name: str) -> str:
        return "output" if name.startswith("output_") else "cache"

    @app.post("/api/catalog/{target}/{name}/enrich", dependencies=[Depends(require_write)])
    def api_enrich(target: str, name: str):
        t = _target(target)
        _safe_relname(name)
        return _enrich_cached(t, name, _artifact_kind(name))

    @app.post("/api/catalog/{target}/{name}/size", dependencies=[Depends(require_write)])
    def api_size(target: str, name: str):
        t = _target(target)
        _safe_relname(name)
        rel = f"{t.cfg.data_dir}/{name}"
        size, source = None, "ok"
        try:
            if t.kind == "ssh":
                out = t.read_text_command(f"du -sb {shlex.quote(rel)} | cut -f1")
                size = int(out.strip().split()[0])
            else:
                import os
                total = 0
                base = t.repo / rel
                for root, _dirs, files in os.walk(base):
                    for f in files:
                        try:
                            total += os.path.getsize(os.path.join(root, f))
                        except OSError:
                            pass
                size = total
        except (OSError, ValueError, RuntimeError, IndexError) as exc:
            source = f"error:{exc}"
        key = f"{t.name}:{name}"
        mtime = _artifact_dir_mtime(t, name)
        payload = db.get_enrichment(key, mtime) or asdict(enrich.enrich_artifact(t, name, _artifact_kind(name)))
        payload["size_bytes"] = size
        db.put_enrichment(key, mtime, payload)
        return {"size_bytes": size, "source": source}

    @app.post("/api/catalog/{target}/{name}/adopt", dependencies=[Depends(require_write)])
    def api_adopt(target: str, name: str):
        """Adopt an externally-started run (issue #119): register the catalog artifact
        directory `name` as a monitor-only RUNNING run. Liveness is inferred later from
        the watched directory's mtime (see daemon.QueueWorker._settle_external); the
        process itself is never launched, queried via a handle, or stopped by the GUI."""
        t = _target(target)
        _safe_relname(name)
        if not _LABEL_RE.match(name):
            raise HTTPException(422, "artifact name is not a valid run label")
        run_id = name                                   # the artifact name is stable + unique per target
        existing = db.get_run(run_id)
        if existing is not None and existing["status"] in ("running", "launching"):
            raise HTTPException(422, f"'{name}' is already adopted and active")
        watch_path = f"{t.cfg.data_dir}/{name}"
        watch_mtime = _artifact_dir_mtime(t, name)
        # Find a fresh run_*.log whose mtime is close to the artifact dir mtime -- best
        # effort only: without a matching log, progress falls back to the cache timeline.
        log_path = None
        try:
            for e in api_logs(target):
                if e["name"].startswith("run_") and e["name"].endswith(".log") \
                        and abs(e["mtime"] - watch_mtime) < settings.adopt_alive_window_s:
                    log_path = f"{t.cfg.logs_dir}/{e['name']}"
                    break
        except Exception:
            log_path = None
        now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
        if existing is not None:
            # A row exists but is in a terminal state (the running/launching case already
            # 422'd above): re-adopt by resetting the row in place, since an INSERT would
            # violate the run_id primary key.
            db.reactivate_external_run(run_id, log_path, watch_path, watch_mtime, now_iso)
            db.add_event(run_id, "status", f"re-adopted (was {existing['status']}); watch "
                                           f"{watch_path}, log {log_path or 'none found'}")
        else:
            db.insert_external_run(run_id, target, name, "unknown", log_path,
                                   watch_path, watch_mtime, now_iso)
            db.add_event(run_id, "status", f"adopted external run (watch {watch_path}, "
                                           f"log {log_path or 'none found'})")
        return {"run_id": run_id, "log_path": log_path, "watch_path": watch_path}

    @app.get("/api/catalog/{target}/diff")
    def api_diff(target: str, a: str, b: str):
        t = _target(target)
        _safe_relname(a)
        _safe_relname(b)
        pa = _enrich_cached(t, a, _artifact_kind(a))
        pb = _enrich_cached(t, b, _artifact_kind(b))
        ca, cb = pa.get("effective_config", {}), pb.get("effective_config", {})
        by = registry.by_key()
        diff = []
        for key in sorted(set(ca) | set(cb)):
            va, vb = ca.get(key), cb.get(key)
            if va != vb:
                grp = by[key].group if key in by else "other"
                diff.append({"key": key, "group": grp, "a": va, "b": vb})
        return {"a": pa, "b": pb, "diff": diff}

    def _is_log_name(n: str) -> bool:
        """Restrict the log viewer to the documented artifact naming scheme.

        A bare `*.log` / `*_stage_runtime` substring match (the earlier version of this
        filter) would also surface unrelated `*.log` files that happen to live in
        `logs_dir` -- e.g. third-party or ad-hoc logs never produced by this pipeline.
        Only `run_*.log` / `rc_*.log` (written by `LocalTarget`/`SshTarget`) and
        `*_stage_runtime.csv` (written by the synpp stage-timing collector) are surfaced.
        """
        return ((n.startswith(("run_", "rc_")) and n.endswith(".log"))
                or n.endswith("_stage_runtime.csv"))

    @app.get("/api/logs")
    def api_logs(target: str):
        t = _target(target)
        out = [e for e in t.listdir(t.cfg.logs_dir) if _is_log_name(e["name"])]
        return sorted(out, key=lambda e: e["mtime"], reverse=True)

    @app.get("/api/logs/{name}/view", response_class=PlainTextResponse)
    def api_log_view(target: str, name: str, tail_bytes: int = 200000):
        t = _target(target)
        _safe_relname(name)
        try:
            return t.read_text(f"{t.cfg.logs_dir}/{name}", tail_bytes=tail_bytes)
        except (FileNotFoundError, RuntimeError):
            raise HTTPException(404, "log not found")

    # ---- dynamic execution targets (Task 14) -------------------------------
    def _target_row(name: str, t: ExecutionTarget) -> dict:
        return {"name": name, "kind": t.kind, "host": t.cfg.host, "repo": t.cfg.repo,
                "origin": "config" if name in config_target_names else "user"}

    @app.get("/api/targets")
    def api_targets():
        return sorted((_target_row(name, t) for name, t in targets.items()), key=lambda r: r["name"])

    @app.post("/api/targets", dependencies=[Depends(require_write)])
    def api_add_target(name: str = Form(...), host: str = Form(...), repo: str = Form(...)):
        try:
            targetstore.validate_new_target(name, host, repo, existing=set(targets),
                                            config_names=config_target_names)
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        cfg = TargetConfig(name=name, kind="ssh", repo=repo, host=host,
                           runner=targetstore.DEFAULT_SSH_RUNNER)
        candidate = get_target(cfg)
        probe_result = candidate.probe()
        if not probe_result["ok"]:
            # Deliberately NOT persisted: a target that fails its connectivity test on
            # the very first probe cannot be scientifically relied upon for a launch,
            # so it must not silently enter the store (see fallback-transparency policy).
            raise HTTPException(422, f"connection test failed: {probe_result['message']}")
        store = targetstore.load_dynamic_targets(settings.targets_store_path)
        store[name] = cfg
        targetstore.save_dynamic_targets(settings.targets_store_path, store)
        targets[name] = candidate
        logger.info("added dynamic ssh target '%s' (host=%s, repo=%s, git=%s)",
                    name, host, repo, probe_result["git_commit"])
        return {"ok": True, "name": name, "git_commit": probe_result["git_commit"]}

    @app.post("/api/targets/{name}/test")
    def api_test_target(name: str):
        t = _target(name)
        if t.kind == "local":
            return {"ok": True, "message": "local filesystem"}
        return t.probe()

    @app.delete("/api/targets/{name}", dependencies=[Depends(require_write)])
    def api_delete_target(name: str):
        _target(name)
        if name in config_target_names:
            raise HTTPException(422, "config-file targets are immutable; edit runcontrol.toml")
        store = targetstore.load_dynamic_targets(settings.targets_store_path)
        store.pop(name, None)
        targetstore.save_dynamic_targets(settings.targets_store_path, store)
        del targets[name]
        logger.info("removed dynamic ssh target '%s'", name)
        return {"ok": True}

    # ---- HTML pages ---------------------------------------------------------
    def _home_ctx(request: Request) -> dict:
        status = api_status()
        history = [r for r in db.list_runs() if r["status"] in ("done", "failed", "stopped", "ended", "unknown")]
        queued = [db.get_run(rid) for rid in db.queue_ids()]
        return {"request": request, "status": status, "history": history[:20],
                "queued": queued, "targets": sorted(targets)}

    @app.get("/", response_class=HTMLResponse)
    def page_home(request: Request):
        return templates.TemplateResponse("home.html", _home_ctx(request))

    @app.get("/fragments/hero", response_class=HTMLResponse)
    def fragment_hero(request: Request):
        return templates.TemplateResponse("_hero.html", _home_ctx(request))

    @app.get("/runs/{run_id}", response_class=HTMLResponse)
    def page_run(request: Request, run_id: str, tab: str = "overview"):
        run = _enrich(_run_or_404(run_id))
        events = db.events(run_id)
        return templates.TemplateResponse("run.html", {"request": request, "run": run,
                                                       "events": events, "tab": tab,
                                                       "targets": sorted(targets)})

    @app.get("/catalog", response_class=HTMLResponse)
    def page_catalog(request: Request, target: str):
        # Loaded on explicit visit only -- no HTMX auto-polling -- because the scan performs
        # real target I/O (manifest_glob + listdir over the artifact directory) that must not
        # run in the background on every page.
        result = api_catalog(target)
        return templates.TemplateResponse("catalog.html", {
            "request": request, "target": target, "targets": sorted(targets),
            "runs": result["runs"], "n_manifest": result["n_manifest"], "n_legacy": result["n_legacy"],
            # Consumed by the "stale?" chip: an age-based hint (dir mtime, always available)
            # on legacy cache directories that have not been touched in a long time.
            "now_epoch": time.time(), "stale_age_days": settings.stale_age_days})

    @app.get("/catalog/{target}/{name}/details", response_class=HTMLResponse)
    def page_catalog_details(request: Request, target: str, name: str):
        # Enrichment is computed/served-from-cache only on this explicit expand action --
        # never eagerly for the whole catalog page (see the no-auto-polling constraint).
        t = _target(target)
        _safe_relname(name)
        payload = _enrich_cached(t, name, _artifact_kind(name))
        return templates.TemplateResponse("_catalog_details.html",
                                          {"request": request, "target": target, "e": payload})

    @app.get("/catalog/diff", response_class=HTMLResponse)
    def page_catalog_diff(request: Request, target: str, a: str, b: str):
        result = api_diff(target, a, b)     # reuse the API logic
        return templates.TemplateResponse("catalog_diff.html",
                                          {"request": request, "target": target,
                                           "a_name": a, "b_name": b, "result": result})

    @app.get("/logs", response_class=HTMLResponse)
    def page_logs(request: Request, target: str):
        # Legacy log viewer (issue #119): lists run_*.log / rc_*.log / *_stage_runtime.csv
        # in the target's logs_dir and lets the user read one on demand -- no auto-polling,
        # the same explicit-action discipline as the rest of catalog v2.
        entries = api_logs(target)
        return templates.TemplateResponse("logs.html", {
            "request": request, "target": target, "targets": sorted(targets), "entries": entries})

    @app.get("/studio", response_class=HTMLResponse)
    def page_studio(request: Request, target: str, template: str | None = None):
        t = _target(target)
        entries = api_templates(target)
        # Default to the first available template so a bare "/studio?target=x" visit shows
        # a populated inspector immediately, instead of an empty "select a template" state.
        if template is None and entries:
            template = entries[0]["name"]
        inspected = None
        if template:
            inspected = api_template_inspect(template, target)
        return templates.TemplateResponse("studio.html", {
            "request": request, "target": target, "targets": sorted(targets),
            "templates_list": entries, "selected": template, "inspected": inspected})

    @app.get("/targets", response_class=HTMLResponse)
    def page_targets(request: Request):
        return templates.TemplateResponse("targets.html", {
            "request": request, "targets": sorted(targets), "target_rows": api_targets()})

    return app
