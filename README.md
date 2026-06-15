# C.H.A.R.L.I.E.
**Completely Helpful And Rather Local Intelligent Engine**

Charlie is a high-fidelity, voice-first local AI assistant built for speed, privacy, and agency.

## 🚀 Recent Architectural Upgrades (June 2026)
- **Passive Activation**: Hands-free wake word detection ("Charlie") via `openWakeWord`.
- **Hybrid Intelligence**: `LLMRouter` automatically balances local Llama-3 (Ollama) with high-reasoning cloud models.
- **Real-world Agency**: Integrated MCP (Model Context Protocol) with support for 80+ tools.
- **Procedural Audio**: Zero-latency non-file-based audio feedback.
- **Smart Mode**: 15-second conversational follow-up window.

## 🛠 Core Systems
- **Hearing**: `Whisper` (distil-large-v3) in an isolated subprocess.
- **Voice**: `Kokoro-ONNX` (Local, GPU Accelerated).
- **Brain**: Dual-path LLM architecture with automatic fallback.
- **Memory**: SQLite-backed long-term semantic storage.
- **Research**: Real-time web intelligence via `SearXNG` + `Crawl4AI`.

## 📂 Project Structure

```text
.
├── main.py              # Entry point & event loop
├── charlie/
│   ├── core.py          # The "Brain" (routing, tool use, research)
│   ├── voice.py         # The "Senses" (ASR, TTS, Chimes)
│   ├── wake_word.py     # Passive attention engine
│   ├── llm_router.py    # Local/Cloud logic
│   ├── mcp_client.py    # Tool server connectivity
│   ├── discovery.py     # System self-awareness logic
│   └── personality.py   # Identity & dynamic prompt generation
├── SOUL.md              # Core personality & values
├── CHANGELOG.md         # Source of truth for upgrades
└── mcp_config.json      # External tool configuration
```

## ⚡ Quick Start
1. Configure your `.env` (see `.env.example`).
2. Configure tools in `mcp_config.json`.
3. Run: `uv run python main.py`
4. Say **"Charlie"** to begin.

---
*“I treat data-driven skepticism as a moral imperative.”* — Charlie
