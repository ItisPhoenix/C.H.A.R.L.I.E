# ruff: noqa: E402
import sys
import io
import os
import logging
import re
import time
import asyncio
# Windows: pyzmq needs Selector event loop, not Proactor.
# Suppress the pyzmq RuntimeWarning about add_reader — tornado 6.x
# already provides the fallback, but the warning fires on first use.
import warnings
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    warnings.filterwarnings("ignore", message=".*add_reader.*", category=RuntimeWarning)
import subprocess
import uuid


# --- Text normalization for multi-app commands ---
# When user says "Open Chrome calculator notepad", insert "and" between items
# so the model treats them as separate commands
_APP_LIST_PATTERN = re.compile(
    r'(?:open|start|launch|run)\s+'
    r'([a-zA-Z][a-zA-Z0-9]*'
    r'(?:\s+(?:and\s+)?[a-zA-Z][a-zA-Z0-9]*)*)',
    re.IGNORECASE
)

_KNOWN_APPS = {
    'chrome', 'firefox', 'edge', 'opera', 'brave', 'vivaldi',
    'notepad', 'calculator', 'calc', 'paint', 'explorer', 'file',
    'word', 'excel', 'powerpoint', 'outlook', 'teams', 'slack',
    'discord', 'spotify', 'vlc', 'steam', 'code', 'vscode',
    'terminal', 'powershell', 'cmd', 'prompt',
}


def _normalize_app_list(text: str) -> str:
    """Insert 'and' between app names in commands like 'Open Chrome calculator notepad'."""
    def _replace_match(m: re.Match) -> str:
        prefix = m.group(0)[:m.start(1) - m.start(0)]
        items_str = m.group(1)
        items = items_str.split()
        if len(items) <= 1:
            return m.group(0)
        # Separate known apps from unknown words
        apps = []
        others = []
        for item in items:
            if item.lower() in _KNOWN_APPS:
                apps.append(item)
            else:
                others.append(item)
        if len(apps) < 2:
            return m.group(0)
        # Rebuild with "and" between apps
        normalized_apps = ' and '.join(apps)
        if others:
            return f"{prefix}{normalized_apps} {' '.join(others)}"
        return f"{prefix}{normalized_apps}"
    return _APP_LIST_PATTERN.sub(_replace_match, text)
from pathlib import Path

# 1. SETUP ENVIRONMENT FIRST
class SafeStreamWrapper:
    def __init__(self, stream):
        self.stream = stream

    def write(self, data):
        try:
            return self.stream.write(data)
        except OSError as e:
            if e.errno not in (22, 32, 9):
                raise
        except ValueError:
            pass

    def flush(self):
        try:
            return self.stream.flush()
        except OSError as e:
            if e.errno not in (22, 32, 9):
                raise
        except ValueError:
            pass

    def __getattr__(self, name):
        return getattr(self.stream, name)

if sys.platform == 'win32':
    sys.stdout = SafeStreamWrapper(io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True, write_through=True))
    sys.stderr = SafeStreamWrapper(io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', line_buffering=True, write_through=True))
else:
    sys.stdout = SafeStreamWrapper(sys.stdout)
    sys.stderr = SafeStreamWrapper(sys.stderr)

os.makedirs("logs", exist_ok=True)
LOG_FILE = "logs/charlie.log"

# 2. CONFIGURE SPLIT LOGGING
root_logger = logging.getLogger()
root_logger.setLevel(logging.DEBUG)

file_formatter = logging.Formatter('%(asctime)s [%(name)s] [%(levelname)s] %(funcName)s:%(lineno)d - %(message)s')
file_handler = logging.FileHandler(LOG_FILE, encoding='utf-8', mode='a')
file_handler.setFormatter(file_formatter)

console_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(console_formatter)

root_logger.handlers = []
root_logger.addHandler(file_handler)
root_logger.addHandler(console_handler)

# 3. NOW IMPORT CHARLIE MODULES
from charlie.config import config
from charlie.session_store import SessionStore
from charlie.core import Brain
from charlie.personality import get_emotion_for_context, parse_voice_command
from charlie.voice import VoiceEngine
from charlie.ipc import EventBus

logger = logging.getLogger("charlie.main")
# Unique launch identity -- every main() invocation gets one so the sidebar can
# filter "this launch" vs "all history" (Hermes-style single-DB isolation).
_LAUNCH_ID: str = str(uuid.uuid4())


# Streaming TTS flush thresholds (chars, not words)
# First sentence: speak after first sentence boundary. Force-flush at 200 chars if no boundary.
_SENTENCE_BOUNDARY = re.compile(r'(?<=[.!?])\s+')
_MAX_FLUSH_CHARS = 200  # Force-flush at word boundary if no sentence boundary seen
async def main():
    # Suppress pyzmq CancelledError traceback on Windows shutdown.
    # See web_server_entry.py for full explanation.
    loop = asyncio.get_running_loop()
    _orig_handler = loop.call_exception_handler
    def _guarded_handler(ctx):
        if not isinstance(ctx.get("exception"), asyncio.CancelledError):
            _orig_handler(ctx)
    loop.call_exception_handler = _guarded_handler

    logger.info("Charlie is waking up...")
    voice = None
    store = None
    speech_echo_cooldown = 0.0
    last_emotion = "neutral"
    web_proc = None

    try:
        store = SessionStore(config.session_db_path)
    except Exception as e:
        logger.error(f"Failed to initialize SessionStore: {e}")
        return

    def speaking_callback(text):
        if voice:
            voice.speak(text, last_emotion)

    loop = asyncio.get_running_loop()

    def on_tool_call(name, args):
        if event_bus:
            asyncio.run_coroutine_threadsafe(
                event_bus.emit("tool_call", {"name": name, "args": args}), loop
            )

    def on_tool_result(name, result):
        if event_bus:
            asyncio.run_coroutine_threadsafe(
                event_bus.emit("tool_result", {"name": name, "text": result}), loop
            )

    try:
        brain = Brain(
            config,
            on_thought_callback=speaking_callback,
            session_store=store,
            on_tool_call=on_tool_call,
            on_tool_result=on_tool_result
        )
    except Exception as e:
        logger.error(f"Failed to initialize Brain: {e}")
        if store:
            store.close()
        return

    # Placeholder for event_bus (set later in async context)
    event_bus = None
    # Use the session id provided by the web UI when available; otherwise default
    current_web_session_id = "default"

    def ensure_session_ready(session_id: str):
        if not session_id:
            return
        try:
            store.create_session(session_id, title="New Chat", source="voice", launch_id=_LAUNCH_ID)
        except Exception as exc:
            logger.debug(f"ensure_session_ready skipped: {exc}")

    def update_session_title_from_text(session_id: str, user_text: str) -> None:
        if not session_id or not user_text:
            return
        try:
            rows = store.get_sessions()
            session_map = {row[0]: row for row in rows}
            session = session_map.get(session_id)
            if not session:
                return
            current_title = session[1] or "New Chat"
            if current_title != "New Chat":
                return
            candidate = " ".join(user_text.strip().split()[:6]).strip()
            if not candidate:
                return
            store.update_session_title(session_id, candidate)
            if event_bus:
                asyncio.run_coroutine_threadsafe(
                    event_bus.emit("session_update", {"session_id": session_id, "title": candidate}), loop
                )
        except Exception as exc:
            logger.debug(f"update_session_title_from_text skipped: {exc}")

    def on_speech(text: str):
        nonlocal current_web_session_id
        text = _normalize_app_list(text)
        logger.info(f"Speech detected: {text}")
        session_id = "default"
        if current_web_session_id not in (None, "default", ""):
            session_id = current_web_session_id
        ensure_session_ready(session_id)
        asyncio.run_coroutine_threadsafe(_process(text, brain, voice, session_id=session_id), loop)

    async def _process(text, brain, voice, session_id="default", platform="voice"):
        nonlocal speech_echo_cooldown, last_emotion
        if time.time() < speech_echo_cooldown:
            logger.info(f"Echo suppressed: {text}")
            return

        # Emit transcript event
        if event_bus:
            asyncio.create_task(event_bus.emit("transcript", {"text": text, "source": platform, "session_id": session_id}))

        print(f"\rHeard: {text}", flush=True)
        if voice.is_speaking.is_set():
            # Barge-in detection: command words always interrupt immediately
            _BARGE_COMMANDS = {"stop", "wait", "no", "cancel", "quiet", "shut", "enough"}
            words = set(text.lower().strip().split())
            if words & _BARGE_COMMANDS:
                logger.info("Barge-in: Command word detected. Stopping TTS.")
                voice.stop_tts()
                speech_echo_cooldown = time.time() + 1.5
            else:
                # Echo detection: is this a subset of what Charlie is currently saying?
                _tts_text = getattr(voice, "_last_speech_text", "").lower()
                spoken_words = set(_tts_text.split()) if _tts_text else set()
                new_words = set(text.lower().strip().split())
                is_echo = (
                    new_words
                    and spoken_words
                    and len(new_words) <= 4
                    and new_words.issubset(spoken_words)
                )
                if is_echo:
                    logger.info(f"Echo suppressed (during TTS): {text}")
                    return
                # New content during TTS -- barge in (cancel current turn)
                logger.info("Barge-in: New user input during TTS. Canceling.")
                voice.stop_tts()
                brain.cancel_chat()
                speech_echo_cooldown = time.time() + 0.8

        # Route !search command
        if text.strip().startswith("!search "):
            query = text.strip()[len("!search "):].strip()
            print("Searching history...", end="\r", flush=True)
            results = store.search(query)
            if not results:
                response_str = "No matching history found."
            else:
                response_str = f"Found {len(results)} result(s):\n"
                for role, content in results:
                    truncated = content[:120] + "..." if len(content) > 120 else content
                    response_str += f"- [{role}]: {truncated}\n"
            print(f"\n{response_str}", flush=True)
            voice.speak(response_str, last_emotion)
            return

        # Store user message
        try:
            store.append("user", text, session_id=session_id)
        except Exception as e:
            logger.warning(f"Failed to archive user message: {e}")
        # Voice command detection (before LLM call)
        cmd_emotion = parse_voice_command(text)
        if cmd_emotion is not None:
            last_emotion = cmd_emotion
            ack_map = {"energetic": "Got it. Switching to energetic.", "calm": "Got it, calming down."}
            ack = ack_map.get(cmd_emotion, "Got it.")
            voice.speak(ack, cmd_emotion)
            return

        # Detect emotion for this turn
        detected_emotion = get_emotion_for_context(text, [])

        # Sparkle announcements on emotion change
        sparkle = ""
        if detected_emotion != last_emotion:
            sparkle_map = {"energetic": "Oh, exciting! ", "calm": "Got it, calming down. ", "sad": "I hear you. "}
            sparkle = sparkle_map.get(detected_emotion, "")
        last_emotion = detected_emotion

        # Emit thinking event
        if event_bus:
            asyncio.create_task(event_bus.emit("thinking", {}))

        print("Charlie is thinking...", end="\r", flush=True)

        # Streaming buffer
        sentence_buffer = ""
        web_buffer = ""  # sentence buffer for web UI token events
        full_reply_buffer = ""
        is_first_chunk = True

        is_first_flush = True
        async for chunk in brain.chat_stream(text, platform=platform):
            if is_first_chunk:
                print("\r" + " " * 30 + "\r", end="", flush=True) # Clear thinking
                is_first_chunk = False
            print(chunk, end="", flush=True)
            sentence_buffer += chunk
            full_reply_buffer += chunk
            web_buffer += chunk

            # Emit web UI token: buffer until sentence boundary for coherent display
            if event_bus and _SENTENCE_BOUNDARY.search(web_buffer):
                parts = _SENTENCE_BOUNDARY.split(web_buffer)
                for part in parts[:-1]:
                    if part.strip():
                        asyncio.create_task(event_bus.emit("token", {"text": part.strip() + ". ", "session_id": session_id}))
                web_buffer = parts[-1]

            # Progressive flush: sentence boundary > force-flush safety net.
            # Clause boundaries removed — they cause awkward mid-sentence pauses.
            flushed = False

            # Early first-flush: wait for first sentence boundary, or force at 150 chars
            if is_first_flush:
                if _SENTENCE_BOUNDARY.search(sentence_buffer):
                    parts = _SENTENCE_BOUNDARY.split(sentence_buffer)
                    if len(parts) > 1:
                        first_sentence = parts[0].strip()
                        if first_sentence:
                            voice.speak(first_sentence, detected_emotion)
                            sentence_buffer = parts[-1]  # keep remainder (next sentence start)
                            flushed = True
                            is_first_flush = False
                elif len(sentence_buffer) >= 150:
                    idx = sentence_buffer.rfind(' ', 0, 150)
                    if idx > 0:
                        voice.speak(sentence_buffer[:idx].strip(), detected_emotion)
                        sentence_buffer = sentence_buffer[idx:].lstrip()
                    is_first_flush = False
                    flushed = True

            if not flushed:
                # Sentence boundary flush: split on .!? and speak complete sentences
                if _SENTENCE_BOUNDARY.search(sentence_buffer):
                    parts = _SENTENCE_BOUNDARY.split(sentence_buffer)
                    if len(parts) > 1:
                        for part in parts[:-1]:
                            if part.strip():
                                voice.speak(part.strip(), detected_emotion)
                        sentence_buffer = parts[-1]
                        flushed = True

            if not flushed and len(sentence_buffer) >= _MAX_FLUSH_CHARS:
                # Force-flush at word boundary to avoid mid-word splits
                idx = sentence_buffer.rfind(' ', 0, _MAX_FLUSH_CHARS)
                if idx > 0:
                    voice.speak(sentence_buffer[:idx].strip(), detected_emotion)
                    sentence_buffer = sentence_buffer[idx:].lstrip()
                elif sentence_buffer.strip():
                    voice.speak(sentence_buffer[:_MAX_FLUSH_CHARS].strip(), detected_emotion)
                    sentence_buffer = sentence_buffer[_MAX_FLUSH_CHARS:]

        # Final web UI flush — emit any remaining text stuck in web_buffer
        if event_bus and web_buffer.strip():
            asyncio.create_task(event_bus.emit("token", {"text": web_buffer.strip(), "session_id": session_id}))

        # Final TTS -- chunks already printed everything
        if sentence_buffer.strip():
            voice.speak(sparkle + sentence_buffer, detected_emotion)

        # Emit response_done event
        if event_bus:
            asyncio.create_task(event_bus.emit("response_done", {"session_id": session_id}))

        # Archive assistant reply
        if full_reply_buffer.strip():
            try:
                store.append("assistant", full_reply_buffer, session_id=session_id)
            except Exception as e:
                logger.warning(f"Failed to archive assistant message: {e}")

        # Learning loop: deferred to background -- doesn't block next turn
        if full_reply_buffer.strip() and text.strip():
            async def _background_learn(user_text: str, reply_text: str):
                try:
                    learning_prompt = (
                        f"User said: {user_text}\n"
                        f"Charlie replied: {reply_text}\n"
                        "Extract 0-1 new user preferences (e.g., 'prefers short answers'). "
                        "Output ONLY the preference line, or output nothing if nothing new."
                    )
                    learning = ""
                    async for chunk in brain.chat_stream(learning_prompt, skip_pre_search=True):
                        learning += chunk
                    learning = learning.strip() if learning.strip() else ""
                    if learning:
                        u_path = Path(config.user_file)
                        existing = u_path.read_text(encoding="utf-8") if u_path.exists() else ""
                        if learning not in existing:
                            with open(u_path, "a", encoding="utf-8") as f:
                                f.write(f"\n{learning}")
                            logger.info(f"Learning: {learning}")
                except Exception as e:
                    logger.debug(f"Learning loop skipped: {e}")

            # Fire-and-forget: learning runs in background, doesn't block user
            asyncio.create_task(_background_learn(text, full_reply_buffer))

    async def consume_web_commands(event_bus, brain, voice):
        """Read commands from the web UI and dispatch them."""
        while True:
            try:
                cmd = await event_bus.next_command()
                logger.debug(f"ZMQ received command: {cmd}")
                if cmd.get("type") == "chat":
                    await _process(cmd["text"], brain, voice, session_id=cmd.get("session_id", "default"), platform="web")
                elif cmd.get("type") == "stop":
                    voice.stop_tts()
                    brain.cancel_chat()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error handling web command: {e}", exc_info=True)

    # Start web server subprocess
    try:
        web_entry = os.path.join(os.path.dirname(__file__), "charlie", "web_server_entry.py")
        _web_env = os.environ.copy()
        _web_env["CHARLIE_LAUNCH_ID"] = _LAUNCH_ID
        web_proc = subprocess.Popen(
            [sys.executable, web_entry],
            cwd=os.path.dirname(__file__),
            env=_web_env,
        )
        logger.info(f"Web server subprocess started (PID: {web_proc.pid})")
    except Exception as e:
        logger.warning(f"Failed to start web server: {e}")

    logger.info("Loading AI models (Whisper, VAD, Kokoro)...")
    try:
        # TTS lifecycle callbacks for IPC events
        def on_tts_start():
            if event_bus:
                asyncio.run_coroutine_threadsafe(event_bus.emit("speaking_start", {}), loop)

        def on_tts_stop():
            if event_bus:
                asyncio.run_coroutine_threadsafe(event_bus.emit("speaking_stop", {}), loop)
        voice = VoiceEngine(
            config,
            on_speech=on_speech,
            on_tts_start=on_tts_start,
            on_tts_stop=on_tts_stop,
        )
        voice.start()

        # Connection test & Dynamic Welcome
        logger.debug("Requesting dynamic welcome message from LLM...")
        welcome_msg = ""
        # Wrap the generator in a timeout to avoid hangs if LLM IP is unreachable
        try:
            async with asyncio.timeout(25.0):
                async for chunk in brain.chat_stream("Give me a one-sentence warm, friendly welcome message as you start up. Speak only in English."):
                    welcome_msg += chunk
        except asyncio.TimeoutError:
            logger.warning("Dynamic welcome timed out after 25s. Using fallback.")
            welcome_msg = "Welcome, Sir; I'm online and ready to help."
        except Exception as e:
            logger.warning(f"Dynamic welcome failed: {type(e).__name__}: {e}. Using fallback.")
            welcome_msg = "Welcome, Sir; I'm online and ready to help."

        print("=" * 40, flush=True)
        print("   Charlie is online and listening", flush=True)
        print("=" * 40, flush=True)
        print(f"\rCharlie: {welcome_msg}", flush=True)
        voice.speak(welcome_msg, "neutral")

        # Run voice loop + web command consumer concurrently via ZeroMQ
        async with EventBus(pub_port=5555, pull_port=5556, is_producer=True) as bus:
            event_bus = bus
            await asyncio.gather(
                _voice_loop_idle(voice),
                consume_web_commands(bus, brain, voice),
            )
    except KeyboardInterrupt:
        logger.info("Interrupt received, shutting down...")
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.exception(f"Fatal error: {e}")
    finally:
        if 'voice' in locals() and voice is not None:
            voice.stop()
        if 'brain' in locals():
            await brain.close()
        if 'store' in locals() and store is not None:
            store.close()
        if web_proc is not None:
            web_proc.terminate()
            try:
                web_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                web_proc.kill()

        logging.shutdown()
        # Force exit to ensure background threads don't hang the process on Windows
        os._exit(0)


async def _voice_loop_idle(voice):
    """Keep the main coroutine alive while voice threads run."""
    try:
        while True:
            await asyncio.sleep(1)
    except asyncio.CancelledError:
        pass

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        os._exit(0)
