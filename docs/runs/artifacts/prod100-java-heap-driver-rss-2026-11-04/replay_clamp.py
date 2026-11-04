"""What the driver-aware heap clamp returns for measured drivers (read-only; docs/runs/prod100-java-heap-driver-rss-2026-11-04.yml).

Usage (from the repository root, in the pipeline environment):
    python <this file> <memory_total_kb> <java_memory> <label>=<driver_rss_kb> [<label>=<driver_rss_kb> ...]

Runs the code under review, braunschweig.java_heap.effective_java_heap, with the machine total and the root
process RSS of recorder samples (kB, as the recorder reports them; GiB = kB / (1024 * 1024)), and prints next to
each result what the machine-only clamp, braunschweig.resources.effective_java_memory, returns for the same
machine. Nothing is derived by hand: the log lines are the code's own output.
"""
import logging
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO))

from braunschweig import java_heap, resources  # noqa: E402

KIB_PER_GIB = 1024.0 * 1024.0


def main(memory_total_kb, java_memory, samples):
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
    machine = resources.MachineResources(
        cores=64, memory_gb=memory_total_kb / KIB_PER_GIB, cores_source="recorder",
        memory_source="recorder memory_total_kb")
    print("machine: %d kB = %.4f GiB, configured java_memory %s" % (
        memory_total_kb, machine.memory_gb, java_memory))
    for label, rss_kb in samples:
        driver_gb = rss_kb / KIB_PER_GIB
        print("--- %s: driver RSS %d kB = %.4f GiB" % (label, rss_kb, driver_gb))
        aware = java_heap.effective_java_heap(java_memory, machine=machine, env={}, driver_rss_gb=driver_gb)
        machine_only = resources.effective_java_memory(java_memory, machine=machine, env={})
        print("    driver-aware -Xmx%s, machine-only -Xmx%s" % (aware, machine_only))


if __name__ == "__main__":
    main(int(sys.argv[1]), sys.argv[2],
         [(item.split("=")[0], int(item.split("=")[1])) for item in sys.argv[3:]])
