# C.H.A.R.L.I.E.
Completely Helpful And Rather Local Intelligent Engine

## Usage
1. `uv sync`
2. `uv run python main.py`

## Features
- **Voice-First Interaction**: Uses faster-whisper and Kokoro-ONNX with emotional tone adaptation.
- **Asynchronous Research Agent**: Non-blocking web search and deep research.
  - Type `research <topic>` for a background report.
  - Immediate feedback with background chime ("Ding!") when results are ready.
- **Continuous Learning**: Semantic memory layer for long-term knowledge retention.
- **Private Search Integration**: Native support for local SearXNG instances.

## Configuration
- **Dual-Model Routing**: Define `FAST_LLM_URL` in `.env` for backend tasks (decomposition/synthesis) to reduce latency while keeping a high-reasoning model for chat.
- **SearXNG**: To unblock local search, run:
  ```bash
  cd SearXNG
  docker compose up -d
  ```
