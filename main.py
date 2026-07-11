# ruff: noqa: E402, I001
import asyncio
import io
import logging
import os
import re
import sys
import time
from typing import Callable, Tuple

# Windows event-loop policy (must precede zmq/asyncio imports)
from charlie.runtime import configure as _configure_platform

_configure_platform()
import subprocess
import uuid

from charlie.text_utils import normalize_app_list as _normalize_app_list


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


if sys.platform == "win32":
    sys.stdout = SafeStreamWrapper(
        io.TextIOWrapper(
            sys.stdout.buffer, encoding="utf-8", line_buffering=True, write_through=True
        )
    )
    sys.stderr = SafeStreamWrapper(
        io.TextIOWrapper(
            sys.stderr.buffer, encoding="utf-8", line_buffering=True, write_through=True
        )
    )
else:
    sys.stdout = SafeStreamWrapper(sys.stdout)
    sys.stderr = SafeStreamWrapper(sys.stderr)

os.makedirs("logs", exist_ok=True)
LOG_FILE = "logs/charlie.log"

# 2. CONFIGURE SPLIT LOGGING
root_logger = logging.getLogger()
root_logger.setLevel(logging.DEBUG)

file_formatter = logging.Formatter(
    "%(asctime)s [%(name)s] [%(levelname)s] %(funcName)s:%(lineno)d - %(message)s"
)
file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8", mode="a")
file_handler.setFormatter(file_formatter)

console_formatter = logging.Formatter(
    "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(console_formatter)

root_logger.handlers = []
root_logger.addHandler(file_handler)
root_logger.addHandler(console_handler)

# 3. NOW IMPORT CHARLIE MODULES
from charlie.config import config
from charlie.core import Brain
from charlie.ipc import EventBus
from charlie.memory_store import MemoryStore
from charlie.personality import get_emotion_for_context, parse_voice_command
from charlie.session_store import SessionStore
from charlie.voice import VoiceEngine
from charlie.blackboard import Blackboard
from charlie.swarm import SwarmOrchestrator

logger = logging.getLogger("charlie.main")
# Unique launch identity -- every main() invocation gets one so the sidebar can
# filter "this launch" vs "all history".
_LAUNCH_ID: str = str(uuid.uuid4())


# Streaming TTS flush thresholds (chars, not words)
# First sentence: speak after first sentence boundary. Force-flush at 200 chars if no boundary.
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+")
_CLAUSE_BOUNDARY = re.compile(r"(?<=[,;])\s+")
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_MAX_FLUSH_CHARS = 200  # Force-flush at word boundary if no sentence boundary seen


def _flush_complete_sentences(
    buffer: str, sink: "Callable[[str], None]"
) -> Tuple[str, bool]:
    """Split `buffer` on sentence boundaries and feed complete sentences to `sink`.

    Returns the leftover (incomplete trailing sentence) and whether any complete
    sentence was flushed. The trailing `parts[-1]` is the carry-over for the
    next chunk; `parts[:-1]` are complete sentences.
    """
    if not _SENTENCE_BOUNDARY.search(buffer):
        return buffer, False
    parts = _SENTENCE_BOUNDARY.split(buffer)
    for part in parts[:-1]:
        if part.strip():
            sink(part)
    return parts[-1], len(parts) > 1



def _strip_think(text: str) -> str:
    """Remove reasoning/thought blocks so they never reach the chat UI."""
    return _THINK_RE.sub("", text).strip()


_SEARCH_RESULTS_RE = re.compile(
    r"\[SEARCH RESULTS.*?\]|\[END SEARCH RESULTS\]",
    re.DOTALL | re.IGNORECASE,
)
_TOOL_LINE_RE = re.compile(r"(?m)^(TOOL:.*|\s*\{.*\}.*)$")


def _strip_search_result_tags(text: str) -> str:
    """Remove [SEARCH RESULTS] blocks and their end markers from text."""
    return _SEARCH_RESULTS_RE.sub("", text).strip()


def _strip_tool_lines(text: str) -> str:
    """Remove TOOL: ... lines and raw JSON tool-call artifacts from text."""
    lines = text.splitlines()
    kept = [ln for ln in lines if not _TOOL_LINE_RE.match(ln)]
    return "\n".join(kept).strip()


def _safe_speak(voice, text: str, emotion: str, label: str = "") -> None:
    """Speak text, logging (not swallowing) any TTS failure.

    A mid-stream TTS error must never abort the answer generation loop --
    the UI token stream and message persistence downstream must still run.
    """
    if not text or not text.strip():
        return
    try:
        voice.speak(text.strip(), emotion)
    except Exception:
        logger.warning(
            "TTS speak failed%s: dropping audio only, answer continues",
            f" ({label})" if label else "",
            exc_info=True,
        )


def _schedule_process(coro, loop):
    fut = asyncio.run_coroutine_threadsafe(coro, loop)
    try:
        fut.add_done_callback(
            lambda f: (
                logger.error("Answer turn failed", exc_info=f.exception())
                if f.exception() is not None
                else None
            )
        )
    except Exception:  # pragma: no cover - add_done_callback itself failed
        logger.warning("Could not attach failure callback to answer task", exc_info=True)
    return fut


async def main():
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
    # Initialize vector memory store (graceful degradation if no embedding backend)
    memory_store = None
    try:
        memory_store = MemoryStore(config)
    except Exception as e:
        logger.warning(f"Vector memory disabled: {e}")
    # Initialize Blackboard for agent swarm coordination
    blackboard = Blackboard()


    def speaking_callback(text):
        if voice:
            voice.speak(text, last_emotion)

    loop = asyncio.get_running_loop()

    def on_tool_call(name, args):
        if event_bus:
            asyncio.run_coroutine_threadsafe(
                event_bus.emit("tool_call", {"name": name, "args": args, "session_id": current_web_session_id}), loop
            )

    def on_tool_result(name, result):
        if event_bus:
            asyncio.run_coroutine_threadsafe(
                event_bus.emit(
                    "tool_result",
                    {"name": name, "text": result, "session_id": current_web_session_id},
                ),
                loop,
            )

    def on_thinking_update(name, args):
        if event_bus:
            desc = f"I'll use the {name} tool"
            if args:
                summary = str(args)[:80]
                desc += f" with {summary}"
            asyncio.run_coroutine_threadsafe(
                event_bus.emit("thinking_update", {"text": desc, "session_id": current_web_session_id}), loop
            )

    try:
        brain = Brain(
            config,
            on_thought_callback=speaking_callback,
            session_store=store,
            memory_store=memory_store,
            on_tool_call=on_tool_call,
            on_tool_result=on_tool_result,
            on_thinking_update=on_thinking_update,
            blackboard=blackboard,
        )
    except Exception as e:
        logger.error(f"Failed to initialize Brain: {e}")
        if store:
            store.close()
        return

    # Wire vector memory store into tool registry
    from charlie.tools import registry as tool_registry
    if memory_store is not None:
        tool_registry.set_memory_store(memory_store)
    # Wire knowledge graph into tool registry
    if brain is not None and hasattr(brain, "memory_graph"):
        tool_registry.set_memory_graph(brain.memory_graph)
    # Wire blackboard into tool registry
    tool_registry.set_blackboard(blackboard)

    # Wire the plugin system into the tool registry (no-op unless enabled).
    # The SAME registry the LLM calls, so when PLUGINS_ENABLED=true the
    # plugin_* tools appear alongside the built-in tools and are gated by
    # the flag off by default.
    from charlie.tools import register_plugin_tools

    try:
        plugin_manager = register_plugin_tools(config)
        if plugin_manager is None:
            logger.info("Plugin system disabled (PLUGINS_ENABLED=false).")
        else:
            logger.info("Plugin system ACTIVE: plugin_* tools registered.")
    except Exception as e:
        logger.warning(f"Plugin system failed to initialize: {e}")
        plugin_manager = None

    # Wire the MCP subsystem into the SAME shared tool registry (no-op unless enabled).
    mcp_client = None
    try:
        if config.mcp_enabled:
            from charlie.mcp_client import start_mcp

            mcp_client = start_mcp(config)
            if mcp_client is None:
                logger.info("MCP subsystem not started (no servers configured)")
        else:
            logger.info("MCP subsystem not enabled (MCP_ENABLED=false)")
    except Exception as e:
        logger.warning(f"MCP subsystem failed to initialize: {e}")
        mcp_client = None

    # Build the swarm with a real LLM client so agents do genuine work.
    if brain is not None:
        from charlie.utils import build_auth_headers

        class _AgentLLMClient:
            def __init__(self, client, model: str, api_key: str) -> None:
                self._client = client
                self.model = model
                self.headers = build_auth_headers(api_key)

            def post(self, path: str, *, json=None, **kwargs):
                headers = dict(kwargs.pop("headers", {}))
                headers.update(self.headers)
                return self._client.post(path, json=json, headers=headers, **kwargs)

        swarm = SwarmOrchestrator(
            blackboard,
            llm_client=_AgentLLMClient(brain.client, config.small_llm_model, config.small_llm_key),
        )
    else:
        swarm = SwarmOrchestrator(blackboard)

    # Placeholder for event_bus (set later in async context)
    event_bus = None
    # Use the session id provided by the web UI when available; otherwise default
    current_web_session_id = "default"

    def ensure_session_ready(session_id: str):
        if not session_id:
            return
        try:
            store.create_session(
                session_id, title="New Chat", source="voice", launch_id=_LAUNCH_ID
            )
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
                    event_bus.emit(
                        "session_updated", {"session_id": session_id, "title": candidate}
                    ),
                    loop,
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
        _schedule_process(_process(text, brain, voice, session_id=session_id), loop)

    async def _process(text, brain, voice, session_id="default", platform="voice"):
        nonlocal speech_echo_cooldown, last_emotion
        if time.time() < speech_echo_cooldown:
            logger.info(f"Echo suppressed: {text}")
            return

        print(f"\rHeard: {text}", flush=True)
        if config.enable_barge_in and voice.is_speaking.is_set():
            # Barge-in detection: command words always interrupt immediately
            _BARGE_COMMANDS = {
                "stop",
                "wait",
                "no",
                "cancel",
                "quiet",
                "shut",
                "enough",
            }
            words = set(text.lower().strip().split())
            if words & _BARGE_COMMANDS:
                logger.info("Barge-in: Command word detected. Stopping TTS.")
                voice.stop_tts()
                brain.cancel_chat()
                speech_echo_cooldown = time.time() + 1.5
            else:
                # Echo detection: is this a subset of what Charlie is currently saying?
                if voice.is_echo(text):
                    logger.info(f"Echo suppressed (during TTS): {text}")
                    return
                # New content during TTS -- barge in (cancel current turn)
                logger.info("Barge-in: New user input during TTS. Canceling.")
                voice.stop_tts()
                brain.cancel_chat()
                speech_echo_cooldown = time.time() + 0.8

        # Route !search command
        if text.strip().startswith("!search "):
            query = text.strip()[len("!search ") :].strip()
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
        # Route /memory-review command
        if text.strip().lower() in ("/memory-review", "!memory-review"):
            if brain is None:
                response_str = "Brain not initialized."
            else:
                graph = brain.memory_graph
                facts = graph.get_all_facts()
                if not facts:
                    response_str = "Knowledge graph is empty."
                else:
                    # Build summary
                    subjects = {}
                    for s, p, o in facts:
                        subjects.setdefault(s, []).append(f"{p} -> {o}")
                    response_str = f"Knowledge graph: {len(facts)} facts.\n"
                    for subj, preds in sorted(subjects.items()):
                        response_str += f"  {subj}:\n"
                        for pred in preds[:3]:
                            response_str += f"    {pred}\n"
                        if len(preds) > 3:
                            response_str += f"    ... +{len(preds)-3} more\n"
            print(f"\n{response_str}", flush=True)
            voice.speak(response_str, last_emotion)
            return

        # Emit transcript event only if this turn was not suppressed
        if event_bus:
            asyncio.create_task(
                event_bus.emit(
                    "transcript",
                    {"text": text, "source": platform, "session_id": session_id},
                )
            )

        # Store user message
        try:
            store.append("user", text, session_id=session_id)
            store.touch_session(session_id)
            update_session_title_from_text(session_id, text)
        except Exception as e:
            logger.warning(f"Failed to archive user message or touch session: {e}")
        # Voice command detection (before LLM call)
        cmd_emotion = parse_voice_command(text)
        if cmd_emotion is not None:
            last_emotion = cmd_emotion
            ack_map = {
                "energetic": "Got it. Switching to energetic.",
                "calm": "Got it, calming down.",
            }
            ack = ack_map.get(cmd_emotion, "Got it.")
            voice.speak(ack, cmd_emotion)
            return

        # Detect emotion for this turn
        detected_emotion = get_emotion_for_context(text)

        # Sparkle announcements on emotion change
        sparkle = ""
        if detected_emotion != last_emotion:
            sparkle_map = {
                "energetic": "Oh, exciting! ",
                "calm": "Got it, calming down. ",
                "sad": "I hear you. ",
            }
            sparkle = sparkle_map.get(detected_emotion, "")
        last_emotion = detected_emotion

        # Emit thinking event
        if event_bus:
            asyncio.create_task(event_bus.emit("thinking", {"session_id": session_id}))

        print("Charlie is thinking...", end="\r", flush=True)

        # Streaming buffer
        sentence_buffer = ""
        web_buffer = ""  # sentence buffer for web UI token events
        full_reply_buffer = ""
        is_first_chunk = True

        is_first_flush = True
        async for chunk in brain.chat_stream(text, platform=platform):
            if is_first_chunk:
                print("\r" + " " * 30 + "\r", end="", flush=True)
                is_first_chunk = False
            print(chunk, end="", flush=True)
            sentence_buffer += chunk
            full_reply_buffer += chunk
            web_buffer += chunk

            # Real-time UI token stream: emit whole sentences as they complete.
            # This is the ONLY source of "token" events for the chat UI, so the
            # text accumulates without duplication. Internal model text like
            # <think>...</think>, [SEARCH RESULTS]...[/SEARCH RESULTS], and
            # TOOL: ... lines are stripped here so reasoning/tool metadata
            # never leaks into the chat.
            if event_bus and _SENTENCE_BOUNDARY.search(web_buffer):
                parts = _SENTENCE_BOUNDARY.split(web_buffer)
                for part in parts[:-1]:
                    if part.strip():
                        safe = _strip_search_result_tags(part.strip())
                        safe = _strip_tool_lines(safe)
                        safe = _strip_think(safe)
                        if safe:
                            asyncio.create_task(
                                event_bus.emit(
                                    "token",
                                    {
                                        "text": safe if safe.endswith((".", "!", "?")) else safe + ". ",
                                        "session_id": session_id,
                                    },
                                )
                            )
                web_buffer = parts[-1]

            # Progressive flush: sentence boundary > clause boundary > force-flush.
            flushed = False

            # Early first-flush: wait for first sentence boundary, or force at 150 chars
            if is_first_flush:
                sentence_buffer, flushed = _flush_complete_sentences(
                    sentence_buffer,
                    lambda part: _safe_speak(voice, part, detected_emotion, "first-flush"),
                )
                if flushed:
                    is_first_flush = False
                elif len(sentence_buffer) >= 150:
                    idx = sentence_buffer.rfind(" ", 0, 150)
                    if idx > 0:
                        _safe_speak(voice, sentence_buffer[:idx], detected_emotion, "first-force")
                        sentence_buffer = sentence_buffer[idx:].lstrip()
                    is_first_flush = False
                    flushed = True

            if not flushed:
                sentence_buffer, flushed = _flush_complete_sentences(
                    sentence_buffer,
                    lambda part: _safe_speak(voice, part, detected_emotion, "sentence"),
                )

            if not flushed and len(sentence_buffer) >= _MAX_FLUSH_CHARS:
                # Force-flush: prefer clause (comma/semicolon) boundary,
                # fall back to word boundary to avoid mid-word splits.
                clause_idx = _CLAUSE_BOUNDARY.search(sentence_buffer[:_MAX_FLUSH_CHARS])
                if clause_idx:
                    flush_end = clause_idx.end()
                    _safe_speak(voice, sentence_buffer[:flush_end], detected_emotion, "clause")
                    sentence_buffer = sentence_buffer[flush_end:].lstrip()
                else:
                    word_idx = sentence_buffer.rfind(" ", 0, _MAX_FLUSH_CHARS)
                    if word_idx > 0:
                        _safe_speak(voice, sentence_buffer[:word_idx], detected_emotion, "word")
                        sentence_buffer = sentence_buffer[word_idx:].lstrip()
                    elif sentence_buffer.strip():
                        _safe_speak(
                            voice,
                            sentence_buffer[:_MAX_FLUSH_CHARS],
                            detected_emotion,
                            "force",
                        )
                        sentence_buffer = sentence_buffer[_MAX_FLUSH_CHARS:]

        # Final web UI flush - emit any remaining text stuck in web_buffer
        if event_bus and web_buffer.strip():
            asyncio.create_task(
                event_bus.emit(
                    "token",
                    {
                        "text": _strip_think(
                            _strip_tool_lines(
                                _strip_search_result_tags(web_buffer.strip())
                            )
                        ),
                        "session_id": session_id,
                    },
                )
            )


        # Final TTS
        if sentence_buffer.strip():
            _safe_speak(voice, sparkle + sentence_buffer, detected_emotion, "final")

        # Persist the generated reply, falling back to web_buffer if cancelled.
        final_reply = full_reply_buffer.strip() or web_buffer.strip()
        if final_reply:
            try:
                store.append("assistant", final_reply, session_id=session_id)
                store.touch_session(session_id)
            except Exception as e:
                logger.warning(
                    f"Failed to archive assistant message or touch session: {e}"
                )

        # Emit response_done event so the UI can stop its typing indicator.
        if event_bus:
            asyncio.create_task(
                event_bus.emit("response_done", {"session_id": session_id})
            )

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
                    async for chunk in brain.chat_stream(
                        learning_prompt, skip_pre_search=True, skip_tools=True
                    ):
                        learning += chunk
                    learning = learning.strip()
                    clean_learning = learning.lower().rstrip(".")
                    if not learning or any(clean_learning.startswith(p) for p in (
                        "nothing", "none", "no new", "no preference", "no change", "no update"
                    )):
                        return

                    from charlie.tools import registry as tool_registry
                    existing = ""
                    u_path = Path(config.user_file)
                    if u_path.exists():
                        existing = u_path.read_text(encoding="utf-8")

                    if learning not in existing:
                        await asyncio.get_running_loop().run_in_executor(
                            None,
                            tool_registry.execute_tool,
                            "memory",
                            {
                                "action": "add",
                                "target": "user",
                                "content": learning,
                            },
                        )
                        logger.info(f"Learning: {learning}")
                except Exception as e:
                    logger.debug(f"Learning loop skipped: {e}")

            # Fire-and-forget: learning runs in background, doesn't block user
            asyncio.create_task(_background_learn(text, full_reply_buffer))

    async def consume_web_commands(event_bus, brain, voice):
        """Read commands from the web UI and dispatch them."""
        nonlocal current_web_session_id
        while True:
            try:
                cmd = await event_bus.next_command()
                logger.debug(f"ZMQ received command: {cmd}")
                cmd_type = cmd.get("type")
                if cmd_type == "chat":
                    payload_sid = cmd.get("payload", {}).get("session_id")
                    current_web_session_id = cmd.get("session_id") or payload_sid or "default"
                    chat_text = cmd.get("text") or cmd.get("payload", {}).get("text", "")
                    await _process(
                        chat_text,
                        brain,
                        voice,
                        session_id=current_web_session_id,
                        platform="web",
                    )
                elif cmd_type == "session_active":
                    payload_sid = cmd.get("payload", {}).get("session_id")
                    current_web_session_id = cmd.get("session_id") or payload_sid or "default"
                    logger.info(f"Active session updated to: {current_web_session_id}")
                elif cmd_type == "stop":
                    voice.stop_tts()
                    brain.cancel_chat()
                elif cmd_type == "task_create":
                    payload = cmd.get("payload", {})
                    task_name = payload.get("name", "Web Task")
                    assigned = payload.get("assigned_to", "")
                    blackboard.add_task(task_name, assigned_to=assigned)
                    # Broadcast update
                    await event_bus.emit("blackboard_update", blackboard.snapshot())
                elif cmd_type == "agent_kill":
                    agent_name = cmd.get("payload", {}).get("name")
                    if agent_name:
                        swarm.terminate_agent(agent_name)
                        await event_bus.emit("blackboard_update", blackboard.snapshot())
                elif cmd_type == "hitl_approve":
                    task_id = cmd.get("payload", {}).get("task_id")
                    approved = cmd.get("payload", {}).get("approved", True)
                    if task_id:
                        # Feed the feedback/approval into the swarm orchestrator or task result
                        blackboard.update_task(task_id, status="done" if approved else "failed")
                        await event_bus.emit("blackboard_update", blackboard.snapshot())
                elif cmd_type == "audio_control":
                    payload = cmd.get("payload", {})
                    state = voice.set_audio_state(
                        muted=payload.get("muted"),
                        volume=payload.get("volume"),
                    )
                    await event_bus.emit("audio_state", state)
                elif cmd_type == "mic_control":
                    payload = cmd.get("payload", {})
                    mic_state = voice.set_mic_state(bool(payload.get("mic_muted", True)))
                    await event_bus.emit("mic_state", mic_state)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error handling web command: {e}", exc_info=True)

    # Start web server subprocess
    try:
        web_entry = os.path.join(
            os.path.dirname(__file__), "charlie", "web_server_entry.py"
        )
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
                asyncio.run_coroutine_threadsafe(
                    event_bus.emit("speaking_start", {"session_id": current_web_session_id}), loop
                )

        def on_tts_stop():
            if event_bus:
                asyncio.run_coroutine_threadsafe(
                    event_bus.emit("speaking_stop", {"session_id": current_web_session_id}), loop
                )

        voice = VoiceEngine(
            config,
            on_speech=on_speech,
            on_tts_start=on_tts_start,
            on_tts_stop=on_tts_stop,
        )
        voice.start()

        def on_wake_word():
            if event_bus:
                asyncio.run_coroutine_threadsafe(
                    event_bus.emit("wake_word", {}), loop
                )

        voice.set_wake_word_callback(on_wake_word)

        # Connection test & Dynamic Welcome
        logger.debug("Requesting dynamic welcome message from LLM...")
        welcome_msg = ""
        # Wrap the generator in a timeout to avoid hangs if LLM IP is unreachable
        try:
            async with asyncio.timeout(25.0):
                async for chunk in brain.chat_stream(
                    "Give me a very brief, one-sentence startup welcome. Be warm, natural, "
                    "and speak like a human colleague (not an AI assistant). "
                    "Do NOT say 'How can I help you' or 'How can I assist'. Speak only in English.",
                    skip_tools=True
                ):
                    welcome_msg += chunk
        except asyncio.TimeoutError:
            logger.warning("Dynamic welcome timed out after 25s. Using fallback.")
            welcome_msg = "Hey there. I'm online and listening."
        except Exception as e:
            logger.warning(
                f"Dynamic welcome failed: {type(e).__name__}: {e}. Using fallback."
            )
            welcome_msg = "Hey there. I'm online and listening."

        print("=" * 40, flush=True)
        print("   Charlie is online and listening", flush=True)
        print("=" * 40, flush=True)
        print(f"\rCharlie: {welcome_msg}", flush=True)
        voice.speak(welcome_msg, "neutral")

        # Real GPU utilization, re-read every tick so the dashboard reflects
        # live load. Cached briefly (1s) to avoid hammering nvidia-smi on every
        # status emit; falls back to 0.0 only when no NVIDIA GPU is present.
        _gpu_reader: dict = {"value": 0.0, "ts": 0.0}

        def _read_gpu_percent() -> float:
            now = time.monotonic()
            if now - _gpu_reader["ts"] < 1.0:
                return _gpu_reader["value"]
            _gpu_reader["ts"] = now
            try:
                out = subprocess.run(
                    [
                        "nvidia-smi",
                        "--query-gpu=utilization.gpu",
                        "--format=csv,noheader,nounits",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=2.0,
                    check=False,
                )
                if out.returncode == 0 and out.stdout.strip():
                    _gpu_reader["value"] = float(out.stdout.strip().splitlines()[0].strip())
                else:
                    _gpu_reader["value"] = 0.0
            except (FileNotFoundError, subprocess.SubprocessError, ValueError, OSError):
                _gpu_reader["value"] = 0.0
            return _gpu_reader["value"]

        async def _emit_system_status_and_blackboard(bus):
            import psutil
            try:
                while True:
                    # Emit system status metrics
                    cpu_percent = psutil.cpu_percent()
                    ram_percent = psutil.virtual_memory().percent
                    await bus.emit("system_status", {
                        "cpu": cpu_percent,
                        "ram": ram_percent,
                        "gpu": await asyncio.to_thread(_read_gpu_percent),
                        "active_agents": list(swarm.active_agents) if hasattr(swarm, "active_agents") else []
                    })
                    # Emit blackboard update
                    await bus.emit("blackboard_update", blackboard.snapshot())
                    await asyncio.sleep(1.0)
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.error(f"Metric emitter error: {e}")

        # Run voice loop + web command consumer concurrently via ZeroMQ
        async with EventBus(pub_port=5555, pull_port=5556, is_producer=True) as bus:
            event_bus = bus
            voice.set_event_bus(bus)

            class ZmqLogHandler(logging.Handler):
                def emit(self, record):
                    try:
                        log_entry = self.format(record)
                        try:
                            loop = asyncio.get_running_loop()
                            loop.create_task(bus.emit("log", {"line": log_entry}))
                        except RuntimeError:
                            pass
                    except Exception:
                        pass

            zmq_handler = ZmqLogHandler()
            zmq_handler.setFormatter(
                logging.Formatter("%(asctime)s [%(name)s] [%(levelname)s] - %(message)s")
            )
            zmq_handler.setLevel(logging.INFO)
            logging.getLogger().addHandler(zmq_handler)

            try:
                await asyncio.gather(
                    _voice_loop_idle(voice),
                    consume_web_commands(bus, brain, voice),
                    swarm.run(),
                    _emit_system_status_and_blackboard(bus),
                )
            finally:
                logging.getLogger().removeHandler(zmq_handler)
    except KeyboardInterrupt:
        logger.info("Interrupt received, shutting down...")
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.exception(f"Fatal error: {e}")
    finally:
        if "voice" in locals() and voice is not None:
            voice.stop()
        if "brain" in locals():
            await brain.close()
        if "store" in locals() and store is not None:
            store.close()
        if mcp_client is not None:
            try:
                mcp_client.stop()
                logger.info("MCP subsystem stopped")
            except Exception as e:
                logger.warning(f"MCP subsystem stop error: {e}")
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
