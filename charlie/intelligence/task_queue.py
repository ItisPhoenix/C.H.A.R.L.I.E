import logging
import threading
from dataclasses import dataclass, field
from typing import Callable, List, Optional

from charlie.perception.world_model import WorldModel

logger = logging.getLogger("charlie.intelligence.task_queue")

@dataclass(order=True)
class BackgroundTask:
    priority: int
    name: str = field(compare=False)
    func: Callable = field(compare=False)
    args: tuple = field(default=(), compare=False)
    kwargs: dict = field(default_factory=dict, compare=False)

class AutonomousTaskQueue:
    """
    AutonomousTaskQueue: Background maintenance worker.
    Executes tasks only when user is idle for > 5 minutes.
    Interrupts execution if user returns.
    """
    def __init__(self, world_model: WorldModel):
        self.world = world_model
        self.queue: List[BackgroundTask] = []
        self._stop_event = threading.Event()
        self._current_task_cancel = threading.Event()
        self._condition = threading.Condition()
        self._thread: Optional[threading.Thread] = None
        self._current_task_thread: Optional[threading.Thread] = None
        self.is_running_task = False

    def add_task(self, name: str, func: Callable, priority: int = 10, *args, **kwargs):
        task = BackgroundTask(priority=priority, name=name, func=func, args=args, kwargs=kwargs)
        with self._condition:
            self.queue.append(task)
            self.queue.sort()
            self._condition.notify()
        logger.debug("task_added | %s | priority=%s", name, priority)

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._worker_loop, daemon=True)
        self._thread.start()
        logger.info("task_queue_ignited")

    def stop(self):
        self._stop_event.set()
        with self._condition:
            self._condition.notify_all()
        if self._thread:
            self._thread.join(timeout=2)
        logger.info("task_queue_halted")

    def _worker_loop(self):
        while not self._stop_event.is_set():
            task_ready = False
            with self._condition:
                while (not self.queue or self.world.user_idle_seconds <= 300) and not self._stop_event.is_set():
                    self._condition.wait(timeout=10.0)

                if self._stop_event.is_set(): break
                if self.queue and self.world.user_idle_seconds > 300:
                    task_ready = True

            if task_ready:
                self._execute_next()

    def _execute_next(self):
        if not self.queue: return

        task = self.queue.pop(0)
        logger.info("task_start | %s", task.name)
        self.is_running_task = True

        try:
            # Reset cancel event for this task
            self._current_task_cancel.clear()

            # We run in a separate thread so we can monitor user return
            def wrapper():
                try:
                    # Pass cancel_event if task accepts it
                    import inspect
                    sig = inspect.signature(task.func)
                    if 'cancel_event' in sig.parameters:
                        task.func(*task.args, cancel_event=self._current_task_cancel, **task.kwargs)
                    else:
                        task.func(*task.args, **task.kwargs)
                except Exception as e:
                    logger.error("task_failed | %s | %s", task.name, e)
                finally:
                    self.is_running_task = False

            t = threading.Thread(target=wrapper, daemon=True)
            t.start()

            # Monitor loop: abort if user returns
            while t.is_alive():
                if self.world.user_idle_seconds < 10:
                    logger.warning("task_interrupted | user_returned | %s", task.name)
                    self._current_task_cancel.set()
                    self.is_running_task = False
                    break
                self._stop_event.wait(1.0)

        except Exception as e:
            logger.error("task_dispatch_failed | %s | %s", task.name, e)
            self.is_running_task = False
