"""CLI: `python -m braunschweig.runcontrol serve|status --config runcontrol.toml`.

serve: start the queue worker (daemon thread) + uvicorn on settings.host:port.
status: one-shot text table of known runs (headless check, also used to
verify the DB from the terminal). Logged startup includes bind address,
targets and DB path for traceability."""
from __future__ import annotations

import argparse
import logging
import threading
from pathlib import Path

from .daemon import QueueWorker
from .db import Database
from .settings import load_settings
from .targets import get_target

logger = logging.getLogger("runcontrol")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="runcontrol", description="eqasim-bs pipeline run manager")
    sub = p.add_subparsers(dest="command", required=True)
    for name in ("serve", "status"):
        sp = sub.add_parser(name)
        sp.add_argument("--config", default="runcontrol.toml", help="path to runcontrol.toml")
    return p


def cmd_status(db: Database) -> None:
    rows = db.list_runs()
    if not rows:
        print("no runs recorded")
        return
    for r in rows:
        print(f"{r['run_id']:<40} {r['status']:<9} label={r['label']:<8} target={r['target']:<8} config={r['config_path']}")


def cmd_serve(settings, db: Database) -> None:
    import uvicorn

    from .app import create_app

    targets = {name: get_target(cfg) for name, cfg in settings.targets.items()}
    worker = QueueWorker(db, targets)
    thread = threading.Thread(target=worker.run_forever, args=(settings.poll_seconds,),
                              daemon=True, name="runcontrol-queue")
    thread.start()
    logger.info("runcontrol serving on http://%s:%d (targets: %s, db: %s)",
                settings.host, settings.port, ", ".join(sorted(targets)), settings.db_path)
    uvicorn.run(create_app(settings, db, worker, targets), host=settings.host, port=settings.port)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    args = build_parser().parse_args()
    settings = load_settings(Path(args.config))
    db = Database(settings.db_path)
    if args.command == "status":
        cmd_status(db)
    else:
        cmd_serve(settings, db)


if __name__ == "__main__":
    main()
