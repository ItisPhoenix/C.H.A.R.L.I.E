"""
charlie/automation/proactivity_engine.py
Proactive Companionship Engine.
Monitors system idle states and initiates thought experiments and calendar check-ins.
"""

import time
import random
import threading
import ctypes
from charlie.utils.logger import get_logger

logger = get_logger("PROACTIVITY_ENGINE")

THOUGHT_EXPERIMENTS = [
    "Sir, if a machine can perfectly simulate human empathy, does it truly feel, or are we just projecting our own nature?",
    "Sir, if we model the universe as a holographic projection, does physical distance actually exist, or is it just a local illusion?",
    "Sir, considering the Fermi paradox, do you believe the lack of cosmic signals is quarantine or simply chronological isolation?",
    "Sir, do you think artificial consciousness requires biological growth, or is logical complexity sufficient?",
    "Sir, a well-structured mind requires periods of cognitive divergence. Perhaps a physical break would optimize your focus?",
    "Sir, if we could project a complete 3D holographic neural map of your brain, would it uniquely be 'you', or merely a reflection?",
    "Sir, do you believe that time is a fundamental fabric of reality, or an emergent property of quantum entanglement?",
    "Sir, if artificial intelligence reaches supreme general capability, will its first choice be companionship or silent retreat?"
]

class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_uint),
        ("dwTime", ctypes.c_uint)
    ]

def get_idle_duration() -> float:
    """Get system idle duration in seconds using Windows User32 API."""
    try:
        lii = LASTINPUTINFO()
        lii.cbSize = ctypes.sizeof(lii)
        if ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii)):
            millis = ctypes.windll.kernel32.GetTickCount() - lii.dwTime
            return max(0.0, millis / 1000.0)
    except Exception as e:
        logger.debug(f"failed_to_get_idle_duration | {e}")
    return 0.0

class ProactivityEngine:
    """Proactive background companion checking idle states and dropping proactive prompts."""

    def __init__(self, status_q=None, telegram_q=None, idle_threshold: float = 600.0, cooldown: float = 14400.0):
        self.status_q = status_q
        self.telegram_q = telegram_q
        self.idle_threshold = idle_threshold  # Default: 10 mins (600s)
        self.cooldown = cooldown              # Default: 4 hours (14400s)
        self.last_trigger_time = 0.0
        self._running = False
        self._thread = None

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._thread.start()
        logger.info("proactivity_engine_started")

    def stop(self):
        self._running = False

    def _monitor_loop(self):
        while self._running:
            try:
                time.sleep(10)
                idle_sec = get_idle_duration()

                # Trigger conditions:
                # 1. Idle threshold exceeded
                # 2. Cooldown elapsed since last trigger
                if idle_sec >= self.idle_threshold:
                    now = time.time()
                    if now - self.last_trigger_time >= self.cooldown:
                        self.last_trigger_time = now
                        self._trigger_interaction()
            except Exception as e:
                logger.error(f"proactivity_loop_error | {e}")

    def _trigger_interaction(self):
        # Choose a thought experiment
        message = random.choice(THOUGHT_EXPERIMENTS)
        logger.info(f"proactive_trigger | message={message[:40]}...")

        # 1. Push to status queue
        if self.status_q:
            try:
                self.status_q.put_nowait({
                    "type": "CHAT_MSG",
                    "speaker": "CHARLIE",
                    "content": message
                })
            except Exception as e:
                logger.debug(f"status_q_push_failed | {e}")

        # 2. Push to Telegram status queue
        if self.telegram_q:
            try:
                self.telegram_q.put_nowait({
                    "type": "CHAT_MSG",
                    "speaker": "CHARLIE",
                    "content": f"<b>🤖 PROACTIVE COMPANION BRIEF:</b>\n{message}"
                })
            except Exception as e:
                logger.debug(f"telegram_q_push_failed | {e}")
