import asyncio
import logging
import re

from charlie.brain.llm_client import RateLimitExceeded

logger = logging.getLogger("charlie.brain.stream")

# Maximum buffered TTS items when queue is full (backpressure)
_TTS_BUFFER_MAX = 50


class StreamHandler:
    def __init__(self, brain):
        self.brain = brain
        # Backpressure buffer for TTS when queue is full
        self._tts_buffer: list[dict] = []

    def _put_with_backpressure(self, q, item: dict) -> None:
        """Put item on queue; buffer it if queue is full instead of dropping."""
        try:
            q.put(item, block=False)
            # Flush any buffered items now that space may exist
            self._flush_tts_buffer()
        except Exception:
            # Queue full — buffer instead of dropping
            if len(self._tts_buffer) < _TTS_BUFFER_MAX:
                self._tts_buffer.append(item)
                logger.debug(
                    f"tts_backpressure_buffer | buffered={len(self._tts_buffer)}"
                )
            else:
                logger.warning(
                    f"tts_backpressure_drop | buffer_full={_TTS_BUFFER_MAX}"
                )

    def _flush_tts_buffer(self) -> None:
        """Drain buffered TTS items into the queue if space exists."""
        if not self._tts_buffer:
            return
        tts_q = getattr(self.brain, "tts_q", None)
        if not tts_q:
            return
        while self._tts_buffer:
            try:
                tts_q.put(self._tts_buffer[0], block=False)
                self._tts_buffer.pop(0)
            except Exception:
                # Still full, stop flushing
                break

    async def simple_llm_call(self, prompt: str, temp: float = 0.3) -> str:
        """Lightweight non-ReAct call for meta-tasks like greetings."""
        fallback_msg = "System operational. Welcome back, Sir."
        messages = [
            {"role": "system", "content": "You are C.H.A.R.L.I.E., a professional AI assistant."},
            {"role": "user", "content": prompt},
        ]

        try:
            response = await self.brain.llm_client.complete(
                messages, max_tokens=64, temperature=temp,
            )
            res = response.content

            if not res or len(res.strip()) < 15:
                await asyncio.sleep(2.0)
                retry_resp = await self.brain.llm_client.complete(
                    [
                        {"role": "system", "content": "You are C.H.A.R.L.I.E., a professional AI assistant."},
                        {"role": "user", "content": "Briefly greet Sir and state that systems are online."},
                    ],
                    max_tokens=64,
                    temperature=0.1,
                )
                res = retry_resp.content

            return res or fallback_msg
        except RateLimitExceeded:
            return "Rate limit exceeded. Please wait a moment, Sir."
        except Exception as e:
            err_str = str(e) if str(e) else type(e).__name__
            logger.error(f"simple_llm_call_failed | Exception: {err_str}")
            return fallback_msg

    async def monitor_inference_lag(self, source: str) -> None:
        """Emits 'Neural Link Lag' alerts if LLM response exceeds 8 seconds."""
        try:
            await asyncio.sleep(8)
            msg = "Neural link lag detected. Synchronizing with local workstation..."
            if source == "local" or source == "all":
                self.brain._safe_put(self.brain.status_q, {"type": "THINKING_STATUS", "content": msg})
            if (source.startswith("telegram") or source == "all") and self.brain.telegram_q:
                self.brain._safe_put(self.brain.telegram_q, {"type": "CHAT_MSG", "speaker": "SYSTEM", "content": f"📡 {msg}"})
        except asyncio.CancelledError:
            pass

    async def emit_thinking(self, content: str, source: str = "local"):
        """Routes partial thinking tokens to the correct channel."""
        if source == "local" or source == "all":
            self.brain._safe_put(self.brain.status_q, {"type": "THINKING_STATUS", "content": content})
        if (source.startswith("telegram") or source == "all") and self.brain.telegram_q:
            self.brain._safe_put(self.brain.telegram_q, {"type": "STREAM_PARTIAL", "content": content})

    async def stream_chat_completion(self, payload, source, sent_sentences_global):
        """
        Handles SSE streaming via LLMClient, delta parsing, and real-time TTS pipelining.
        Returns the full concatenated response.

        LLMClient handles retry + rate limiting.  This method handles:
        - Thought block filtering
        - TTS pipelining with backpressure
        - Status/progress emission
        """
        full_response_parts = []
        in_thought_block = False

        messages = payload.get("messages", [])

        try:
            async for data in self.brain.llm_client.stream(
                messages,
                max_tokens=payload.get("max_tokens", 1024),
                temperature=payload.get("temperature", 0.7),
                **{k: v for k, v in payload.items() if k not in ("messages", "max_tokens", "temperature")},
            ):
                try:
                    choices = data.get("choices", [])
                    if not choices:
                        continue
                    delta = choices[0].get("delta", {})
                    content = delta.get("content")
                    reasoning = delta.get("reasoning_content") or delta.get("reasoning", "")

                    if content:
                        if "<thought" in content.lower() or "<reasoning" in content.lower():
                            in_thought_block = True

                        is_json_artifact = content.strip() in ["{", "}", '"', ":", ","]

                        if not in_thought_block and not is_json_artifact:
                            await self.emit_thinking(content, source)

                        # TTS Pipelining
                        current_full = "".join(full_response_parts)
                        if not in_thought_block and '"final_answer": "' in current_full:
                            fa_marker = '"final_answer": "'
                            fa_start = current_full.find(fa_marker) + len(fa_marker)
                            current_fa = current_full[fa_start:]
                            if '"' in current_fa and not current_fa.endswith('\\"'):
                                current_fa = current_fa[:current_fa.find('"')]

                            fa_fragments = re.split(r"(?<=[.!?|;:,])\s+", current_fa)
                            if len(fa_fragments) > 1:
                                for fa_s in fa_fragments[:-1]:
                                    fa_s_clean = fa_s.strip().replace('\\"', '"').replace("\\n", " ")
                                    fa_s_clean = re.sub(r"<[^>]+>", "", fa_s_clean)
                                    fa_s_clean = re.sub(r"(?i)\b(user|assistant|system|sir|tts|charlie):\s*", "", fa_s_clean)
                                    fa_s_clean = fa_s_clean.replace("<endofturn>", "").replace("<startofturn>", "").strip()

                                    if fa_s_clean and len(fa_s_clean) > 5:
                                        norm_fa = fa_s_clean.lower().strip()
                                        if norm_fa not in sent_sentences_global:
                                            if source == "local":
                                                self._put_with_backpressure(
                                                    self.brain.tts_q,
                                                    {"type": "SPEAK", "content": fa_s_clean},
                                                )
                                            sent_sentences_global.add(norm_fa)

                        if "</thought>" in content.lower() or "</reasoning>" in content.lower():
                            in_thought_block = False
                        full_response_parts.append(content)
                    elif reasoning:
                        full_response_parts.append(reasoning)

                except Exception as e:
                    logger.debug(f"stream_delta_error | {e}")
                    continue

            # Flush remaining TTS buffer at stream end
            self._flush_tts_buffer()
            return "".join(full_response_parts)

        except RateLimitExceeded:
            logger.warning("stream_rate_limited")
            self._flush_tts_buffer()
            return "".join(full_response_parts)
        except RuntimeError as rt_err:
            # LLMClient raises RuntimeError after exhausting retries
            logger.warning(f"stream_failed | {rt_err} | partial_len={sum(len(p) for p in full_response_parts)}")
            self._flush_tts_buffer()
            return "".join(full_response_parts)
        except Exception as outer_err:
            logger.error(f"stream_outer_error | {outer_err}")
            self._flush_tts_buffer()
            return "".join(full_response_parts)
