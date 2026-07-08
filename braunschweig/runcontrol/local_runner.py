"""Detached local run wrapper.

Usage: python -m braunschweig.runcontrol.local_runner <runner> <config> <log> <exit_marker>

Runs `<python> <runner> <config>` (e.g. scripts/run_synpp.py config_x.yml)
with stdout+stderr appended to <log>, then writes the exit code
to <exit_marker>. This is the local equivalent of the server's
`run_pipeline.sh ... ; echo $? > marker` tmux invocation: the marker file is
how runcontrol distinguishes done/failed from crashed-without-trace.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print("usage: local_runner <runner> <config> <log> <exit_marker>", file=sys.stderr)
        return 2
    runner, config, log_path, exit_marker = argv
    Path(log_path).parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a", encoding="utf-8", errors="replace") as log:
        rc = subprocess.run([sys.executable, runner, config], stdout=log, stderr=subprocess.STDOUT).returncode
    Path(exit_marker).write_text(str(rc), encoding="ascii")
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
