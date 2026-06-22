"""Share expensive sampling-independent synpp stage caches across runs/machines.

synpp stores each completed stage in ``working_directory`` as two artifacts named
by the stage's content hash:

    <module>__<hash>.p        # pickled stage return value
    <module>__<hash>.cache/   # files the stage wrote via context.path()

synpp recomputes ``<hash>`` from the stage's config dependencies and re-validates
it when loading, so we never recompute the hash ourselves:

- ``export`` copies a stage's ``<module>__<hash>.{p,cache}`` from a working_directory
  into a shared, syncable store.
- ``prime`` copies the store's entries for the requested modules into a target
  working_directory BEFORE synpp runs. synpp then HITS the entry whose hash matches
  the target config and safely recomputes any that do not (a miss is never a
  corruption -- it only forgoes the speedup).

Module names are dotted and never contain ``__``; the ``<module>__`` prefix is the
exact entry separator, so matching is unambiguous (e.g. ``...german_wide`` never
matches ``...german_wide_xl``). No silent fallbacks: export/prime log explicit
counts. See docs/superpowers/specs/2026-06-22-shared-stage-cache-design.md.
"""
from __future__ import annotations

import logging
import os
import shutil

logger = logging.getLogger(__name__)

_RESULT_SUFFIX = ".p"
_CACHE_SUFFIX = ".cache"


def find_stage_entries(directory: str, module: str) -> list:
    """Return the ``<module>__<hash>`` basenames in ``directory`` that have a ``.p``.

    The match requires the exact ``<module>__`` prefix, so a module name that is a
    string prefix of another (``german_wide`` vs ``german_wide_xl``) never collides.
    """
    prefix = module + "__"
    entries = []
    if not os.path.isdir(directory):
        return entries
    for name in sorted(os.listdir(directory)):
        if name.startswith(prefix) and name.endswith(_RESULT_SUFFIX):
            entries.append(name[: -len(_RESULT_SUFFIX)])
    return entries


def _copy_entry(src_dir: str, dst_dir: str, entry: str) -> None:
    """Copy ``<entry>.p`` and, when present, ``<entry>.cache/`` from src to dst.

    An existing ``.cache`` dir at the destination is replaced so a re-export/re-prime
    cannot leave a half-stale directory.
    """
    os.makedirs(dst_dir, exist_ok=True)
    shutil.copy2(
        os.path.join(src_dir, entry + _RESULT_SUFFIX),
        os.path.join(dst_dir, entry + _RESULT_SUFFIX),
    )
    cache_src = os.path.join(src_dir, entry + _CACHE_SUFFIX)
    if os.path.isdir(cache_src):
        cache_dst = os.path.join(dst_dir, entry + _CACHE_SUFFIX)
        if os.path.exists(cache_dst):
            shutil.rmtree(cache_dst)
        shutil.copytree(cache_src, cache_dst)


def export(working_directory: str, modules: list, store: str) -> dict:
    """Copy each module's synpp cache entries from ``working_directory`` into ``store``.

    Returns ``{"exported": [<entry>...], "skipped": [<module>...]}`` where ``skipped``
    lists modules that had no cache entry in ``working_directory`` (logged, never
    silently ignored).
    """
    exported, skipped = [], []
    for module in modules:
        entries = find_stage_entries(working_directory, module)
        if not entries:
            skipped.append(module)
            continue
        for entry in entries:
            _copy_entry(working_directory, store, entry)
            exported.append(entry)
    logger.info(
        "[cache_share] export: %d entr(ies) for %d module(s) -> %s; "
        "skipped (no cache present) %d %s",
        len(exported), len(modules), store, len(skipped), skipped or "",
    )
    return {"exported": exported, "skipped": skipped}


def prime(working_directory: str, modules: list, store: str, recompute: list) -> dict:
    """Copy store entries for ``modules`` (minus ``recompute``) into ``working_directory``.

    ``recompute`` lists modules to deliberately NOT prime (so synpp recomputes them);
    ``"*"`` in ``recompute`` forces recompute of all requested modules. Entries already
    present in the target are left untouched (never overwritten). Returns a report with
    ``primed`` / ``skipped_present`` / ``forced`` / ``missing_in_store`` for traceability.
    """
    recompute = recompute or []
    force_all = "*" in recompute
    primed, skipped_present, forced, missing = [], [], [], []
    for module in modules:
        if force_all or module in recompute:
            forced.append(module)
            continue
        entries = find_stage_entries(store, module)
        if not entries:
            missing.append(module)
            continue
        for entry in entries:
            if os.path.exists(os.path.join(working_directory, entry + _RESULT_SUFFIX)):
                skipped_present.append(entry)
                continue
            _copy_entry(store, working_directory, entry)
            primed.append(entry)
    logger.info(
        "[cache_share] prime: primed %d, already-present %d, forced %d, "
        "missing-in-store %d (store=%s)%s",
        len(primed), len(skipped_present), len(forced), len(missing), store,
        (" missing=%s" % missing) if missing else "",
    )
    return {
        "primed": primed,
        "skipped_present": skipped_present,
        "forced": forced,
        "missing_in_store": missing,
    }
