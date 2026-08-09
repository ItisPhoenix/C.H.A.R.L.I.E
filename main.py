# ruff: noqa: E402, I001
import asyncio
import io
import logging
import os
import re
import sys
import tempfile
import time
from typing import Callable, Dict, Tuple

# Windows event-loop policy (must precede zmq/asyncio imports)
from charlie.runtime import configure as _configure_platform

_configure_platform()
import subprocess
import uuid

from charlie.text_utils import normalize_app_list as _normalize_app_list


from pathlib import Path


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
# pytest importing this module must not attach a FileHandler to the real log.
LOG_FILE = "logs/test_charlie.log" if "pytest" in sys.modules else "logs/charlie.log"

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
# Telegram's Bot API embeds the token in request URLs -- both httpx and telegram log it below.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.INFO)

from charlie.config import Config, config
from charlie.core import Brain
from charlie.ipc import EventBus
from charlie.memory_store import MemoryStore
from charlie.personality import get_emotion_for_context, parse_voice_command, parse_yes_no
from charlie.session_store import SessionStore
from charlie.utils import make_id
from charlie.voice import VoiceEngine
from charlie.monitors import start_monitor_thread

logger = logging.getLogger("charlie.main")
# Unique launch identity -- every main() invocation gets one so the sidebar can
# filter "this launch" vs "all history".
_LAUNCH_ID: str = str(uuid.uuid4())


# Streaming TTS flush thresholds (chars, not words)
# First sentence: speak after first sentence boundary. Force-flush at 200 chars if no boundary.
# Also split before a new list item's newline (numbered "\n2. " or bulleted "\n- ")
# so items get Kokoro's natural inter-utterance gap instead of running together.
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+|\n+(?=\s*(?:\d+[.)]|[-*•])\s)")
_CLAUSE_BOUNDARY = re.compile(r"(?<=[,;])\s+")
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_MAX_FLUSH_CHARS = 200  # Force-flush at word boundary if no sentence boundary seen
# First-flush gate: clause boundary or this many chars, whichever comes first.
# 20 chars outran this deployment's slow remote LLM, causing an audible gap.
_FIRST_FLUSH_MAX_CHARS = 60


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


def _safe_speak(
    voice, text: str, emotion: str, label: str = "", platform: str = "voice", session_id: str = ""
) -> None:
    """Speak text, logging (not swallowing) any TTS failure.

    A mid-stream TTS error must never abort the answer generation loop --
    the UI token stream and message persistence downstream must still run.
    Telegram turns have no local listener; the same incremental chunk instead
    feeds the Telegram message-edit stream (see charlie.telegram_bot.TelegramBot.stream_append).
    """
    if not text or not text.strip():
        return
    if platform == "telegram":
        from charlie.telegram_bot import get_active_bot
        bot = get_active_bot()
        if bot is not None and session_id:
            chat_id = session_id.split(":", 1)[1]
            asyncio.ensure_future(bot.stream_append(chat_id, text))
        return
    try:
        voice.speak(text.strip(), emotion)
    except Exception:
        logger.warning(
            "TTS speak failed%s: dropping audio only, answer continues",
            f" ({label})" if label else "",
            exc_info=True,
        )


async def _relay_written_file_to_telegram(bot, session_id: str, path: str) -> None:
    """A file_write call on a Telegram turn also gets sent as a document attachment, artifact-style."""
    if not path:
        return
    try:
        with open(path, "rb") as f:
            content = f.read()
    except Exception as e:
        logger.warning(f"Failed to read file for Telegram relay: {e}")
        return
    chat_id = session_id.split(":", 1)[1]
    await bot.send_document(chat_id, os.path.basename(path), content)


def _emit_threadsafe(event_bus, loop, event_type, payload):
    """Fire-and-forget event_bus.emit from any thread; logs failures the future would otherwise swallow."""
    fut = asyncio.run_coroutine_threadsafe(event_bus.emit(event_type, payload), loop)
    fut.add_done_callback(
        lambda f: logger.warning(f"Event emit '{event_type}' failed: {f.exception()}") if f.exception() else None
    )
    return fut


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


async def _restart_mcp_client(old_client, config):
    """Stop old_client and start a fresh one, both off the event loop.

    mcp_client.stop() and start_mcp() are synchronous and block on
    subprocess handshakes (up to config.timeout, default 30s per server);
    called directly inside an async def they freeze the whole event loop,
    including consume_web_commands, for that long.
    """
    if old_client is not None:
        await asyncio.to_thread(old_client.stop)
    if not config.mcp_enabled:
        return None
    from charlie.mcp_client import start_mcp
    return await asyncio.to_thread(start_mcp, config)


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
    # VAD-fragmented duplicate text within this window is suppressed (see on_speech).
    recent_turn_texts: Dict[str, float] = {}
    _DEDUPE_WINDOW_SEC = 20.0
    web_proc = None
    # True while a chat turn's LLM/tool loop runs -- see _dispatch_or_queue.
    turn_active = False
    pending_turns: list = []
    # Set below, only when a token + user ID are configured -- see the Telegram startup block.
    telegram_bot = None

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

    def speaking_callback(text):
        if voice:
            voice.speak(text, last_emotion)

    loop = asyncio.get_running_loop()

    def on_tool_call(name, args, turn_id=None, session_id=None):
        if event_bus:
            _emit_threadsafe(
                event_bus, loop, "tool_call",
                {
                    "name": name, "args": args, "turn_id": turn_id,
                    "session_id": session_id or current_web_session_id,
                },
            )

    def on_tool_result(name, result, turn_id=None, session_id=None, arguments=None):
        if event_bus:
            _emit_threadsafe(
                event_bus, loop, "tool_result",
                {
                    "name": name, "text": result, "turn_id": turn_id,
                    "session_id": session_id or current_web_session_id,
                },
            )
        if (
            name == "file_write"
            and session_id
            and session_id.startswith("telegram:")
            and arguments
            and not str(result).startswith("Error")
        ):
            from charlie.telegram_bot import get_active_bot
            bot = get_active_bot()
            if bot is not None:
                asyncio.run_coroutine_threadsafe(
                    _relay_written_file_to_telegram(bot, session_id, arguments.get("path", "")), loop
                )

    def on_queue_update():
        if event_bus:
            _emit_threadsafe(
                event_bus, loop, "queue_update",
                {
                    "count": len(pending_turns),
                    "ids": [i for i, _, _, _ in pending_turns][:5],
                    "texts": [t for _, t, _, _ in pending_turns][:5],
                    "session_ids": [s for _, _, s, _ in pending_turns][:5],
                },
            )

    def on_thinking_update(name, args):
        if event_bus:
            desc = f"I'll use the {name} tool"
            if args:
                summary = str(args)[:80]
                desc += f" with {summary}"
            _emit_threadsafe(event_bus, loop, "thinking_update", {"text": desc, "session_id": current_web_session_id})

    def on_agent_spawned(agent_id, task):
        if event_bus:
            _emit_threadsafe(
                event_bus, loop, "agent_spawned",
                {"agent_id": agent_id, "task": task, "session_id": current_web_session_id},
            )

    def on_agent_status(agent_id, tool_name):
        if event_bus:
            _emit_threadsafe(
                event_bus, loop, "agent_status",
                {"agent_id": agent_id, "tool_name": tool_name, "session_id": current_web_session_id},
            )

    def on_agent_result(agent_id, result):
        if event_bus:
            _emit_threadsafe(
                event_bus, loop, "agent_result",
                {"agent_id": agent_id, "result": result, "session_id": current_web_session_id},
            )

    def on_skill_installed(name, raw_text):
        if event_bus:
            _emit_threadsafe(event_bus, loop, "skill_installed", {"name": name, "raw_text": raw_text})

    try:
        brain = Brain(
            config,
            on_thought_callback=speaking_callback,
            session_store=store,
            memory_store=memory_store,
            on_tool_call=on_tool_call,
            on_tool_result=on_tool_result,
            on_thinking_update=on_thinking_update,
            on_agent_spawned=on_agent_spawned,
            on_agent_status=on_agent_status,
            on_agent_result=on_agent_result,
            on_skill_installed=on_skill_installed,
        )
    except Exception as e:
        logger.error(f"Failed to initialize Brain: {e}")
        if store:
            store.close()
        return

    asyncio.create_task(brain.prewarm())

    # Wire vector memory store into tool registry
    from charlie.tools import registry as tool_registry
    if memory_store is not None:
        tool_registry.set_memory_store(memory_store)

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
    if plugin_manager is None:
        # Always keep a manager available so a mirrored "extension_enabled"/
        # "extension_disabled" command (see consume_web_commands below) can
        # enable/disable one built-in plugin even when the blanket
        # PLUGINS_ENABLED flag is off -- matches charlie/web_server.py's
        # identical fallback, needed for the same per-plugin-control reason.
        from charlie.plugins import PluginManager

        plugin_manager = PluginManager()

    # Wire the MCP subsystem into the SAME shared tool registry (no-op unless enabled).
    # Runs on a thread, awaited later, so it overlaps with VoiceEngine/STT startup instead of blocking it.
    mcp_client = None

    async def _start_mcp_task():
        nonlocal mcp_client
        try:
            if config.mcp_enabled:
                from charlie.mcp_client import start_mcp

                mcp_client = await asyncio.to_thread(start_mcp, config)
                if mcp_client is None:
                    logger.info("MCP subsystem not started (no servers configured)")
            else:
                logger.info("MCP subsystem not enabled (MCP_ENABLED=false)")
        except Exception as e:
            logger.warning(f"MCP subsystem failed to initialize: {e}")
            mcp_client = None

    mcp_start_task = asyncio.create_task(_start_mcp_task())

    async def _reload_extensions_task():
        """Boot-time counterpart to charlie/web_server.py's _load_extensions():
        that function only restores the web-server process's own registry, so
        without this the voice process's Brain -- where the real chat
        tool-calling loop runs -- starts every restart with zero previously
        installed extensions until each one is reinstalled by hand. Awaits
        mcp_start_task first so a config-driven MCP client (if any) is already
        in place before mcp-kind entries potentially replace it."""
        nonlocal mcp_client
        await mcp_start_task
        import json

        try:
            with open(config.extensions_state_path, "r", encoding="utf-8") as f:
                entries = json.load(f)
        except FileNotFoundError:
            return
        except Exception:
            logger.warning("Failed to read extensions state file", exc_info=True)
            return

        from charlie.extensions.install import install_extension
        from charlie.tools import registry as _ext_registry

        for entry in entries:
            if not entry.get("enabled", True):
                continue
            kind = entry.get("kind", "")
            name = entry.get("name", "")
            source = entry.get("source", "")
            raw_text = entry.get("raw_text", "")
            try:
                tool_names, mcp_client = install_extension(
                    kind, name, source, raw_text,
                    registry=_ext_registry, plugin_manager=plugin_manager, mcp_client=mcp_client,
                    plugin_allow_dirs=config.plugin_allow_dirs,
                )
                if kind == "skill":
                    from charlie.extensions.skills import format_skill_block, parse_skill_md
                    manifest = parse_skill_md(raw_text)
                    brain.add_installed_skill_block(name, format_skill_block(manifest))
                logger.info(
                    "Reloaded extension '%s' (%s) into voice process on boot: %s", name, kind, tool_names,
                )
            except Exception:
                logger.warning("Failed to reload extension '%s' on boot", name, exc_info=True)

    extensions_reload_task = asyncio.create_task(_reload_extensions_task())

    # Placeholder for event_bus (set later in async context)
    event_bus = None
    # Per-launch fallback, not the old shared "default" bucket across all launches.
    current_web_session_id = f"voice_{_LAUNCH_ID}"
    _voice_fallback_session_id = current_web_session_id

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
                _emit_threadsafe(event_bus, loop, "session_updated", {"session_id": session_id, "title": candidate})
        except Exception as exc:
            logger.debug(f"update_session_title_from_text skipped: {exc}")

    def on_speech(text: str):
        nonlocal current_web_session_id
        text = _normalize_app_list(text)
        logger.info(f"Speech detected: {text}")

        now = time.time()
        normalized = text.strip().lower()
        for stale in [k for k, t in recent_turn_texts.items() if now - t >= _DEDUPE_WINDOW_SEC]:
            del recent_turn_texts[stale]
        last_dispatch = recent_turn_texts.get(normalized)
        if last_dispatch is not None and now - last_dispatch < _DEDUPE_WINDOW_SEC:
            logger.info(f"Duplicate utterance suppressed ({now - last_dispatch:.1f}s ago): {text}")
            return
        recent_turn_texts[normalized] = now

        session_id = _voice_fallback_session_id
        if current_web_session_id not in (None, _voice_fallback_session_id, ""):
            session_id = current_web_session_id
        ensure_session_ready(session_id)
        _schedule_process(_dispatch_or_queue(text, session_id), loop)

    async def _dispatch_or_queue(text, session_id, platform="voice"):
        """Run the turn now, or queue it if one is already running tool calls.

        Only ever called via _schedule_process (run_coroutine_threadsafe), so
        this always executes on the loop thread -- the turn_active check and
        pending_turns mutation below are a single synchronous span with no
        await in between, making them atomic with respect to any other
        coroutine on this loop without needing a lock.
        """
        nonlocal turn_active
        from charlie.core import get_active_voice_approval
        # A gated tool call inside the still-running turn is waiting on a
        # spoken yes/no -- that answer must reach _process() immediately
        # (it routes to resolve_tool_approval), never queued behind the
        # very turn it's meant to unblock.
        # is_speaking gates voice barge-in only -- a web user has no TTS to
        # interrupt, so their follow-up must queue purely on turn_active.
        not_speaking_or_web = platform != "voice" or not voice.is_speaking.is_set()
        if turn_active and not_speaking_or_web and not get_active_voice_approval():
            pending_turns.append((make_id(8), text, session_id, platform))
            logger.info(f"Queued utterance (a turn is already running tool calls): {text}")
            on_queue_update()
            return
        await _process(text, brain, voice, session_id=session_id, platform=platform)

    async def _process(text, brain, voice, session_id="default", platform="voice"):
        nonlocal speech_echo_cooldown, last_emotion, turn_active
        if time.time() < speech_echo_cooldown:
            logger.info(f"Echo suppressed: {text}")
            return

        # A gated tool call (destructive shell command / sensitive file path)
        # is waiting on a spoken yes/no -- route this utterance to the answer
        # instead of starting a new chat turn. See
        # charlie.core.Brain.request_tool_approval / get_active_voice_approval.
        from charlie.core import get_active_voice_approval
        pending_approval_id = get_active_voice_approval()
        if pending_approval_id:
            answer = parse_yes_no(text)
            if answer is None:
                voice.speak("Sorry, I didn't catch that. Say yes to continue or no to cancel.", last_emotion)
                return
            from charlie.core import resolve_tool_approval
            resolve_tool_approval(pending_approval_id, answer)
            voice.speak("Okay, running it." if answer else "Cancelled.", last_emotion)
            return

        print(f"\rHeard: {text}", flush=True)
        # Unconditional so is_echo()'s post-speech grace window actually fires, not just mid-speech.
        if voice.is_echo(text):
            logger.info(f"Echo suppressed: {text}")
            return
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

        # Emit transcript event for voice-originated turns only. The web
        # client already renders its own optimistic user bubble the instant
        # it sends the chat command (see handleSendMessage in page.tsx), so
        # echoing a "transcript" event for platform="web" too produced a
        # duplicate user bubble on every web chat message. Voice has no
        # client-side echo of its own -- this event is its only way to get
        # recognized speech into the web UI transcript feed.
        if event_bus and platform == "voice":
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
        turn_active = True
        _turn_generation = brain._chat_generation
        try:
            async for chunk in brain.chat_stream(text, platform=platform, session_id=session_id):
                if brain._chat_generation != _turn_generation:
                    break  # cancelled mid-stream -- stop flushing/speaking chunks chat_stream had already buffered
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

                # Early first-flush: sentence > clause > force at _FIRST_FLUSH_MAX_CHARS.
                if is_first_flush:
                    def _speak_first(part: str) -> None:
                        nonlocal sparkle
                        _safe_speak(voice, sparkle + part, detected_emotion, "first-flush", platform, session_id)
                        sparkle = ""

                    sentence_buffer, flushed = _flush_complete_sentences(
                        sentence_buffer, _speak_first
                    )
                    if flushed:
                        is_first_flush = False
                    else:
                        clause_idx = _CLAUSE_BOUNDARY.search(sentence_buffer)
                        if clause_idx:
                            flush_end = clause_idx.end()
                            _safe_speak(
                                voice, sparkle + sentence_buffer[:flush_end], detected_emotion,
                                "first-clause", platform, session_id,
                            )
                            sparkle = ""
                            sentence_buffer = sentence_buffer[flush_end:].lstrip()
                            is_first_flush = False
                            flushed = True
                        elif len(sentence_buffer) >= _FIRST_FLUSH_MAX_CHARS:
                            idx = sentence_buffer.rfind(" ", 0, _FIRST_FLUSH_MAX_CHARS)
                            if idx > 0:
                                _safe_speak(
                                    voice, sparkle + sentence_buffer[:idx], detected_emotion,
                                    "first-force", platform, session_id,
                                )
                                sparkle = ""
                                sentence_buffer = sentence_buffer[idx:].lstrip()
                            is_first_flush = False
                            flushed = True

                if not flushed:
                    sentence_buffer, flushed = _flush_complete_sentences(
                        sentence_buffer,
                        lambda part: _safe_speak(voice, part, detected_emotion, "sentence", platform, session_id),
                    )

                if not flushed and len(sentence_buffer) >= _MAX_FLUSH_CHARS:
                    # Force-flush: prefer clause (comma/semicolon) boundary,
                    # fall back to word boundary to avoid mid-word splits.
                    clause_idx = _CLAUSE_BOUNDARY.search(sentence_buffer[:_MAX_FLUSH_CHARS])
                    if clause_idx:
                        flush_end = clause_idx.end()
                        _safe_speak(
                            voice, sentence_buffer[:flush_end], detected_emotion, "clause", platform, session_id
                        )
                        sentence_buffer = sentence_buffer[flush_end:].lstrip()
                    else:
                        word_idx = sentence_buffer.rfind(" ", 0, _MAX_FLUSH_CHARS)
                        if word_idx > 0:
                            _safe_speak(
                                voice, sentence_buffer[:word_idx], detected_emotion, "word", platform, session_id
                            )
                            sentence_buffer = sentence_buffer[word_idx:].lstrip()
                        elif sentence_buffer.strip():
                            _safe_speak(
                                voice,
                                sentence_buffer[:_MAX_FLUSH_CHARS],
                                detected_emotion,
                                "force",
                                platform,
                                session_id,
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
                _safe_speak(voice, sparkle + sentence_buffer, detected_emotion, "final", platform, session_id)

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
                if platform == "telegram" and telegram_bot is not None:
                    asyncio.create_task(telegram_bot.stream_finish(session_id.split(":", 1)[1], quick_actions=True))

            # Emit response_done event so the UI can stop its typing indicator.
            if event_bus:
                asyncio.create_task(
                    event_bus.emit("response_done", {"session_id": session_id})
                )
        except Exception:
            # Turn failures used to be silent -- surface one, then re-raise.
            _safe_speak(
                voice, "Sorry, something went wrong on my end. Try again?", last_emotion,
                "turn-failed", platform, session_id,
            )
            if platform == "telegram" and telegram_bot is not None:
                asyncio.create_task(telegram_bot.stream_finish(session_id.split(":", 1)[1]))
            if event_bus:
                asyncio.create_task(
                    event_bus.emit("response_done", {"session_id": session_id})
                )
            raise
        finally:
            turn_active = False
            if pending_turns:
                _next_id, next_text, next_session, next_platform = pending_turns.pop(0)
                logger.info(f"Dequeuing pending turn: {next_text}")
                on_queue_update()
                _schedule_process(
                    _dispatch_or_queue(next_text, next_session, next_platform), loop
                )

        # Learning loop: deferred to background -- doesn't block next turn.
        # Skipped for screen-content queries -- the reply is a description of
        # whatever's on screen at that moment, never a genuine user preference,
        # and storing it as one pollutes memory with stale screen snapshots that
        # resurface on later "what's on my screen" queries.
        from charlie.core import _is_deterministic_reply
        from charlie.core import _SCREEN_QUERY_RE as _screen_query_re
        if (
            full_reply_buffer.strip() and text.strip()
            and not _screen_query_re.search(text)
            and not _is_deterministic_reply(text)
        ):

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
                        learning_prompt, skip_pre_search=True, skip_tools=True, skip_fast_paths=True
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
                        result = await asyncio.get_running_loop().run_in_executor(
                            None,
                            tool_registry.execute_tool,
                            "memory",
                            {
                                "action": "add",
                                "target": "user",
                                "content": learning,
                            },
                        )
                        if result.startswith("Error") or result.startswith("Memory full"):
                            logger.warning(f"Learning write failed: {result}")
                        else:
                            logger.info(f"Learning: {learning}")
                except Exception as e:
                    logger.debug(f"Learning loop skipped: {e}")

            # Fire-and-forget: learning runs in background, doesn't block user
            asyncio.create_task(_background_learn(text, full_reply_buffer))

    async def _reload_voice_engine():
        """Stop and respawn VoiceEngine so mic/VAD/ASR/TTS-model/wake-word settings take effect.

        These are all baked into VoiceEngine.__init__ or the ASR worker subprocess it
        spawns (see charlie/config.py's "voice" restart tier), so a live attribute
        change alone never reaches them -- only recreating the engine does.
        """
        nonlocal voice

        def _rebuild():
            nonlocal voice
            try:
                voice.stop()
            except Exception as ex:
                logger.warning(f"Error stopping voice engine on reload: {ex}")
            voice = VoiceEngine(
                config,
                on_speech=on_speech,
                on_tts_start=on_tts_start,
                on_tts_stop=on_tts_stop,
            )
            voice.start()
            voice.set_wake_word_callback(on_wake_word)

        try:
            # Off-thread: VoiceEngine.__init__ loading the ONNX model inline froze the event loop live.
            await asyncio.to_thread(_rebuild)
            logger.info("VoiceEngine reloaded.")
        except Exception as ex:
            logger.error(f"Error reloading VoiceEngine: {ex}", exc_info=True)

    async def _reload_mcp_client():
        """Stop the MCP subprocess client and restart it if still enabled."""
        nonlocal mcp_client
        from charlie.tools import registry
        for k in [k for k in registry._tools if k.startswith("mcp_")]:
            registry._tools.pop(k, None)
        try:
            mcp_client = await _restart_mcp_client(mcp_client, config)
        except Exception as ex:
            logger.warning(f"Error reloading MCP client: {ex}")
            mcp_client = None

    def _reload_plugin_tools():
        """Re-register plugin tools to match the current enabled flag / allow-dirs."""
        from charlie.tools import registry
        for k in [k for k in registry._tools if k.startswith("plugin_")]:
            registry._tools.pop(k, None)
        if config.plugins_enabled:
            try:
                from charlie.tools import register_plugin_tools
                register_plugin_tools(config)
            except Exception as ex:
                logger.warning(f"Error registering plugins on reload: {ex}")

    async def _do_system_restart():
        """Reload off the command queue -- an inline hung reload step froze chat behind it live."""
        try:
            async with asyncio.timeout(90.0):
                from dotenv import load_dotenv
                load_dotenv(override=True)

                env_values = {
                    spec["key"]: os.getenv(spec["key"])
                    for spec in Config.editable_field_specs()
                    if os.getenv(spec["key"]) is not None
                }
                config.apply_env_updates(env_values)

                await _reload_mcp_client()
                await asyncio.to_thread(_reload_plugin_tools)
                await _reload_voice_engine()
                await brain.refresh_llm_client()
                await brain.refresh_vision_client()
                brain.rebuild_stable_tier()

            if event_bus:
                await event_bus.emit("alert", {
                    "severity": "success",
                    "message": "System configuration successfully reloaded and engine restarted.",
                })
        except asyncio.TimeoutError:
            logger.error("System restart timed out after 90s -- one reload step hung.")
            if event_bus:
                await event_bus.emit("alert", {
                    "severity": "error",
                    "message": "Reload timed out after 90s. Restart Charlie to be safe.",
                })
        except Exception:
            logger.error("System restart failed", exc_info=True)
            if event_bus:
                await event_bus.emit("alert", {
                    "severity": "error",
                    "message": "Reload failed. Check logs.",
                })

    async def consume_web_commands(event_bus, brain):
        """Read commands from the web UI and dispatch them."""
        nonlocal current_web_session_id, voice, mcp_client
        while True:
            try:
                cmd = await event_bus.next_command()
                logger.debug(f"ZMQ received command: {cmd}")
                cmd_type = cmd.get("type")
                if cmd_type == "chat":
                    payload_sid = cmd.get("payload", {}).get("session_id")
                    current_web_session_id = cmd.get("session_id") or payload_sid or _voice_fallback_session_id
                    from charlie.recovery import set_active_session_id
                    set_active_session_id(current_web_session_id)
                    chat_text = cmd.get("text") or cmd.get("payload", {}).get("text", "")
                    await _dispatch_or_queue(chat_text, current_web_session_id, platform="web")
                elif cmd_type == "queue_cancel":
                    cancel_id = cmd.get("payload", {}).get("id")
                    before = len(pending_turns)
                    pending_turns[:] = [t for t in pending_turns if t[0] != cancel_id]
                    if len(pending_turns) != before:
                        on_queue_update()
                elif cmd_type == "session_active":
                    payload_sid = cmd.get("payload", {}).get("session_id")
                    current_web_session_id = cmd.get("session_id") or payload_sid or _voice_fallback_session_id
                    from charlie.recovery import set_active_session_id
                    set_active_session_id(current_web_session_id)
                    logger.info(f"Active session updated to: {current_web_session_id}")
                elif cmd_type == "ws_connection_count":
                    from charlie.recovery import set_active_ws_count
                    set_active_ws_count(cmd.get("count", 0))
                elif cmd_type == "recovery_approve":
                    payload = cmd.get("payload", {})
                    proposal_id = payload.get("proposal_id")
                    if proposal_id:
                        from charlie.recovery import pending_proposals
                        fut = pending_proposals.get(proposal_id)
                        if fut and not fut.done():
                            fut.set_result(True)
                elif cmd_type == "recovery_reject":
                    payload = cmd.get("payload", {})
                    proposal_id = payload.get("proposal_id")
                    if proposal_id:
                        from charlie.recovery import pending_proposals
                        fut = pending_proposals.get(proposal_id)
                        if fut and not fut.done():
                            fut.set_result(False)
                elif cmd_type == "tool_approve":
                    payload = cmd.get("payload", {})
                    request_id = payload.get("request_id")
                    if request_id:
                        from charlie.core import resolve_tool_approval
                        resolve_tool_approval(request_id, True)
                elif cmd_type == "tool_reject":
                    payload = cmd.get("payload", {})
                    request_id = payload.get("request_id")
                    if request_id:
                        from charlie.core import resolve_tool_approval
                        resolve_tool_approval(request_id, False)
                elif cmd_type == "stop":
                    voice.stop_tts()
                    brain.cancel_chat()
                elif cmd_type == "cancel_agent":
                    payload = cmd.get("payload", {})
                    agent_id = payload.get("agent_id")
                    if agent_id:
                        found = brain.cancel_agent(agent_id)
                        # cancel_agent() returning True only means the task was signalled --
                        # a sub-agent blocked inside a run_in_executor tool call won't
                        # actually stop until that call returns, asyncio can't interrupt it.
                        # This ack tells the UI whether cancellation was even possible,
                        # not that it already happened.
                        await event_bus.emit("agent_cancel_ack", {"agent_id": agent_id, "found": found})
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
                elif cmd_type == "extension_installed":
                    # Mirrors charlie/web_server.py's confirm_extension(): the
                    # dashboard's Extensions tab only registers tools into that
                    # process's own registry, which the actual chat loop here
                    # never sees. Re-run the same install against this
                    # process's registry/mcp_client/plugin_manager so Charlie
                    # can actually call the extension in a real conversation.
                    payload = cmd.get("payload", {})
                    try:
                        from charlie.extensions.install import install_extension
                        from charlie.tools import registry as _ext_registry

                        tool_names, mcp_client = install_extension(
                            payload.get("kind", ""),
                            payload.get("name", ""),
                            payload.get("source", ""),
                            payload.get("raw_text", ""),
                            registry=_ext_registry,
                            plugin_manager=plugin_manager,
                            mcp_client=mcp_client,
                            plugin_allow_dirs=config.plugin_allow_dirs,
                        )
                        if payload.get("kind") == "skill":
                            from charlie.extensions.skills import format_skill_block, parse_skill_md
                            manifest = parse_skill_md(payload.get("raw_text", ""))
                            brain.add_installed_skill_block(payload.get("name", ""), format_skill_block(manifest))
                        logger.info(
                            "Mirrored extension install '%s' (%s) into voice process: %s",
                            payload.get("name"), payload.get("kind"), tool_names,
                        )
                    except Exception as ex:
                        logger.warning(
                            f"Failed to mirror extension install '{payload.get('name')}': {ex}",
                            exc_info=True,
                        )
                elif cmd_type == "extension_enabled":
                    payload = cmd.get("payload", {})
                    kind = payload.get("kind", "")
                    ext_name = payload.get("name", "")
                    try:
                        from charlie.tools import registry as _ext_registry
                        if kind == "mcp" and mcp_client is not None:
                            mcp_client.enable_server(_ext_registry, ext_name)
                        elif kind == "plugin":
                            from charlie.extensions.install import builtin_plugin
                            from charlie.tools import enable_plugin
                            enable_plugin(
                                _ext_registry, plugin_manager,
                                builtin_plugin(ext_name, config.plugin_allow_dirs),
                            )
                        # skill/openapi: nothing to do, disable_extension() never
                        # unregisters those tools (see web_server.py's comment).
                    except Exception as ex:
                        logger.warning(f"Failed to mirror extension enable '{ext_name}': {ex}", exc_info=True)
                elif cmd_type == "extension_disabled":
                    payload = cmd.get("payload", {})
                    kind = payload.get("kind", "")
                    ext_name = payload.get("name", "")
                    try:
                        from charlie.tools import registry as _ext_registry
                        if kind == "mcp" and mcp_client is not None:
                            mcp_client.disable_server(_ext_registry, ext_name)
                        elif kind == "plugin":
                            from charlie.tools import disable_plugin
                            disable_plugin(_ext_registry, plugin_manager, ext_name)
                    except Exception as ex:
                        logger.warning(f"Failed to mirror extension disable '{ext_name}': {ex}", exc_info=True)
                elif cmd_type == "extension_uninstalled":
                    payload = cmd.get("payload", {})
                    kind = payload.get("kind", "")
                    ext_name = payload.get("name", "")
                    try:
                        from charlie.tools import registry as _ext_registry
                        if kind == "mcp" and mcp_client is not None:
                            mcp_client.remove_server(_ext_registry, ext_name)
                        elif kind in ("skill", "openapi"):
                            for tool_name in payload.get("tool_names", []):
                                _ext_registry.unregister_tool(tool_name)
                        if kind == "skill":
                            brain.remove_installed_skill_block(ext_name)
                    except Exception as ex:
                        logger.warning(f"Failed to mirror extension uninstall '{ext_name}': {ex}", exc_info=True)
                elif cmd_type == "system_restart":
                    logger.info("System restart command received. Reloading configuration and engine...")
                    asyncio.create_task(_do_system_restart())
                elif cmd_type == "background_task_start":
                    payload = cmd.get("payload", {})
                    from charlie import background_task
                    try:
                        await background_task.start(
                            config, event_bus, payload.get("text", ""),
                            session_store=store, memory_store=memory_store, voice=voice,
                        )
                    except RuntimeError as ex:
                        await event_bus.emit("alert", {"severity": "warn", "message": str(ex)})
                elif cmd_type == "background_task_cancel":
                    payload = cmd.get("payload", {})
                    from charlie import background_task
                    background_task.cancel(payload.get("task_id", ""))
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

    # Start floating pet subprocess (Windows-only, PySide6)
    if config.pet_enabled and sys.platform == "win32":
        try:
            pet_entry = os.path.join(
                os.path.dirname(__file__), "charlie", "pet_entry.py"
            )
            pet_proc = subprocess.Popen(
                [sys.executable, pet_entry],
                cwd=os.path.dirname(__file__),
                env=_web_env,
            )
            logger.info(f"Pet subprocess started (PID: {pet_proc.pid})")
        except Exception as e:
            logger.warning(f"Failed to start pet window: {e}")

    logger.info("Loading AI models (Whisper, VAD, Kokoro)...")
    try:
        # TTS lifecycle callbacks for IPC events
        def on_tts_start(text: str = ""):
            if event_bus:
                payload = {"session_id": current_web_session_id, "text": text}
                _emit_threadsafe(event_bus, loop, "speaking_start", payload)

        def on_tts_stop():
            if event_bus:
                _emit_threadsafe(event_bus, loop, "speaking_stop", {"session_id": current_web_session_id})

        voice = VoiceEngine(
            config,
            on_speech=on_speech,
            on_tts_start=on_tts_start,
            on_tts_stop=on_tts_stop,
        )
        voice.start()

        def on_wake_word():
            if event_bus:
                _emit_threadsafe(event_bus, loop, "wake_word", {})
            if config.browser_enabled and config.browser_warm_on_wake:
                from charlie.browser import controller as browser_controller
                browser_controller.warm()

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
                    skip_tools=True, skip_fast_paths=True
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

        async def _emit_system_status(bus):
            import psutil
            try:
                while True:
                    cpu_percent = psutil.cpu_percent()
                    ram_percent = psutil.virtual_memory().percent
                    await bus.emit("system_status", {
                        "cpu": cpu_percent,
                        "ram": ram_percent,
                        "gpu": await asyncio.to_thread(_read_gpu_percent),
                    })
                    await asyncio.sleep(1.0)
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.error(f"Metric emitter error: {e}")

        # Run voice loop + web command consumer concurrently via ZeroMQ
        async with EventBus(pub_port=5555, pull_port=5556, is_producer=True) as bus:
            event_bus = bus
            voice.set_event_bus(bus)
            import charlie.recovery
            charlie.recovery._event_bus = bus
            charlie.recovery.set_active_session_id(current_web_session_id)
            import charlie.tools
            charlie.tools.set_event_bus(bus, asyncio.get_running_loop())

            from charlie import reminders as _reminders
            _reminders.set_loop(asyncio.get_running_loop())

            def _on_reminder_fired(reminder_id: str, text: str) -> None:
                msg = f"Reminder: {text}"
                _safe_speak(voice, msg, "neutral", "reminder")
                asyncio.ensure_future(bus.emit("alert", {"severity": "info", "message": msg}))

            _reminders.set_fire_callback(_on_reminder_fired)

            if config.telegram_bot_token and config.telegram_user_id:
                from charlie.telegram_bot import TelegramBot, set_active_bot

                async def _on_telegram_message(text: str, chat_id: str) -> None:
                    session_id = f"telegram:{chat_id}"
                    store.create_session(session_id, source="telegram")
                    await telegram_bot.stream_start(chat_id)
                    async with telegram_bot.typing(chat_id):
                        await _dispatch_or_queue(text, session_id, platform="telegram")

                async def _on_telegram_approval(approval_id: str, approved: bool) -> None:
                    from charlie.core import resolve_tool_approval
                    resolve_tool_approval(approval_id, approved)

                async def _on_telegram_voice(audio_bytes: bytes, _caption: str, chat_id: str) -> None:
                    from charlie import asr_worker
                    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as f:
                        f.write(audio_bytes)
                        tmp_path = f.name
                    try:
                        text = await asyncio.to_thread(
                            asr_worker.transcribe_file, tmp_path,
                            config.whisper_model, config.gpu_device, config.default_language,
                        )
                    finally:
                        os.unlink(tmp_path)
                    if not text.strip():
                        await telegram_bot.send_message(chat_id, "I couldn't make out any speech in that voice note.")
                        return
                    session_id = f"telegram:{chat_id}"
                    store.create_session(session_id, source="telegram")
                    await telegram_bot.stream_start(chat_id)
                    async with telegram_bot.typing(chat_id):
                        await _dispatch_or_queue(text, session_id, platform="telegram")

                async def _on_telegram_photo(photo_bytes: bytes, caption: str, chat_id: str) -> None:
                    from charlie.desktop.vision import to_data_url
                    description = await brain._describe_image(to_data_url(photo_bytes))
                    photo_note = f"[Photo attached -- {description}]"
                    text = f"{caption}\n\n{photo_note}" if caption else photo_note
                    session_id = f"telegram:{chat_id}"
                    store.create_session(session_id, source="telegram")
                    await telegram_bot.stream_start(chat_id)
                    async with telegram_bot.typing(chat_id):
                        await _dispatch_or_queue(text, session_id, platform="telegram")

                telegram_bot = TelegramBot(
                    config.telegram_bot_token,
                    config.telegram_user_id,
                    on_message=_on_telegram_message,
                    on_approval=_on_telegram_approval,
                    on_voice=_on_telegram_voice,
                    on_photo=_on_telegram_photo,
                )
                try:
                    await telegram_bot.start()
                    set_active_bot(telegram_bot)
                except Exception as e:
                    logger.warning(f"Failed to start Telegram bot: {e}")
                    telegram_bot = None

            _monitor_loop = asyncio.get_running_loop()

            def _push_telegram_alert(message: str) -> None:
                """Best-effort proactive push to Telegram -- alerts you wouldn't otherwise see away from the PC.

                Uses run_coroutine_threadsafe (not ensure_future) because this is also called from
                _on_resource_alert, which runs on the charlie-monitors background thread with no event
                loop of its own -- ensure_future there raised "no current event loop in thread".
                """
                if telegram_bot is None or not config.telegram_user_id:
                    return
                asyncio.run_coroutine_threadsafe(
                    telegram_bot.send_message(config.telegram_user_id, message), _monitor_loop
                )

            from charlie import background_task as _background_task
            interrupted_task = _background_task.check_interrupted_task()
            if interrupted_task is not None:
                _interrupted_msg = (
                    f"Note: your background task \"{interrupted_task.get('text', '')}\" was "
                    f"interrupted by a restart at step {interrupted_task.get('current_step', 0) + 1} "
                    f"of {len(interrupted_task.get('steps', []))}."
                )
                logger.info(_interrupted_msg)
                await bus.emit("alert", {"severity": "warning", "message": _interrupted_msg})
                voice.speak(_interrupted_msg, "neutral")
                _push_telegram_alert(_interrupted_msg)

            def _read_cpu_ram_percent() -> Tuple[float, float]:
                import psutil
                return psutil.cpu_percent(), psutil.virtual_memory().percent

            def _on_resource_alert(message: str) -> None:
                logger.warning(f"Resource alert: {message}")
                try:
                    asyncio.run_coroutine_threadsafe(
                        bus.emit("alert", {"severity": "warning", "message": message}),
                        _monitor_loop,
                    )
                except Exception:
                    logger.warning("Failed to emit resource alert event", exc_info=True)
                try:
                    voice.speak(message, "neutral")
                except Exception:
                    logger.warning("Failed to speak resource alert", exc_info=True)
                _push_telegram_alert(message)

            try:
                start_monitor_thread(
                    get_cpu_ram=_read_cpu_ram_percent,
                    on_alert=_on_resource_alert,
                    cpu_threshold_pct=config.alert_cpu_pct,
                    ram_threshold_pct=config.alert_ram_pct,
                )
            except Exception:
                logger.error("Failed to start resource monitor thread", exc_info=True)

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
                    consume_web_commands(bus, brain),
                    _emit_system_status(bus),
                    mcp_start_task,
                    extensions_reload_task,
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
        if "telegram_bot" in locals() and telegram_bot is not None:
            try:
                await telegram_bot.stop()
            except Exception as e:
                logger.warning(f"Telegram bot stop error: {e}")
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
