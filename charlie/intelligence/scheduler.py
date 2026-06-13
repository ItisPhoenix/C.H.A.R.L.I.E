import json
import logging
import os
import subprocess
import threading
import time
from typing import Optional

from charlie.intelligence.task_queue import AutonomousTaskQueue

logger = logging.getLogger("charlie.intelligence.scheduler")


class TaskScheduler:
    """
    TaskScheduler: Manages periodic triggers for background tasks.
    Adds tasks to AutonomousTaskQueue based on time intervals.
    """

    def __init__(self, task_queue: AutonomousTaskQueue, brain=None):
        self.queue = task_queue
        self.brain = brain
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

        self.state_path = "config/scheduler_state.json"
        self.last_runs = self._load_state()

    def _load_state(self) -> dict:
        if os.path.exists(self.state_path):
            try:
                with open(self.state_path, "r") as f:
                    return json.load(f)
            except Exception:
                pass
        return {
            "vram_temp_check": 0.0,
            "memory_graph_refresh": 0.0,
            "chroma_vacuum": 0.0,
            "git_summary": 0.0,
            "daily_briefing": 0.0,
            "skill_evolution": 0.0,
            "user_model_review": 0.0,
        }

    def _save_state(self):
        try:
            os.makedirs("config", exist_ok=True)
            with open(self.state_path, "w") as f:
                json.dump(self.last_runs, f)
        except Exception:
            pass

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._scheduler_loop, daemon=True)
        self._thread.start()
        logger.info("scheduler_ignited")

    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2)
        logger.info("scheduler_halted")

    def _scheduler_loop(self):
        while not self._stop_event.is_set():
            try:
                self._check_and_schedule()
            except Exception as e:
                logger.error(f"scheduler_check_failed | {e}")
            time.sleep(60)  # Check every minute

    def _check_and_schedule(self):
        now = time.time()

        # 1. Hourly: VRAM/CPU temp check
        if now - self.last_runs["vram_temp_check"] >= 3600:
            self.queue.add_task("VRAM/CPU Temp Check", self._task_vram_check, priority=30)
            self.last_runs["vram_temp_check"] = now

        # 2. Every 30 min: Memory Graph refresh
        if now - self.last_runs["memory_graph_refresh"] >= 1800:
            self.queue.add_task("Memory Graph Refresh", self._task_graph_refresh, priority=20)
            self.last_runs["memory_graph_refresh"] = now

        # 3. Daily: ChromaDB vacuum + re-index
        if now - self.last_runs["chroma_vacuum"] >= 86400:
            self.queue.add_task("ChromaDB Vacuum", self._task_chroma_vacuum, priority=80)
            self.last_runs["chroma_vacuum"] = now

        # 4. Weekly: git log -> self-summary
        if now - self.last_runs["git_summary"] >= 604800:
            self.queue.add_task("Git Summary", self._task_git_summary, priority=90)
            self.last_runs["git_summary"] = now

        # 5. Daily: Morning briefing (once per day, only if morning hours 6-10)
        import datetime

        hour = datetime.datetime.now().hour
        if now - self.last_runs["daily_briefing"] >= 86400 and 6 <= hour <= 10:
            self.queue.add_task("Daily Briefing", self._task_daily_briefing, priority=10)
            self.last_runs["daily_briefing"] = now

        # 6. Weekly: Self-evolution (optimize skills using free cloud APIs — OpenRouter, NIM, Groq)
        if now - self.last_runs.get("skill_evolution", 0) >= 604800:
            self.queue.add_task("Skill Evolution", self._task_skill_evolution, priority=95)
            self.last_runs["skill_evolution"] = now

        # 7. Daily: User model review (update USER.md from recent sessions)
        if now - self.last_runs.get("user_model_review", 0) >= 86400:
            self.queue.add_task("User Model Review", self._task_user_model_review, priority=85)
            self.last_runs["user_model_review"] = now

        self._save_state()

    # --- Real task implementations ---

    def _task_vram_check(self):
        """Check VRAM and CPU temperature. Alert if critical."""
        try:
            import psutil

            from charlie.utils.system import get_vram_percent

            vram_pct = get_vram_percent()
            cpu_temp = None

            # Try to read CPU temperature (Windows: requires LibreHardwareMonitor or similar)
            try:
                temps = psutil.sensors_temperatures()
                if temps:
                    for name, entries in temps.items():
                        if entries:
                            cpu_temp = entries[0].current
                            break
            except (AttributeError, KeyError):
                pass  # Temperature sensors not available

            status_parts = [f"VRAM={vram_pct:.0f}%"]
            if cpu_temp is not None:
                status_parts.append(f"CPU_Temp={cpu_temp:.0f}C")

            # Alert thresholds
            alerts = []
            if vram_pct > 90:
                alerts.append(f"VRAM critical: {vram_pct:.0f}%")
            if cpu_temp is not None and cpu_temp > 85:
                alerts.append(f"CPU temperature critical: {cpu_temp:.0f}C")

            if alerts:
                msg = "SYSTEM ALERT: " + " | ".join(alerts)
                logger.warning(f"bg_task | vram_check | {msg}")
                # Push alert to status_q
                if self.brain:
                    try:
                        self.brain.status_q.put_nowait(
                            {
                                "type": "INTEGRATION_UPDATE",
                                "data": {"service": "system", "count": 1, "detail": msg},
                            }
                        )
                    except Exception:
                        pass
            else:
                logger.info(f"bg_task | vram_check | {' | '.join(status_parts)}")

        except Exception as e:
            logger.error(f"bg_task | vram_check | error={e}")

    def _task_graph_refresh(self):
        """Refresh the memory graph index."""
        try:
            if self.brain and hasattr(self.brain, "graph_builder"):
                self.brain.graph_builder.run_full_index()
                logger.info("bg_task | memory_graph_refresh | completed")
            else:
                logger.warning("bg_task | memory_graph_refresh | no graph_builder available")
        except Exception as e:
            logger.error(f"bg_task | memory_graph_refresh | error={e}")

    def _task_chroma_vacuum(self):
        """Vacuum and deduplicate ChromaDB."""
        try:
            if self.brain and hasattr(self.brain, "memory"):
                self.brain.memory.consolidate()
                logger.info("bg_task | chroma_vacuum | completed")
            else:
                logger.warning("bg_task | chroma_vacuum | no memory manager available")
        except Exception as e:
            logger.error(f"bg_task | chroma_vacuum | error={e}")

    def _task_git_summary(self):
        """Generate weekly git summary."""
        try:
            result = subprocess.run(
                ["git", "log", "--oneline", "--since=7.days", "--no-merges"],
                capture_output=True,
                text=True,
                timeout=15,
                cwd=os.getcwd(),
            )
            if result.returncode == 0 and result.stdout.strip():
                lines = result.stdout.strip().split("\n")
                summary = f"Git Summary (last 7 days): {len(lines)} commits\n"
                summary += "\n".join(f"  {line}" for line in lines[:20])
                if len(lines) > 20:
                    summary += f"\n  ... and {len(lines) - 20} more"
                logger.info(f"bg_task | git_summary | {len(lines)} commits found")

                # Push to status_q
                if self.brain:
                    try:
                        self.brain.status_q.put_nowait(
                            {
                                "type": "INTEGRATION_UPDATE",
                                "data": {"service": "git", "count": len(lines), "detail": summary},
                            }
                        )
                    except Exception:
                        pass
            else:
                logger.info("bg_task | git_summary | no commits in last 7 days")
        except Exception as e:
            logger.error(f"bg_task | git_summary | error={e}")


    def _task_skill_evolution(self):
        """Run self-evolution cycle on skills."""
        try:
            if not self.brain or not hasattr(self.brain, "evolution_engine"):
                return
            from pathlib import Path

            result = self.brain.evolution_engine.run_evolution(
                outcome_tracker=getattr(self.brain, "outcome_tracker", None),
                session_search=getattr(self.brain, "session_search", None),
                skills_dir=Path("charlie/skills"),
                llm_client=getattr(self.brain, "llm_client", None),
                status_q=self.brain.status_q,
            )
            logger.info(
                f"bg_task | skill_evolution | reviewed={result.get('skills_reviewed', 0)} improved={result.get('skills_improved', 0)}"
            )
        except Exception as e:
            logger.error(f"bg_task | skill_evolution | error={e}")

    def _task_user_model_review(self):
        """Update user profile from recent sessions."""
        try:
            if not self.brain or not hasattr(self.brain, "user_model"):
                return
            # Get recent turns from session search
            session_search = getattr(self.brain, "session_search", None)
            if session_search and hasattr(session_search, "get_recent"):
                turns = session_search.get_recent(limit=30)
                if turns:
                    self.brain.user_model.review_session(
                        turns,
                        llm_client=getattr(self.brain, "llm_client", None),
                    )
                    logger.info(f"bg_task | user_model_review | turns={len(turns)}")
        except Exception as e:
            logger.error(f"bg_task | user_model_review | error={e}")
