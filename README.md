# Charlie

A low-latency, voice-first AI assistant that runs entirely on your local machine.

**C**ompletely **H**elpful **A**nd **R**ather **L**ocal **I**ntelligent **E**ngine.

```
Voice in  -> VAD -> Whisper ASR -> LLM (streaming) -> Kokoro TTS -> Voice out
~1.2s       ~80ms   ~410ms        ~200ms              ~50ms         ~50ms
```

---

## Features

- **Voice-first**: Continuous listening, speaks responses aloud. No keyboard needed.
- **Streaming TTS**: Speaks as the LLM generates -- no waiting for full replies.
- **Barge-in**: Interrupt Charlie mid-sentence. Say "stop", "wait", or just start talking.
- **Tool calling**: Web search (SearXNG/Exa/Tavily/DuckDuckGo), shell commands, file I/O.
- **Emotional tone**: Adapts speech speed and energy based on your mood.
- **Persistent memory**: Remembers facts across sessions via `MEMORY.md` and `USER.md`.
- **Local-first**: All speech processing runs locally. Only the LLM call goes to the network.
- **Self-hosted search**: SearXNG integration for private web search with no API key.

---

## Architecture

```
main.py                   Entry point, logging, voice loop, TTS flush
charlie/
  core.py                 Brain class -- LLM orchestration, tool loop, streaming
  voice.py                VoiceEngine -- VAD, ASR, TTS (Kokoro), audio I/O
  tools.py                ToolRegistry -- web_search, shell_execute, file_read/write
  config.py               Config dataclass from .env
  personality.py          Emotion classification + voice command parsing
  asr_worker.py           Whisper ASR subprocess
  session_store.py        SQLite + FTS5 session history
```

**Pipeline**:
1. **VAD** (Voice Activity Detection) detects when you start/stop speaking
2. **Whisper ASR** transcribes your speech to text
3. **Brain** sends text to LLM with system prompt, memory, and tool definitions
4. **Streaming**: LLM response is flushed to TTS at sentence/clause boundaries
5. **Kokoro TTS** synthesizes speech audio
6. **Speaker** plays the audio

---

## Requirements

- **OS**: Windows 11 (PowerShell for system commands)
- **Python**: 3.12+
- **GPU**: NVIDIA GPU with CUDA (for Whisper ASR and Kokoro TTS)
- **LLM**: Any OpenAI-compatible API endpoint

---

## Quick Start

### 1. Install dependencies

```bash
pip install -r requirements.txt
# or
uv sync
```

### 2. Configure

```bash
cp .env.example .env
```

Edit `.env` with your settings:

```env
# Required: Your LLM endpoint
LLM_URL=https://your-api-endpoint/v1
LLM_API_KEY=your-key-here
LLM_MODEL=your-model-id

# Optional: Self-hosted SearXNG for private web search
SEARXNG_URL=http://localhost:8080

# Optional: Hardware overrides
MIC_INDEX=-1          # -1 = system default, >=0 = specific device
OUTPUT_INDEX=-1
GPU_DEVICE=cuda
```

### 3. Run

```bash
python main.py
```

Charlie will initialize the voice engine, download models on first run, and start listening.

---

## Configuration

All settings are via environment variables (`.env` file). See `.env.example` for the full list.

| Variable | Default | Description |
|---|---|---|
| `LLM_URL` | (required) | OpenAI-compatible API base URL |
| `LLM_API_KEY` | (required) | API key for the LLM |
| `LLM_MODEL` | (required) | Model ID to use |
| `SEARXNG_URL` | (empty) | Self-hosted SearXNG URL for private search |
| `WHISPER_MODEL` | `distil-large-v3` | Whisper model for ASR |
| `KOKORO_VOICE` | `af_heart` | Kokoro TTS voice |
| `VAD_THRESHOLD` | `0.75` | Voice activity detection sensitivity |
| `SILENCE_TIMEOUT` | `1.0` | Seconds of silence before processing |
| `ENABLE_BARGE_IN` | `true` | Allow interrupting Charlie mid-response |
| `LLM_DISABLE_REASONING` | `true` | Disable chain-of-thought for lower latency |

---

## Voice Commands

Say these while Charlie is speaking to control behavior:

| Command | Effect |
|---|---|
| "stop" / "wait" / "cancel" | Interrupt and stop speaking |
| "be energetic" / "speak faster" | Increase speech energy and speed |
| "calm down" / "speak slower" | Slow down and speak calmly |

---

## Search Providers

Charlie tries search providers in this order:

1. **SearXNG** (self-hosted, no API key) -- recommended
2. **Exa** (requires `EXA_API_KEY`)
3. **Tavily** (requires `TAVILY_API_KEY`)
4. **DuckDuckGo** (free, no key needed, rate-limited)

Set `SEARXNG_URL` in `.env` for the best experience.

---

## Project Structure

```
.
  main.py               # Entry point
  .env                  # Your configuration (not tracked)
  .env.example          # Configuration template
  MEMORY.md             # Persistent system context
  SOUL.md               # Personality and behavior rules
  USER.md               # User preferences
  sessions.db           # Conversation history (SQLite)
  charlie/
    __init__.py
    core.py             # LLM orchestration
    voice.py            # Voice pipeline
    tools.py            # Tool definitions
    config.py           # Configuration
    personality.py      # Emotion detection
    asr_worker.py       # ASR subprocess
    session_store.py    # Session database
    charlie.onnx        # Embedding model
  models/
    kokoro-v1.0.onnx    # Kokoro TTS model (~310MB)
    voices-v1.0.bin     # Voice embeddings (~27MB)
  tests/
    test_personality.py
    test_tools.py
  logs/
    charlie.log
```

---

## Development

```bash
# Run linter
uv run ruff check .

# Run tests
uv run pytest -v

# Run both
uv run ruff check . && uv run pytest -v
```

---

## How It Works

### Streaming TTS
Charlie does not wait for the full LLM response before speaking. As tokens arrive:
- **Sentence boundaries** (`.`, `!`, `?`) trigger immediate TTS flush
- **Clause boundaries** (`,`, `;`, `:`) also trigger flush for faster response
- **Force flush** at 100 characters prevents long pauses

### Tool Loop
When the LLM wants to use a tool (e.g., web search):
1. Tool executes with a 15-second timeout
2. Result is injected into the conversation
3. LLM generates a final answer from the result
4. Max 4 tool rounds per question

### Barge-in
When you speak during Charlie's response:
1. Command words ("stop", "wait") trigger immediate stop
2. New speech cancels the current LLM generation
3. Cooldown prevents double-trigger within 0.8s

### Text Humanization
Before TTS, Charlie cleans LLM output for natural speech:
- Ellipsis converted to natural pause
- Dashes converted to clause breaks (commas)
- Repeated punctuation normalized
- Markdown artifacts stripped
- Numbers converted to words

---

## License

MIT
