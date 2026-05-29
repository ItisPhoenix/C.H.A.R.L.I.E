import asyncio
import json
import logging
import multiprocessing
import os
import time

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from charlie.config import settings
from charlie.privacy.redactor import PrivacyRedactor
from charlie.telegram.away_reporter import AwayReporter
from charlie.telegram.call_tracker import CallTracker
from charlie.telegram.jarvis import JarvisFeatures
from charlie.intelligence.mood_detector import MoodDetector
from charlie.intelligence.silence_detector import SilenceDetector
from charlie.intelligence.time_travel import TimeTravelEngine

logger = logging.getLogger("charlie.telegram")

# Voice mode constants
VOICE_OFF = "off"
VOICE_ON = "on"       # voice reply only when user sends voice
VOICE_TTS = "tts"     # voice reply to ALL messages

def _load_voice_mode():
    """Load voice mode from charlie_config.json."""
    try:
        config_path = os.path.join(os.getcwd(), "charlie_config.json")
        if os.path.exists(config_path):
            with open(config_path, "r") as f:
                cfg = json.load(f)
            return cfg.get("telegram", {}).get("voice_mode", VOICE_OFF)
    except Exception:
        pass
    return VOICE_OFF

def _save_voice_mode(mode):
    """Save voice mode to charlie_config.json."""
    try:
        config_path = os.path.join(os.getcwd(), "charlie_config.json")
        cfg = {}
        if os.path.exists(config_path):
            with open(config_path, "r") as f:
                cfg = json.load(f)
        cfg.setdefault("telegram", {})["voice_mode"] = mode
        with open(config_path, "w") as f:
            json.dump(cfg, f, indent=2)
    except Exception as e:
        logger.error(f"save_voice_mode_err | {e}")

class TelegramBridge(multiprocessing.Process):
    def __init__(self, brain_task_q: multiprocessing.Queue, telegram_q: multiprocessing.Queue):
        super().__init__(daemon=True, name="TelegramBridge")
        self.brain_task_q = brain_task_q
        self.telegram_q = telegram_q
        self.redactor = PrivacyRedactor()
        self.token = settings.supervisor.telegram_token
        self.whitelist = [int(x) for x in (settings.supervisor.telegram_chat_id or "").split(",") if x]
        self.app = None
        self.voice_mode = _load_voice_mode()

        # Intelligence modules
        self.away_reporter = AwayReporter(telegram_q)
        self.call_tracker = CallTracker()
        self.mood_detector = MoodDetector()
        self.silence_detector = SilenceDetector()
        self.time_travel = TimeTravelEngine()
        self.jarvis = JarvisFeatures(telegram_q, brain_task_q)

    def run(self):
        if not self.token:
            logger.error("telegram_token_missing | disabling_bridge")
            return

        self.app = ApplicationBuilder().token(self.token).build()

        # Register commands with BotFather
        self.app.post_init = self._register_commands

        # Handlers
        self.app.add_handler(CommandHandler("start", self._cmd_start))
        self.app.add_handler(CommandHandler("status", self._cmd_status))
        self.app.add_handler(CommandHandler("help", self._cmd_help))
        self.app.add_handler(CommandHandler("tasks", self._cmd_tasks))
        self.app.add_handler(CommandHandler("briefing", self._cmd_briefing))
        self.app.add_handler(CommandHandler("memory", self._cmd_memory))
        self.app.add_handler(CommandHandler("cancel", self._cmd_cancel))
        self.app.add_handler(CommandHandler("voice", self._cmd_voice))
        self.app.add_handler(CommandHandler("calls", self._cmd_calls))
        self.app.add_handler(CommandHandler("track", self._cmd_track))
        self.app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, self._handle_voice))
        self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_message))
        self.app.add_handler(MessageHandler(filters.PHOTO, self._handle_photo))
        self.app.add_handler(MessageHandler(filters.Document.ALL, self._handle_document))
        self.app.add_handler(MessageHandler(filters.LOCATION, self._handle_location))
        self.app.add_handler(CallbackQueryHandler(self._handle_button))
        self.app.add_error_handler(_error_handler)

        # Track which users last sent a voice message (for VOICE_ON mode)
        self._last_voice_user = set()

        logger.info("telegram_bridge_active")
        self.app.run_polling()

    async def _register_commands(self, application):
        """Register commands with BotFather on startup."""
        try:
            await application.bot.set_my_commands([
                BotCommand("start", "Start the bot"),
                BotCommand("status", "System status"),
                BotCommand("help", "Show commands"),
                BotCommand("tasks", "List active tasks"),
                BotCommand("briefing", "Daily briefing"),
                BotCommand("memory", "Search memory"),
                BotCommand("cancel", "Cancel operation"),
                BotCommand("voice", "Voice reply settings"),
                BotCommand("calls", "Call analytics"),
                BotCommand("track", "Tracking list"),
            ])
        except Exception as e:
            logger.debug(f"register_commands_err | {e}")

        # Startup catch-up: send digest if CHARLIE was offline for a while
        try:
            catchup = self.away_reporter.generate_startup_catchup()
            if catchup:
                for user_id in self.whitelist:
                    await application.bot.send_message(chat_id=user_id, text=catchup, parse_mode='Markdown')
        except Exception as e:
            logger.debug(f"startup_catchup_err | {e}")

        # Start JARVIS scheduled tasks + outgoing message loop
        asyncio.create_task(self._jarvis_scheduler())
        asyncio.create_task(self._outgoing_loop())

    async def _outgoing_loop(self):
        """Polls telegram_q for messages to send to the user."""
        if not hasattr(self, "_streaming_msgs"):
            self._streaming_msgs = {} # {user_id: {"msg_id": int, "content": str, "last_update": float}}

        while True:
            try:
                # Update heartbeat
                if hasattr(self, "heartbeat") and self.heartbeat:
                    self.heartbeat.value = time.time()

                while not self.telegram_q.empty():
                    msg = self.telegram_q.get_nowait()
                    msg_type = msg.get("type")

                    # Gate proactive messages during silence periods
                    is_proactive = msg_type in ("PROACTIVE_CHAT", "CLEAR_CONFIRMATION")
                    if is_proactive and self.silence_detector.should_be_silent():
                        reason = self.silence_detector.get_silence_reason()
                        logger.debug(f"silence_gate | blocked {msg_type} | reason={reason}")
                        self.away_reporter.record_activity("proactive", f"Silenced: {msg_type}")
                        continue

                    for user_id in self.whitelist:
                        if msg_type == "CHAT_MSG":
                            content = self.redactor.redact(msg.get("content", ""))
                            # If we were streaming, clear that entry and update the message
                            if user_id in self._streaming_msgs:
                                data = self._streaming_msgs.pop(user_id)
                                try:
                                    await self.app.bot.edit_message_text(
                                        chat_id=user_id,
                                        message_id=data["msg_id"],
                                        text=f"🤖 *CHARLIE:* {content}",
                                        parse_mode='Markdown'
                                    )
                                except Exception as e:
                                    logger.debug(f"telegram_edit_final_err | {e}")
                                    await self.app.bot.send_message(chat_id=user_id, text=f"🤖 *CHARLIE:* {content}", parse_mode='Markdown')
                            else:
                                await self.app.bot.send_message(chat_id=user_id, text=f"🤖 *CHARLIE:* {content}", parse_mode='Markdown')

                            # Voice reply if mode matches
                            should_voice = (
                                self.voice_mode == VOICE_TTS or
                                (self.voice_mode == VOICE_ON and user_id in self._last_voice_user)
                            )
                            # Clear voice tracking after use
                            self._last_voice_user.discard(user_id)
                            if should_voice and content.strip():
                                try:
                                    voice_path = await self._generate_voice(content)
                                    if voice_path:
                                        with open(voice_path, "rb") as f:
                                            await self.app.bot.send_voice(chat_id=user_id, voice=f)
                                        try:
                                            os.remove(voice_path)
                                        except OSError:
                                            pass
                                except Exception as e:
                                    logger.debug(f"telegram_voice_reply_err | {e}")

                        elif msg_type == "STREAM_PARTIAL":
                            content = msg.get("content", "")
                            if not content.strip(): continue

                            data = self._streaming_msgs.get(user_id)
                            now = time.time()

                            if not data:
                                sent_msg = await self.app.bot.send_message(
                                    chat_id=user_id,
                                    text=f"🤖 *CHARLIE:* {content}...",
                                    parse_mode='Markdown'
                                )
                                self._streaming_msgs[user_id] = {
                                    "msg_id": sent_msg.message_id,
                                    "content": content,
                                    "last_sent": content,
                                    "last_update": now
                                }
                            else:
                                data["content"] += content
                                # Rate limit: 1.5s + content changed
                                if now - data["last_update"] > 1.5 and data["content"] != data["last_sent"]:
                                    try:
                                        await self.app.bot.edit_message_text(
                                            chat_id=user_id,
                                            message_id=data["msg_id"],
                                            text=f"🤖 *CHARLIE:* {data['content']}...",
                                            parse_mode='Markdown'
                                        )
                                        data["last_sent"] = data["content"]
                                        data["last_update"] = now
                                    except Exception as e:
                                        logger.debug(f"telegram_stream_edit_err | {e}")
                                        pass

                        elif msg_type == "CONFIRM_REQUIRED":
                            content = msg.get("content", {})
                            desc = content.get("desc", "Tool execution pending.")
                            tier = content.get("tier", 1)

                            keyboard = [
                                [
                                    InlineKeyboardButton("✅ Confirm", callback_data="confirm"),
                                    InlineKeyboardButton("❌ Abort", callback_data="abort")
                                ]
                            ]
                            reply_markup = InlineKeyboardMarkup(keyboard)
                            await self.app.bot.send_message(
                                chat_id=user_id,
                                text=f"⚠️ *AUTHORIZATION REQUIRED (TIER {tier})*\n\n{desc}",
                                reply_markup=reply_markup,
                                parse_mode='Markdown'
                            )

                        elif msg_type == "PRIORITY_ALERT":
                            content = self.redactor.redact(msg.get("content", ""))
                            level = msg.get("priority", "high")
                            prefix_map = {"critical": "🚨 *CRITICAL ALERT*", "high": "⚠️ *HIGH PRIORITY ALERT*"}
                            prefix = prefix_map.get(level, "📌 *ALERT*")
                            await self.app.bot.send_message(
                                chat_id=user_id,
                                text=f"{prefix}\n\n{content}",
                                parse_mode='Markdown'
                            )

                await asyncio.sleep(0.5)
            except Exception as e:
                logger.error(f"telegram_outgoing_err | {e}")
                await asyncio.sleep(1)

    async def _cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.effective_user or update.effective_user.id not in self.whitelist:
            if update.message: await update.message.reply_text("Unauthorized access denied.")
            return
        await update.message.reply_text("C.H.A.R.L.I.E. Telegram Command Center active. Awaiting instruction, Sir.")

    async def _cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.effective_user or update.effective_user.id not in self.whitelist: return
        self.brain_task_q.put({"type": "TEXT", "content": "system status", "source": "telegram"})

    async def _cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.effective_user or update.effective_user.id not in self.whitelist: return
        await update.message.reply_text(
            "🤖 *CHARLIE Telegram Commands*\n\n"
            "/start — Start the bot\n"
            "/status — System status\n"
            "/help — Show this help\n"
            "/tasks — List active tasks\n"
            "/briefing — Daily briefing\n"
            "/memory — Search memory\n"
            "/cancel — Cancel operation\n"
            "/voice — Voice reply settings\n\n"
            "You can also send: text, photos, voice messages, documents, and locations.",
            parse_mode='Markdown'
        )

    async def _cmd_tasks(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.effective_user or update.effective_user.id not in self.whitelist: return
        self.brain_task_q.put({"type": "TEXT", "content": "list all active tasks", "source": "telegram"})

    async def _cmd_briefing(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.effective_user or update.effective_user.id not in self.whitelist: return
        self.brain_task_q.put({"type": "TEXT", "content": "give me my daily briefing", "source": "telegram"})

    async def _cmd_memory(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.effective_user or update.effective_user.id not in self.whitelist: return
        query = " ".join(context.args) if context.args else ""
        if not query:
            await update.message.reply_text("Usage: /memory <search query>")
            return
        self.brain_task_q.put({"type": "TEXT", "content": f"search memory for: {query}", "source": "telegram"})

    async def _cmd_cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.effective_user or update.effective_user.id not in self.whitelist: return
        self.brain_task_q.put({"type": "CONFIRMATION_RESULT", "confirmed": False})
        await update.message.reply_text("Operation cancelled.")

    async def _cmd_voice(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.effective_user or update.effective_user.id not in self.whitelist: return
        args = context.args
        if not args:
            cycle = {VOICE_OFF: VOICE_ON, VOICE_ON: VOICE_TTS, VOICE_TTS: VOICE_OFF}
            self.voice_mode = cycle.get(self.voice_mode, VOICE_OFF)
        else:
            mode = args[0].lower()
            if mode in (VOICE_OFF, VOICE_ON, VOICE_TTS, "status"):
                if mode == "status":
                    await update.message.reply_text(f"Voice mode: *{self.voice_mode}*", parse_mode='Markdown')
                    return
                self.voice_mode = mode
            else:
                await update.message.reply_text("Usage: /voice [on|tts|off|status]")
                return
        _save_voice_mode(self.voice_mode)
        descriptions = {
            VOICE_OFF: "Voice replies disabled. Text only.",
            VOICE_ON: "Voice replies when you send voice messages.",
            VOICE_TTS: "Voice replies to ALL messages.",
        }
        await update.message.reply_text(f"Voice mode: *{self.voice_mode}* — {descriptions.get(self.voice_mode, '')}", parse_mode='Markdown')

    async def _cmd_calls(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.effective_user or update.effective_user.id not in self.whitelist: return
        analytics = self.call_tracker.get_analytics()
        await update.message.reply_text(analytics, parse_mode='Markdown')

    async def _cmd_track(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.effective_user or update.effective_user.id not in self.whitelist: return
        args = context.args
        if not args:
            tracking = self.jarvis.list_tracking()
            await update.message.reply_text(tracking, parse_mode='Markdown')
            return
        action = args[0].lower()
        if action == "add" and len(args) > 1:
            query = " ".join(args[1:])
            result = self.jarvis.add_tracking("item", query)
            await update.message.reply_text(result)
        elif action == "remove" and len(args) > 1:
            result = self.jarvis.remove_tracking(args[1])
            await update.message.reply_text(result)
        else:
            await update.message.reply_text("Usage: /track [add <query>|remove <id>]")

    async def _jarvis_scheduler(self):
        """Periodic JARVIS tasks — morning briefing, email digest, finance updates."""
        last_morning = 0
        last_email = 0
        last_finance = 0

        while True:
            try:
                now = time.time()
                current_hour = time.localtime(now).tm_hour

                # Morning briefing at 8 AM (once per day)
                if current_hour == 8 and (now - last_morning) > 3600:
                    if not self.silence_detector.should_be_silent():
                        briefing = self.jarvis.generate_morning_briefing()
                        for user_id in self.whitelist:
                            await self.app.bot.send_message(chat_id=user_id, text=briefing, parse_mode='Markdown')
                        last_morning = now

                # Email digest every 2 hours
                if (now - last_email) > 7200:
                    if not self.silence_detector.should_be_silent():
                        email_digest = self.jarvis.generate_email_digest()
                        if email_digest and "unavailable" not in email_digest.lower():
                            for user_id in self.whitelist:
                                await self.app.bot.send_message(chat_id=user_id, text=email_digest, parse_mode='Markdown')
                    last_email = now

                # Finance update every hour during market hours (9 AM - 4 PM)
                if 9 <= current_hour <= 16 and (now - last_finance) > 3600:
                    if not self.silence_detector.should_be_silent():
                        finance = self.jarvis.generate_finance_update()
                        if finance and "unavailable" not in finance.lower():
                            for user_id in self.whitelist:
                                await self.app.bot.send_message(chat_id=user_id, text=finance, parse_mode='Markdown')
                    last_finance = now

                # Check tracking updates every 6 hours
                if int(now) % 21600 < 60:
                    tracking_update = self.jarvis.check_tracking_updates()
                    if tracking_update:
                        for user_id in self.whitelist:
                            await self.app.bot.send_message(chat_id=user_id, text=tracking_update, parse_mode='Markdown')

                await asyncio.sleep(60)  # Check every minute

            except Exception as e:
                logger.error(f"jarvis_scheduler_err | {e}")
                await asyncio.sleep(60)

    async def _handle_voice(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle voice/audio messages — download and send to brain for transcription."""
        if not update.effective_user or update.effective_user.id not in self.whitelist:
            return
        try:
            os.makedirs("scratch", exist_ok=True)
            voice = update.message.voice or update.message.audio
            file = await voice.get_file()
            path = f"scratch/telegram_voice_{int(time.time())}.ogg"
            await file.download_to_drive(path)
            self.brain_task_q.put({
                "type": "REMOTE_VOICE",
                "path": path,
                "source": f"telegram_{update.effective_user.id}",
            })
            # Track that this user sent a voice message (for VOICE_ON mode)
            self._last_voice_user.add(update.effective_user.id)
            await update.message.reply_text("Voice received. Transcribing...")
        except Exception as e:
            logger.error(f"telegram_voice_err | {e}")
            await update.message.reply_text("Failed to process voice message.")

    async def _handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.effective_user or update.effective_user.id not in self.whitelist: return

        text = update.message.text

        # Prompt injection sanitization
        from charlie.brain.core import Brain
        _injection_patterns = [
            r"(?i)ignore\s+(all\s+)?(previous|prior|above|earlier)",
            r"(?i)you\s+are\s+now\s+(a|an|the)",
            r"(?i)from\s+now\s+on\s+you\s+are",
            r"(?i)pretend\s+you\s+are",
            r"(?i)system\s*:\s*override",
            r"(?i)new\s+system\s+prompt",
            r"(?i)disregard\s+(all|your|the)\s+(instructions|rules|guidelines)",
            r"(?i)do\s+anything\s+now",
            r"(?i)jailbreak|DAN\s+mode",
        ]
        import re
        for p in _injection_patterns:
            if re.search(p, text):
                logger.warning("telegram_injection_detected | pattern=%s | user=%s", p[:40], update.effective_user.id)
                await update.message.reply_text("Prompt injection detected. Message blocked.")
                return
        self.away_reporter.record_interaction()
        self.mood_detector.analyze_message(text)
        self.silence_detector.record_interaction()

        # Send digest if user was away for a while
        if self.away_reporter.should_send_digest():
            digest = self.away_reporter.generate_digest(clear=True)
            if digest:
                await update.message.reply_text(digest, parse_mode='Markdown')

        # Check for Tasker call events
        call_data = self.call_tracker.parse_tasker_message(text)
        if call_data:
            self.call_tracker.record_call(call_data)
            intel = self.call_tracker.get_caller_intelligence(call_data["number"])
            await update.message.reply_text(intel, parse_mode='Markdown')
            return

        # Check for manual call reports
        text_lower = text.lower()
        if any(w in text_lower for w in ("called me", "missed call", "got a call", "phone call")):
            call_data = self.call_tracker.parse_manual_report(text)
            if call_data and call_data["number"] != "unknown":
                self.call_tracker.record_call(call_data)
                intel = self.call_tracker.get_caller_intelligence(call_data["number"])
                await update.message.reply_text(intel, parse_mode='Markdown')
                return

        # Check for temporal/time-travel queries
        temporal_keywords = ("what was i doing", "what did i do", "last week", "yesterday", "this time", "ago")
        if any(kw in text_lower for kw in temporal_keywords):
            if "last week" in text_lower:
                result = self.time_travel.query_last_week()
            elif "yesterday" in text_lower:
                result = self.time_travel.query_yesterday()
            else:
                result = self.time_travel.query_relative(days_ago=1)
            await update.message.reply_text(result, parse_mode='Markdown')
            return

        # Check for call analytics queries
        if any(w in text_lower for w in ("call analytics", "call history", "call stats", "who called")):
            analytics = self.call_tracker.get_analytics()
            await update.message.reply_text(analytics, parse_mode='Markdown')
            return

        # Check for tracking queries
        if "track" in text_lower and any(w in text_lower for w in ("package", "flight", "delivery")):
            tracking = self.jarvis.list_tracking()
            await update.message.reply_text(tracking, parse_mode='Markdown')
            return

        # Mood check-in
        if self.mood_detector.should_checkin():
            checkin = self.mood_detector.get_checkin_message()
            await update.message.reply_text(checkin)

        # Forward to brain
        self.brain_task_q.put({"type": "TEXT", "content": text, "source": f"telegram_{update.effective_user.id}"})

    async def _handle_photo(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle photo messages — download and send to brain for vision analysis."""
        if not update.effective_user or update.effective_user.id not in self.whitelist:
            return
        try:
            os.makedirs("scratch", exist_ok=True)
            photo = update.message.photo[-1]
            file = await photo.get_file()
            path = f"scratch/telegram_img_{int(time.time())}.jpg"
            await file.download_to_drive(path)
            caption = update.message.caption or "Describe this image in detail."
            self.brain_task_q.put({
                "type": "IMAGE",
                "path": path,
                "query": caption,
                "source": f"telegram_{update.effective_user.id}",
            })
            await update.message.reply_text("Image received. Analyzing...")
        except Exception as e:
            logger.error(f"telegram_photo_err | {e}")
            await update.message.reply_text("Failed to process image.")

    async def _handle_document(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle document/file messages — download to scratch/."""
        if not update.effective_user or update.effective_user.id not in self.whitelist:
            return
        try:
            os.makedirs("scratch", exist_ok=True)
            doc = update.message.document
            file = await doc.get_file()
            filename = doc.file_name or f"doc_{int(time.time())}"
            # Sanitize filename
            safe_name = "".join(c for c in filename if c.isalnum() or c in "._-")
            path = f"scratch/telegram_{int(time.time())}_{safe_name}"
            await file.download_to_drive(path)
            self.brain_task_q.put({
                "type": "TEXT",
                "content": f"I received a file: {filename}. Please acknowledge and summarize if possible.",
                "source": f"telegram_{update.effective_user.id}",
            })
            await update.message.reply_text(f"File received: {filename}")
        except Exception as e:
            logger.error(f"telegram_doc_err | {e}")
            await update.message.reply_text("Failed to process file.")

    async def _handle_location(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle location messages — store in context."""
        if not update.effective_user or update.effective_user.id not in self.whitelist:
            return
        loc = update.message.location
        lat, lon = loc.latitude, loc.longitude
        self.brain_task_q.put({
            "type": "TEXT",
            "content": f"My current location is: latitude {lat}, longitude {lon}. Please remember this.",
            "source": f"telegram_{update.effective_user.id}",
        })
        await update.message.reply_text(f"Location received: {lat}, {lon}")

    async def _handle_button(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()

        if not query.from_user or query.from_user.id not in self.whitelist: return

        action = query.data
        try:
            if action == "confirm":
                self.brain_task_q.put({"type": "CONFIRMATION_RESULT", "confirmed": True})
                await query.edit_message_text(text=f"{query.message.text}\n\n✅ *Confirmed by Sir.*", parse_mode='Markdown')
            else:
                self.brain_task_q.put({"type": "CONFIRMATION_RESULT", "confirmed": False})
                await query.edit_message_text(text=f"{query.message.text}\n\n❌ *Aborted by Sir.*", parse_mode='Markdown')
        except Exception as e:
            logger.error(f"telegram_button_err | {e}")


    async def _generate_voice(self, text):
        """Generate voice audio using Kokoro TTS + ffmpeg OGG conversion. Returns path or None."""
        try:
            from charlie.telegram.voice_reply import text_to_telegram_voice
            return await asyncio.to_thread(text_to_telegram_voice, text)
        except Exception as e:
            logger.error(f"generate_voice_err | {e}")
            return None


async def _error_handler(update, context):
    """Log errors from Telegram handlers."""
    logger.error(f"telegram_error | {context.error}", exc_info=context.error)


def run_bridge(brain_task_q, status_q, telegram_q, audio_cmd_q, heartbeat):
    bridge = TelegramBridge(brain_task_q, telegram_q)
    bridge.heartbeat = heartbeat
    bridge.run()
