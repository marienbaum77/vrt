"""Observability subsystem: Prometheus + JSON export of the pipeline / verification metrics.

Metric catalogue (names follow the project brief)::

    vrt_diff_time_ms{algorithm}              histogram  wall time of one comparison
    vrt_memory_peak_mb{algorithm}            histogram  peak traced memory of one comparison
    vrt_false_positive_rate{algorithm,split} gauge      share of unchanged pairs wrongly flagged (labelled data)
    vrt_false_negative_rate{algorithm,split} gauge      share of real regressions missed       (labelled data)
    vrt_threshold_sensitivity{parameter}     gauge      share of verdicts that flip when the parameter moves +10 %
    vrt_comparisons_total{result}            counter    passed / failed / error comparisons
    vrt_capture_time_ms                      histogram  capture time (navigation + stabilisation + screenshots)
    vrt_capture_total{outcome}               counter    stable / unstable / error captures
    vrt_diff_pixels                          histogram  significant pixels per comparison
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest
from prometheus_client.exposition import CONTENT_TYPE_LATEST

from .profiling import summarize

TIME_BUCKETS = (10, 25, 50, 100, 200, 400, 800, 1600, 3200, 6400, 12800)
MEM_BUCKETS = (4, 8, 16, 32, 64, 128, 256, 512, 1024, 2048)
PIXEL_BUCKETS = (0, 10, 100, 1000, 10_000, 100_000, 1_000_000)


class Metrics:
    """One metrics registry (the API service owns a long-lived instance, the CLI a per-run one)."""

    content_type = CONTENT_TYPE_LATEST

    def __init__(self) -> None:
        self.registry = CollectorRegistry()
        self._lock = threading.Lock()
        self._obs: Dict[str, List[float]] = {"diff_time_ms": [], "memory_peak_mb": [], "capture_time_ms": []}
        self._gauges: Dict[str, Dict[str, float]] = {}
        r = self.registry
        self.diff_time = Histogram("vrt_diff_time_ms", "Wall time of one image comparison (ms)", ["algorithm"], buckets=TIME_BUCKETS, registry=r)
        self.memory_peak = Histogram("vrt_memory_peak_mb", "Peak traced memory of one comparison (MB)", ["algorithm"], buckets=MEM_BUCKETS, registry=r)
        self.fp_rate = Gauge("vrt_false_positive_rate", "False positive rate on labelled data", ["algorithm", "split"], registry=r)
        self.fn_rate = Gauge("vrt_false_negative_rate", "False negative rate on labelled data", ["algorithm", "split"], registry=r)
        self.sensitivity = Gauge("vrt_threshold_sensitivity", "Share of verdicts flipping when a parameter changes by +10%", ["parameter"], registry=r)
        self.comparisons = Counter("vrt_comparisons", "Comparisons by result", ["result"], registry=r)
        self.capture_time = Histogram("vrt_capture_time_ms", "Screenshot capture time (ms)", buckets=TIME_BUCKETS, registry=r)
        self.captures = Counter("vrt_capture", "Captures by outcome", ["outcome"], registry=r)
        self.diff_pixels = Histogram("vrt_diff_pixels", "Significant pixels per comparison", buckets=PIXEL_BUCKETS, registry=r)

    # ------------------------------------------------------------------ observations
    def observe_comparison(
        self,
        *,
        result: str,
        time_ms: float,
        memory_peak_mb: Optional[float] = None,
        diff_pixels: Optional[int] = None,
        algorithm: str = "vrt",
    ) -> None:
        with self._lock:
            self.comparisons.labels(result).inc()
            self.diff_time.labels(algorithm).observe(time_ms)
            self._obs["diff_time_ms"].append(float(time_ms))
            if memory_peak_mb is not None:
                self.memory_peak.labels(algorithm).observe(memory_peak_mb)
                self._obs["memory_peak_mb"].append(float(memory_peak_mb))
            if diff_pixels is not None:
                self.diff_pixels.observe(diff_pixels)

    def observe_capture(self, time_ms: float, stable: bool = True, error: bool = False) -> None:
        with self._lock:
            self.captures.labels("error" if error else ("stable" if stable else "unstable")).inc()
            if not error:
                self.capture_time.observe(time_ms)
                self._obs["capture_time_ms"].append(float(time_ms))

    def set_error_rates(self, algorithm: str, split: str, fp_rate: float, fn_rate: float) -> None:
        with self._lock:
            self.fp_rate.labels(algorithm, split).set(fp_rate)
            self.fn_rate.labels(algorithm, split).set(fn_rate)
            self._gauges.setdefault("false_positive_rate", {})[f"{algorithm}/{split}"] = fp_rate
            self._gauges.setdefault("false_negative_rate", {})[f"{algorithm}/{split}"] = fn_rate

    def set_threshold_sensitivity(self, parameter: str, value: float) -> None:
        with self._lock:
            self.sensitivity.labels(parameter).set(value)
            self._gauges.setdefault("threshold_sensitivity", {})[parameter] = value

    # ----------------------------------------------------------------------- export
    def render_prometheus(self) -> bytes:
        return generate_latest(self.registry)

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "diff_time_ms": summarize(self._obs["diff_time_ms"]),
                "memory_peak_mb": summarize(self._obs["memory_peak_mb"]),
                "capture_time_ms": summarize(self._obs["capture_time_ms"]),
                "false_positive_rate": dict(self._gauges.get("false_positive_rate", {})),
                "false_negative_rate": dict(self._gauges.get("false_negative_rate", {})),
                "threshold_sensitivity": dict(self._gauges.get("threshold_sensitivity", {})),
            }

    def export_json(self, path: Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.snapshot(), indent=2, ensure_ascii=False))
        return path

    def export_prometheus(self, path: Path) -> Path:
        """Write the text exposition format (usable with node_exporter's textfile collector)."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self.render_prometheus())
        return path


_GLOBAL: Optional[Metrics] = None


def global_metrics() -> Metrics:
    global _GLOBAL
    if _GLOBAL is None:
        _GLOBAL = Metrics()
    return _GLOBAL
