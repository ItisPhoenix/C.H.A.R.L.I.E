"""
charlie/automation/clipboard_diagnostician.py
Background clipboard listener.
Captures clipboard content, identifies tracebacks and compiler errors, and prepares direct optimization advice.
"""

import time
import threading
import hashlib
import re
import pyperclip
from charlie.utils.logger import get_logger

logger = get_logger("CLIPBOARD_DIAGNOSTICIAN")


class ClipboardDiagnostician:
    """Monitors the clipboard for compiler errors/tracebacks and prepares optimization advice."""

    def __init__(self, status_q=None, telegram_q=None):
        self.status_q = status_q
        self.telegram_q = telegram_q
        self._running = False
        self._thread = None
        self.last_content_hash = None

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._thread.start()
        logger.info("clipboard_diagnostician_started")

    def stop(self):
        self._running = False

    def _monitor_loop(self):
        while self._running:
            try:
                time.sleep(2.0)
                content = pyperclip.paste()
                if not content or not isinstance(content, str):
                    continue

                content = content.strip()
                if not content:
                    continue

                # Hash content to prevent repeating the same warning
                content_hash = hashlib.md5(content.encode('utf-8')).hexdigest()
                if content_hash == self.last_content_hash:
                    continue

                self.last_content_hash = content_hash

                # Check if it looks like a traceback or compiler error
                if self._is_traceback(content):
                    logger.info("traceback_detected_in_clipboard")
                    self._diagnose(content)
            except Exception as e:
                logger.debug(f"clipboard_monitor_loop_error | {e}")

    def _is_traceback(self, text: str) -> bool:
        """Determines if the text contains a traceback or compiler error."""
        text_lower = text.lower()

        # 1. Direct python traceback indicator
        if "traceback (most recent call last):" in text_lower:
            return True

        # 2. Check for File "...", line X patterns with common exception names
        has_file_line = re.search(r'file\s+["\'].*?["\'],\s+line\s+\d+', text_lower)
        if has_file_line:
            return True

        # 3. Check for specific compiler error output (e.g. rust panic, node exception, python syntax error)
        if any(ind in text_lower for ind in ["panic", "uncaught exception", "unhandledrejection", "compilation error"]):
            return True

        return False

    def _diagnose(self, traceback_text: str):
        """Generates advice for the traceback."""
        # 1. Fast local rule-based diagnostics
        advice = self._get_local_advice(traceback_text)

        # Format the briefing message
        briefing = f"Sir, I detected a traceback in your clipboard. Here is my immediate analysis:\n\n{advice}"

        # 2. Push to status queue
        if self.status_q:
            try:
                self.status_q.put_nowait({
                    "type": "CHAT_MSG",
                    "speaker": "CHARLIE",
                    "content": briefing
                })
            except Exception as e:
                logger.debug(f"status_q_push_failed | {e}")

        # 3. Push to Telegram
        if self.telegram_q:
            try:
                self.telegram_q.put_nowait({
                    "type": "CHAT_MSG",
                    "speaker": "CHARLIE",
                    "content": f"<b>🔍 AUTOMATED CLIPBOARD DIAGNOSTIC BRIEF:</b>\n{briefing}"
                })
            except Exception as e:
                logger.debug(f"telegram_q_push_failed | {e}")

        # 4. Trigger asynchronous LLM diagnostics if brain is available
        from charlie.utils import queue_bridge
        brain = queue_bridge.get_brain()
        if brain and hasattr(brain, "llm_client") and brain.llm_client:
            # We can schedule a background task in the brain's event loop
            import asyncio
            if hasattr(brain, "loop") and brain.loop:
                asyncio.run_coroutine_threadsafe(
                    self._query_llm_diagnostics(brain.llm_client, traceback_text),
                    brain.loop
                )

    def _get_local_advice(self, text: str) -> str:
        """Parses traceback text and provides rule-based recommendations."""
        recommendations = []
        text_lower = text.lower()

        if "filenotfounderror" in text_lower:
            recommendations.append("📍 **FileNotFoundError:** The target file or path does not exist. Verify the target directory, filename, and relative/absolute path routing.")
        if "importerror" in text_lower or "modulenotfounderror" in text_lower:
            match = re.search(r"no module named ['\"](.*?)['\"]", text_lower)
            module_name = match.group(1) if match else "the required package"
            recommendations.append(f"📦 **ModuleNotFoundError:** Missing dependency. Run `uv pip install {module_name}` or `pip install {module_name}` to sync dependencies.")
        if "syntaxerror" in text_lower:
            recommendations.append("⚠️ **SyntaxError:** Code violates language grammar rules. Check for missing closing parentheses `)`, quotes `\"`, or incorrect indentation structure.")
        if "indentationerror" in text_lower:
            recommendations.append("📐 **IndentationError:** Mismatched spacing/tabs. Align tabs and spaces (prefer 4 spaces for Python consistency).")
        if "keyerror" in text_lower:
            recommendations.append("🔑 **KeyError:** Attempted to access a dictionary key that does not exist. Add a safety check: `dict.get(key, default)`.")
        if "attributeerror" in text_lower:
            recommendations.append("🧩 **AttributeError:** Trying to reference an attribute or method that is undefined on this class/object type.")
        if "typeerror" in text_lower:
            recommendations.append("🔢 **TypeError:** Invalid operation applied to mismatched data types (e.g. adding string to integer).")

        if not recommendations:
            recommendations.append("⚙️ **Standard Traceback:** Review the line specified at the bottom of the stack trace to locate the error origin.")

        return "\n".join(recommendations)

    async def _query_llm_diagnostics(self, llm_client, traceback_text: str):
        """Asynchronously query LLM for premium optimization advice."""
        try:
            logger.info("requesting_llm_diagnostics")
            prompt = (
                "You are C.H.A.R.L.I.E., a helpful autonomous AI system. "
                "The user copied this traceback/compiler error to their clipboard:\n"
                f"```\n{traceback_text[:1500]}\n```\n"
                "Provide a brief, terse explanation of what went wrong "
                "and exactly how the user can resolve/fix it in 3 bullet points."
            )

            response = await llm_client.complete(
                system_prompt="You are C.H.A.R.L.I.E., an elite local assistant.",
                messages=[{"role": "user", "content": prompt}]
            )

            if response:
                logger.info("llm_diagnostics_retrieved")
                formatted_response = f"Sir, I have analyzed the traceback via secondary reasoning. Here is the suggested resolution:\n\n{response}"

                # Push premium diagnostics back to queues
                if self.status_q:
                    self.status_q.put_nowait({
                        "type": "CHAT_MSG",
                        "speaker": "CHARLIE",
                        "content": formatted_response
                    })
                if self.telegram_q:
                    self.telegram_q.put_nowait({
                        "type": "CHAT_MSG",
                        "speaker": "CHARLIE",
                        "content": f"<b>🧠 AI LOG OPTIMIZATION BRIEF:</b>\n{formatted_response}"
                    })
        except Exception as e:
            logger.error(f"llm_diagnostics_query_failed | {e}")
