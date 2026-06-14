# C.H.A.R.L.I.E.
Completely Helpful And Rather Local Intelligent Engine

A voice-first, local AI assistant with GPU-accelerated speech pipeline.

## Quick Start
```bash
uv sync
uv run python main.py
```

## Architecture

### Speech Pipeline
- **VAD**: Silero VAD (local, GPU) — real-time voice activity detection
- **ASR**: faster-whisper `distil-large-v3` in isolated subprocess (CUDA) — ~26ms
- **TTS**: Kokoro ONNX via `onnxruntime-gpu` (CUDA) — ~700ms per utterance
- **Fillers**: Pre-synthesized backchannel audio, 0ms playback cost

### LLM Routing
- **Primary**: NVIDIA NIM (`nemotron-3-super-120b-a12b`) via HTTPS
- **Fallback**: Same API, automatic retry on failure
- **Summarization**: Shared backend model for research decomposition/synthesis

### Research
- **Web Search**: Local SearXNG instance (`docker compose up -d` in `SearXNG/`)
- **Memory**: SQLite semantic memory layer for long-term context retention

### Performance Targets
- ASR → LLM TTFT: <500ms (sub-second)
- TTS: <1000ms per utterance (GPU)
- Total perceived latency: <2s from end of speech to start of reply
