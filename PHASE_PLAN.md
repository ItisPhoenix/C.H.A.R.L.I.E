# C.H.A.R.L.I.E. Phase Plan

## Phase 1: Voice Chat (Week 1)
**Goal**: Wake word → LLM → TTS working end-to-end

### 1.1 Vision Sentinel Disabled ✅
- `charlie_config.json`: `vision.sentinel_enabled: false`
- Fixes Brain restarts from VCAMDS spam

### 1.2 Wire AudioEngine Wake → ChainExecutor
- Wake detection already puts `{"type": "WAKE"}` on `brain_task_q`
- Reactor handles `WAKE` type (added)
- STT puts `{"type": "TEXT", "content": text}` on `brain_task_q`
- ChainExecutor processes → LLM → TTS

### 1.3 AudioEngine.say(text) ✅
- Added method to queue TTS via `tts_q`

### 1.4 Interrupt (Barge-in)
- VAD during TTS playback
- If user speaks while Charlie speaks → interrupt_event.set()
- Stop playback, flush TTS queue, restart listening

### 1.5 Standby Mode
- Wake phrases: "wake up", "system online", "charlie online"
- `SET_STANDBY` command via `audio_cmd_q`
- Status: IDLE when standby, LISTENING when active

### 1.6 Mute/Unmute
- `MUTE` / `UNMUTE` commands via `audio_cmd_q`
- Dashboard button + voice "mute"/"unmute"

### 1.7 Test End-to-End
- `python main.py --daemon`
- Say "Charlie" → hear response
- Verify interrupt, standby, mute work