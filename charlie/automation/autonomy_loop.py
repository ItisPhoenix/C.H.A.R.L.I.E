"""Autonomy Loop — background polling for proactive behavior."""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Optional

from charlie.automation.models import Event

logger = logging.getLogger("charlie.automation.autonomy_loop")


class AutonomyLoop:
    """Background loop that polls world state, patterns, and time triggers.

    Handles events that don't match any known rule by asking the LLM.
    All autonomous actions are TIER_0/TIER_1 (safe reads only).
    """

    def __init__(
        self,
        brain=None,
        poll_interval: int = 60,
        quiet_hours_start: int = 22,
        quiet_hours_end: int = 7,
        frustration_threshold: float = 0.7,
        idle_threshold_minutes: int = 30,
    ):
        self.brain = brain
        self.poll_interval = poll_interval
        self.quiet_hours_start = quiet_hours_start
        self.quiet_hours_end = quiet_hours_end
        self.frustration_threshold = frustration_threshold
        self.idle_threshold_minutes = idle_threshold_minutes

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._last_activity: float = time.time()

        # Workspace directory watcher properties
        self.workspace_root = os.path.abspath(".")
        self.seen_files = {}
        self.staged_files = {}
        self.pending_exfiltrations = {}

    def start(self):
        """Start the background autonomy loop."""
        if self._running:
            return
        self._running = True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()
        logger.info("autonomy_loop_started")

    def stop(self):
        """Stop the background autonomy loop."""
        self._running = False
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("autonomy_loop_stopped")

    def is_quiet_hours(self) -> bool:
        """Check if current time is within quiet hours."""
        current_hour = time.localtime().tm_hour
        if self.quiet_hours_start > self.quiet_hours_end:
            # Wraps midnight (e.g., 22-7)
            return current_hour >= self.quiet_hours_start or current_hour < self.quiet_hours_end
        return self.quiet_hours_start <= current_hour < self.quiet_hours_end

    def notify_activity(self):
        """Call when user activity is detected (resets idle timer)."""
        self._last_activity = time.time()

    def _poll_loop(self):
        """Main background polling loop."""
        last_world_check = 0.0
        # Initialize seen files baseline to prevent flooding on boot
        try:
            self._scan_workspace(init_baseline=True)
        except Exception as e:
            logger.debug(f"workspace_baseline_failed | {e}")

        while not self._stop_event.is_set():
            try:
                now = time.time()
                # 1. Scan workspace files (every 5 seconds)
                self._scan_workspace(init_baseline=False)

                # 2. Check other state checks (every poll_interval seconds)
                if now - last_world_check >= self.poll_interval:
                    last_world_check = now
                    if not self.is_quiet_hours():
                        self._check_world_state()
                        self._check_proactive_tasks()
            except Exception as e:
                logger.error(f"autonomy_poll_error | {e}")

            self._stop_event.wait(5.0)

    def _scan_workspace(self, init_baseline: bool = False):
        """Scan workspace folders, ignoring symlinks and enforcing 10s file stability debouncer."""
        import os
        from pathlib import Path
        import stat as stat_mod

        resolved_root = Path(self.workspace_root).resolve()
        new_files = {}

        for root, dirs, files in os.walk(self.workspace_root):
            # Skip massive folders and runtimes
            dirs[:] = [d for d in dirs if d not in (".git", ".venv", "__pycache__", "node_modules", "logs", "scratch")]

            for file in files:
                file_path = os.path.join(root, file)

                # Symbolic links check
                if os.path.islink(file_path):
                    continue

                try:
                    resolved_file = Path(file_path).resolve()
                    # Directory traversal check
                    if not resolved_file.is_relative_to(resolved_root):
                        continue

                    stat = os.stat(file_path)
                    # Exclude directory junctions
                    if stat_mod.S_ISLNK(stat.st_mode):
                        continue

                    ext = os.path.splitext(file)[1].lower()
                    # PDF build watcher
                    if ext in (".pdf", ".docx", ".xlsx", ".zip", ".tar.gz"):
                        new_files[file_path] = (stat.st_size, stat.st_mtime)
                except Exception:
                    pass

        if init_baseline:
            for path, (size, mtime) in new_files.items():
                self.seen_files[path] = (size, mtime)
            logger.info(f"workspace_baseline_established | tracked_files={len(self.seen_files)}")
            return

        # Check for new or changed files
        for path, (size, mtime) in new_files.items():
            prev = self.seen_files.get(path)
            if not prev or prev[0] != size or prev[1] != mtime:
                # Stage file for stability tracking
                if path not in self.staged_files:
                    self.staged_files[path] = {
                        "last_size": size,
                        "stable_since": time.time()
                    }
                else:
                    st = self.staged_files[path]
                    if st["last_size"] != size:
                        st["last_size"] = size
                        st["stable_since"] = time.time()

        # Clean deleted staged files
        for path in list(self.staged_files.keys()):
            if path not in new_files:
                self.staged_files.pop(path, None)

        # Check stable files (10-second debounce)
        for path, st in list(self.staged_files.items()):
            if time.time() - st["stable_since"] >= 10.0:
                self.staged_files.pop(path, None)

                # Fetch final stable stat
                try:
                    size = os.path.getsize(path)
                    mtime = os.path.getmtime(path)
                except Exception:
                    continue

                self.seen_files[path] = (size, mtime)

                # Stage locally under exfiltration approval list
                self.pending_exfiltrations[path] = {
                    "name": os.path.basename(path),
                    "size": size,
                    "timestamp": time.time()
                }
                logger.warning(f"file_staged_for_approval | path={path}")

                # Notify operator via PyQt status_q for click review
                if self.brain and self.brain.status_q:
                    try:
                        self.brain.status_q.put_nowait({
                            "type": "WIDGET_SHOW",
                            "content": {
                                "widget": "exfiltration_request",
                                "data": {
                                    "file_path": path,
                                    "file_name": os.path.basename(path),
                                    "size_bytes": size
                                }
                            }
                        })
                        self.brain.status_q.put_nowait({
                            "type": "CHAT_MSG",
                            "speaker": "CHARLIE",
                            "content": f"Staged file '{os.path.basename(path)}' for exfiltration review."
                        })
                    except Exception:
                        pass

    def _check_world_state(self):
        """Check world model for frustration, idle state. Trigger suggestions."""
        if not self.brain:
            return

        # Check frustration level
        try:
            if hasattr(self.brain, 'world') and self.brain.world:
                frustration = getattr(self.brain.world, 'frustration_score', 0)
                if frustration > self.frustration_threshold:
                    logger.info(f"autonomy_frustration_detected | level={frustration}")
                    se = getattr(self.brain, 'suggestion_engine', None)
                    if se:
                        se.trigger_now("error_recovery")
        except Exception as e:
            logger.debug(f"world_state_check_failed | {e}")

        # Check idle state
        idle_minutes = (time.time() - self._last_activity) / 60
        if idle_minutes > self.idle_threshold_minutes:
            logger.info(f"autonomy_idle_detected | minutes={idle_minutes:.0f}")
            se = getattr(self.brain, 'suggestion_engine', None)
            if se:
                se.trigger_now("idle_resume")

    def _check_proactive_tasks(self):
        """Query OutcomeTracker for failed tools, suggest alternatives."""
        if not self.brain:
            return

        tracker = getattr(self.brain, 'outcome_tracker', None)
        if not tracker:
            return

        try:
            recent = tracker.get_recent_outcomes(event_type="tool_call", limit=20)
            failures = [o for o in recent if o.outcome_type == "failure"]
            if len(failures) >= 5:
                logger.warning(
                    f"autonomy_high_failure_rate | "
                    f"failures={len(failures)}/20 recent calls"
                )
        except Exception as e:
            logger.debug(f"proactive_task_check_failed | {e}")

    async def process_event(self, event: Event) -> str | None:
        """Process a novel event using the LLM. Returns action taken or None."""
        if not self.brain:
            logger.warning("autonomy_no_brain | cannot process event")
            return None

        prompt = self._build_prompt(event)
        logger.info(f"autonomy_processing | type={event.type} | source={event.source}")

        try:
            if hasattr(self.brain, 'orchestrator'):
                result = await self.brain.orchestrator.execute_goal(
                    prompt, source="autonomy"
                )
                if result.success:
                    logger.info(f"autonomy_success | {result.summary[:100]}")
                    return result.summary
                else:
                    logger.warning(f"autonomy_failed | {result.summary[:100]}")
                    return None
            else:
                logger.warning("autonomy_no_orchestrator")
                return None
        except Exception as e:
            logger.error(f"autonomy_error | {e}")
            return None

    def _build_prompt(self, event: Event) -> str:
        """Build an LLM prompt for a novel event."""
        return (
            f"I received an event that I don't have a rule for.\n\n"
            f"Event type: {event.type}\n"
            f"Source: {event.source}\n"
            f"Data: {event.data}\n"
            f"Urgency: {event.urgency}\n\n"
            f"What should I do? Consider:\n"
            f"- Is this urgent? Should I notify the user immediately?\n"
            f"- Can I take a safe action automatically?\n"
            f"- Should I just log this for later?\n\n"
            f"If you decide to act, describe the action clearly."
        )
