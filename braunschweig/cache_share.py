"""Share expensive sampling-independent synpp stage caches across runs/machines.

synpp stores each completed stage in ``working_directory`` as two artifacts named
by the stage's content hash:

    <module>__<hash>.p        # pickled stage return value
    <module>__<hash>.cache/   # files the stage wrote via context.path()

synpp recomputes ``<hash>`` from the stage's config dependencies and re-validates
it when loading, so we never recompute the hash ourselves:

- ``export`` copies a stage's ``<module>__<hash>.{p,cache}`` from a working_directory
  into a shared, syncable store, with a ``.metadata.json`` envelope containing its
  native ``pipeline.json`` record and tracked creation runtime.
- ``prime`` copies the store's entries for the requested modules into a target
  working_directory BEFORE synpp runs and merges metadata for newly copied entries.
  synpp checks configuration, implementation, dependency output hashes (timestamps
  for legacy records) and validation tokens. Copying artifacts alone cannot produce a
  hit in a fresh directory.

Module names are dotted and never contain ``__``; the ``<module>__`` prefix is the
exact entry separator, so matching is unambiguous (e.g. ``...german_wide`` never
matches ``...german_wide_xl``). No silent fallbacks: export/prime log explicit
counts. See docs/superpowers/specs/2026-06-22-shared-stage-cache-design.md.

Coherence of a primed record with its parents is judged by CONTENT (spec
2026-10-16 section 4.5): a child's record is kept when, for every parent present in the target metadata, the
child's recorded ``dependency_output_hashes`` digest equals the parent's ``output_hash``
digest, so an identical output primed from another run is a hit although timestamps
differ. Two legacy (unhashed) records fall back to exact timestamp equality; a mixed
pair is dropped (content cannot be compared). A parent ABSENT from the target metadata
keeps the record when it carries that parent's recorded digest: the run re-executes the
parent and the fork compares digests, so a mismatch never becomes a hit. This is never
more permissive than the fork's ``braunschweig.synpp_fork.staleness.assess``: it can drop
a record the fork would still accept, which is a safe miss, never a false hit.

The user-facing behaviour and the known safe misses (migrated records, pre-Phase-3 stores,
the unshared ``braunschweig.freight.extraction_tool``) are in docs/features/cache-share.md;
the decision text is ADR-0149.
"""
from __future__ import annotations

import logging
import json
import os
import shutil
import tempfile
from collections import defaultdict
from contextlib import contextmanager
import importlib.metadata
import platform
import struct
import sys

logger = logging.getLogger(__name__)

_RESULT_SUFFIX = ".p"
_CACHE_SUFFIX = ".cache"
_METADATA_SUFFIX = ".metadata.json"
_METADATA_VERSION = 1


def _runtime_fingerprint() -> dict:
    """Conservatively identify the runtime that actually created an entry."""
    packages = {}
    for dist in importlib.metadata.distributions():
        distribution_metadata = dist.metadata
        name = distribution_metadata.get("Name")
        if name:
            packages[name.lower().replace("_", "-")] = distribution_metadata["Version"]
    return {
        "system": platform.system(), "machine": platform.machine(),
        "bits": struct.calcsize("P") * 8, "byteorder": sys.byteorder,
        "python": platform.python_version(), "implementation": platform.python_implementation(),
        "packages": packages,
    }


@contextmanager
def track_run(working_directory: str):
    """Record provenance only for native entries produced during a successful run.

    Unchanged cache hits without a prior envelope stay unknown. In particular an
    explicit export cannot retrospectively label old artifacts with today's runtime.

    An entry the fork's early cutoff spared keeps its ``updated`` but its record changes
    (``dependencies[parent]`` takes the parent's new ``updated``; the recorded output digest
    is equal by definition and stays). An EXISTING envelope then
    follows that record so ``export`` still sees them equal; its ``environment`` is kept
    (provenance is the runtime that created the artifact, not the one that re-validated it).
    """
    before = _read_metadata(os.path.join(working_directory, "pipeline.json"))
    yield
    after = _read_metadata(os.path.join(working_directory, "pipeline.json"))
    environment = None
    for entry, record in after.items():
        if entry in before and before[entry].get("updated") == record.get("updated"):
            _refresh_envelope(os.path.join(working_directory, entry + _METADATA_SUFFIX), record)
            continue
        if (os.path.isfile(os.path.join(working_directory, entry + _RESULT_SUFFIX))
                and os.path.isdir(os.path.join(working_directory, entry + _CACHE_SUFFIX))):
            if environment is None:
                environment = _runtime_fingerprint()
            _write_metadata(os.path.join(working_directory, entry + _METADATA_SUFFIX), {
                "version": _METADATA_VERSION, "environment": environment, "metadata": record,
            })


def _refresh_envelope(sidecar: str, record: dict) -> None:
    """Update the ``metadata`` of an existing, valid envelope to ``record``; never create one."""
    if not os.path.isfile(sidecar):
        return
    envelope = _read_metadata(sidecar)
    if (envelope.get("version") == _METADATA_VERSION and envelope.get("environment")
            and envelope.get("metadata") != record):
        _write_metadata(sidecar, {**envelope, "metadata": record})


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


_INCOHERENCE_REASONS = ("output_hash_mismatch", "legacy_timestamp_mismatch",
                        "mixed_hashed_legacy", "missing_parent")


def _incoherence(entry: dict, metadata: dict):
    """Return why ``entry`` is incoherent with its parents in ``metadata``, or ``None``.

    The reason is one of ``_INCOHERENCE_REASONS``; the first failing parent decides.

    A parent that is ABSENT from ``metadata`` (not shared, so absent from a fresh directory) does
    not make the record incoherent when the record carries ``dependency_output_hashes[parent]``:
    the run then re-executes that parent (``not_in_meta``) and the fork compares the new output
    digest with the recorded one, so a differing output recomputes the child and never becomes a
    hit. Without a recorded digest the child would recompute for certain, so the record is
    dropped as ``missing_parent``."""
    for dependency, timestamp in entry.get("dependencies", {}).items():
        parent = metadata.get(dependency)
        if parent is None:
            if entry.get("dependency_output_hashes", {}).get(dependency) is not None:
                continue
            return "missing_parent"
        recorded = entry.get("dependency_output_hashes", {}).get(dependency)
        current = (parent.get("output_hash") or {}).get("digest")
        if recorded is not None and current is not None:
            if recorded != current:
                return "output_hash_mismatch"
        elif recorded is None and current is None:
            if parent.get("updated") != timestamp:
                return "legacy_timestamp_mismatch"
        else:
            return "mixed_hashed_legacy"
    return None


def _coherence_basis(entry: dict, metadata: dict) -> str:
    """Name the rule that accepted an entry already known to be coherent (``_incoherence`` is None).

    ``no_parents``: nothing to compare; ``absent_parent_by_digest``: at least one parent is absent
    from ``metadata`` and will be decided at run time by its recorded digest; ``legacy_timestamp_match``:
    at least one parent was judged by equal timestamps (legacy records); else ``output_hash_match``.
    Used only for the kept counts that ``prime`` logs."""
    dependencies = entry.get("dependencies", {})
    if not dependencies:
        return "no_parents"
    if any(dependency not in metadata for dependency in dependencies):
        return "absent_parent_by_digest"
    recorded = entry.get("dependency_output_hashes", {})
    if any(recorded.get(dependency) is None for dependency in dependencies):
        return "legacy_timestamp_match"
    return "output_hash_match"


def _coherent(entry: dict, metadata: dict) -> bool:
    """A child's record is coherent with its parents when every present parent either
    agrees by output hash or, for two legacy records, by equal timestamps, and every absent
    parent has a digest recorded in the child (decided at run time). A mixed pair (one side
    hashed, the other not) is incoherent: content cannot be compared, so synpp must
    recompute (spec 2026-10-16 section 4.5)."""
    return _incoherence(entry, metadata) is None


def _drop_incoherent_metadata(metadata: dict, copied: list, counts: dict = None) -> int:
    """Retain only entries coherent with their parents, for copied entries and descendants.

    Content, not timestamps, decides (see ``_coherent``): a parent imported from another
    run keeps the child valid exactly when its output hash equals the one the child
    recorded. A parent absent from ``metadata`` keeps the child when the child recorded a
    digest for it (the run recomputes the parent and compares digests, so a mismatch is never
    a hit); without that digest the child is dropped as ``missing_parent``. Unrelated target
    entries are outside this check and remain untouched.
    When ``counts`` is given it is filled with the number of dropped records per reason
    (``_INCOHERENCE_REASONS``) so the caller can log them.
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
        stale = {entry: reason for entry in affected if entry in metadata
                 and (reason := _incoherence(metadata[entry], metadata)) is not None}
        if not stale:
            return removed
        for entry, reason in stale.items():
            del metadata[entry]
            removed += 1
            if counts is not None:
                counts[reason] = counts.get(reason, 0) + 1


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

    Both artifacts are copied into a temporary entry before either final path is
    published. The cache directory is published first and the result file last;
    therefore the ``.p`` file used for discovery/``skip_existing`` only appears
    after a complete cache copy. Existing artifacts are restored if publication
    raises, so re-export/re-prime cannot leave a half-stale entry.
    """
    os.makedirs(dst_dir, exist_ok=True)
    result_src = os.path.join(src_dir, entry + _RESULT_SUFFIX)
    cache_src = os.path.join(src_dir, entry + _CACHE_SUFFIX)
    result_dst = os.path.join(dst_dir, entry + _RESULT_SUFFIX)
    cache_dst = os.path.join(dst_dir, entry + _CACHE_SUFFIX)

    with tempfile.TemporaryDirectory(
        dir=dst_dir, prefix=".cache-share-", suffix=".tmp",
    ) as staging_dir:
        staged_result = os.path.join(staging_dir, "result" + _RESULT_SUFFIX)
        staged_cache = os.path.join(staging_dir, "result" + _CACHE_SUFFIX)
        shutil.copy2(result_src, staged_result)
        has_cache = os.path.isdir(cache_src)
        if has_cache:
            shutil.copytree(cache_src, staged_cache)

        backup_dir = os.path.join(staging_dir, "previous")
        os.makedirs(backup_dir)
        backup_result = os.path.join(backup_dir, "result" + _RESULT_SUFFIX)
        backup_cache = os.path.join(backup_dir, "result" + _CACHE_SUFFIX)
        published_result = False
        published_cache = False
        try:
            if os.path.exists(result_dst):
                os.replace(result_dst, backup_result)
            if has_cache and os.path.exists(cache_dst):
                os.replace(cache_dst, backup_cache)
            if has_cache:
                os.replace(staged_cache, cache_dst)
                published_cache = True
            os.replace(staged_result, result_dst)
            published_result = True
        except Exception:
            if published_result and os.path.exists(result_dst):
                os.remove(result_dst)
            if published_cache and os.path.exists(cache_dst):
                shutil.rmtree(cache_dst)
            if os.path.exists(backup_result):
                os.replace(backup_result, result_dst)
            if os.path.exists(backup_cache):
                os.replace(backup_cache, cache_dst)
            raise


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
            # legacy/OFF exports into a store that already has metadata. Keep the
            # old sidecar until staging succeeds so an interrupted copy leaves the
            # complete previously published entry intact.
            _copy_entry(working_directory, store, entry)
            if os.path.exists(sidecar):
                os.remove(sidecar)
            envelope = _read_metadata(os.path.join(working_directory, entry + _METADATA_SUFFIX)) if share_metadata else {}
            if (entry in metadata and envelope.get("version") == _METADATA_VERSION
                    and envelope.get("metadata") == metadata[entry] and envelope.get("environment")
                    and os.path.isdir(os.path.join(working_directory, entry + _CACHE_SUFFIX))):
                _write_metadata(sidecar, envelope)
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
    environment = _runtime_fingerprint() if share_metadata else None
    envelopes = {}
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
                envelope = _read_metadata(os.path.join(store, entry + _METADATA_SUFFIX))
                compatible = (envelope.get("version") == _METADATA_VERSION
                              and envelope.get("environment") == environment)
                record = envelope.get("metadata", {}) if compatible else {}
                metadata.pop(entry, None)
                if record and os.path.isdir(os.path.join(store, entry + _CACHE_SUFFIX)):
                    metadata[entry] = record
                    envelopes[entry] = envelope
                    with_metadata += 1
                elif envelope:
                    logger.info("[cache_share] prime: unknown or incompatible runtime provenance: %s", entry)
            primed.append(entry)
    if share_metadata and primed:
        counts = {}
        removed = _drop_incoherent_metadata(metadata, primed, counts=counts)
        if removed:
            logger.info(
                "[cache_share] prime: invalidated %d incoherent dependency records "
                "(output-hash mismatch %d, legacy timestamp mismatch %d, "
                "mixed hashed/legacy pair %d, missing parent %d)",
                removed, counts.get("output_hash_mismatch", 0),
                counts.get("legacy_timestamp_mismatch", 0),
                counts.get("mixed_hashed_legacy", 0), counts.get("missing_parent", 0))
        with_metadata = sum(entry in metadata for entry in primed)
        kept = {"output_hash_match": 0, "legacy_timestamp_match": 0, "no_parents": 0,
                "absent_parent_by_digest": 0}
        for entry in primed:
            if entry in metadata:
                kept[_coherence_basis(metadata[entry], metadata)] += 1
        logger.info(
            "[cache_share] prime: kept %d copied records (output-hash match %d, "
            "legacy equal timestamps %d, no parents %d, "
            "absent parent decided by digest at run time %d)",
            with_metadata, kept["output_hash_match"], kept["legacy_timestamp_match"],
            kept["no_parents"], kept["absent_parent_by_digest"])
        for entry in primed:
            sidecar = os.path.join(working_directory, entry + _METADATA_SUFFIX)
            if entry in metadata:
                _write_metadata(sidecar, envelopes[entry])
            elif os.path.exists(sidecar):
                os.remove(sidecar)
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
