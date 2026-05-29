"""
charlie/tools/intrusion_patrol.py
Network Intrusion Sentinel (TIER_1).
Background daemon and passive auditing tools to detect port scanning,
anomalous socket connections, and listening port hijack attempts.
"""

import time
import threading
import psutil
from charlie.tools.tool_decorator import tool, RiskTier
from charlie.utils.logger import get_logger

logger = get_logger("INTRUSION_PATROL")

# Known baseline safe ports for C.H.A.R.L.I.E.
DEFAULT_SAFE_PORTS = {80, 443, 3000, 8090, 135, 445, 902, 912, 5357, 49664, 49665, 49666, 49667, 49668}

@tool(
    name="audit_network_connections",
    description="Audit active host socket connections and listening ports to check for network anomalies",
    risk_tier=RiskTier.TIER_1,
    category="security",
)
def audit_network_connections() -> str:
    """Scan and list all active listening and established network sockets with process details."""
    try:
        connections = psutil.net_connections(kind="inet")
        listening = []
        established = []

        for conn in connections:
            proc_name = "System/Unknown"
            if conn.pid:
                try:
                    p = psutil.Process(conn.pid)
                    proc_name = p.name()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass

            local_addr = f"{conn.laddr.ip}:{conn.laddr.port}"
            remote_addr = f"{conn.raddr.ip}:{conn.raddr.port}" if conn.raddr else "N/A"

            entry = {
                "proto": "TCP" if conn.type == 1 else "UDP",
                "local": local_addr,
                "remote": remote_addr,
                "status": conn.status,
                "pid": conn.pid,
                "proc": proc_name,
                "port": conn.laddr.port
            }

            if conn.status == "LISTEN":
                listening.append(entry)
            elif conn.status == "ESTABLISHED":
                established.append(entry)

        # Build markdown summary
        lines = ["### 🌐 [NETWORK SECURITY AUDIT]"]
        lines.append(f"Total Active Sockets: {len(connections)}")
        lines.append(f"Listening Ports: {len(listening)} | Established Connections: {len(established)}\n")

        lines.append("#### 📥 LISTENING PORTS")
        lines.append("| Protocol | Local Address | Process Name | PID | Status |")
        lines.append("| :--- | :--- | :--- | :--- | :--- |")
        for item in sorted(listening, key=lambda x: x["port"]):
            status_tag = "⚠️ UNKNOWN" if item["port"] not in DEFAULT_SAFE_PORTS else "✅ SAFE"
            lines.append(f"| {item['proto']} | `{item['local']}` | **{item['proc']}** | {item['pid']} | {status_tag} |")

        lines.append("\n#### 🔌 ACTIVE ESTABLISHED CONNECTIONS")
        lines.append("| Protocol | Local Address | Remote Address | Process Name | PID |")
        lines.append("| :--- | :--- | :--- | :--- | :--- |")
        for item in established[:15]:  # Cap at 15 for output readability
            lines.append(f"| {item['proto']} | `{item['local']}` | `{item['remote']}` | **{item['proc']}** | {item['pid']} |")

        if len(established) > 15:
            lines.append(f"\n*...and {len(established) - 15} more established connections.*")

        return "\n".join(lines)
    except Exception as e:
        logger.error(f"network_audit_failed | {e}")
        return f"Error executing network audit: {e}"


class NetworkIntrusionSentinel:
    """Background loop monitoring new listening ports and notifying system queues."""

    def __init__(self, status_q=None, telegram_q=None, poll_interval: float = 10.0):
        self.status_q = status_q
        self.telegram_q = telegram_q
        self.poll_interval = poll_interval
        self.known_listening_ports = set()
        self._running = False
        self._thread = None

        # Populate initial baseline
        try:
            for conn in psutil.net_connections(kind="inet"):
                if conn.status == "LISTEN":
                    self.known_listening_ports.add(conn.laddr.port)
        except Exception:
            pass

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._thread.start()
        logger.info("sentinel_background_started")

    def stop(self):
        self._running = False

    def _monitor_loop(self):
        while self._running:
            try:
                time.sleep(self.poll_interval)
                current_listening = {}

                for conn in psutil.net_connections(kind="inet"):
                    if conn.status == "LISTEN":
                        port = conn.laddr.port
                        proc_name = "Unknown"
                        if conn.pid:
                            try:
                                proc_name = psutil.Process(conn.pid).name()
                            except Exception:
                                pass
                        current_listening[port] = proc_name

                # Check for new ports
                for port, proc in current_listening.items():
                    if port not in self.known_listening_ports:
                        self.known_listening_ports.add(port)
                        # Avoid noisy alerts on safe baseline ports
                        if port in DEFAULT_SAFE_PORTS:
                            continue

                        # Suspect port detected!
                        logger.warning(f"unauthorized_listening_port_detected | port={port} | proc={proc}")

                        # Push to status_q for alert
                        if self.status_q:
                            try:
                                self.status_q.put_nowait({
                                    "type": "PHOENIX_ALERT",
                                    "content": f"Unauthorized Port {port} opened by {proc}!"
                                })
                            except Exception:
                                pass

                        # Push to telegram_q
                        if self.telegram_q:
                            try:
                                self.telegram_q.put_nowait({
                                    "type": "CHAT_MSG",
                                    "speaker": "CHARLIE",
                                    "content": f"<b>⚠️ SECURITY SENTINEL ALERT:</b>\nNew listening port discovered:\n- Port: <code>{port}</code>\n- Process: <code>{proc}</code>"
                                })
                            except Exception:
                                pass
            except Exception as e:
                logger.error(f"sentinel_loop_error | {e}")
