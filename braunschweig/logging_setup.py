"""Central logging for eqasim-bs (cleancensus-style).

A single coloured console formatter for the WHOLE run (root logger, so synpp +
braunschweig + matsim all inherit) + a plain UTF-8 file log. Colour auto-disables
on non-TTY / NO_COLOR / redirected output, so the file stays clean. The FILE log
keeps the ``%(asctime)s %(levelname)s %(name)s %(message)s`` ISO format that
``braunschweig.analysis.runtime`` parses for per-stage durations — do not change it.
Ported from cleancensus/logsetup.py (raw ANSI, zero new deps).
"""
from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path

_LEVEL_COLOR = {
    "DEBUG": "\x1b[2m", "INFO": "\x1b[32m", "WARNING": "\x1b[33m",
    "ERROR": "\x1b[31m", "CRITICAL": "\x1b[1;31m",
}
_DIM, _ACCENT, _RESET = "\x1b[2m", "\x1b[36m", "\x1b[0m"
_PREFIXES = ("braunschweig.", "synthesis.", "matsim.", "data.", "synpp.")
_FILE_FMT = "%(asctime)s %(levelname)s %(name)s %(message)s"
_FILE_DATEFMT = "%Y-%m-%dT%H:%M:%S"


def _want_color(color) -> bool:
    if color == "auto" or color is None:
        if os.environ.get("NO_COLOR"):
            return False
        return bool(getattr(sys.stderr, "isatty", lambda: False)())
    return bool(color)


def _short_stage(name: str) -> str:
    for p in _PREFIXES:
        if name.startswith(p):
            return name[len(p):]
    return name


class ColorFormatter(logging.Formatter):
    """Format ``HH:MM:SS │ LEVEL │ stage │ message`` with optional ANSI colour."""

    def __init__(self, color="auto"):
        super().__init__()
        self.color = _want_color(color)

    def format(self, record: logging.LogRecord) -> str:
        ts = self.formatTime(record, "%H:%M:%S")
        stage = _short_stage(record.name)
        level = record.levelname
        msg = record.getMessage()
        if record.exc_info:
            msg = msg + "\n" + self.formatException(record.exc_info)
        if self.color:
            lc = _LEVEL_COLOR.get(level, "")
            return (f"{_DIM}{ts}{_RESET} {_DIM}│{_RESET} {lc}{level:<7}{_RESET} "
                    f"{_DIM}│{_RESET} {_ACCENT}{stage:<16}{_RESET} {_DIM}│{_RESET} {msg}")
        return f"{ts} │ {level:<7} │ {stage:<16} │ {msg}"


def _force_utf8_streams() -> None:
    """Make stdout/stderr emit UTF-8 so box/block glyphs never raise on cp1252."""
    for stream in (sys.stdout, sys.stderr):
        enc = (getattr(stream, "encoding", "") or "").lower().replace("-", "")
        if enc == "utf8":
            continue
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except (AttributeError, ValueError, OSError):
            pass


def setup_logging(level: str = "INFO", color="auto",
                  log_dir: str = "logs", log_file: str | None = None) -> str:
    """Configure the ROOT logger once (idempotent). Returns the file-log path."""
    _force_utf8_streams()
    root = logging.getLogger()
    root.setLevel(getattr(logging, str(level).upper(), logging.INFO))

    if log_file is None:
        log_file = "run_" + time.strftime("%Y%m%dT%H%M%S") + ".log"
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    path = str(Path(log_dir) / log_file)

    has_console = any(
        isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
        and getattr(h, "_eqasim_console", False)
        for h in root.handlers
    )
    has_file = any(getattr(h, "_eqasim_file", False) for h in root.handlers)

    if not has_console:
        ch = logging.StreamHandler(stream=sys.stderr)
        ch.setFormatter(ColorFormatter(color=color))
        ch._eqasim_console = True  # type: ignore[attr-defined]
        root.addHandler(ch)
    else:
        for h in root.handlers:
            if getattr(h, "_eqasim_console", False):
                h.setFormatter(ColorFormatter(color=color))

    if not has_file:
        fh = logging.FileHandler(path, encoding="utf-8")
        fh.setFormatter(logging.Formatter(_FILE_FMT, _FILE_DATEFMT))
        fh._eqasim_file = True  # type: ignore[attr-defined]
        root.addHandler(fh)
        return path
    return next(h.baseFilename for h in root.handlers if getattr(h, "_eqasim_file", False))


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def color_enabled(color="auto") -> bool:
    return _want_color(color)
