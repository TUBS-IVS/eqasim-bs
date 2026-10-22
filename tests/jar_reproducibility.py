"""Build a jar stage twice and return the fork's output hash of each build (issue #474).

Early cutoff (ADR-0149) spares a stage's children only when the stage's output hash
(``braunschweig.synpp_fork.output_hash.hash_stage_output``, with the stage's own
``_OUTPUT_HASH_EXCLUDE``) is unchanged. A rebuild of a jar stage from the same inputs must
therefore reproduce that hash, also in another working directory (a fresh run, a moved
checkout). The helper lays each build out as synpp does (``<hash>.cache`` plus the pickled
return value in ``<hash>.p``) in two working directories of different path lengths, so an
embedded absolute path changes a file size, not only its bytes.
"""
import fnmatch
import os
import pickle

from braunschweig.synpp_fork.output_hash import hash_stage_output

STAGE_HASH = "jarstage"
WORKING_DIRECTORIES = ("a", "working_directory_with_a_longer_path")
#: Windows cannot start a process in a working directory longer than 258 characters
#: (CreateProcess, MAX_PATH; the error is a bare "WinError 267"), and Maven runs in a
#: subdirectory of the cache directory. A long --basetemp hits this before Maven starts.
WINDOWS_MAX_CACHE_PATH = 240


def build_twice(module, make_context, root):
    """Run ``module.execute`` once per working directory; return the two output hashes.

    ``make_context(path)`` returns a stage context whose ``path()`` is ``path``. Fails if the
    stage's exclude globs would hide the jar it returns: a test that excludes the jar
    itself would prove nothing.
    """
    exclude = getattr(module, "_OUTPUT_HASH_EXCLUDE", ())
    hashes = []
    for name in WORKING_DIRECTORIES:
        working_directory = os.path.join(str(root), name)
        cache = os.path.join(working_directory, STAGE_HASH + ".cache")
        if os.name == "nt" and len(os.path.abspath(cache)) > WINDOWS_MAX_CACHE_PATH:
            raise RuntimeError("cache directory %s has %d characters; Windows cannot start Maven "
                               "below it (MAX_PATH): use a shorter --basetemp"
                               % (cache, len(os.path.abspath(cache))))
        os.makedirs(cache)
        jar = module.execute(make_context(cache))
        assert os.path.isfile(os.path.join(cache, jar)), jar
        assert not any(fnmatch.fnmatchcase(jar, pattern) for pattern in exclude), (jar, exclude)
        with open(os.path.join(working_directory, STAGE_HASH + ".p"), "wb") as handle:
            pickle.dump(jar, handle)
        hashes.append(hash_stage_output(working_directory, STAGE_HASH, exclude))
    return hashes[0], hashes[1]
