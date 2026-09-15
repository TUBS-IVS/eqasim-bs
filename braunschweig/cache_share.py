"""Share expensive sampling-independent synpp stage caches across runs/machines.

synpp stores each completed stage in ``working_directory`` as two artifacts named
by the stage's content hash:

    <module>__<hash>.p        # pickled stage return value
    <module>__<hash>.cache/   # files the stage wrote via context.path()

synpp recomputes ``<hash>`` from the stage's config dependencies and re-validates
it when loading, so we never recompute the hash ourselves:

- ``export`` copies a stage's ``<module>__<hash>.{p,cache}`` from a working_directory
  into a shared, syncable store, with a ``.metadata.json`` sidecar containing its
  native ``pipeline.json`` record.
- ``prime`` copies the store's entries for the requested modules into a target
  working_directory BEFORE synpp runs and merges metadata for newly copied entries.
  synpp checks configuration, implementation, dependency timestamps and validation
  tokens. Copying artifacts alone cannot produce a hit in a fresh directory.

Module names are dotted and never contain ``__``; the ``<module>__`` prefix is the
exact entry separator, so matching is unambiguous (e.g. ``...german_wide`` never
matches ``...german_wide_xl``). No silent fallbacks: export/prime log explicit
counts. See docs/superpowers/specs/2026-06-22-shared-stage-cache-design.md.
"""
from __future__ import annotations

import logging
import json
import os
import shutil
import tempfile
from collections import defaultdict

logger = logging.getLogger(__name__)

_RESULT_SUFFIX = ".p"
_CACHE_SUFFIX = ".cache"
_METADATA_SUFFIX = ".metadata.json"


def _read_metadata(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as stream:
        metadata = json.load(stream)
    if not isinstance(metadata, dict):
        raise ValueError("Cache metadata must be a JSON object: %s" % path)
    return metadata


def _write_metadata(path: str, metadata: dict) -> None:
    """Replace metadata only after its complete JSON has been written."""
    fd, temporary = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(metadata, stream)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)


def _drop_incoherent_metadata(metadata: dict, copied: list) -> int:
    """Retain only exact dependency snapshots for copied entries and descendants.

    synpp checks whether a parent is NEWER than the child's recorded parent. A
    parent imported from another run can be OLDER and still contain different
    results. Cross-run merging therefore needs exact timestamp equality. Unrelated
    target entries are outside this check and remain untouched.
    """
    downstream = defaultdict(list)
    for entry, record in metadata.items():
        for dependency in record.get("dependencies", {}):
            downstream[dependency].append(entry)
    affected = set(copied)
    pending = list(copied)
    for entry in pending:
        for child in downstream[entry]:
            if child not in affected:
                affected.add(child)
                pending.append(child)
    removed = 0
    while True:
        stale = [entry for entry in affected if entry in metadata and any(
            dependency not in metadata or metadata[dependency].get("updated") != timestamp
            for dependency, timestamp in metadata[entry].get("dependencies", {}).items()
        )]
        if not stale:
            return removed
        for entry in stale:
            del metadata[entry]
            removed += 1


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


def export(working_directory: str, modules: list, store: str, skip_existing: bool = False,
           *, share_metadata: bool = True) -> dict:
    """Copy each module's synpp cache entries from ``working_directory`` into ``store``.

    ``skip_existing`` controls the overwrite policy when an entry with the SAME
    ``<module>__<hash>`` already exists in ``store``:

    - ``False`` (default, CLI behaviour): the store entry is re-copied (overwritten
      together with metadata from the same source run).
    - ``True`` (used by the automatic post-run export): the existing store entry is
      left untouched and reported in ``skipped_present`` -- so the store is never
      clobbered. Different configuration hashes coexist. An implementation or input
      change can keep the filename while invalidating its metadata; synpp retains
      responsibility for detecting this. Refresh such entries with an explicit export.

    Returns ``{"exported": [...], "skipped": [<module>...], "skipped_present": [...]}``
    where ``skipped`` lists modules that had no cache entry in ``working_directory`` and
    ``skipped_present`` lists entries left in place because they were already in the
    store (only populated when ``skip_existing`` is True). Both are logged, never
    silently ignored.

    ``share_metadata=False`` retains artifact-only export. Missing metadata in a
    legacy store remains an explicit cache miss, never an inferred valid record.
    Export and prime require exclusive use of their destination directories.
    """
    metadata = _read_metadata(os.path.join(working_directory, "pipeline.json")) if share_metadata else {}
    exported, skipped, skipped_present = [], [], []
    with_metadata = 0
    for module in modules:
        entries = find_stage_entries(working_directory, module)
        if not entries:
            skipped.append(module)
            continue
        for entry in entries:
            if skip_existing and os.path.exists(os.path.join(store, entry + _RESULT_SUFFIX)):
                skipped_present.append(entry)
                continue
            sidecar = os.path.join(store, entry + _METADATA_SUFFIX)
            # Never associate an older record with newly copied artifacts, including
            # legacy/OFF exports into a store that already has metadata.
            if os.path.exists(sidecar):
                os.remove(sidecar)
            _copy_entry(working_directory, store, entry)
            if entry in metadata and os.path.isdir(os.path.join(working_directory, entry + _CACHE_SUFFIX)):
                _write_metadata(sidecar, metadata[entry])
                with_metadata += 1
            exported.append(entry)
    logger.info(
        "[cache_share] export: %d entr(ies) for %d module(s) -> %s; "
        "already-in-store %d; skipped (no cache present) %d %s",
        len(exported), len(modules), store, len(skipped_present), len(skipped), skipped or "",
    )
    logger.info("[cache_share] export: metadata for %d/%d copied entries (enabled=%s)",
                with_metadata, len(exported), share_metadata)
    return {"exported": exported, "skipped": skipped, "skipped_present": skipped_present}


def prime(working_directory: str, modules: list, store: str, recompute: list,
          *, share_metadata: bool = True) -> dict:
    """Copy store entries for ``modules`` (minus ``recompute``) into ``working_directory``.

    ``recompute`` lists modules to deliberately NOT prime (so synpp recomputes them);
    ``"*"`` in ``recompute`` forces recompute of all requested modules. Entries already
    present in the target are left untouched (never overwritten). Returns a report with
    ``primed`` / ``skipped_present`` / ``forced`` / ``missing_in_store`` for traceability.
    With ``share_metadata=False`` no target registry is read or changed. Metadata
    is never attached to a skipped existing payload, whose provenance may differ.
    """
    metadata_path = os.path.join(working_directory, "pipeline.json")
    metadata = _read_metadata(metadata_path) if share_metadata else {}
    with_metadata = 0
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
            # A leftover registry record must not validate a partial copy after
            # an I/O failure. Existing payloads above remain entirely untouched.
            if share_metadata and entry in metadata:
                metadata.pop(entry)
                _drop_incoherent_metadata(metadata, [entry])
                _write_metadata(metadata_path, metadata)
            _copy_entry(store, working_directory, entry)
            if share_metadata:
                record = _read_metadata(os.path.join(store, entry + _METADATA_SUFFIX))
                metadata.pop(entry, None)
                if record and os.path.isdir(os.path.join(store, entry + _CACHE_SUFFIX)):
                    metadata[entry] = record
                    with_metadata += 1
            primed.append(entry)
    if share_metadata and primed:
        removed = _drop_incoherent_metadata(metadata, primed)
        if removed:
            logger.info("[cache_share] prime: invalidated %d incoherent dependency records", removed)
        with_metadata = sum(entry in metadata for entry in primed)
        _write_metadata(metadata_path, metadata)
    logger.info("[cache_share] prime: metadata for %d/%d copied entries (enabled=%s); "
                "entries without metadata require recomputation",
                with_metadata, len(primed), share_metadata)
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
