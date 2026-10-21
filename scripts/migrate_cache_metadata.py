"""Rewrite a synpp working directory's pipeline.json to the fork's validity fields without
recomputing anything (spec §4.7; plan "Content-addressed stage cache", Rulings F, G and H).

    python scripts/migrate_cache_metadata.py configs/base_bs.yml configs/overlays/test_100pct.yml \
        [--working-directory <dir>] [--legacy-source-rev <rev>] [--legacy-source-repo <dir>] [--dry-run]

Why a git revision is needed. A legacy entry stores synpp 1.6.2's md5 of the stage module's raw
text and, for source-hashing stages, an md5 over the raw text of the helper modules. The
semantic-token refactor edited the validate() of every source-hashing stage, so these md5 values
can no longer be reproduced from the current files. They are reproduced from the pre-refactor
revision instead (``LEGACY_SOURCE_REV``, the origin/main base of the refactor) and the current
code is proven to differ from it only by that refactor:

1. Module hash. stored md5 == md5(current source) -> proven; else stored md5 == md5(text at REV)
   AND the module is refactor-equivalent between REV and now -> proven; else ``module_hash_mismatch``.
   Proven entries get the fork's semantic module hash (the registry wrapper's ``module_hash``).
2. Token (stages that declare ``_HELPER_MODULES`` or ``_DEFERRED_HELPER_MODULE_NAMES`` and store a
   string token). REV's validate() is replayed: its text is executed in a scratch copy of the
   module namespace while ``inspect.getsource`` returns the REV text of every module whose file
   differs between REV and the working tree. Replay == stored AND every module whose REV text the
   replay hashed is refactor-equivalent -> proven, and the CURRENT ``validate()`` value is stored
   (exactly what run() compares). Entries not in the registry (another config variant): a
   pure-loop stage stores ``source_token(...)`` over its tuples; a composite token (data part)
   cannot be evaluated outside the registry and stays legacy (``token_unverifiable``).
3. Output hash from the artifacts on disk (``_OUTPUT_HASH_EXCLUDE`` honoured).
4. ``dependency_output_hashes`` from the parents' digests, only where the upstream timestamp
   rule says the child already saw the parent's current output (otherwise the child stays stale).
5. ``fork = {"format": 1}`` only when 1-3 passed or did not apply. Every other entry is left
   byte-for-byte unchanged (``kept_legacy``, one log line per entry naming why): the upstream
   timestamp rule keeps deciding it, which is the safe direction. Entries that already carry
   ``fork`` or ``output_hash`` are left alone (``already_migrated``): re-hashing their output
   could turn a changed parent output into a hit.

"Refactor-equivalent": the semantic digests (docstring-free AST) of the two texts are equal after
removing the module-level ``def validate`` (only a plain FunctionDef: an ``async def validate``
or a ``validate = ...`` assignment is ordinary code and stays in) and the module-level imports of exactly
``import hashlib``, ``import inspect``, ``import importlib`` and ``from braunschweig import
synpp_tokens`` (unaliased). Only the listed names are dropped from an import statement; any other
name in the same statement stays in.

``updated`` and ``dependencies`` are never touched. The rewrite is atomic and keeps
``pipeline.json.bk`` (braunschweig.synpp_fork.metadata.write_meta). Exit 0 on success, also when
entries were kept legacy (they are reported); exit 1 on error. Run
scripts/explain_stale_stages.py afterwards.
"""
from __future__ import annotations

import argparse
import ast
import contextlib
import copy
import hashlib
import importlib
import inspect
import logging
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

log = logging.getLogger("migrate_cache_metadata")

# origin/main base of the semantic-token refactor (#468 Phase 3); reachable from main through the
# merge commit. The stage sources at this revision are the ones every pre-refactor cache was built with.
LEGACY_SOURCE_REV = "205a0fb7152ea75c2ac48f2afc0b05b43e99ce15"

COUNT_KEYS = ("module_hash_migrated", "module_hash_mismatch", "token_migrated", "token_mismatch",
              "token_not_applicable", "token_unverifiable", "output_hashed", "output_missing",
              "not_in_dag", "already_migrated", "dependency_hashes_filled",
              "dependency_hashes_skipped_stale", "dependency_hashes_parent_legacy", "migrated",
              "kept_legacy")

# Module-level imports the refactor added or removed; nothing else is ignored.
_TOOLING_IMPORTS = frozenset({("import", "hashlib"), ("import", "inspect"), ("import", "importlib"),
                              ("from", 0, "braunschweig", "synpp_tokens")})


def _import_key(node, alias):
    if alias.asname is not None:
        return None  # an aliased import binds another name: never tooling
    if isinstance(node, ast.Import):
        return ("import", alias.name)
    return ("from", node.level, node.module, alias.name)


def _normalised_digest(text: str) -> str:
    from braunschweig import synpp_tokens
    tree = ast.parse(text)
    body = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "validate":
            continue
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            kept = [alias for alias in node.names if _import_key(node, alias) not in _TOOLING_IMPORTS]
            if not kept:
                continue
            node.names = kept
        body.append(node)
    tree.body = body
    # Same canonical form as synpp_tokens.semantic_module_digest (docstrings removed).
    canonical = ast.dump(synpp_tokens._strip_docstrings(tree), annotate_fields=True, include_attributes=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def refactor_equivalent(legacy_text: str, current_text: str) -> bool:
    """True when the two module texts differ at most by the semantic-token refactor (see module doc)."""
    return _normalised_digest(legacy_text) == _normalised_digest(current_text)


def _md5(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


class SourceRecord:
    """What a patched ``inspect.getsource`` was asked for during one legacy replay."""

    def __init__(self):
        self.served = []        # (module, rev_text) answered with the text at REV
        self.requested = set()  # names of every module asked for


class GitLegacySource:
    """The module texts at a git revision of ``repo``.

    ``rev_text(path)`` is the text at REV of the file ``path``: read with ``git show`` when the file
    differs between REV and the working tree, the current text when it does not (identical by
    ``git diff``), None when the file is outside ``repo`` or absent at REV. Texts are LF-normalised
    and BOM-free, as ``inspect.getsource`` returns them. Git missing or REV absent raises RuntimeError: without the
    revision nothing can be proven, and silently keeping everything legacy would hide that.
    """

    def __init__(self, repo, rev: str, *, git: str = "git"):
        self.repo = Path(repo).resolve()
        self.rev = rev
        self.git = git
        try:
            # git paths are relative to the top level; a sub-directory argument is widened to it.
            self.repo = Path(self._git("rev-parse", "--show-toplevel").strip()).resolve()
            self._git("cat-file", "-e", "%s^{commit}" % rev)
        except (OSError, subprocess.CalledProcessError) as error:
            detail = error.stderr.strip() if isinstance(error, subprocess.CalledProcessError) else error
            raise RuntimeError(
                "legacy source revision %s is not available in %s (%s). The migration proves cache "
                "entries against the code they were built with; fetch it (`git fetch origin main`) or "
                "pass --legacy-source-rev / --legacy-source-repo." % (rev, self.repo, detail)) from error
        self._at_rev = set(self._git("ls-tree", "-r", "--name-only", "--full-tree", rev).splitlines())
        self._differing = set(self._git("diff", "--name-only", rev, "--").splitlines())
        self._texts = {}

    def _git(self, *args) -> str:
        return subprocess.run([self.git, "-C", str(self.repo), "-c", "core.quotepath=off", *args],
                              capture_output=True, text=True, encoding="utf-8", check=True).stdout

    def _relative(self, path):
        try:
            return Path(path).resolve().relative_to(self.repo).as_posix()
        except ValueError:
            return None

    def differs(self, path) -> bool:
        """True when ``path`` is in ``repo`` and its text at REV may differ from the current text."""
        rel = self._relative(path)
        return rel is not None and (rel in self._differing or rel not in self._at_rev)

    def rev_text(self, path):
        rel = self._relative(path)
        if rel is None or rel not in self._at_rev:
            return None
        if rel not in self._texts:
            if rel in self._differing:
                raw = subprocess.run([self.git, "-C", str(self.repo), "show", "%s:%s" % (self.rev, rel)],
                                     capture_output=True, check=True).stdout
                # utf-8-sig: inspect.getsource (tokenize.open) drops a UTF-8 BOM.
                self._texts[rel] = raw.decode("utf-8-sig").replace("\r\n", "\n")
            else:
                with open(path, encoding="utf-8-sig") as handle:   # universal newlines, no BOM, like getsource
                    self._texts[rel] = handle.read()
        return self._texts[rel]

    @contextlib.contextmanager
    def patched_getsource(self):
        """Process-wide ``inspect.getsource`` returning the REV text of every module whose file differs.

        Yields a ``SourceRecord``: ``served`` lists the (module, rev_text) pairs the patch answered
        with REV text, so the caller can prove each of them refactor-equivalent; ``requested`` names
        every module asked for (served or answered with its working-tree text), so the caller can
        check that the new token covers no module the legacy token did not.

        Only module objects are served REV text. A legacy validate() calling getsource on a
        function or class gets its working-tree text; its replayed token then differs from the
        stored one wherever that text changed, so such an entry fails closed (token_mismatch).
        Process-wide, because some validate() bodies import inspect locally; restored in ``finally``.
        """
        real = inspect.getsource
        record = SourceRecord()
        served = record.served

        def getsource(obj):
            path = getattr(obj, "__file__", None) if inspect.ismodule(obj) else None
            if inspect.ismodule(obj):
                record.requested.add(obj.__name__)
            if path and self.differs(path):
                text = self.rev_text(path)
                if text is not None:
                    served.append((obj, text))
                    return text
            return real(obj)

        inspect.getsource = getsource
        try:
            yield record
        finally:
            inspect.getsource = real


def _replay_legacy_token(module, context, legacy_source):
    """REV's validate() run on REV texts; returns (token, SourceRecord)."""
    text = legacy_source.rev_text(module.__file__)
    if text is None:
        raise LookupError("%s is not in the legacy source revision" % module.__name__)
    nodes = [node for node in ast.parse(text).body if isinstance(node, ast.FunctionDef) and node.name == "validate"]
    if len(nodes) != 1:
        raise LookupError("%s has %d module-level validate() at the legacy revision" % (module.__name__, len(nodes)))
    # Scratch namespace: the current module's names (equal to REV's outside validate() once the
    # module is refactor-equivalent) plus the tooling modules the legacy body used.
    namespace = dict(vars(module))
    namespace.update(hashlib=hashlib, importlib=importlib, inspect=inspect)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), module.__file__, "exec"), namespace)
    with legacy_source.patched_getsource() as record:
        token = namespace["validate"](context)
    return token, record


def _not_equivalent(served) -> list:
    """Names of the served modules whose current text is not refactor-equivalent to the REV text."""
    out = []
    for module, rev_text in served:
        if not refactor_equivalent(rev_text, inspect.getsource(module)):
            out.append(module.__name__)
    return sorted(set(out))


def _prove_module_hash(module, stored, legacy_source):
    """(proven, reason) for a stored md5 module hash."""
    if not inspect.ismodule(module):
        return False, "not a module stage (only module stages carry a provable md5 module hash)"
    current = inspect.getsource(module)
    if _md5(current) == stored:
        return True, "unchanged source"
    rev_text = legacy_source.rev_text(module.__file__)
    if rev_text is None:
        return False, "stored md5 differs from the current source and the file is absent at the legacy revision"
    if _md5(rev_text) != stored:
        return False, "stored md5 matches neither the current source nor the legacy revision"
    if not refactor_equivalent(rev_text, current):
        return False, "the module changed since the legacy revision beyond the validate() refactor"
    return True, "legacy revision + refactor-equivalent"


def _prove_token(module, wrapper, stage, record, entry, working_directory, legacy_source):
    """(status, new_token, reason); status in token_migrated / token_mismatch / token_unverifiable."""
    from synpp.pipeline import ValidateContext
    from braunschweig import synpp_tokens
    cache_path = "%s/%s.cache" % (working_directory, entry)
    config = stage["config"] if stage is not None else record.get("config", {})
    try:
        replayed, record_of_sources = _replay_legacy_token(module, ValidateContext(config, cache_path),
                                                           legacy_source)
    except Exception as error:  # reported per entry and counted; the entry stays legacy
        return "token_unverifiable", None, "legacy validate() replay failed (%s: %s)" % (type(error).__name__, error)
    if replayed != record["validation_token"]:
        return "token_mismatch", None, "the replayed legacy token differs from the stored token"
    changed = _not_equivalent(record_of_sources.served)
    if changed:
        return "token_mismatch", None, "hashed modules changed beyond the refactor: " + ", ".join(changed)
    if stage is not None:
        new_token = wrapper.validate(ValidateContext(stage["config"], cache_path))
        widened = _widened_modules(new_token, record_of_sources.requested)
        if widened:
            return ("token_mismatch", None, "token_coverage_widened: the new token hashes modules the "
                    "legacy token did not: " + ", ".join(widened))
        return "token_migrated", new_token, "registry"
    helpers = getattr(module, "_HELPER_MODULES", ())
    deferred = getattr(module, "_DEFERRED_HELPER_MODULE_NAMES", ())
    loop_token = synpp_tokens.source_token(helpers, deferred, stage_label=module.__name__ + ".validate()")
    try:
        current = wrapper.validate(ValidateContext(config, cache_path))
    except Exception as error:
        current = error
    if current != loop_token:
        return ("token_unverifiable", None,
                "not in the registry and the token is not a pure source loop (composite token)")
    widened = _widened_modules(loop_token, record_of_sources.requested)
    if widened:
        return ("token_mismatch", None, "token_coverage_widened: the new token hashes modules the "
                "legacy token did not: " + ", ".join(widened))
    return "token_migrated", loop_token, "off-registry pure loop"


def _widened_modules(new_token, requested) -> list:
    """Modules a structured new token digests that the legacy replay never asked for.

    The replay proves only the modules the legacy token covered; a module the refactor added to
    the token has no proof that its cache-time text equals the current one."""
    if not isinstance(new_token, dict) or not isinstance(new_token.get("modules"), dict):
        return []
    return sorted(set(new_token["modules"]) - set(requested))


def _is_source_hashing(module) -> bool:
    return hasattr(module, "_HELPER_MODULES") or hasattr(module, "_DEFERRED_HELPER_MODULE_NAMES")


def migrate(registry: dict, working_directory: str, meta: dict, *, dry_run: bool = False,
            legacy_source=None) -> dict:
    """Migrate ``meta`` in place (a copy when ``dry_run``) and return the counts (see COUNT_KEYS).

    The proof counts (module_hash_*, token_*, output_*) count proof outcomes per entry; an entry is
    written only when all of its proofs passed (``migrated``), otherwise it is ``kept_legacy``.

    When migrate raises (git failure, an unreadable or symlinked cache directory, ...), ``meta`` may
    already be partly mutated: the caller must discard it and must not write it. ``main`` never
    writes pipeline.json after an exception.
    """
    from braunschweig.synpp_fork import output_hash, staleness
    if legacy_source is None:
        legacy_source = GitLegacySource(REPO, LEGACY_SOURCE_REV)
    if dry_run:
        meta = copy.deepcopy(meta)
    counts = {key: 0 for key in COUNT_KEYS}
    wrappers = {stage["wrapper"].name: stage["wrapper"] for stage in registry.values()}
    migrated = set()
    for entry in sorted(meta):
        record = meta[entry]
        name = entry.rsplit("__", 1)[0]
        wrapper = wrappers.get(name)
        if wrapper is None:
            counts["not_in_dag"] += 1
            counts["kept_legacy"] += 1
            log.warning("[migrate] %s: kept legacy (stage %s is not in the DAG)", entry, name)
            continue
        if "fork" in record or "output_hash" in record:
            counts["already_migrated"] += 1
            log.info("[migrate] %s: already migrated, left unchanged", entry)
            continue
        stage = registry.get(entry)
        module = wrapper.instance
        candidate = copy.deepcopy(record)
        failures = []
        stored_module_hash = record.get("module_hash")
        if stored_module_hash is None or (not staleness.is_legacy_module_hash(stored_module_hash)
                                          and stored_module_hash != wrapper.module_hash):
            # Neither synpp 1.6.2's md5 nor the current digest (or absent): nothing to prove, and
            # run() would recompute it; never mark it migrated.
            counts["module_hash_mismatch"] += 1
            failures.append("module hash: %s" % ("missing" if stored_module_hash is None else
                                                 "neither a legacy md5 nor the current semantic digest"))
        elif staleness.is_legacy_module_hash(stored_module_hash):
            proven, reason = _prove_module_hash(module, stored_module_hash, legacy_source)
            if proven:
                if len(wrapper.module_hash) != 64:
                    raise RuntimeError("the registry carries md5 module hashes: build it with the fork "
                                       "installed (braunschweig.synpp_fork.install())")
                candidate["module_hash"] = wrapper.module_hash
                counts["module_hash_migrated"] += 1
            else:
                counts["module_hash_mismatch"] += 1
                failures.append("module hash: " + reason)
        if _is_source_hashing(module) and isinstance(record.get("validation_token"), str):
            status, token, reason = _prove_token(module, wrapper, stage, record, entry, working_directory,
                                                 legacy_source)
            counts[status] += 1
            if status == "token_migrated":
                candidate["validation_token"] = token
            else:
                failures.append("token: " + reason)
        else:
            counts["token_not_applicable"] += 1
        if failures:
            counts["kept_legacy"] += 1
            log.warning("[migrate] %s: kept legacy (%s)", entry, "; ".join(failures))
            continue
        if not staleness.artifacts_present(working_directory, entry):
            counts["output_missing"] += 1
            counts["kept_legacy"] += 1
            log.warning("[migrate] %s: kept legacy (cache artifacts %s.p / %s.cache missing)", entry, entry, entry)
            continue
        exclude = getattr(module, "_OUTPUT_HASH_EXCLUDE", ())
        candidate["output_hash"] = output_hash.hash_stage_output(working_directory, entry, exclude)
        counts["output_hashed"] += 1
        candidate["fork"] = {"format": 1}
        meta[entry] = candidate
        migrated.add(entry)
        counts["migrated"] += 1
        log.info("[migrate] %s: migrated (output hash over %d files, %d bytes)", entry,
                 candidate["output_hash"]["n_files"], candidate["output_hash"]["bytes"])
    for entry in sorted(migrated):
        record = meta[entry]
        filled = {}
        for parent, child_seen in sorted(record.get("dependencies", {}).items()):
            parent_record = meta.get(parent)
            if not staleness.has_output_hash(parent_record):
                counts["dependency_hashes_parent_legacy"] += 1
                log.info("[migrate] %s: parent %s has no output hash; the timestamp rule stays for it",
                         entry, parent)
            elif parent_record.get("updated") is None or parent_record["updated"] > child_seen:
                # Upstream rule: the child is stale for this parent. Recording the parent's current
                # digest would turn that into a hit.
                counts["dependency_hashes_skipped_stale"] += 1
                log.warning("[migrate] %s: parent %s was updated after the child; no parent digest "
                            "recorded (the child stays stale)", entry, parent)
            else:
                filled[parent] = parent_record["output_hash"]["digest"]
                counts["dependency_hashes_filled"] += 1
        record["dependency_output_hashes"] = filled
    return counts


def report(counts: dict, total: int) -> None:
    for key in COUNT_KEYS:
        log.info("[migrate] %s: %d", key, counts[key])
    rate = 100.0 * counts["migrated"] / total if total else 0.0
    line = "[migrate] migrated %d/%d (%.1f%%), kept legacy %d" % (counts["migrated"], total, rate,
                                                                   counts["kept_legacy"])
    if counts["already_migrated"]:
        line += ", already migrated %d" % counts["already_migrated"]
    log.info(line)
    if counts["kept_legacy"]:
        log.warning("[migrate] WARNING: %d entries kept legacy; the upstream timestamp rule decides them and "
                    "those with a changed module hash or token recompute (see the per-entry lines above)",
                    counts["kept_legacy"])


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("base")
    parser.add_argument("overlay", nargs="?")
    parser.add_argument("--working-directory")
    parser.add_argument("--legacy-source-rev", default=LEGACY_SOURCE_REV,
                        help="git revision whose sources the legacy cache entries were built with "
                             "(default: %(default)s)")
    parser.add_argument("--legacy-source-repo", default=str(REPO),
                        help="git repository holding that revision (default: this checkout)")
    parser.add_argument("--dry-run", action="store_true", help="compute and report, write nothing")
    args = parser.parse_args(argv)
    try:
        from braunschweig.synpp_fork import metadata, registry
        settings = registry.load_settings(args.base, args.overlay)
        working_directory = args.working_directory or settings.get("working_directory")
        if not working_directory:
            raise RuntimeError("no working_directory in the config and none given")
        if not os.path.isfile(os.path.join(working_directory, "pipeline.json")):
            raise RuntimeError("no pipeline.json in %s" % working_directory)
        legacy_source = GitLegacySource(args.legacy_source_repo, args.legacy_source_rev)
        stages = registry.build_registry_from_settings(settings)   # installs the fork first
        meta = metadata.read_meta(working_directory)
        counts = migrate(stages, working_directory, meta, dry_run=args.dry_run, legacy_source=legacy_source)
        report(counts, len(meta))
        if args.dry_run:
            log.info("[migrate] dry run: pipeline.json not written")
        else:
            metadata.write_meta(meta, working_directory)
            log.info("[migrate] pipeline.json rewritten (backup pipeline.json.bk)")
    except Exception as error:
        log.error("[migrate] error: %s: %s", type(error).__name__, error)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
