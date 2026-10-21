"""Latency / memory probes used for the ``diff_time_ms`` and ``memory_peak_mb`` metrics."""
from __future__ import annotations

import gc
import os
import resource
import statistics
import time
import tracemalloc
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple


def rss_mb() -> float:
    """Current resident set size of this process (MB)."""
    try:
        import psutil

        return psutil.Process(os.getpid()).memory_info().rss / 2**20
    except Exception:  # pragma: no cover
        with open("/proc/self/statm") as fh:
            return int(fh.read().split()[1]) * os.sysconf("SC_PAGE_SIZE") / 2**20


def peak_rss_mb() -> float:
    """Peak RSS of this process since start (Linux: ru_maxrss is in KiB)."""
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


@dataclass
class Profile:
    time_ms: float  # median wall time of the timed runs
    time_ms_all: List[float] = field(default_factory=list)
    memory_peak_mb: Optional[float] = None  # tracemalloc peak of one extra run (Python + NumPy buffers)
    rss_peak_mb: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "time_ms": round(self.time_ms, 3),
            "memory_peak_mb": None if self.memory_peak_mb is None else round(self.memory_peak_mb, 3),
            "rss_peak_mb": round(self.rss_peak_mb, 1),
        }


def profile_call(fn: Callable[[], Any], repeat: int = 1, memory: bool = True) -> Tuple[Any, Profile]:
    """Run ``fn`` and measure it.

    Timing runs are done *without* tracemalloc (it slows allocation-heavy code); the peak memory is
    measured on one additional traced run.  NumPy registers its buffers with tracemalloc, so the
    figure covers all image arrays created by the comparison.
    """
    times: List[float] = []
    result = None
    for _ in range(max(1, repeat)):
        t0 = time.perf_counter()
        result = fn()
        times.append((time.perf_counter() - t0) * 1000.0)
    peak = None
    if memory:
        gc.collect()
        tracemalloc.start()
        try:
            fn()
            peak = tracemalloc.get_traced_memory()[1] / 2**20
        finally:
            tracemalloc.stop()
    return result, Profile(statistics.median(times), times, peak, peak_rss_mb())


def measure_peak_memory(fn: Callable[[], Any]) -> float:
    """Peak traced memory (MB) of one call of ``fn`` (tracemalloc: Python objects + NumPy buffers)."""
    gc.collect()
    tracemalloc.start()
    try:
        fn()
        return tracemalloc.get_traced_memory()[1] / 2**20
    finally:
        tracemalloc.stop()


def percentile(values: List[float], q: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * q / 100.0
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def summarize(values: List[float]) -> Dict[str, float]:
    if not values:
        return {"count": 0}
    return {
        "count": len(values),
        "mean": round(statistics.fmean(values), 3),
        "p50": round(percentile(values, 50), 3),
        "p95": round(percentile(values, 95), 3),
        "p99": round(percentile(values, 99), 3),
        "max": round(max(values), 3),
    }
