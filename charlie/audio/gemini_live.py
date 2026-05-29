"""
charlie/audio/gemini_live.py

GeminiLiveTransport — Bidirectional audio streaming via Gemini Live API.
Connects to Gemini's WebSocket endpoint for real-time voice conversations.
"""

import asyncio
import json
import logging
import threading
from typing import Callable, Optional

import numpy as np

logger = logging.getLogger("charlie.audio.gemini_live")

# Gemini Live WebSocket endpoint
GEMINI_LIVE_WS_URL = (
    "wss://generativelanguage.googleapis.com/ws/"
    "google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent"
)


class GeminiLiveTransport:
    """Bidirectional audio transport via Gemini Live API.

    Sends mic audio to Gemini and receives audio responses in real-time.
    Bypasses local STT/TTS entirely.
    """

    def __init__(
        self,
        api_key: str,
        model: str = "gemini-2.5-flash-native-audio-latest",
        voice_name: str = "Puck",
        sample_rate: int = 16000,
        on_audio_response: Optional[Callable[[np.ndarray], None]] = None,
        on_text_response: Optional[Callable[[str], None]] = None,
        on_interrupt: Optional[Callable[[], None]] = None,
    ):
        self.api_key = api_key
        self.model = model
        self.voice_name = voice_name
        self.sample_rate = sample_rate
        self.on_audio_response = on_audio_response
        self.on_text_response = on_text_response
        self.on_interrupt = on_interrupt

        self._ws = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._connected = False
        self._setup_complete = False
        self._stop_event = threading.Event()

    @property
    def connected(self) -> bool:
        return self._connected and self._setup_complete

    def start(self) -> None:
        """Start the Gemini Live connection in a background thread."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        logger.info("gemini_live_started")

    def stop(self) -> None:
        """Stop the Gemini Live connection."""
        self._stop_event.set()
        if self._loop:
            asyncio.run_coroutine_threadsafe(self._disconnect(), self._loop)
        if self._thread:
            self._thread.join(timeout=3)
        self._connected = False
        self._setup_complete = False
        logger.info("gemini_live_stopped")

    def send_audio(self, audio: np.ndarray) -> None:
        """Send audio chunk to Gemini. Expects float32 numpy array."""
        if not self.connected or not self._loop:
            return
        # Convert to 16-bit PCM bytes
        pcm = (audio * 32767).astype(np.int16).tobytes()
        asyncio.run_coroutine_threadsafe(
            self._send_pcm(pcm), self._loop
        )

    def send_text(self, text: str) -> None:
        """Send a text message to Gemini (for typed input)."""
        if not self.connected or not self._loop:
            return
        asyncio.run_coroutine_threadsafe(
            self._send_client_content(text), self._loop
        )

    def _run_loop(self) -> None:
        """Run the asyncio event loop in a background thread."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._connect_and_receive())
        except Exception as e:
            logger.error(f"gemini_live_loop_error | {e}")
        finally:
            self._connected = False
            self._setup_complete = False

    async def _connect_and_receive(self) -> None:
        """Connect to Gemini Live WebSocket and receive messages."""
        try:
            import websockets
        except ImportError:
            logger.error("websockets not installed — pip install websockets")
            return

        url = f"{GEMINI_LIVE_WS_URL}?key={self.api_key}"
        headers = {"Content-Type": "application/json"}

        try:
            # websockets 15.x uses additional_headers
            async with websockets.connect(url, additional_headers=headers) as ws:
                self._ws = ws
                self._connected = True
                logger.info("gemini_live_connected")

                # Send setup message
                await self._send_setup()

                # Receive loop
                async for message in ws:
                    if self._stop_event.is_set():
                        break
                    await self._handle_message(message)

        except Exception as e:
            logger.error(f"gemini_live_ws_error | {e}")
        finally:
            self._connected = False
            self._setup_complete = False
            self._ws = None

    async def _send_setup(self) -> None:
        """Send the initial setup message to configure the session."""
        setup = {
            "setup": {
                "model": f"models/{self.model}",
                "generation_config": {
                    "response_modalities": ["AUDIO"],
                    "speech_config": {
                        "voice_config": {
                            "prebuilt_voice_config": {
                                "voice_name": self.voice_name
                            }
                        }
                    },
                },
                "system_instruction": {
                    "parts": [
                        {
                            "text": (
                                "You are CHARLIE, a helpful AI assistant. "
                                "Respond naturally and concisely in conversation."
                            )
                        }
                    ]
                },
            }
        }
        await self._ws.send(json.dumps(setup))
        logger.info(f"gemini_live_setup_sent | model={self.model} voice={self.voice_name}")

    async def _send_pcm(self, pcm_bytes: bytes) -> None:
        """Send PCM audio bytes to Gemini."""
        if not self._ws:
            return
        msg = {
            "realtime_input": {
                "media_chunks": [
                    {
                        "data": pcm_bytes.hex(),
                        "mime_type": "audio/pcm;rate=16000",
                    }
                ]
            }
        }
        await self._ws.send(json.dumps(msg))

    async def _send_client_content(self, text: str) -> None:
        """Send text content to Gemini."""
        if not self._ws:
            return
        msg = {
            "client_content": {
                "turns": [
                    {
                        "role": "user",
                        "parts": [{"text": text}],
                    }
                ],
                "turn_complete": True,
            }
        }
        await self._ws.send(json.dumps(msg))

    async def _handle_message(self, raw: str | bytes) -> None:
        """Handle an incoming message from Gemini."""
        if isinstance(raw, bytes):
            # Binary audio response
            if self.on_audio_response:
                audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32767.0
                self.on_audio_response(audio)
            return

        # JSON message
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return

        # Setup complete
        if "setupComplete" in data:
            self._setup_complete = True
            logger.info("gemini_live_setup_complete")
            return

        # Server content (text or audio)
        if "serverContent" in data:
            content = data["serverContent"]
            parts = content.get("modelTurn", {}).get("parts", [])
            for part in parts:
                if "text" in part and self.on_text_response:
                    self.on_text_response(part["text"])
                if "inlineData" in part and self.on_audio_response:
                    import base64
                    audio_b64 = part["inlineData"].get("data", "")
                    if audio_b64:
                        raw_bytes = base64.b64decode(audio_b64)
                        audio = np.frombuffer(raw_bytes, dtype=np.int16).astype(np.float32) / 32767.0
                        self.on_audio_response(audio)

            # Check for turn complete / interruption
            if content.get("turnComplete"):
                logger.debug("gemini_live_turn_complete")
            if content.get("interrupted"):
                if self.on_interrupt:
                    self.on_interrupt()
                logger.debug("gemini_live_interrupted")

        # Tool calls (if Gemini requests function calls)
        if "toolCall" in data:
            logger.debug(f"gemini_live_tool_call | {data['toolCall']}")

    async def _disconnect(self) -> None:
        """Gracefully close the WebSocket."""
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
