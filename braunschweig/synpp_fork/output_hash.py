"""Content hash of a synpp stage's output: its pickle and every file in its cache directory.

The digest is the validity signal children compare against (spec §4.3/§4.4). Everything is
hashed by default; a stage may declare ``_OUTPUT_HASH_EXCLUDE = ("*.log",)`` (fnmatch globs
on the path relative to its cache directory) for files whose content changes without
meaning. Unreadable files and unreadable directories raise: an output that cannot be hashed
is never "unchanged" and never "no hash".

Symlink rule: the cache directory ``<hash>.cache`` itself may be a symlink (server cache
farms link whole entries) and is hashed through. A symlink *inside* the cache directory
(file or directory entry) raises, because hashing it would either skip content silently or
hash something outside the entry; stages must write real files. This holds even when an exclude glob would skip the symlink
(stricter on purpose). Windows directory junctions are not detected by ``os.path.islink`` on
Python 3.10; no stage creates junctions.

Exclude globs use ``fnmatch.fnmatchcase`` so the digest does not depend on the platform.
"""
from __future__ import annotations

import fnmatch
import hashlib
import os

OUTPUT_HASH_FORMAT = 1
_CHUNK = 1 << 20


def _sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _raise_walk_error(error: OSError) -> None:
    # os.walk swallows scandir errors by default; an unreadable directory must not
    # silently hash as empty.
    raise error


def _reject_symlink(path: str) -> None:
    if os.path.islink(path):
        raise OSError(
            "symlinks inside a stage cache directory are not hashed: %s is a symlink; "
            "stages must write real files into their cache directory" % path)


def hash_stage_output(working_directory: str, stage_hash: str, exclude_globs: tuple = ()) -> dict:
    if not isinstance(exclude_globs, tuple) or not all(isinstance(g, str) for g in exclude_globs):
        raise TypeError("_OUTPUT_HASH_EXCLUDE must be a tuple of glob strings, got %r" % (exclude_globs,))
    pickle_path = os.path.join(working_directory, stage_hash + ".p")
    cache_dir = os.path.join(working_directory, stage_hash + ".cache")
    if not os.path.isfile(pickle_path) or not os.path.isdir(cache_dir):
        raise FileNotFoundError("stage artifacts missing for %s under %s" % (stage_hash, working_directory))
    pickle_digest = _sha256_file(pickle_path)
    total = os.path.getsize(pickle_path)
    lines = []
    for root, dirs, files in os.walk(cache_dir, onerror=_raise_walk_error):
        dirs.sort()
        for name in dirs:
            _reject_symlink(os.path.join(root, name))
        for name in sorted(files):
            path = os.path.join(root, name)
            _reject_symlink(path)
            relative = os.path.relpath(path, cache_dir).replace(os.sep, "/")
            if any(fnmatch.fnmatchcase(relative, pattern) for pattern in exclude_globs):
                continue
            size = os.path.getsize(path)
            lines.append("%s\0%d\0%s\n" % (relative, size, _sha256_file(path)))
            total += size
    lines.sort()  # global order by relative path, independent of directory walk order
    files_digest = hashlib.sha256("".join(lines).encode("utf-8")).hexdigest()
    digest = hashlib.sha256((pickle_digest + "\n" + files_digest).encode("utf-8")).hexdigest()
    return {"format": OUTPUT_HASH_FORMAT, "digest": digest, "pickle": pickle_digest,
            "files": files_digest, "n_files": len(lines), "bytes": total}
