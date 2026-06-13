import asyncio
import base64
import io
import time
from threading import Lock

from charlie.utils.logger import get_logger

logger = get_logger("charlie.brain.vision")


def _lazy_imports():
    import pygetwindow as gw
    from mss import mss
    from PIL import Image
    return gw, mss, Image


class VisionHandler:
    def __init__(self, brain):
        self.brain = brain
        self.last_sentinel_scan = 0.0
        self.last_sentinel_report = ""
        self._last_vision_use = 0.0
        self._vram_lock = Lock()
        self._lazy = None

    def _get_lazy(self):
        if self._lazy is None:
            self._lazy = _lazy_imports()
        return self._lazy

    def _mark_vision_use(self):
        with self._vram_lock:
            self._last_vision_use = time.time()

    def _vision_idle_seconds(self) -> float:
        with self._vram_lock:
            return time.time() - self._last_vision_use if self._last_vision_use > 0 else 0

    def capture_screen(self, for_vision=False, region: tuple | None = None) -> str:
        """Captures primary monitor (or a region), returns base64 PNG string.

        Args:
            for_vision: Use smaller thumbnail for vision model input.
            region: Optional (x, y, width, height) tuple to capture a sub-region.
        """
        from charlie.tools.vision_context import is_sensitive_window_active

        if is_sensitive_window_active():
            logger.warning("capture_screen_blocked | sensitive active window focus detected")
            gw, mss, Image = self._get_lazy()
            black_img = Image.new("RGB", (100, 100), color="black")
            buf = io.BytesIO()
            black_img.save(buf, format="PNG")
            return base64.b64encode(buf.getvalue()).decode("utf-8")

        gw, mss, Image = self._get_lazy()
        with mss() as sct:
            if region:
                x, y, w, h = region
                monitor = {"left": x, "top": y, "width": w, "height": h}
            else:
                monitor = sct.monitors[1]
            screenshot = sct.grab(monitor)
            img = Image.frombytes("RGB", screenshot.size, screenshot.bgra, "raw", "BGRX")

            # Apply OCR Redaction via PrivacyRedactor using a temp file
            import os
            import tempfile
            from charlie.privacy.redactor import get_redactor

            # Use NamedTemporaryFile to avoid predictable filename race condition
            temp_in = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
            temp_out = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
            temp_out_path = temp_out.name
            temp_out.close()
            try:
                img.save(temp_in, format="PNG")
                temp_in.close()
                redactor = get_redactor()
                redacted_path = redactor.redact_image(temp_in.name, temp_out_path)
                if os.path.exists(redacted_path):
                    img = Image.open(redacted_path)
            except Exception as e:
                logger.error(f"capture_screen_redaction_failed | {e}")
            finally:
                for path in (temp_in.name, temp_out_path):
                    if os.path.exists(path):
                        try:
                            os.remove(path)
                        except Exception:
                            pass

            target_size = (336, 336) if for_vision else (384, 384)
            img.thumbnail(target_size)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return base64.b64encode(buf.getvalue()).decode("utf-8")

    async def ask_vision(self, query: str, region: tuple | None = None) -> str:
        self._mark_vision_use()
        """Vision request grounded in current OS window context.

        Args:
            query: The question to ask the vision model.
            region: Optional (x, y, width, height) tuple for region-specific capture.
        """
        if not self.brain.context_builder.vram_warning_check():
            return "Vision unavailable — VRAM too high, Sir."

        try:
            await self.brain.model_manager.load_vision_model()
            b64_img = self.capture_screen(for_vision=True, region=region)
            gw, mss, Image = self._get_lazy()
            try:
                titles = [w.strip() for w in gw.getAllTitles() if w.strip()]
                os_context = ", ".join(titles[:5])
            except Exception:
                os_context = "Unknown"

            prompt = (
                f"OPEN WINDOWS: {os_context}\nQUERY: {query}\nAnswer based ONLY on what you see. Be extremely brief."
            )

            # Route through ModelRouter for provider abstraction + failover
            try:
                response = await self.brain.model_router.complete(
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": prompt},
                                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64_img}"}},
                            ],
                        }
                    ],
                    task_type="vision",
                    stream=False,
                )
                if response and hasattr(response, "content"):
                    await self.brain.model_manager.load_text_model()
                    return response.content
                result = str(response) if response else "Vision model returned empty response"
                await self.brain.model_manager.load_text_model()
                return result
            except Exception as router_err:
                logger.warning("vision_router_failed | falling back to direct call | error=%s", router_err)
                return await self._ask_vision_direct(prompt, b64_img)
        except Exception as e:
            logger.error("ask_vision_failed | error=%s", e)
            await self.brain.model_manager.load_text_model()
            return "Vision failed."

    async def _ask_vision_direct(self, prompt: str, b64_img: str) -> str:
        """Direct HTTP call to vision model (fallback)."""
        import aiohttp

        try:
            payload = {
                "model": self.brain.model_manager.llm_vision_model,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64_img}"}},
                        ],
                    }
                ],
                "stream": False,
            }

            async with self.brain.session.post(
                f"{self.brain.model_manager.llm_vision_url}/chat/completions",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=40),
            ) as r:
                data = await r.json()
                if r.status >= 400 or "error" in data:
                    await self.brain.model_manager.load_text_model()
                    return "Sir, vision model error."
                result = data.get("choices", [{}])[0].get("message", {}).get("content", "Unclear.")
                await self.brain.model_manager.load_text_model()
                return result
        except Exception as e:
            logger.error("_ask_vision_direct_failed | error=%s", e)
            await self.brain.model_manager.load_text_model()
            return "Vision failed."

    async def analyze_image(self, image_path: str, query: str = "Describe this image.") -> str:
        self._mark_vision_use()
        """Analyze an arbitrary image file with the vision model."""
        if not self.brain.context_builder.vram_warning_check():
            return "Vision unavailable — VRAM too high, Sir."

        import os

        try:
            if not os.path.exists(image_path):
                return f"Image not found: {image_path}"

            # Load and encode the image
            gw, mss, Image = self._get_lazy()
            img = Image.open(image_path)
            img.thumbnail((512, 512))  # Larger than screen capture for detail
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            b64_img = base64.b64encode(buf.getvalue()).decode("utf-8")

            await self.brain.model_manager.load_vision_model()

            prompt = f"QUERY: {query}\nAnswer based ONLY on what you see in this image."

            # Route through ModelRouter for provider abstraction + failover
            try:
                response = await self.brain.model_router.complete(
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": prompt},
                                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64_img}"}},
                            ],
                        }
                    ],
                    task_type="vision",
                    stream=False,
                )
                if response and hasattr(response, "content"):
                    await self.brain.model_manager.load_text_model()
                    return response.content
                result = str(response) if response else "Vision model returned empty response"
                await self.brain.model_manager.load_text_model()
                return result
            except Exception as router_err:
                logger.warning("analyze_image_router_failed | falling back to direct call | error=%s", router_err)
                return await self._analyze_image_direct(prompt, b64_img)
        except Exception as e:
            logger.error("analyze_image_failed | error=%s", e)
            await self.brain.model_manager.load_text_model()
            return f"Image analysis failed: {e}"

    async def _analyze_image_direct(self, prompt: str, b64_img: str) -> str:
        """Direct HTTP call for image analysis (fallback)."""
        import aiohttp

        try:
            payload = {
                "model": self.brain.model_manager.llm_vision_model,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64_img}"}},
                        ],
                    }
                ],
                "stream": False,
            }

            async with self.brain.session.post(
                f"{self.brain.model_manager.llm_vision_url}/chat/completions",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=40),
            ) as r:
                data = await r.json()
                if r.status >= 400 or "error" in data:
                    await self.brain.model_manager.load_text_model()
                    return "Vision model error."
                result = data.get("choices", [{}])[0].get("message", {}).get("content", "Unclear.")
                await self.brain.model_manager.load_text_model()
                return result
        except Exception as e:
            logger.error("_analyze_image_direct_failed | error=%s", e)
            await self.brain.model_manager.load_text_model()
            return f"Image analysis failed: {e}"

    def run_sentinel_scan(self) -> None:
        """Autonomously glances at screen for technical distress."""
        if not self.brain.context_builder.vram_warning_check():
            return
        prompt = (
            "Sir is currently working. Look at the screen. "
            "Scan for EXPLICIT technical errors, syntax errors (red squiggles), "
            "terminal crashes, or tracebacks. "
            "If you see something requiring attention, describe it briefly. "
            "If everything looks normal, say exactly: CLEAR"
        )
        try:
            future = asyncio.run_coroutine_threadsafe(self.ask_vision(prompt), self.brain.loop)
            report = future.result(timeout=45)
            if report and "CLEAR" not in report.upper():
                if report != self.last_sentinel_report:
                    logger.info("sentinel_anomaly_detected", report=report)
                    self.last_sentinel_report = report

                    # Feed frustration detector
                    if hasattr(self.brain, "ace"):
                        self.brain.ace.detector.process_error(report)

                    self.brain._safe_put(
                        self.brain.brain_task_q,
                        {
                            "type": "PROACTIVE_EVENT",
                            "source": "sentinel",
                            "content": report,
                        },
                    )
            elif report and "CLEAR" in report.upper():
                self.last_sentinel_report = ""
        except Exception as e:
            logger.error(f"sentinel_scan_failed | {e}")

    def peripheral_vision_loop(self) -> None:
        """Legacy background loop for peripheral vision (sentinel scan removed — see LLMSettings)."""
        while True:
            try:
                # active_window now updated by ACE in world_model
                self.brain.active_window = self.brain.world.active_app
            except Exception as e:
                logger.error(f"peripheral_vision_err | {e}")
            time.sleep(2)

    # --- High-level vision tools ---

    async def analyze_screen(self, query: str = "Describe what's on this screen") -> str:
        """Analyze the current screen with a vision model.

        Args:
            query: What to ask about the screen contents.
        """
        return await self.ask_vision(query)

    async def read_text(self, region: tuple | None = None) -> str:
        """Extract text from screen or a specific region.

        Args:
            region: Optional (x, y, width, height) tuple for region capture.
        """
        if region:
            return await self.ask_vision(
                "Read and extract ALL visible text from this screen region. Return only the text, no description.",
                region=region,
            )
        return await self.ask_vision(
            "Read and extract ALL visible text from this screen. Return only the text, no description."
        )

    async def find_element(self, description: str) -> str:
        """Find a UI element by description on the current screen.

        Args:
            description: Natural language description of the element to locate.
        """
        return await self.ask_vision(
            f"Find the UI element described as: '{description}'. "
            "Describe its exact location (top-left, center, bottom-right, etc.) "
            "and any nearby text that identifies it."
        )

    async def detect_error(self) -> str:
        """Scan the current screen for error messages, dialogs, or alerts."""
        return await self.ask_vision(
            "Look at this screen carefully. Are there any error messages, "
            "error dialogs, crash reports, warning banners, or alert popups visible? "
            "If yes, describe each error found. If no errors, say 'No errors detected.'"
        )
