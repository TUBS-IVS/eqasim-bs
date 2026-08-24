"""Tests for the scripts/verify_braunschweig_inputs.py --check-urls mode.

Covers the two pure, network-free parts of that mode:

- ``url_from_source``: which ``Input.source`` strings yield a checkable URL. The
  field is documentation prose in the general case, so this classification decides
  how much of the catalog the weekly CI check actually probes -- getting it wrong
  silently shrinks the check instead of failing it.
- ``probe_url``'s memoisation: several inputs legitimately share one source page
  (the six A3 ENTD files, both regionalstatistik tables, both Pendleratlas
  exports, both INKAR entries), so the probe must run ONCE per distinct URL. Tested
  with an injected cache, so no request is made.

The network half (HEAD -> ranged GET, retries) is deliberately not unit-tested
here: it is exercised end-to-end by running the mode, and mocking requests would
only assert the mock.
"""
import importlib.util
import os

import pytest

_SCRIPT_PATH = os.path.join(
    os.path.dirname(__file__), "..", "scripts", "verify_braunschweig_inputs.py"
)


def _load_script():
    spec = importlib.util.spec_from_file_location("verify_braunschweig_inputs", _SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("source,expected", [
    # A bare URL (A1/A2/B1 shape).
    ("https://www.kba.de/DE/Statistik/x_node.html",
     "https://www.kba.de/DE/Statistik/x_node.html"),
    # A URL carrying a query string must not be truncated (B1/B2 shape).
    ("https://www.regionalstatistik.de/genesis/online?operation=statistic&code=13111",
     "https://www.regionalstatistik.de/genesis/online?operation=statistic&code=13111"),
    # A URL followed by a parenthesised hint (B11/B12 shape): the hint is dropped,
    # the URL is still checked. Testing the WHOLE string for "no whitespace" would
    # misclassify these six catalog entries as prose.
    ("https://www.bmv.de/x.xlsx (run python scripts/download_regiostar.py)",
     "https://www.bmv.de/x.xlsx"),
    # Trailing punctuation must not become part of the URL.
    ("https://x.de, alternatively the mirror at https://y.de", "https://x.de"),
    ("https://x.de.", "https://x.de"),
    ("https://x.de;", "https://x.de"),
    # Pure prose -> no URL (B10 shape).
    ("infas mobility report - provided by ZGB / BMDV (non-commercial).", None),
    ("Generated locally by scripts/extract_mid_tables.py from B10.", None),
    # A URL that appears only LATER in the prose is NOT extracted: the leading token
    # is the documented convention, and scanning prose would fetch whatever an
    # unrelated sentence happens to mention.
    ("see https://x.de", None),
    # Degenerate inputs must not raise.
    ("", None),
    ("   ", None),
    # A non-http scheme is not a checkable download source.
    ("ftp://x.de/file.zip", None),
])
def test_url_from_source_classification(source, expected):
    verify = _load_script()
    assert verify.url_from_source(source) == expected


def test_url_from_source_resolves_every_catalog_entry_without_raising():
    """Every real INPUTS entry must classify cleanly (a URL or an explicit None)."""
    verify = _load_script()
    for inp in verify.INPUTS:
        result = verify.url_from_source(inp.source)
        assert result is None or result.startswith(("http://", "https://"))


def test_probe_url_is_memoised_per_distinct_url():
    """A second input sharing a source page reuses the probe instead of re-requesting.

    Guards the amplification defect: without this, the six A3 ENTD entries hit one
    French host six times with up to four requests each, and a single outage
    produced six identical required-unreachable lines.
    """
    verify = _load_script()
    url = "https://example.invalid/shared-source"
    # Pre-seeded cache: a real request would need the network, so a cache hit is the
    # only way this returns at all -- which is exactly what is being asserted.
    cache = {url: {"ok": True, "detail": "HEAD 200 (attempt 1)", "saw_http_status": True}}

    first = verify.probe_url(url, cache)
    second = verify.probe_url(url, cache)

    assert first["ok"] is True and second["ok"] is True
    # The reuse is visible in the output, not silent (CLAUDE.md fallback transparency).
    assert "reused for this URL" in first["detail"]
    assert "reused for this URL" in second["detail"]
    # The cached entry itself is not mutated by being read.
    assert cache[url]["detail"] == "HEAD 200 (attempt 1)"


def test_check_url_skips_restricted_and_generated_without_probing():
    """Skips are reported with a reason and never touch the network."""
    verify = _load_script()
    empty_cache: dict = {}

    restricted = verify.Input(
        name="X", rel_path="x", source="https://example.invalid/x", restricted=True)
    generated = verify.Input(
        name="Y", rel_path="y", source="https://example.invalid/y", generated=True)
    prose = verify.Input(
        name="Z", rel_path="z", source="obtained by hand from the agency")

    for inp, reason in [(restricted, "restricted delivery"),
                        (generated, "generated locally"),
                        (prose, "not-a-URL")]:
        result = verify.check_url(inp, empty_cache)
        assert result["status"] == "SKIPPED"
        assert reason in result["detail"]
        assert result["url"] == ""
    # No probing happened at all, so nothing was cached.
    assert empty_cache == {}
