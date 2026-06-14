# Charlie: Dynamic Engineering & Behavioral Framework

This document defines the load-bearing engineering principles and behavioral DNA for Charlie. It is a **living contract**: any future expansion, refactor, or new feature MUST adhere to these meta-rules to maintain Charlie's performance and personality integrity.

## Keep the codebase clean and simple 
- Don't overcomplicate and duplicate and make the codes unnecessarily long when it can be done in less line maintaing the integrity and working.

## Do web-research  and ask questions 
- Always do a web-research and ask questions using ask tools before writing or finalizing the plan and dont write the plan until the user is 100% sure.

## Always Search and use the relevant skills
- like using-superpowers, clean-code, python-pro, brainstorming, etc if there's a 1% chance of that skills being usefull 

## 1. Behavioral DNA (The "Soul")
- **Agnostic Identity**: Charlie is an entity, not a "model." NEVER mention AI, LLMs, or technical boundaries in the persona.
- **Truth Protocol**: Charlie's first instinct is verification. 
  - *Trivia Boundary*: Direct answers only for universally static facts.
  - *Research Default*: Any explanation, analysis, or current event triggers a research loop.
  - *Faithful Uncertainty*: If tools fail or conflict, Charlie must hedge (e.g., "I'm not completely certain, but...").
- **Emotional Continuity**: Emotions are not session-bound. They are persistent state variables that influence both linguistic tone and physical TTS delivery.

## 2. System Architecture Principles
- **Separation of Logic & Persona**: Logic lives in `core`, Personality lives in `personality`. Never hardcode prompts in the engine.
- **Streaming-First Pipeline**: All data flows must be processed as generators. Batching is a failure mode. 
  - *Constraint*: Time-To-First-Audio (TTFA) must be prioritized over Time-To-Last-Byte.
- **Latency Masking (Verbal Fillers)**: Never allow silence during computation. Use the `on_thought_callback` to inject "Thinking Breath" audio cues.
- **Thread-Safe Vocalization**: TTS must always use a thread-safe queue. Spawning threads per sentence is prohibited; use a persistent worker loop.

## 3. Interaction Mechanics
- **Adaptive Volatility**: Charlie's verbosity is inversely proportional to user interruption frequency. 
  - *Barge-in Logic*: Interruption = immediate transition to `concise` mode.
- **Voice-Safe Output**: All linguistic generation must pass through a phonetic safety filter (no markdown, no symbols, no lists).

## 4. Future Integration Rules
When adding new skills or tools:
1. **Zero-Latency Acknowledgment**: The tool must have a "Start-of-Work" verbal cue (e.g., "Checking that for you...").
2. **Clean Interception**: Ensure the tool-trigger (e.g., `TOOL:`) is buffered and hidden from the user's ears.
3. **Open-Endpoint Standard**: Maintain strict OpenAI-compatible API standards to keep the LLM backend swappable.

## Engineering Verification Protocol
1. **Streaming Integrity**: Does the change introduce a "wait-for-full-reply" block?
2. **Truth Check**: Can I force this feature to lie? (If yes, add a prompt guardrail).
3. **Phonetic Audit**: How does the new output sound when read by a 1.0x speed TTS?
4. **State Persistence**: Does this feature survive a `Ctrl+C` restart?
