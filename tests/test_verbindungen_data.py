"""Tests for the VerBindungen data layer (#124 P1).

Covers the download-script pure helpers, the loader parsing helpers and the
loader stage invariants on small synthetic fixtures. No network, no large data.

Run with::

    python -m pytest tests/test_verbindungen_data.py -v
"""
from __future__ import annotations

import os
import pathlib
import sys

import pandas as pd
import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))


# --- download script helpers ------------------------------------------------

def test_needs_download_missing_file(tmp_path):
    from download_verbindungen import needs_download
    target = tmp_path / "absent.csv"
    assert needs_download(str(target), expected_sha256=None, force=False) is True


def test_needs_download_present_no_hash_pinned(tmp_path):
    from download_verbindungen import needs_download
    target = tmp_path / "present.csv"
    target.write_bytes(b"hello")
    # No pinned hash yet (first acquisition): keep the existing file.
    assert needs_download(str(target), expected_sha256=None, force=False) is False


def test_needs_download_hash_mismatch_triggers_redownload(tmp_path):
    from download_verbindungen import needs_download, _sha256_of
    target = tmp_path / "present.csv"
    target.write_bytes(b"hello")
    good = _sha256_of(str(target))
    assert needs_download(str(target), expected_sha256=good, force=False) is False
    assert needs_download(str(target), expected_sha256="0" * 64, force=False) is True
    assert needs_download(str(target), expected_sha256=good, force=True) is True


def test_render_provenance_contains_all_fields():
    from download_verbindungen import render_provenance
    entries = [dict(
        filename="QZM-Berufspendler-VerBindungen-Verkehrszellen.csv",
        offer_id="767413386339078144",
        url="https://mobilithek.info/mdp-api/files/aux/767413386339078144/QZM-Berufspendler-VerBindungen-Verkehrszellen.csv",
        sha256="abc123",
        size_bytes=5426330,
        downloaded_at="2026-07-15T12:00:00",
    )]
    text = render_provenance(entries)
    assert "QZM-Berufspendler-VerBindungen-Verkehrszellen.csv" in text
    assert "767413386339078144" in text
    assert "abc123" in text
    assert "31.12.2019" in text          # reference date note
    assert "LICENSE_FREE_USE_OPEN_DATA" in text
