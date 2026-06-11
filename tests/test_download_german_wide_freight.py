import gzip
from pathlib import Path

from scripts.download_german_wide_freight import (
    BASE_URL, FILES, plan_downloads, write_readme,
)


def test_plan_downloads_builds_url_and_target_pairs(tmp_path):
    plans = plan_downloads(tmp_path)
    assert len(plans) == len(FILES)
    for (url, target), name in zip(plans, FILES):
        assert url == BASE_URL + name
        assert target == tmp_path / name


def test_plan_downloads_skips_existing_nonempty_file(tmp_path):
    existing = tmp_path / FILES[0]
    with gzip.open(existing, "wb") as f:
        f.write(b"x" * 1024)
    plans = plan_downloads(tmp_path)
    assert all(target.name != FILES[0] for _url, target in plans)


def test_write_readme_records_provenance(tmp_path):
    write_readme(tmp_path)
    text = (tmp_path / "README.md").read_text(encoding="utf-8")
    assert "german-wide-freight" in text
    assert "10.1016/j.procs.2022.03.080" in text
    assert BASE_URL in text
