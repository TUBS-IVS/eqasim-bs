import hashlib
import importlib
import inspect
import sys
import textwrap

import pytest


def test_comment_docstring_and_formatting_edits_keep_the_digest():
    from braunschweig import synpp_tokens
    a = 'def f(x):\n    """doc"""\n    return x + 1  # comment\n'
    b = '# header comment\n\ndef f(x):\n\n    return (x +\n            1)\n'
    assert synpp_tokens.semantic_module_digest(a) == synpp_tokens.semantic_module_digest(b)


def test_body_and_constant_edits_change_the_digest():
    from braunschweig import synpp_tokens
    base = synpp_tokens.semantic_module_digest("X = 1\ndef f(x):\n    return x + X\n")
    assert synpp_tokens.semantic_module_digest("X = 2\ndef f(x):\n    return x + X\n") != base
    assert synpp_tokens.semantic_module_digest("X = 1\ndef f(x):\n    return x - X\n") != base


def test_class_and_module_docstrings_are_stripped_but_other_strings_stay():
    from braunschweig import synpp_tokens
    with_doc = '"""module doc"""\nclass C:\n    """class doc"""\n    NAME = "kept"\n'
    without = 'class C:\n    NAME = "kept"\n'
    assert synpp_tokens.semantic_module_digest(with_doc) == synpp_tokens.semantic_module_digest(without)
    assert synpp_tokens.semantic_module_digest(without) != synpp_tokens.semantic_module_digest('class C:\n    NAME = "changed"\n')


# Pinned in the eqasim env (Python 3.10, synpp 1.6.2) on 2026-10-21. ast.dump is specific to the
# Python minor version, so the literal is only asserted on the interpreter it was computed with.
_GOLDEN_SOURCE = '"""doc"""\nX = 1\n\n\ndef f(x):\n    """doc"""\n    return x + X\n'
_GOLDEN_DIGEST = "edc913dc2bb4aaf77a5fd42bdf0b1b50620e82a3bc7beb218858d37637cceff0"
_GOLDEN_PYTHON = "3.10"


def test_golden_digest_of_a_fixed_snippet_is_pinned():
    """A change of this digest without a TOKEN_FORMAT bump would silently keep stale caches valid;
    bump TOKEN_FORMAT and update this pin together."""
    from braunschweig import synpp_tokens
    if synpp_tokens.python_tag() != _GOLDEN_PYTHON:
        pytest.skip("ast.dump differs between Python minors; the golden digest was computed on "
                    + _GOLDEN_PYTHON + ", this is " + synpp_tokens.python_tag())
    assert synpp_tokens.semantic_module_digest(_GOLDEN_SOURCE) == _GOLDEN_DIGEST


def test_source_token_has_exactly_the_documented_shape(helper_modules):
    from braunschweig import synpp_tokens
    token = synpp_tokens.source_token((helper_modules,), ())
    assert token == {"format": synpp_tokens.TOKEN_FORMAT, "python": synpp_tokens.python_tag(),
                     "modules": {"tok_helper_a": synpp_tokens.semantic_module_digest("A = 1\n")}}
    assert synpp_tokens.TOKEN_FORMAT == 2   # moves together with _GOLDEN_DIGEST, never alone


@pytest.fixture
def helper_modules(tmp_path, monkeypatch):
    (tmp_path / "tok_helper_a.py").write_text("A = 1\n", encoding="utf-8")
    (tmp_path / "tok_helper_b.py").write_text("B = 2\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    for name in ("tok_helper_a", "tok_helper_b"):
        monkeypatch.delitem(sys.modules, name, raising=False)
    importlib.invalidate_caches()
    return importlib.import_module("tok_helper_a")


def test_source_token_keys_modules_by_dotted_name_and_dedupes(helper_modules):
    from braunschweig import synpp_tokens
    token = synpp_tokens.source_token((helper_modules,), ("tok_helper_b", "tok_helper_a"))
    assert token["format"] == 2 and token["python"] == synpp_tokens.python_tag()
    assert sorted(token["modules"]) == ["tok_helper_a", "tok_helper_b"]
    assert token == synpp_tokens.source_token((helper_modules,), ("tok_helper_b", "tok_helper_a"))


def test_a_missing_deferred_module_raises_and_names_it(helper_modules):
    from braunschweig import synpp_tokens
    with pytest.raises(RuntimeError, match="tok_missing"):
        synpp_tokens.source_token((helper_modules,), ("tok_missing",), stage_label="probe")


def test_legacy_source_token_equals_the_old_md5_loop(helper_modules):
    from braunschweig import synpp_tokens
    digest = hashlib.md5()
    digest.update(inspect.getsource(helper_modules).encode("utf-8"))
    digest.update(inspect.getsource(importlib.import_module("tok_helper_b")).encode("utf-8"))
    assert synpp_tokens.legacy_source_token((helper_modules,), ("tok_helper_b",)) == digest.hexdigest()


def test_whitespace_only_line_inside_a_string_literal_changes_the_digest():
    from braunschweig import synpp_tokens
    spaced = 'X = """a\n   \nb"""\n'
    empty = 'X = """a\n\nb"""\n'
    assert synpp_tokens.semantic_module_digest(spaced) != synpp_tokens.semantic_module_digest(empty)


def test_an_indented_block_still_digests():
    from braunschweig import synpp_tokens
    block = '    def m(self):\n        """doc"""\n        return 1\n'
    flat = 'def m(self):\n    return 1\n'
    assert synpp_tokens.semantic_module_digest(block) == synpp_tokens.semantic_module_digest(flat)


def test_a_helper_module_without_source_raises_runtime_error_naming_it():
    import types
    from braunschweig import synpp_tokens
    with pytest.raises(RuntimeError, match="tok_no_source") as info:
        synpp_tokens.source_token((types.ModuleType("tok_no_source"),), (), stage_label="probe")
    assert "probe" in str(info.value) and info.value.__cause__ is not None
