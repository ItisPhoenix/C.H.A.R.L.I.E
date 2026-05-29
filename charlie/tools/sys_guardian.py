"""
C.H.A.R.L.I.E. — System Guardian Tool
Monitors CPU temperature, memory usage, disk space, and process health.
Provides alerts and automatic remediation suggestions.
"""
import threading
import time
from collections import deque
from datetime import datetime
from typing import Any, Dict, List, Optional

import psutil

from charlie.security.tiers import RiskTier, risk_tier
from charlie.utils.logger import get_logger

logger = get_logger("SysGuardian")

# Thresholds
CPU_TEMP_WARNING = 70  # Celsius
CPU_TEMP_CRITICAL = 85
CPU_USAGE_WARNING = 85  # Percent
CPU_USAGE_CRITICAL = 95
MEMORY_WARNING = 80  # Percent
MEMORY_CRITICAL = 90
DISK_WARNING = 85  # Percent
DISK_CRITICAL = 95
PROCESS_CPU_WARNING = 50  # Single process CPU %
PROCESS_MEMORY_WARNING = 10  # Single process memory %

# Monitoring intervals
CHECK_INTERVAL = 15  # seconds
HISTORY_SIZE = 60  # Keep 60 samples (~15 minutes)


class SysGuardian:
    """
    SysGuardian: Monitors system health metrics and provides alerts.
    Tracks CPU temp, memory, disk, and identifies resource-heavy processes.
    """

    def __init__(self, status_q=None):
        self.status_q = status_q
        self._running = False
        self._thread = None

        # History for trend analysis
        self._cpu_history = deque(maxlen=HISTORY_SIZE)
        self._memory_history = deque(maxlen=HISTORY_SIZE)
        self._disk_history = deque(maxlen=HISTORY_SIZE)

        # Alert state
        self._active_alerts: List[Dict] = []
        self._last_alert_time = {}

        # Thresholds (can be adjusted)
        self.cpu_temp_warning = CPU_TEMP_WARNING
        self.cpu_temp_critical = CPU_TEMP_CRITICAL
        self.cpu_usage_warning = CPU_USAGE_WARNING
        self.cpu_usage_critical = CPU_USAGE_CRITICAL
        self.memory_warning = MEMORY_WARNING
        self.memory_critical = MEMORY_CRITICAL
        self.disk_warning = DISK_WARNING
        self.disk_critical = DISK_CRITICAL

    def start(self):
        """Starts the system monitoring thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._thread.start()
        logger.info("sys_guardian_started")

    def stop(self):
        """Stops the system monitoring thread."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)
        logger.info("sys_guardian_stopped")

    def _monitor_loop(self):
        """Main monitoring loop."""
        while self._running:
            try:
                metrics = self._collect_metrics()
                self._update_history(metrics)
                self._check_thresholds(metrics)
                self._report_status(metrics)
            except Exception as e:
                logger.debug(f"sys_monitor_error | {e}")
            time.sleep(CHECK_INTERVAL)

    def _collect_metrics(self) -> Dict[str, Any]:
        """Collects all system metrics."""
        metrics = {
            "timestamp": datetime.now().isoformat(),
        }

        # CPU metrics
        try:
            metrics["cpu_percent"] = psutil.cpu_percent(interval=1)
            metrics["cpu_count"] = psutil.cpu_count()
            metrics["cpu_freq"] = psutil.cpu_freq().current if psutil.cpu_freq() else None

            # CPU temperature (if available)
            try:
                temps = psutil.sensors_temperatures()
                if temps:
                    # Try to find CPU temperature
                    for name, entries in temps.items():
                        if "cpu" in name.lower() or "core" in name.lower():
                            metrics["cpu_temp"] = entries[0].current
                            break
                    else:
                        # Use first available temperature
                        for entries in temps.values():
                            if entries:
                                metrics["cpu_temp"] = entries[0].current
                                break
            except (AttributeError, OSError):
                # sensors_temperatures not available on Windows
                metrics["cpu_temp"] = None
        except Exception as e:
            logger.debug(f"cpu_metrics_error | {e}")

        # Memory metrics
        try:
            mem = psutil.virtual_memory()
            metrics["memory_percent"] = mem.percent
            metrics["memory_used_gb"] = mem.used / (1024**3)
            metrics["memory_total_gb"] = mem.total / (1024**3)
            metrics["memory_available_gb"] = mem.available / (1024**3)
        except Exception as e:
            logger.debug(f"memory_metrics_error | {e}")

        # Disk metrics
        try:
            disk = psutil.disk_usage("C:\\")
            metrics["disk_percent"] = disk.percent
            metrics["disk_used_gb"] = disk.used / (1024**3)
            metrics["disk_total_gb"] = disk.total / (1024**3)
            metrics["disk_free_gb"] = disk.free / (1024**3)
        except Exception as e:
            logger.debug(f"disk_metrics_error | {e}")

        # Top processes by CPU and memory
        try:
            processes = []
            for p in psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent"]):
                try:
                    processes.append({
                        "pid": p.info["pid"],
                        "name": p.info["name"],
                        "cpu_percent": p.info["cpu_percent"] or 0,
                        "memory_percent": p.info["memory_percent"] or 0,
                    })
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass

            # Sort by CPU and memory
            by_cpu = sorted(processes, key=lambda x: x["cpu_percent"], reverse=True)[:5]
            by_mem = sorted(processes, key=lambda x: x["memory_percent"], reverse=True)[:5]
            metrics["top_cpu_processes"] = by_cpu
            metrics["top_memory_processes"] = by_mem
        except Exception as e:
            logger.debug(f"process_metrics_error | {e}")

        return metrics

    def _update_history(self, metrics: Dict):
        """Updates the metric history."""
        self._cpu_history.append({
            "timestamp": metrics["timestamp"],
            "cpu_percent": metrics.get("cpu_percent"),
            "cpu_temp": metrics.get("cpu_temp"),
        })
        self._memory_history.append({
            "timestamp": metrics["timestamp"],
            "memory_percent": metrics.get("memory_percent"),
        })
        self._disk_history.append({
            "timestamp": metrics["timestamp"],
            "disk_percent": metrics.get("disk_percent"),
        })

    def _check_thresholds(self, metrics: Dict):
        """Checks metrics against thresholds and generates alerts."""
        time.time()
        new_alerts = []

        # CPU temperature check
        cpu_temp = metrics.get("cpu_temp")
        if cpu_temp:
            if cpu_temp >= self.cpu_temp_critical:
                new_alerts.append({
                    "type": "cpu_temp_critical",
                    "severity": "critical",
                    "message": f"CPU temperature critical: {cpu_temp:.1f}°C",
                    "value": cpu_temp,
                })
            elif cpu_temp >= self.cpu_temp_warning:
                new_alerts.append({
                    "type": "cpu_temp_warning",
                    "severity": "warning",
                    "message": f"CPU temperature high: {cpu_temp:.1f}°C",
                    "value": cpu_temp,
                })

        # CPU usage check
        cpu_percent = metrics.get("cpu_percent", 0)
        if cpu_percent >= self.cpu_usage_critical:
            new_alerts.append({
                "type": "cpu_usage_critical",
                "severity": "critical",
                "message": f"CPU usage critical: {cpu_percent:.1f}%",
                "value": cpu_percent,
            })
        elif cpu_percent >= self.cpu_usage_warning:
            new_alerts.append({
                "type": "cpu_usage_warning",
                "severity": "warning",
                "message": f"CPU usage high: {cpu_percent:.1f}%",
                "value": cpu_percent,
            })

        # Memory check
        memory_percent = metrics.get("memory_percent", 0)
        if memory_percent >= self.memory_critical:
            new_alerts.append({
                "type": "memory_critical",
                "severity": "critical",
                "message": f"Memory usage critical: {memory_percent:.1f}%",
                "value": memory_percent,
            })
        elif memory_percent >= self.memory_warning:
            new_alerts.append({
                "type": "memory_warning",
                "severity": "warning",
                "message": f"Memory usage high: {memory_percent:.1f}%",
                "value": memory_percent,
            })

        # Disk check
        disk_percent = metrics.get("disk_percent", 0)
        if disk_percent >= self.disk_critical:
            new_alerts.append({
                "type": "disk_critical",
                "severity": "critical",
                "message": f"Disk space critical: {disk_percent:.1f}% used",
                "value": disk_percent,
            })
        elif disk_percent >= self.disk_warning:
            new_alerts.append({
                "type": "disk_warning",
                "severity": "warning",
                "message": f"Disk space low: {disk_percent:.1f}% used",
                "value": disk_percent,
            })

        # Process-level checks
        top_cpu = metrics.get("top_cpu_processes", [])
        top_mem = metrics.get("top_memory_processes", [])

        for proc in top_cpu[:3]:
            if proc["cpu_percent"] >= PROCESS_CPU_WARNING:
                new_alerts.append({
                    "type": "high_cpu_process",
                    "severity": "info",
                    "message": f"High CPU process: {proc['name']} ({proc['cpu_percent']:.1f}%)",
                    "value": proc["cpu_percent"],
                    "process": proc["name"],
                })

        for proc in top_mem[:3]:
            if proc["memory_percent"] >= PROCESS_MEMORY_WARNING:
                new_alerts.append({
                    "type": "high_memory_process",
                    "severity": "info",
                    "message": f"High memory process: {proc['name']} ({proc['memory_percent']:.1f}%)",
                    "value": proc["memory_percent"],
                    "process": proc["name"],
                })

        # Deduplicate and update alerts
        self._active_alerts = new_alerts

    def _report_status(self, metrics: Dict):
        """Reports status to the status queue."""
        if not self.status_q:
            return
        try:
            status = {
                "cpu_percent": metrics.get("cpu_percent"),
                "cpu_temp": metrics.get("cpu_temp"),
                "memory_percent": metrics.get("memory_percent"),
                "disk_percent": metrics.get("disk_percent"),
                "alerts": self._active_alerts,
            }
            self.status_q.put_nowait(("sys_guardian", status))
        except Exception:
            pass

    @risk_tier(RiskTier.TIER_0)
    def get_status(self, args: dict = None) -> str:
        """Returns current system status as a spoken string."""
        if not self._cpu_history:
            return "System monitoring initializing..."

        latest = {
            "cpu_percent": self._cpu_history[-1].get("cpu_percent"),
            "cpu_temp": self._cpu_history[-1].get("cpu_temp"),
        }
        if self._memory_history:
            latest["memory_percent"] = self._memory_history[-1].get("memory_percent")
        if self._disk_history:
            latest["disk_percent"] = self._disk_history[-1].get("disk_percent")

        parts = []
        if latest.get("cpu_percent") is not None:
            parts.append(f"CPU at {latest['cpu_percent']:.0f}%")
        if latest.get("cpu_temp") is not None:
            parts.append(f"{latest['cpu_temp']:.0f}°C")
        if latest.get("memory_percent") is not None:
            parts.append(f"Memory at {latest['memory_percent']:.0f}%")
        if latest.get("disk_percent") is not None:
            parts.append(f"Disk at {latest['disk_percent']:.0f}%")

        status = ", ".join(parts) if parts else "System nominal"

        if self._active_alerts:
            critical = [a for a in self._active_alerts if a["severity"] == "critical"]
            if critical:
                status += f". {critical[0]['message']}"

        return status

    @risk_tier(RiskTier.TIER_0)
    def get_alerts(self, args: dict = None) -> str:
        """Returns all active alerts."""
        if not self._active_alerts:
            return "No active system alerts."

        lines = ["System Alerts:"]
        for alert in self._active_alerts:
            lines.append(f"- [{alert['severity'].upper()}] {alert['message']}")

        return "\n".join(lines)

    @risk_tier(RiskTier.TIER_0)
    def get_top_processes(self, args: dict = None) -> str:
        """Returns top resource-consuming processes."""
        if not self._cpu_history:
            return "System monitoring initializing..."

        # Get current metrics
        metrics = self._collect_metrics()
        top_cpu = metrics.get("top_cpu_processes", [])
        top_mem = metrics.get("top_memory_processes", [])

        lines = ["Top Processes by CPU:"]
        for i, p in enumerate(top_cpu[:5], 1):
            lines.append(f"{i}. {p['name']}: {p['cpu_percent']:.1f}%")

        lines.append("\nTop Processes by Memory:")
        for i, p in enumerate(top_mem[:5], 1):
            lines.append(f"{i}. {p['name']}: {p['memory_percent']:.1f}%")

        return "\n".join(lines)

    @risk_tier(RiskTier.TIER_0)
    def set_threshold(self, args: dict = None) -> str:
        """Sets alert thresholds. Args: metric, warning, critical."""
        if not args:
            return "Please specify metric, warning, and critical values."

        metric = args.get("metric")
        warning = args.get("warning")
        critical = args.get("critical")

        if not all([metric, warning, critical]):
            return "Please provide metric, warning, and critical values."

        try:
            warning = float(warning)
            critical = float(critical)
        except ValueError:
            return "Warning and critical must be numeric values."

        if metric == "cpu_temp":
            self.cpu_temp_warning = warning
            self.cpu_temp_critical = critical
        elif metric == "cpu_usage":
            self.cpu_usage_warning = warning
            self.cpu_usage_critical = critical
        elif metric == "memory":
            self.memory_warning = warning
            self.memory_critical = critical
        elif metric == "disk":
            self.disk_warning = warning
            self.disk_critical = critical
        else:
            return f"Unknown metric: {metric}. Use cpu_temp, cpu_usage, memory, or disk."

        return f"Thresholds updated for {metric}: warning={warning}, critical={critical}"

    def get_metrics(self) -> Dict[str, Any]:
        """Returns the latest collected metrics."""
        if not self._cpu_history:
            return {}

        return {
            "cpu_percent": self._cpu_history[-1].get("cpu_percent"),
            "cpu_temp": self._cpu_history[-1].get("cpu_temp"),
            "memory_percent": self._memory_history[-1].get("memory_percent") if self._memory_history else None,
            "disk_percent": self._disk_history[-1].get("disk_percent") if self._disk_history else None,
            "alerts": self._active_alerts,
        }

    def get_cpu_temp(self) -> Optional[float]:
        """Returns current CPU temperature or None if not available."""
        if self._cpu_history:
            return self._cpu_history[-1].get("cpu_temp")
        return None

    def get_memory_percent(self) -> Optional[float]:
        """Returns current memory usage percentage."""
        if self._memory_history:
            return self._memory_history[-1].get("memory_percent")
        return None

    def get_disk_percent(self) -> Optional[float]:
        """Returns current disk usage percentage."""
        if self._disk_history:
            return self._disk_history[-1].get("disk_percent")
        return None

    def has_critical_alerts(self) -> bool:
        """Returns True if there are any critical alerts."""
        return any(a["severity"] == "critical" for a in self._active_alerts)
