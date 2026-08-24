"""Run resource recording for long server runs (issue #350).

The pipeline used to be judged and tuned from snapshots taken by hand: a
``ps --sort=-rss | head -6`` sample declared a healthy ``secondary_chainsolvers``
stage deadlocked (it had in fact finished three minutes earlier), and the peak
memory per PopulationSim worker -- the datum needed to tune ``num_workers``
(issue #281) -- was gone with the processes by the time the question was asked.

This package records instead of guessing: :mod:`process_tree` provides the
whole-tree primitives (one CPU number a busy worker cannot hide outside the
sampling window, plus per-process ``VmHWM`` so a phase's true memory peak
survives the process), :mod:`sampler` turns them into one flat sample together
with system RAM/swap/load/disk/IO and the liveness of the log being written,
:mod:`recorder` appends those samples to a per-run JSONL time series in the
run's own directory, and :mod:`summary` reduces a finished series to the fields
a run manifest wants.
"""
