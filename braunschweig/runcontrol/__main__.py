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
from .targetstore import load_dynamic_targets

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

    config_target_names = set(settings.targets)
    # Config targets are inserted first: dict insertion order then keeps them ahead of
    # user-added dynamic targets everywhere they are iterated (e.g. the topbar vitals row).
    target_configs = dict(settings.targets)
    dynamic_configs = load_dynamic_targets(settings.targets_store_path)
    added, skipped = 0, 0
    for name, cfg in dynamic_configs.items():
        if name in target_configs:
            # Config-file targets are immutable seeds and always take precedence; a
            # collision here means the store was edited or copied from another config.
            logger.warning("dynamic target '%s' collides with a config-file target; keeping the config one", name)
            skipped += 1
            continue
        target_configs[name] = cfg
        added += 1
    logger.info("dynamic targets: %d loaded, %d added, %d skipped (config collision)",
               len(dynamic_configs), added, skipped)

    targets = {name: get_target(cfg) for name, cfg in target_configs.items()}
    worker = QueueWorker(db, targets)
    worker._settings_window = settings.adopt_alive_window_s
    thread = threading.Thread(target=worker.run_forever, args=(settings.poll_seconds,),
                              daemon=True, name="runcontrol-queue")
    thread.start()
    logger.info("runcontrol serving on http://%s:%d (targets: %s, db: %s)",
                settings.host, settings.port, ", ".join(sorted(targets)), settings.db_path)
    uvicorn.run(create_app(settings, db, worker, targets, config_target_names=config_target_names),
               host=settings.host, port=settings.port)


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
