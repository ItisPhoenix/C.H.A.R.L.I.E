'use client'

import { Mic, Volume2, Radio } from 'lucide-react'
import { type SetupData } from '../types'

export function VoiceStep({ data, onChange }: { data: SetupData; onChange: (d: SetupData) => void }) {
  return (
    <div className="space-y-8">
      <div className="text-center">
        <h2 className="font-display text-2xl text-charlie-cyan tracking-wide mb-2">Voice Setup</h2>
        <p className="text-charlie-dim text-sm font-body">Configure speech recognition and synthesis</p>
      </div>

      <div className="space-y-4">
        {/* Voice toggle */}
        <div className="flex items-center justify-between glass-card p-4 rounded-lg">
          <div className="flex items-center gap-3">
            <Mic size={20} className="text-charlie-cyan" />
            <div>
              <div className="font-body text-sm text-charlie-text">Voice Enabled</div>
              <div className="text-charlie-dim text-xs">Always listening for wake word</div>
            </div>
          </div>
          <button
            onClick={() => onChange({ ...data, voice_enabled: !data.voice_enabled })}
            className={`w-12 h-6 rounded-full transition-all cursor-pointer ${
              data.voice_enabled ? 'bg-charlie-cyan' : 'bg-charlie-border'
            }`}
          >
            <div
              className={`w-5 h-5 rounded-full bg-white transition-transform ${
                data.voice_enabled ? 'translate-x-6' : 'translate-x-0.5'
              }`}
            />
          </button>
        </div>

        {/* Wake word */}
        <div>
          <label className="text-charlie-dim text-xs font-display tracking-wider uppercase mb-2 flex items-center gap-2">
            <Radio size={12} /> Wake Word
          </label>
          <input
            type="text"
            value={data.wake_word}
            onChange={(e) => onChange({ ...data, wake_word: e.target.value })}
            className="w-full bg-charlie-card border border-charlie-border rounded-lg px-4 py-2.5 text-sm font-mono text-charlie-text focus:border-charlie-cyan/50 transition-all"
          />
        </div>

        {/* STT Model */}
        <div>
          <label className="text-charlie-dim text-xs font-display tracking-wider uppercase mb-2 flex items-center gap-2">
            <Mic size={12} /> Speech-to-Text
          </label>
          <select
            value={data.stt_model}
            onChange={(e) => onChange({ ...data, stt_model: e.target.value })}
            className="w-full bg-charlie-card border border-charlie-border rounded-lg px-4 py-2.5 text-sm font-body text-charlie-text focus:border-charlie-cyan/50 transition-all"
          >
            <option value="faster-whisper">Faster-Whisper (local, fast)</option>
            <option value="whisper">Whisper (local, accurate)</option>
            <option value="gemini-live">Gemini Live (cloud)</option>
          </select>
        </div>

        {/* TTS Model */}
        <div>
          <label className="text-charlie-dim text-xs font-display tracking-wider uppercase mb-2 flex items-center gap-2">
            <Volume2 size={12} /> Text-to-Speech
          </label>
          <select
            value={data.tts_model}
            onChange={(e) => onChange({ ...data, tts_model: e.target.value })}
            className="w-full bg-charlie-card border border-charlie-border rounded-lg px-4 py-2.5 text-sm font-body text-charlie-text focus:border-charlie-cyan/50 transition-all"
          >
            <option value="kokoro">Kokoro (local, fast)</option>
            <option value="piper">Piper (local)</option>
            <option value="gemini-live">Gemini Live (cloud)</option>
          </select>
        </div>
      </div>
    </div>
  )
}
