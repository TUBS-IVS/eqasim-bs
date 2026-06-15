"""Log-friendly progress reporting for eqasim-bs (ported from cleancensus/progress.py).

Fancy coloured in-place bar on a TTY; plain greppable lines when redirected (so file
logs stay clean). progress_iter wraps a sequential iterable; progress_parallel (Task 3)
wraps concurrent.futures for the batch runner. Zero new deps (raw ANSI).
"""
from __future__ import annotations

import os
import sys
import time
from typing import Iterable, Iterator, TypeVar

T = TypeVar("T")

_PARTIALS = "▏▎▍▌▋▊▉"
_SPIN = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
_CYAN, _DIM, _BOLD, _RESET = "\x1b[36m", "\x1b[2m", "\x1b[1m", "\x1b[0m"


def _stdout_is_tty() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    return bool(getattr(sys.stdout, "isatty", lambda: False)())


def _bar(frac: float, width: int = 22) -> str:
    frac = max(0.0, min(1.0, frac))
    eighths = round(frac * width * 8)
    full, rem = divmod(eighths, 8)
    part = _PARTIALS[rem - 1] if rem else ""
    empty = max(0, width - full - (1 if part else 0))
    return f"{_CYAN}{'█' * full}{part}{_RESET}{_DIM}{'░' * empty}{_RESET}"


def format_duration(seconds: float) -> str:
    """Format seconds as H:MM:SS (>= 1h) or M:SS."""
    seconds = max(0.0, float(seconds))
    total_s = int(round(seconds))
    h = total_s // 3600
    m = (total_s % 3600) // 60
    s = total_s % 60
    return f"{h}:{m:02d}:{s:02d}" if h > 0 else f"{m}:{s:02d}"


def _plain_line(label: str, i: int, total: int | None, t_start: float, final: bool) -> None:
    elapsed = time.perf_counter() - t_start
    rate = i / elapsed if elapsed > 0 else 0.0
    if total is not None and total > 0:
        pct = 100 if final else int(100 * i / total)
        remaining = (total - i) / rate if (rate > 0 and not final) else 0.0
        eta = "0:00" if final else format_duration(remaining)
        print(f"[{label}] {pct}% ({i}/{total}) | elapsed {format_duration(elapsed)} "
              f"| ETA {eta} | {rate:.1f} it/s", flush=True)
    else:
        print(f"[{label}] {i} items | elapsed {format_duration(elapsed)} "
              f"| {rate:.1f} it/s", flush=True)


def _tty_draw(label: str, i: int, total: int | None, t_start: float,
              final: bool, spin_i: int) -> None:
    elapsed = time.perf_counter() - t_start
    rate = i / elapsed if elapsed > 0 else 0.0
    spin = " " if final else _SPIN[spin_i % len(_SPIN)]
    if total is not None and total > 0:
        pct = 100 if final else int(100 * i / total)
        remaining = (total - i) / rate if (rate > 0 and not final) else 0.0
        eta = "0:00" if final else format_duration(remaining)
        line = (f"{_CYAN}{spin}{_RESET} {_DIM}{label}{_RESET} "
                f"{_DIM}│{_RESET}{_bar(1.0 if final else i / total)}{_DIM}│{_RESET} "
                f"{_BOLD}{pct:3d}%{_RESET} {_DIM}({i}/{total}) · "
                f"{format_duration(elapsed)} · ETA {eta} · {rate:.1f}/s{_RESET}")
    else:
        line = (f"{_CYAN}{spin}{_RESET} {_DIM}{label}{_RESET} {_BOLD}{i}{_RESET} "
                f"{_DIM}items · {format_duration(elapsed)} · {rate:.1f}/s{_RESET}")
    sys.stdout.write("\r\x1b[2K" + line)
    sys.stdout.flush()


def progress_iter(iterable: Iterable[T], label: str, *,
                  total: int | None = None, min_interval: float = 15.0) -> Iterator[T]:
    """Yield items unchanged, printing progress (fancy on TTY, plain greppable otherwise)."""
    t_start = time.perf_counter()
    t_last = t_start
    last_milestone = -1
    i = 0
    tty = _stdout_is_tty()
    spin_i = 0
    if tty:
        _tty_draw(label, 0, total, t_start, False, spin_i)
    else:
        _plain_line(label, 0, total, t_start, False)
    for item in iterable:
        yield item
        i += 1
        now = time.perf_counter()
        if tty:
            spin_i += 1
            is_last = total is not None and total > 0 and i >= total
            if now - t_last >= 0.066 or is_last:
                _tty_draw(label, i, total, t_start, False, spin_i)
                t_last = now
        else:
            milestone = False
            if total is not None and total > 0:
                cur = int(100 * i / total) // 10
                if cur > last_milestone:
                    last_milestone = cur
                    milestone = True
            if now - t_last >= min_interval or milestone:
                _plain_line(label, i, total, t_start, False)
                t_last = now
    if tty:
        _tty_draw(label, i, total, t_start, True, spin_i)
        sys.stdout.write("\n")
        sys.stdout.flush()
    else:
        _plain_line(label, i, total, t_start, True)


def progress_parallel(futures, label: str, *, total: int | None = None):
    """Yield each future's result as it completes, drawing the same progress as
    progress_iter. ``futures`` is an iterable of concurrent.futures.Future. ``total``
    should be the future count (so pct/ETA are shown)."""
    from concurrent.futures import as_completed

    t_start = time.perf_counter()
    t_last = t_start
    tty = _stdout_is_tty()
    i = 0
    if tty:
        _tty_draw(label, 0, total, t_start, False, 0)
    else:
        _plain_line(label, 0, total, t_start, False)
    for fut in as_completed(list(futures)):
        result = fut.result()
        i += 1
        yield result
        now = time.perf_counter()
        if tty:
            is_last = total is not None and total > 0 and i >= total
            if now - t_last >= 0.066 or is_last:
                _tty_draw(label, i, total, t_start, False, i)
                t_last = now
        else:
            if now - t_last >= 5.0 or (total and i >= total):
                _plain_line(label, i, total, t_start, False)
                t_last = now
    if tty:
        _tty_draw(label, i, total, t_start, True, i)
        sys.stdout.write("\n")
        sys.stdout.flush()
    else:
        _plain_line(label, i, total, t_start, True)
