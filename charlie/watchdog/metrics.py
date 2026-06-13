"""
Metrics collector — exposes Prometheus-format metrics for Charlie.

Format: text/plain; version=0.0.4
https://prometheus.io/docs/instrumenting/exposition_formats/

Metrics exposed:
- charlie_tool_calls_total{tool, status}: counter
- charlie_tool_call_duration_seconds{tool}: summary
- charlie_agent_invocations_total{agent, status}: counter
- charlie_circuit_breaker_state{agent}: gauge
- charlie_memory_usage_bytes: gauge
- charlie_uptime_seconds: gauge
"""

from __future__ import annotations

import time
from collections import defaultdict
from threading import Lock


class MetricsCollector:
    """Thread-safe in-memory metrics collector.

    Exposes Prometheus-format text on demand.
    """

    def __init__(self) -> None:
        self._lock = Lock()
        self._tool_calls: dict[tuple[str, str], int] = defaultdict(int)
        self._tool_durations: dict[str, list[float]] = defaultdict(list)
        self._agent_invocations: dict[tuple[str, str], int] = defaultdict(int)
        self._circuit_breaker_state: dict[str, int] = defaultdict(int)
        self._start_time = time.time()

    def record_tool_call(self, tool: str, status: str, duration_seconds: float) -> None:
        with self._lock:
            self._tool_calls[(tool, status)] += 1
            durations = self._tool_durations[tool]
            durations.append(duration_seconds)
            if len(durations) > 1000:
                self._tool_durations[tool] = durations[-1000:]

    def record_agent_invocation(self, agent: str, status: str) -> None:
        with self._lock:
            self._agent_invocations[(agent, status)] += 1

    def set_circuit_breaker_state(self, agent: str, open_: bool) -> None:
        with self._lock:
            self._circuit_breaker_state[agent] = 1 if open_ else 0

    def render(self) -> str:
        """Render in Prometheus text exposition format."""
        with self._lock:
            lines: list[str] = []

            lines.append("# HELP charlie_tool_calls_total Total tool calls by tool and status")
            lines.append("# TYPE charlie_tool_calls_total counter")
            for (tool, status), count in sorted(self._tool_calls.items()):
                lines.append(
                    f'charlie_tool_calls_total{{tool="{tool}",status="{status}"}} {count}'
                )

            lines.append("")
            lines.append("# HELP charlie_tool_call_duration_seconds Tool call duration in seconds")
            lines.append("# TYPE charlie_tool_call_duration_seconds summary")
            for tool, durations in sorted(self._tool_durations.items()):
                if not durations:
                    continue
                count = len(durations)
                total = sum(durations)
                lines.append(
                    f'charlie_tool_call_duration_seconds_count{{tool="{tool}"}} {count}'
                )
                lines.append(
                    f'charlie_tool_call_duration_seconds_sum{{tool="{tool}"}} {total:.6f}'
                )

            lines.append("")
            lines.append("# HELP charlie_agent_invocations_total Total agent invocations")
            lines.append("# TYPE charlie_agent_invocations_total counter")
            for (agent, status), count in sorted(self._agent_invocations.items()):
                lines.append(
                    f'charlie_agent_invocations_total{{agent="{agent}",status="{status}"}} {count}'
                )

            lines.append("")
            lines.append("# HELP charlie_circuit_breaker_state Circuit breaker state (0=closed, 1=open)")
            lines.append("# TYPE charlie_circuit_breaker_state gauge")
            for agent, state in sorted(self._circuit_breaker_state.items()):
                lines.append(f'charlie_circuit_breaker_state{{agent="{agent}"}} {state}')

            lines.append("")
            lines.append("# HELP charlie_uptime_seconds Process uptime in seconds")
            lines.append("# TYPE charlie_uptime_seconds gauge")
            lines.append(f"charlie_uptime_seconds {time.time() - self._start_time:.1f}")

            try:
                import resource
                usage = resource.getrusage(resource.RUSAGE_SELF)
                rss_kb = usage.ru_maxrss
                lines.append("")
                lines.append("# HELP charlie_memory_usage_bytes Peak memory usage in bytes")
                lines.append("# TYPE charlie_memory_usage_bytes gauge")
                lines.append(f"charlie_memory_usage_bytes {rss_kb * 1024}")
            except Exception:
                pass

            return "\n".join(lines) + "\n"


_collector: MetricsCollector | None = None


def get_collector() -> MetricsCollector:
    """Return the singleton metrics collector, creating it on first call."""
    global _collector
    if _collector is None:
        _collector = MetricsCollector()
    return _collector
