import os
import stat

import pytest


@pytest.fixture
def entry(tmp_path):
    (tmp_path / "s__h.p").write_bytes(b"pickle-bytes")
    cache = tmp_path / "s__h.cache"
    (cache / "sub").mkdir(parents=True)
    (cache / "a.csv").write_bytes(b"1;2\n")
    (cache / "sub/b.log").write_bytes(b"ts 1\n")
    return str(tmp_path), "s__h", cache


def _symlink_or_skip(target, link, target_is_directory=False):
    try:
        os.symlink(str(target), str(link), target_is_directory=target_is_directory)
    except (OSError, NotImplementedError) as error:
        pytest.skip("cannot create symlinks here: %s" % error)


def test_digest_is_deterministic_and_counts_files(entry):
    from braunschweig.synpp_fork import output_hash
    first = output_hash.hash_stage_output(*entry[:2])
    assert first == output_hash.hash_stage_output(*entry[:2])
    assert first["format"] == 1 and first["n_files"] == 2 and first["bytes"] == len(b"pickle-bytes") + 4 + 5


def test_file_edit_changes_files_not_pickle(entry):
    from braunschweig.synpp_fork import output_hash
    working, stage_hash, cache = entry
    before = output_hash.hash_stage_output(working, stage_hash)
    (cache / "a.csv").write_bytes(b"1;3\n")
    after = output_hash.hash_stage_output(working, stage_hash)
    assert after["pickle"] == before["pickle"] and after["files"] != before["files"] and after["digest"] != before["digest"]


def test_exclude_globs_skip_matching_relative_paths(entry):
    from braunschweig.synpp_fork import output_hash
    working, stage_hash, cache = entry
    before = output_hash.hash_stage_output(working, stage_hash, ("*.log", "sub/*"))
    (cache / "sub/b.log").write_bytes(b"ts 2\n")
    assert output_hash.hash_stage_output(working, stage_hash, ("*.log", "sub/*")) == before
    assert before["n_files"] == 1


def test_missing_artifacts_and_bad_excludes_raise(entry):
    from braunschweig.synpp_fork import output_hash
    working, stage_hash, _ = entry
    with pytest.raises(TypeError):
        output_hash.hash_stage_output(working, stage_hash, ["*.log"])
    with pytest.raises(FileNotFoundError):
        output_hash.hash_stage_output(working, "s__missing")


@pytest.mark.skipif(os.name == "nt", reason="file permission bits are not enforced on Windows")
def test_hash_stage_output_raises_on_unreadable_file(entry):
    from braunschweig.synpp_fork import output_hash
    working, stage_hash, cache = entry
    os.chmod(cache / "a.csv", 0)
    try:
        with pytest.raises(PermissionError):
            output_hash.hash_stage_output(working, stage_hash)
    finally:
        os.chmod(cache / "a.csv", stat.S_IRUSR | stat.S_IWUSR)


@pytest.mark.skipif(os.name == "nt", reason="directory permission bits are not enforced on Windows")
def test_hash_stage_output_raises_on_unreadable_subdirectory(entry):
    from braunschweig.synpp_fork import output_hash
    working, stage_hash, cache = entry
    os.chmod(cache / "sub", 0)
    try:
        if os.access(cache / "sub", os.R_OK):
            pytest.skip("permission bits are not enforced for this user (e.g. root)")
        with pytest.raises(PermissionError):
            output_hash.hash_stage_output(working, stage_hash)
    finally:
        os.chmod(cache / "sub", stat.S_IRWXU)


def test_symlinked_file_inside_the_cache_directory_raises(entry, tmp_path):
    from braunschweig.synpp_fork import output_hash
    working, stage_hash, cache = entry
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"x")
    _symlink_or_skip(outside, cache / "link.txt")
    with pytest.raises(OSError, match="symlink"):
        output_hash.hash_stage_output(working, stage_hash)


def test_symlinked_subdirectory_inside_the_cache_directory_raises(entry, tmp_path):
    from braunschweig.synpp_fork import output_hash
    working, stage_hash, cache = entry
    outside = tmp_path / "outside_dir"
    outside.mkdir()
    (outside / "f.txt").write_bytes(b"x")
    _symlink_or_skip(outside, cache / "linkdir", target_is_directory=True)
    with pytest.raises(OSError, match="symlink"):
        output_hash.hash_stage_output(working, stage_hash)


def test_symlinked_cache_directory_hashes_like_the_real_directory(entry, tmp_path):
    from braunschweig.synpp_fork import output_hash
    working, stage_hash, cache = entry
    real = output_hash.hash_stage_output(working, stage_hash)
    farm = tmp_path / "farm"
    farm.mkdir()
    (farm / "s__h.p").write_bytes(b"pickle-bytes")
    _symlink_or_skip(cache, farm / "s__h.cache", target_is_directory=True)
    assert output_hash.hash_stage_output(str(farm), stage_hash) == real
