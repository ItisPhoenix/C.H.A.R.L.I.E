import multiprocessing
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import requests

from charlie.config import settings
from charlie.utils.doctor import Doctor
from charlie.utils.logger import get_logger

logger = get_logger("Phoenix")

# ── Heartbeat Protocol Constants ─────────────────────────────────────────────
HEARTBEAT_TIMEOUT = 120.0  # INCREASED: Allows for slow starts and heavy VRAM usage
MONITOR_INTERVAL = 2.5  # Frequency of watchdog checks

# ── Shared Entry Points ──────────────────────────────────────────────────────


def run_audio(
    audio_q, brain_task_q, tts_q, status_q, audio_cmd_q, heartbeat, interrupt_event
):
    from dotenv import load_dotenv
    load_dotenv()
    from charlie.config import ensure_initialized
    ensure_initialized()

    import pythoncom

    from charlie.audio_proc import AudioEngine

    pythoncom.CoInitializeEx(pythoncom.COINIT_APARTMENTTHREADED)
    engine = AudioEngine(
        brain_task_q, tts_q, status_q, audio_cmd_q, heartbeat, interrupt_event
    )
    engine.run()


def run_browser(browser_req_q, browser_res_q, status_q, heartbeat):
    from dotenv import load_dotenv
    load_dotenv()
    from charlie.config import ensure_initialized
    ensure_initialized()

    from charlie.browser.headless_browser import HeadlessBrowserProcess

    proc = HeadlessBrowserProcess(
        browser_req_q, browser_res_q, heartbeat, status_q=status_q
    )
    proc.run()


def run_brain(
    brain_task_q,
    tts_q,
    status_q,
    audio_cmd_q,
    browser_req_q,
    browser_res_q,
    telegram_q,
    heartbeat,
    interrupt_event,
    reboot_event,
):
    from dotenv import load_dotenv
    load_dotenv()
    from charlie.config import ensure_initialized
    ensure_initialized()

    import pythoncom

    from charlie.brain.core import Brain

    pythoncom.CoInitializeEx(pythoncom.COINIT_APARTMENTTHREADED)
    brain = Brain(
        brain_task_q=brain_task_q,
        tts_q=tts_q,
        status_q=status_q,
        audio_cmd_q=audio_cmd_q,
        browser_req_q=browser_req_q,
        browser_res_q=browser_res_q,
        telegram_q=telegram_q,
        heartbeat=heartbeat,
        interrupt_event=interrupt_event,
        reboot_event=reboot_event,
    )
    brain.run()


def run_telegram(brain_task_q, status_q, telegram_q, audio_cmd_q, heartbeat):
    from dotenv import load_dotenv
    load_dotenv()
    from charlie.config import ensure_initialized
    ensure_initialized()

    import pythoncom

    from charlie.telegram.bridge import run_bridge

    pythoncom.CoInitializeEx(pythoncom.COINIT_APARTMENTTHREADED)
    run_bridge(brain_task_q, status_q, telegram_q, audio_cmd_q, heartbeat)


def run_vision(brain_task_q, status_q, heartbeat):
    from dotenv import load_dotenv
    load_dotenv()
    from charlie.config import ensure_initialized
    ensure_initialized()

    from charlie.vision.activity_sentinel import ActivitySentinel
    # Suppress C-level driver logs (VCAMDS/NBX hive) for the vision process
    if os.name == 'nt':
        try:
            # Re-open stderr to NUL
            null_fd = os.open('NUL', os.O_WRONLY)
            os.dup2(null_fd, 2)
        except Exception:
            pass

    sentinel = ActivitySentinel(brain_task_q, status_q, heartbeat)
    sentinel.run()


# ── Self-Repair Engine ───────────────────────────────────────────────────────


class SelfHealer:
    def __init__(self):
        self.log_path = "logs/charlie.log"
        self.model = settings.llm.primary_model.split("/")[-1]
        self.patch_dir = "hotpatches"
        os.makedirs(self.patch_dir, exist_ok=True)

    def extract_traceback(self):
        """Extracts the most recent traceback from logs."""
        if not os.path.exists(self.log_path):
            return None

        # Read last 50KB of the log file for efficiency
        try:
            filesize = os.path.getsize(self.log_path)
            read_size = min(filesize, 50 * 1024)
            with open(self.log_path, "rb") as f:
                f.seek(filesize - read_size)
                lines = f.read().decode("utf-8", errors="ignore").splitlines()
        except Exception as e:
            logger.debug(f"efficient_log_read_failed | error={e}")
            with open(self.log_path, "r", encoding="utf-8") as f:
                lines = f.readlines()

        tb_lines = []
        found_start = False
        # Search backwards for the start of a traceback
        for line in reversed(lines[-200:]):
            tb_lines.insert(0, line)
            if "Traceback (most recent call last):" in line:
                found_start = True
                break

        return "\n".join(tb_lines) if found_start else None

    def rollback(self, target_file):
        """Restores file from latest backup in hotpatches if exists."""
        backups = sorted(
            Path(self.patch_dir).glob(f"{Path(target_file).name}.*"),
            key=os.path.getmtime,
            reverse=True,
        )
        if backups:
            shutil.copy2(backups[0], target_file)
            logger.info(f"rollback_applied | file={target_file} | backup={backups[0]}")
            return True
        return False

    def _sanitize_traceback(self, traceback: str) -> str:
        """Sanitize traceback before sending to LLM to prevent prompt injection."""
        # Remove any lines that look like prompt injection attempts
        dangerous_patterns = [
            r'ignore\s+(previous|above|all)\s+instructions',
            r'you\s+are\s+now',
            r'system\s*:\s*',
            r'<\|im_start\|>',
            r'<\|im_end\|>',
            r'ASSISTANT\s*:',
            r'USER\s*:',
            r'```\s*(system|prompt|instruction)',
        ]
        lines = traceback.split('\n')
        sanitized = []
        for line in lines:
            is_dangerous = any(re.search(p, line, re.IGNORECASE) for p in dangerous_patterns)
            if not is_dangerous:
                sanitized.append(line)
        return '\n'.join(sanitized[:100])  # Limit length

    def attempt_patch(self, traceback):
        """Generates a fix proposal and saves it to hotpatches."""
        logger.info("self_repair_initiated")

        # Sanitize traceback to prevent prompt injection
        traceback = self._sanitize_traceback(traceback)

        matches = re.findall(r'File "(.*?)", line (\d+)', traceback)
        if not matches:
            return False

        target_file, line_num = matches[-1]
        if not os.path.exists(target_file):
            return False

        # Security: only patch files within the charlie directory
        abs_target = os.path.abspath(target_file)
        abs_charlie = os.path.abspath(os.path.join(os.getcwd(), "charlie"))
        if not abs_target.startswith(abs_charlie):
            logger.warning("self_repair_blocked | target outside charlie dir: %s", target_file)
            return False

        with open(target_file, "r", encoding="utf-8") as f:
            lines = f.readlines()

        start_idx = max(0, int(line_num) - 100)
        end_idx = min(len(lines), int(line_num) + 100)
        source_window = "".join(lines[start_idx:end_idx])

        prompt = f"""You are a Python Senior Engineer. A process in the CHARLIE Autonomous Engine crashed.
ERROR:
{traceback}

FILE: {target_file} (Showing lines {start_idx + 1} to {end_idx})
SOURCE CONTEXT:
{source_window}

Return ONLY the corrected python code block for the lines shown. No explanations. No markdown formatting. Just raw code.
"""
        try:
            url = settings.llm.llm_url.rstrip("/")
            if "/v1" in url:
                resp = requests.post(
                    f"{url}/chat/completions",
                    json={
                        "model": self.model,
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": 0.0,
                    },
                    timeout=60,
                )
                fixed_code = resp.json()["choices"][0]["message"]["content"].strip()
            else:
                resp = requests.post(
                    f"{url}/api/generate",
                    json={"model": self.model, "prompt": prompt, "stream": False},
                    timeout=60,
                )
                fixed_code = resp.json().get("response", "").strip()

            if "import " in fixed_code or "def " in fixed_code:
                fixed_code = re.sub(
                    r"^```python\n|```$", "", fixed_code, flags=re.MULTILINE
                )

                # 1. Save patch for record
                file_basename = os.path.basename(target_file)
                patch_name = f"patch_{file_basename}_{int(time.time())}.py"
                patch_path = os.path.join(self.patch_dir, patch_name)
                with open(patch_path, "w", encoding="utf-8") as f:
                    f.write(fixed_code)

                # Safety Check: syntax + banned patterns
                import py_compile

                try:
                    py_compile.compile(patch_path, doraise=True)
                except py_compile.PyCompileError as e:
                    logger.warning(f"self_repair_blocked | syntax_error={e}")
                    os.remove(patch_path)
                    return False

                # Banned pattern scan — block dangerous code from being auto-patched
                BANNED_PATTERNS = [
                    (r'\beval\s*\(', "eval()"),
                    (r'\bexec\s*\(', "exec()"),
                    (r'\bos\.system\s*\(', "os.system()"),
                    (r'subprocess\.\w+\s*\([^)]*shell\s*=\s*True', "subprocess with shell=True"),
                    (r'\b__import__\s*\(', "__import__()"),
                ]
                for pattern, name in BANNED_PATTERNS:
                    if re.search(pattern, fixed_code):
                        logger.warning(f"self_repair_blocked | banned_pattern={name} in {target_file}")
                        os.remove(patch_path)
                        return False

                # 2. Create Backup before applying hot-patch
                bak_path = target_file + ".bak"
                if not os.path.exists(bak_path):
                    import shutil

                    shutil.copy2(target_file, bak_path)
                    logger.info(f"backup_created: {bak_path}")

                # 3. Apply Patch to Source - Replace only the window lines
                # Split fixed_code into lines, preserving line endings if possible
                fixed_lines = fixed_code.splitlines(keepends=True)
                if not fixed_lines:
                    logger.warning("self_repair_blocked | empty_fix_received")
                    return False

                # Ensure we have the same line endings as original
                # If fixed_lines don't have line endings, add them
                for i, line in enumerate(fixed_lines):
                    if not line.endswith(("\n", "\r\n", "\r")):
                        fixed_lines[i] = line + "\n"

                # Replace the window in the original lines
                new_lines = lines[:start_idx] + fixed_lines + lines[end_idx:]

                # Write back the modified file
                with open(target_file, "w", encoding="utf-8") as f:
                    f.writelines(new_lines)

                logger.info(
                    f"hot_patch_applied: {target_file} | replaced_lines={start_idx + 1}-{end_idx} | record={patch_path}"
                )
                return True
        except Exception as e:
            logger.error("self_repair_failed", error=str(e))

        return False


# ── Phoenix Supervisor ───────────────────────────────────────────────────────


class PhoenixSupervisor:
    def __init__(self, interrupt_event, reboot_event=None):
        self.interrupt_event = interrupt_event
        self.reboot_event = reboot_event or multiprocessing.Event()
        manager = multiprocessing.Manager()
        self.audio_q = manager.Queue(maxsize=100)
        self.brain_task_q = manager.Queue(maxsize=100)
        self.tts_q = manager.Queue(maxsize=100)
        self.status_q = manager.Queue(maxsize=200)
        self.audio_cmd_q = manager.Queue(maxsize=100)
        self.telegram_q = manager.Queue(maxsize=100)

        # Heartbeats (shared multiprocessing values)
        self.heartbeats = {
            "Audio": multiprocessing.Value("d", time.time()),
            "Brain": multiprocessing.Value("d", time.time()),
            "Browser": multiprocessing.Value("d", time.time()),
            "Telegram": multiprocessing.Value("d", time.time()),
            "Vision": multiprocessing.Value("d", time.time()),
        }

        self.browser_req_q = manager.Queue(maxsize=50)
        self.browser_res_q = manager.Queue(maxsize=50)

        self.processes = {}
        self.running = True
        self.healer = SelfHealer()
        self.doctor = Doctor(status_q=self.status_q)

    def _safe_put(self, q, msg):
        """Put message to queue with 1.0s timeout to prevent deadlocks from clogged queues."""
        try:
            if q is not None:
                q.put(msg, timeout=1.0)
        except Exception as e:
            logger.warning(f"queue_put_failed | {e}")

    def _send_direct_telegram(self, text: str):
        """Synchronous fallback loop directly posting SRE alerts to Telegram API."""
        token = settings.supervisor.telegram_token
        chat_id = settings.supervisor.telegram_chat_id
        if not token or not chat_id:
            logger.error("telegram_direct_fallback_failed | token or chat_id not configured")
            return

        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML"
        }
        try:
            resp = requests.post(url, json=payload, timeout=10)
            if resp.status_code == 200:
                logger.info("telegram_direct_fallback_delivered")
            else:
                logger.error(f"telegram_direct_fallback_error | status={resp.status_code} | response={resp.text}")
        except Exception as e:
            logger.error(f"telegram_direct_fallback_failed | {e}")

    def start(self):
        """Unified entry point to launch all processes and begin monitoring."""
        logger.info("phoenix_supervisor_ignited")

        self.start_process("Audio", run_audio)
        self.start_process("Brain", run_brain)
        self.start_process("Browser", run_browser)
        self.start_process("Telegram", run_telegram)
        self.start_process("Vision", run_vision)

        try:
            self.monitor()
        except KeyboardInterrupt:
            self.stop()
        except Exception as e:
            logger.error("supervisor_runtime_error", error=str(e), exc_info=True)
            self.stop()

    def start_process(self, name, target, restarts=0, args=None, daemon=True):
        heartbeat = self.heartbeats[name]
        heartbeat.value = time.time()  # Reset heartbeat on start

        if args is not None:
            args = args + (heartbeat,)
        elif name == "Audio":
            args = (
                self.audio_q,
                self.brain_task_q,
                self.tts_q,
                self.status_q,
                self.audio_cmd_q,
                heartbeat,
                self.interrupt_event,
            )
            daemon = False  # Audio needs to spawn child processes (playback)
        elif name == "Brain":
            args = (
                self.brain_task_q,
                self.tts_q,
                self.status_q,
                self.audio_cmd_q,
                self.browser_req_q,
                self.browser_res_q,
                self.telegram_q,
                heartbeat,
                self.interrupt_event,
                self.reboot_event,
            )
        elif name == "Browser":
            args = (
                self.browser_req_q,
                self.browser_res_q,
                self.status_q,
                heartbeat,
            )
        elif name == "Telegram":
            args = (
                self.brain_task_q,
                self.status_q,
                self.telegram_q,
                self.audio_cmd_q,
                heartbeat,
            )
        elif name == "Vision":
            args = (
                self.brain_task_q,
                self.status_q,
                heartbeat,
            )
        else:
            logger.warning(f"unknown_process_type | name={name}")
            return

        p = multiprocessing.Process(target=target, args=args, name=name, daemon=daemon)
        p.start()
        self.processes[name] = {"process": p, "target": target, "restarts": restarts, "started_at": time.time()}
        logger.info(f"process_started: {name} (PID: {p.pid})")

    def monitor(self):
        print("\n" + "=" * 50)
        print("  P H O E N I X   S U P E R V I S O R")
        print("=" * 50)

        while self.running:
            time.sleep(MONITOR_INTERVAL)
            now = time.time()

            # ── Check for Manual Reboot Signal ──
            if self.reboot_event.is_set():
                logger.info("reboot_signal_detected | restarting_engine")
                self.reboot()
                return

            for name in list(self.processes.keys()):
                data = self.processes[name]
                p = data["process"]
                heartbeat = self.heartbeats[name]

                # Strike decay: if process alive 5+ min, reset strikes
                if p.is_alive() and data.get("started_at") and data["restarts"] > 0:
                    uptime = now - data["started_at"]
                    if uptime > 300:
                        logger.info(f"strike_decay | service={name} | strikes_reset | uptime={uptime:.0f}s")
                        data["restarts"] = 0

                # Check for crash (is_alive) or hang (heartbeat timeout)
                is_hung = (now - heartbeat.value) > HEARTBEAT_TIMEOUT
                if not p.is_alive() or is_hung:
                    if p.is_alive() and is_hung:
                        logger.warning(
                            f"process_hung: {name}. Heartbeat timeout ({now - heartbeat.value:.1f}s). Terminating..."
                        )
                        p.terminate()
                        p.join(timeout=2)
                        if p.is_alive():
                            p.kill()

                    if p.exitcode in (0, 99):
                        # exitcode 0 or 99 = intentional shutdown, not a crash
                        logger.info(
                            f"intentional_shutdown_received: {name} (exit={p.exitcode})"
                        )
                        self.stop()
                        return

                    logger.warning(
                        f"process_failure: {name}. Strike {data['restarts'] + 1} of 3."
                    )

                    tb = self.healer.extract_traceback()

                    # Track restart and check for 3-strike limit (Quarantine)
                    if data["restarts"] >= 3:
                        logger.critical(
                            f"HALTING_RECOVERY | service={name} | strikes_exceeded | placing in quarantine"
                        )
                        data["quarantined"] = True

                        self._safe_put(self.tts_q, {
                            "type": "SPEAK",
                            "content": f"{name} service has crashed consecutively and is now quarantined, Sir."
                        })
                        self._safe_put(self.status_q, {"type": "PHASE", "content": "ALERT"})

                        # Direct Telegram fall-back alert dispatch
                        alert_text = (
                            f"<b>🚨 [SRE QUARANTINE PROTOCOL ACTIVATED]</b>\n\n"
                            f"Service <b>{name}</b> has crashed consecutively (3 strikes exceeded) and has been placed in quarantine to prevent thrashing.\n\n"
                            f"<b>Crash Traceback Extracted:</b>\n"
                            f"<pre>{tb or 'No traceback available.'}</pre>"
                        )
                        self._send_direct_telegram(alert_text)
                        continue

                    # Verbal alert for restart
                    self._safe_put(self.tts_q, {
                        "type": "SPEAK",
                        "content": f"CRITICAL: {name} process failure. Attempting repair and restarting. Cooldown initiated."
                    })

                    # Alert
                    self._safe_put(self.status_q, {"type": "PHOENIX_ALERT", "content": name})

                    if tb:
                        self.doctor.auto_repair_brain(tb)
                        if data.get("is_patched", False):
                            logger.error(f"patch_failed: {name}. Rolling back...")
                            matches = re.findall(r'File "(.*?)", line (\d+)', tb)
                            if matches:
                                self.healer.rollback(matches[-1][0])
                            data["is_patched"] = False
                        else:
                            patched = self.healer.attempt_patch(tb)
                            if patched:
                                logger.info(f"self_repair_success: {name} hot-patched.")
                                data["is_patched"] = True

                    data["restarts"] += 1

                    # 10s cooldown before restart
                    logger.info(
                        f"phoenix_cooldown_started | service={name} | duration=10s"
                    )
                    time.sleep(10)

                    self.start_process(name, data["target"], restarts=data["restarts"])

            # Autonomic Vitals Check (The Doctor)
            try:
                active_pids = [
                    d["process"].pid
                    for d in self.processes.values()
                    if d["process"].is_alive()
                ]
                self.doctor.update_pids(active_pids)
                self.doctor.perform_vitals_check()
            except Exception as e:
                logger.error("doctor_vital_check_failed", error=str(e))

    def stop(self):
        self.running = False
        for name, data in self.processes.items():
            p = data["process"]
            if p.is_alive():
                p.terminate()
                p.join(timeout=5)

    def reboot(self):
        """Full system restart by replacing the current process."""
        self.running = False
        logger.info("executing_os_reboot")

        # Kill all managed processes
        for name, data in self.processes.items():
            p = data["process"]
            if p.is_alive():
                p.terminate()
                p.join(timeout=1)
                if p.is_alive():
                    p.kill()

        # Replace the current process with a fresh one
        # sys.executable is the python interpreter
        # sys.argv contains the script and arguments
        # Use subprocess.Popen for cross-platform compatibility
        # os.execv doesn't work properly on Windows
        try:
            subprocess.Popen([sys.executable] + sys.argv)
            sys.exit(0)
        except Exception as e:
            logger.error(f"reboot_failed | error={e}")
            # Fallback to os.execv for non-Windows platforms
            if platform.system() != "Windows":
                os.execv(sys.executable, [sys.executable] + sys.argv)
            else:
                logger.critical("reboot_fallback_unavailable")
                sys.exit(1)

    def get_status_report(self):
        """Returns a formatted string of current system health for the Brain."""
        vitals = self.doctor.obs.get_vitals()
        stats = []
        for name, data in self.processes.items():
            p = data["process"]
            restarts = data["restarts"]
            pid = p.pid
            if pid in vitals["processes"]:
                v = vitals["processes"][pid]
                stats.append(
                    f"{name}: CPU {v['cpu']:.1f}% | RAM {v['ram_mb']:.1f}MB | Restarts: {restarts}"
                )
            else:
                stats.append(f"{name}: Offline/Initializing | Restarts: {restarts}")

        load_str = f"System Load: CPU {vitals['system_cpu']}% | RAM {vitals['system_ram_percent']}%"
        return "SYSTEM STATUS:\n" + "\n".join(stats) + "\n" + load_str
