"""Symlink farm: make the gitignored inputs of a shared eqasim-data dir reachable from a worktree.

Usage: python3 symlink_farm.py <shared_eqasim_data> <worktree_eqasim_data>

Walks the shared dir top-down. An entry missing in the worktree is symlinked (a directory as a whole);
an entry present in both as directories is recursed into; an entry present in the worktree as a
file (a git-tracked file of the checked-out commit) is kept, so the worktree reads its OWN commit's
reference tables, not the shared checkout's. Top-level caches, outputs, popsim work dirs, resolved
configs and logs are excluded so the run writes them locally.
"""
import fnmatch
import os
import sys

EXCLUDE_TOP = ["cache_*", "output*", "popsim_work*", "matsim_archive", "analysis", "*.yml", "*.yaml",
               "*.log", "*.pid"]

shared, target = (os.path.abspath(p) for p in sys.argv[1:3])
linked = kept = 0


def farm(src, dst, top):
    global linked, kept
    for name in sorted(os.listdir(src)):
        if top and any(fnmatch.fnmatch(name, p) for p in EXCLUDE_TOP):
            continue
        s, d = os.path.join(src, name), os.path.join(dst, name)
        if not os.path.lexists(d):
            os.symlink(s, d)
            linked += 1
        elif os.path.isdir(s) and os.path.isdir(d) and not os.path.islink(d):
            farm(s, d, False)
        else:
            kept += 1


os.makedirs(target, exist_ok=True)
farm(shared, target, True)
print("symlink farm: %d links created, %d tracked entries kept from the worktree" % (linked, kept))
