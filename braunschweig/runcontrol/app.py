"""FastAPI application: JSON API + SSE log tail + HTML pages (Task 11).

Binds to localhost by design (SSH tunnel = auth in V1). All mutating routes
depend on require_write(), a no-op hook kept so a token check / reverse
proxy can be added later without touching the endpoints."""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import yaml
from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import targetstore
from .collectors import catalog, config_inspect, matsim_progress, synpp_progress, vitals
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


def _safe_relname(name: str) -> str:
    """Reject anything that could escape the repo root when read/written by name alone.

    Template files live flat in the repo root (see ExecutionTarget), so a legitimate
    template name never contains a path separator or a ".." segment. Rejecting those
    here -- before the name reaches read_text()/write_text() -- closes a path-traversal
    opening in /api/templates/{name}/inspect and /api/launch's `template` field.
    """
    if not name or "/" in name or "\\" in name or ".." in name:
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
                if current and current["status"] in ("done", "failed", "stopped", "unknown"):
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
            targetstore.validate_new_target(name, host, repo, existing=set(targets))
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
        history = [r for r in db.list_runs() if r["status"] in ("done", "failed", "stopped", "unknown")]
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
            "request": request, "target": target, "targets": sorted(targets), "catalog": result})

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
