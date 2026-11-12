"""Semantic source digests for synpp stage hashes and ``validate()`` tokens.

synpp 1.6.2 keys a stage's cache on the md5 of its module's raw text and the project's
helper tokens did the same over whole import closures (ADR-0136), so a comment in any of
up to 85 modules recomputed hours of work (#344). This module digests the AST instead:
comments, blank lines, formatting and line numbers never reach the AST; docstrings are
removed; constants, decorators, annotations, defaults and the helper tuples stay in, so
every behaviour-relevant edit still changes the digest. ``ast.dump`` output is specific to
the Python minor version, which the token records: a Python upgrade is a deliberate
one-time recompute. Spec: docs/superpowers/specs/2026-10-16-content-addressed-stage-cache-design.md.
"""
from __future__ import annotations

import ast
import hashlib
import importlib
import inspect
import sys
import textwrap

TOKEN_FORMAT = 2

#: Read by scripts/audit_synpp_helper_hash.py (ADR-0136): every source-hashing stage imports this
#: module to BUILD its token, so no stage result depends on it. A change of the digest algorithm
#: is made visible by bumping TOKEN_FORMAT, which differs in every stored token.
_SYNPP_TOKEN_EXEMPTION = (
    "builds the structured stage token; a digest change bumps TOKEN_FORMAT (#468)"
)


def python_tag() -> str:
    return "%d.%d" % sys.version_info[:2]


def _strip_docstrings(tree: ast.AST) -> ast.AST:
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if (body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                node.body = body[1:] or [ast.Pass()]
    return tree


def semantic_module_digest(source: str) -> str:
    """SHA-256 of the docstring-free AST dump of ``source`` (a module or a dedentable block)."""
    try:
        parsed = ast.parse(source)
    except IndentationError:
        # An indented class/function block from inspect.getsource. dedent is applied only
        # here: it rewrites whitespace-only lines, which would alter string literals.
        parsed = ast.parse(textwrap.dedent(source))
    tree = _strip_docstrings(parsed)
    canonical = ast.dump(tree, annotate_fields=True, include_attributes=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _deferred_module(module_name: str, stage_label: str):
    try:
        module = importlib.import_module(module_name)
        inspect.getsource(module)
    except Exception as error:
        raise RuntimeError(
            f"{stage_label}: cannot hash the deferred helper module {module_name!r} "
            f"({type(error).__name__}: {error}). It is a direct dependency of this stage's result, "
            "so it must be importable with a retrievable source; fix the module (or remove it from "
            "_DEFERRED_HELPER_MODULE_NAMES if the dependency is genuinely gone) -- it must not be "
            "skipped, because skipping it would silently reuse stale cached output.") from error
    return module


def source_token(helper_modules, deferred_module_names, *, stage_label: str = "validate()") -> dict:
    """Structured validation token: one semantic digest per helper module, keyed by dotted name."""
    modules = {}
    for module in helper_modules:
        try:
            source = inspect.getsource(module)
        except Exception as error:
            raise RuntimeError(
                f"{stage_label}: cannot retrieve the source of helper module "
                f"{getattr(module, '__name__', module)!r} ({type(error).__name__}: {error}); "
                "a helper module without source must not be skipped.") from error
        modules[module.__name__] = semantic_module_digest(source)
    for module_name in deferred_module_names:
        module = _deferred_module(module_name, stage_label)
        modules[module.__name__] = semantic_module_digest(inspect.getsource(module))
    return {"format": TOKEN_FORMAT, "python": python_tag(), "modules": modules}


#: Read size of ``file_sha256``: the VG250 archive and the LoD2 parquet are too large to hold twice in memory.
_FILE_CHUNK_BYTES = 1 << 20


def file_sha256(path) -> str:
    """sha256 hex of the file's raw bytes, read in 1 MiB chunks.

    Use it for binary or downloaded inputs (archives, parquet, polygon downloads), where every
    byte is content. Raises ``FileNotFoundError`` for a missing file; a caller for which a
    missing file is legal checks for it first.
    """
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(_FILE_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def text_file_sha256(path) -> str:
    """sha256 hex of a COMMITTED text file as git stores it: CRLF line endings become LF.

    Use it for tables that are committed to the repository: a Windows checkout with
    ``core.autocrlf`` holds CRLF while a Linux checkout of the same commit holds LF, and one
    commit must give one token. Do not use it for downloaded or binary files (``file_sha256``).
    The shared definition for stage code: ``content_sha256`` of
    ``braunschweig.data.vrb.fare_model_export`` and of ``braunschweig.parking.tariff_export``
    and the jar-source digest of ``matsim.runtime.eqasim`` delegate here, so a provenance record
    and a cache token of the same committed file always agree. Raises
    ``FileNotFoundError`` for a missing file; a caller for which a missing file is legal checks
    for it first.
    """
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read().replace(b"\r\n", b"\n")).hexdigest()


def legacy_source_token(helper_modules, deferred_module_names) -> str:
    """The pre-2026-10 md5-over-raw-text token; used by the cache migration only."""
    digest = hashlib.md5()
    for module in helper_modules:
        digest.update(inspect.getsource(module).encode("utf-8"))
    for module_name in deferred_module_names:
        digest.update(inspect.getsource(importlib.import_module(module_name)).encode("utf-8"))
    return digest.hexdigest()
