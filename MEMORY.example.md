# MEMORY.md -- System Context Memory
#
# Charlie stores system-level context and learned facts here.
# This file is injected into the system prompt at session start.
# Max 2200 chars. Consolidate entries when full.
# Do NOT put sensitive info here -- it is part of the LLM context.

# Format: one fact per line, natural language.
# Examples:
# Charlie runs on Windows 11 with an RTX 4060 Ti GPU.
# The local LLM is served via Ollama at http://localhost:11434.
# Web search uses SearXNG (self-hosted) with Exa and Tavily as fallbacks.
# The user's project is called Charlie -- a voice-first AI assistant.
# TTS uses Kokoro-ONNX running on CPU to avoid CUDA deadlocks with faster-whisper.
# The voice pipeline order is: VAD -> STT (Whisper) -> LLM -> TTS (Kokoro).
