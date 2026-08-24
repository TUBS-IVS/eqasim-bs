"""Guard: no German PT-ticket literal survives outside the raw-CSV boundary.

The PT ticket taxonomy was renamed to English (issue #329); the ONLY place the
codebook-German names may appear in code is the boundary mapping in
braunschweig/data/mid/reference_tables.py (raw committed CSVs keep German
headers for provenance). 'anderes'/'keine_angabe' appear legitimately in OTHER
survey contexts, so this guard checks only the unambiguous PT-ticket literals.
"""
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
FORBIDDEN = (
    "fahre_nie", "einzelfahrschein", "mehrfachkarte",
    "wochen_monat_ohne_abo", "monat_abo_jahreskarte", "jobticket_semesterticket",
)
ALLOWED_FILES = {
    REPO / "braunschweig" / "data" / "mid" / "reference_tables.py",
    Path(__file__).resolve(),
}
# Tests that deliberately rebuild a RAW committed CSV (codebook-German headers)
# opt out with this marker comment near their fixture.
FIXTURE_MARKER = "PT_RAW_FIXTURE_OK"
SCAN_DIRS = ("braunschweig", "scripts", "matsim", "synthesis", "eqasim_common", "tests")
# Per-feature METHOD documents describe the CURRENT implementation, so a German
# ticket literal there is stale documentation, not provenance -- scan them too
# (docs/features/mid-reference-tables.md still named the German set after the
# #329 rename, which is what motivated this second guard).
#
# docs/decisions/ and docs/runs/ are DELIBERATELY excluded and must stay so:
# ADRs and run manifests are immutable history. They legitimately quote the
# pre-#329 German category names to record what the code did at the time and
# what the rename changed; rewriting them would falsify the record. The same
# goes for the committed reference CSVs, whose German headers are the
# traceability link to the MiD instrument (see the module docstring).
SCAN_DOC_DIRS = ("docs/features",)


def test_no_german_pt_ticket_literals_outside_boundary():
    offenders = []
    scanned = 0
    for top in SCAN_DIRS:
        base = REPO / top
        if not base.exists():
            continue
        for path in base.rglob("*.py"):
            scanned += 1
            if path in ALLOWED_FILES:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if FIXTURE_MARKER in text:
                continue
            hits = [w for w in FORBIDDEN if w in text]
            if hits:
                offenders.append((str(path.relative_to(REPO)), hits))
    assert not offenders, f"German PT-ticket literals outside the boundary: {offenders}"
    # Tripwire, not a coverage target: if REPO/SCAN_DIRS ever stop resolving
    # (file moved, layout change) this guard must fail loudly instead of
    # passing vacuously on an empty scan. 100 is far below the repo's actual
    # Python file count (~1000+) and only guards against a silently empty scan.
    assert scanned > 100, (
        f"only scanned {scanned} files -- REPO/SCAN_DIRS may have stopped "
        "resolving, which would make the assertion above pass vacuously"
    )


def test_no_german_pt_ticket_literals_in_feature_method_docs():
    """Same guard for the per-feature METHOD documents (see SCAN_DOC_DIRS).

    docs/decisions/ and docs/runs/ are excluded on purpose: they are history and
    legitimately carry the pre-#329 German names.
    """
    offenders = []
    scanned = 0
    for top in SCAN_DOC_DIRS:
        base = REPO / top
        assert base.is_dir(), f"{top} does not resolve -- the guard would pass vacuously"
        for path in sorted(base.glob("*.md")):
            scanned += 1
            text = path.read_text(encoding="utf-8", errors="replace")
            hits = [w for w in FORBIDDEN if w in text]
            if hits:
                offenders.append((str(path.relative_to(REPO)), hits))
    assert not offenders, f"stale German PT-ticket literals in method docs: {offenders}"
    # Tripwire against an empty glob (directory renamed / files moved).
    assert scanned > 0, f"scanned no method docs in {SCAN_DOC_DIRS}"
