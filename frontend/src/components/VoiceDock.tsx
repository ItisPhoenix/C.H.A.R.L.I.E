"use client";

import { useMemo, type ReactElement } from "react";
import { Mic, MicOff, Volume2, VolumeX } from "lucide-react";
import { useCharlieStore, type VoiceState, type AudioState, type MicState } from "../store/useCharlieStore";

interface VoiceDockProps {
  state: VoiceState;
  connected: boolean;
  audio: AudioState;
  mic: MicState;
  onAudioControl: (patch: { muted?: boolean; volume?: number }) => void;
  onMicControl: (patch: { mic_muted: boolean }) => void;
}

const BAR_COUNT = 20;
const CENTER = BAR_COUNT / 2;

const MIN_HEIGHT_PX = 4;
const MAX_HEIGHT_PX = 24;

const STATE_LABELS: Record<VoiceState, string> = {
  idle: "Idle",
  listening: "Listening",
  thinking: "Thinking",
  speaking: "Speaking",
};

const STATE_COLORS: Record<VoiceState, string> = {
  idle: "var(--color-status-idle)",
  listening: "var(--color-status-listening)",
  thinking: "var(--color-status-thinking)",
  speaking: "var(--color-status-speaking)",
};

export function VoiceDock({
  state,
  connected,
  audio,
  mic,
  onAudioControl,
  onMicControl,
}: VoiceDockProps): ReactElement {
  const toggleSpeakerMute = () => onAudioControl({ muted: !audio.muted });
  const setVolume = (value: number) =>
    onAudioControl({ volume: value, muted: value === 0 ? audio.muted : false });
  const toggleMic = () => onMicControl({ mic_muted: !mic.mic_muted });
  
  const accentColor = useCharlieStore((s) => s.accentColor);
  const audioLevel = useCharlieStore((s) => s.audioLevel);
  const listeningTrigger = useCharlieStore((s) => s.listeningTrigger);

  const bars = useMemo(
    () => Array.from({ length: BAR_COUNT }).map((_, i) => i),
    []
  );

  const stateColor = STATE_COLORS[state] || "var(--color-status-idle)";
  const effectiveVolume = audio.muted ? 0 : audio.volume;
  const stateLabel =
    state === "listening" && listeningTrigger === "wake_word" ? "Wake Word" : STATE_LABELS[state];

  const voiceDockBorder = !connected
    ? "color-mix(in srgb, var(--color-status-error) 30%, transparent)"
    : mic.mic_muted
    ? "color-mix(in srgb, var(--color-status-idle) 20%, transparent)"
    : audio.muted
    ? "color-mix(in srgb, var(--color-status-error) 25%, transparent)"
    : "var(--color-glass-border)";

  return (
    <div
      data-state={state}
      role="status"
      aria-label={`Voice pipeline: ${!connected ? "offline" : stateLabel}`}
      style={{
        borderColor: voiceDockBorder,
      }}
      className="flex items-center justify-between gap-6 p-3 bg-zinc-950/40 border border-[var(--color-glass-border)] rounded-xl z-20 select-none mx-4 mb-4"
    >
      {/* Dynamic Equalizer Visualizer */}
      <div className="flex-1 flex items-center justify-center gap-[3px] h-[26px]">
        {!connected ? (
          <span className="text-[10px] font-bold uppercase tracking-[0.18em] text-red-500 animate-pulse font-mono">
            Audio offline
          </span>
        ) : (
          bars.map((i) => {
            let scaleY = MIN_HEIGHT_PX / MAX_HEIGHT_PX;
            let animClass = "";

            if (state === "thinking") {
              animClass = "animate-wave-thinking";
            } else if (state === "listening" || state === "speaking") {
              // Mathematical frequency bell curve
              const distFromCenter = Math.abs(i - CENTER);
              const factor = 1 - distFromCenter / CENTER;
              // Add a bit of pseudo frequency randomness, seeded from audioLevel so it varies with each update instead of wall clock
              const pseudoRand = 0.5 + 0.5 * Math.sin(audioLevel * 137 + i);
              scaleY = 0.15 + audioLevel * 0.85 * factor * pseudoRand;
              scaleY = Math.max(0.15, Math.min(1.0, scaleY));
            }

            return (
              <div
                key={i}
                className={`w-[3px] rounded-full transition-transform duration-[80ms] ease-out ${animClass}`}
                style={{
                  backgroundColor: stateColor,
                  height: `${MAX_HEIGHT_PX}px`,
                  animationDelay: animClass ? `${i * 0.04}s` : undefined,
                  transform: `scaleY(${scaleY})`,
                }}
                aria-hidden="true"
              />
            );
          })
        )}
      </div>

      <span
        style={{
          color: !connected ? "var(--color-status-error)" : stateColor,
        }}
        className={`text-[10px] font-bold uppercase tracking-[0.18em] min-w-[80px] text-center font-mono`}
        aria-live="polite"
      >
        {!connected ? "Offline" : stateLabel}
      </span>

      <div
        className={`flex items-center gap-3 border-l border-white/10 pl-6 transition-opacity duration-200 ${
          !connected ? "opacity-40 pointer-events-none" : ""
        }`}
        aria-disabled={!connected}
      >
        {/* Speaker control */}
        <div
          onWheel={(e) => {
            const delta = e.deltaY < 0 ? 0.05 : -0.05;
            const nextVol = Math.max(0, Math.min(1, audio.volume + delta));
            setVolume(nextVol);
          }}
          className="flex items-center gap-2"
        >
          <button
            onClick={toggleSpeakerMute}
            aria-label={audio.muted ? "Unmute speaker" : "Mute speaker"}
            className={`rounded-lg w-[32px] h-[32px] grid place-items-center cursor-pointer transition ${
              audio.muted
                ? "bg-red-500/10 text-red-400 border border-red-500/20"
                : "text-slate-400 hover:text-slate-100 hover:bg-white/5 active:scale-[0.98]"
            }`}
          >
            {audio.muted ? <VolumeX className="w-4 h-4" /> : <Volume2 className="w-4 h-4" />}
          </button>
          <input
            type="range"
            min={0}
            max={1}
            step={0.05}
            value={effectiveVolume}
            onChange={(e) => setVolume(Number(e.target.value))}
            aria-label="Speaker volume"
            style={{ accentColor }}
            className="w-20 cursor-pointer accent-[var(--color-accent-teal)]"
          />
          <span className="text-[10px] font-mono text-slate-500 w-7 text-right">
            {Math.round(effectiveVolume * 100)}
          </span>
        </div>

        {/* Microphone Toggle */}
        <div className="flex items-center gap-2 pl-1">
          <button
            onClick={toggleMic}
            aria-label={mic.mic_muted ? "Unmute microphone" : "Mute microphone"}
            style={{
              boxShadow:
                connected && !mic.mic_muted
                  ? `0 0 ${4 + audioLevel * 20}px ${1 + audioLevel * 4}px rgba(6, 182, 212, ${0.15 + audioLevel * 0.4})`
                  : "none",
            }}
            className={`rounded-lg w-[32px] h-[32px] grid place-items-center cursor-pointer transition ${
              !connected
                ? "bg-red-500/10 text-red-400 border border-red-500/20 animate-pulse"
                : mic.mic_muted
                ? "bg-zinc-800 text-slate-500 hover:bg-zinc-700/80"
                : "text-[var(--color-accent-teal)] bg-cyan-950/20 border border-cyan-500/30 hover:bg-cyan-950/40 hover:text-cyan-300 active:scale-[0.98]"
            }`}
          >
            {mic.mic_muted || !connected ? <MicOff className="w-4 h-4" /> : <Mic className="w-4 h-4" />}
          </button>
          <div className="flex flex-col text-right">
            <span className="text-[10px] font-bold tracking-widest uppercase text-slate-500">
              MIC LINK
            </span>
            <span
              style={{
                color: !connected
                  ? "var(--color-status-error)"
                  : mic.mic_muted
                  ? "var(--color-text-muted)"
                  : "var(--color-accent-teal)",
              }}
              className="text-[10px] font-bold uppercase font-mono"
            >
              {!connected ? "OFFLINE" : mic.mic_muted ? "MUTED" : "LIVE"}
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}
