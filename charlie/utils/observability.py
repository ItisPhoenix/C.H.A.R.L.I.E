import os

import psutil

from charlie.utils.logger import get_logger
from charlie.utils.system import get_vram_usage

logger = get_logger(__name__)


class Observability:
    def __init__(self, tracked_pids=None):
        self.tracked_pids = tracked_pids or [os.getpid()]
        self.thresholds = {
            "cpu_percent": 98.0,
            "system_ram_percent": 99.0,
            "vram_percent": 98.0,
        }

    def update_pids(self, pids):
        self.tracked_pids = pids

    def _get_vram_usage(self):
        return get_vram_usage()

    def get_vitals(self):
        """Captures aggregated system vitals for tracked processes."""
        stats = {
            "processes": {},
            "total_cpu": 0,
            "total_ram_mb": 0,
            "system_cpu": psutil.cpu_percent(),
            "system_ram_percent": psutil.virtual_memory().percent,
            "vram_percent": self._get_vram_usage(),
        }

        for pid in self.tracked_pids:
            try:
                proc = psutil.Process(pid)
                with proc.oneshot():
                    cpu = proc.cpu_percent()
                    ram = proc.memory_info().rss / (1024 * 1024)
                    name = proc.name()

                stats["processes"][pid] = {"name": name, "cpu": cpu, "ram_mb": ram}
                stats["total_cpu"] += cpu
                stats["total_ram_mb"] += ram
            except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
                logger.debug("process_vitals_access_denied", error=str(e))
                continue

        return stats

    def check_thresholds(self, vitals):
        """Returns a list of alerts if thresholds are exceeded."""
        alerts = []
        # Use dynamic thresholds from config
        if vitals.get("system_ram_percent", 0) > self.thresholds["system_ram_percent"]:
            alerts.append("CRITICAL_RAM")
        if vitals.get("vram_percent", 0) > self.thresholds["vram_percent"]:
            alerts.append("CRITICAL_VRAM")
        if vitals.get("system_cpu", 0) > self.thresholds["cpu_percent"]:
            alerts.append("CRITICAL_CPU")
        return alerts
