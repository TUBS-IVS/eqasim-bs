"""Tests for scripts.report_stage_hash_impact: cache-listing parsing.

A raw ``ls <working_directory>`` listing contains both the pickled payload
(``<stage>__<hash>.p``) and its companion cache directory
(``<stage>__<hash>.cache``) for every stored variant. ``read_listing`` must
normalise both to the same basename so counting entries does not double-count
a variant that is present as both files.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.report_stage_hash_impact import read_listing


def test_read_listing_drops_cache_dirs_and_strips_p_suffix(tmp_path):
    listing_path = tmp_path / "cache_entries.txt"
    listing_path.write_text("X__a.p\nX__a.cache\nY\n", encoding="utf-8")

    assert read_listing(str(listing_path)) == {"X__a", "Y"}


def test_read_listing_returns_empty_set_for_missing_path():
    assert read_listing(None) == set()
