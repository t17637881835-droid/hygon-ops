"""Prometheus 风格指标收集器"""
import time
from threading import Lock
from dataclasses import dataclass, field
from typing import Dict


@dataclass
class Counter:
    """简单计数器"""
    value: int = 0
    lock: Lock = field(default_factory=Lock)

    def increment(self, n: int = 1):
        with self.lock:
            self.value += n

    def get(self) -> int:
        return self.value


@dataclass
class Histogram:
    """简单直方图（用于延迟）"""
    buckets: Dict[int, int] = field(default_factory=dict)
    lock: Lock = field(default_factory=Lock)
    bucket_boundaries: tuple = (0.1, 0.5, 1.0, 2.0, 5.0, 10.0)

    def observe(self, value: float):
        with self.lock:
            for boundary in self.bucket_boundaries:
                if value <= boundary:
                    self.buckets[boundary] = self.buckets.get(boundary, 0) + 1
                    break

    def get(self) -> Dict:
        return dict(self.buckets)


class MetricsCollector:
    """应用指标收集器"""

    def __init__(self):
        self.counters: Dict[str, Counter] = {}
        self.histograms: Dict[str, Histogram] = {}
        self._start_time = time.time()

    def counter(self, name: str) -> Counter:
        if name not in self.counters:
            self.counters[name] = Counter()
        return self.counters[name]

    def histogram(self, name: str) -> Histogram:
        if name not in self.histograms:
            self.histograms[name] = Histogram()
        return self.histograms[name]

    def increment(self, name: str, n: int = 1):
        self.counter(name).increment(n)

    def observe(self, name: str, value: float):
        self.histogram(name).observe(value)

    def get_all(self) -> Dict:
        uptime = time.time() - self._start_time
        result = {
            "uptime_seconds": round(uptime, 2),
            "counters": {},
            "histograms": {}
        }
        for name, counter in self.counters.items():
            result["counters"][name] = counter.get()
        for name, hist in self.histograms.items():
            result["histograms"][name] = hist.get()
        return result

    def render_prometheus(self) -> str:
        """渲染为 Prometheus 文本格式"""
        lines = []
        metrics = self.get_all()

        # uptime
        lines.append(f"# HELP app_uptime_seconds Application uptime in seconds")
        lines.append(f"# TYPE app_uptime_seconds gauge")
        lines.append(f"app_uptime_seconds {metrics['uptime_seconds']}")

        # counters
        for name, value in metrics["counters"].items():
            safe_name = name.replace(".", "_").replace("-", "_")
            lines.append(f"# HELP app_{safe_name} Counter {name}")
            lines.append(f"# TYPE app_{safe_name} counter")
            lines.append(f"app_{safe_name} {value}")

        # histograms
        for name, buckets in metrics["histograms"].items():
            safe_name = name.replace(".", "_").replace("-", "_")
            cumulative = 0
            for boundary, count in buckets.items():
                cumulative += count
                lines.append(f"app_{safe_name}_bucket{{le=\"{boundary}\"}} {cumulative}")
            lines.append(f"app_{safe_name}_bucket{{le=\"+Inf\"}} {cumulative}")
            lines.append(f"# TYPE app_{safe_name} histogram")

        return "\n".join(lines)


# 全局指标收集器
_metrics = MetricsCollector()


def metrics():
    return _metrics


def get_metrics() -> Dict:
    return _metrics.get_all()
