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

const STATE_COLOR: Record<string, string> = {
  idle: "bg-status-idle",
  listening: "bg-status-listening",
  thinking: "bg-status-thinking",
  speaking: "bg-status-speaking",
};

const STATE_TEXT: Record<string, string> = {
  idle: "text-[var(--color-status-idle)]",
  listening: "text-[var(--color-status-listening)]",
  thinking: "text-[var(--color-status-thinking)]",
  speaking: "text-[var(--color-status-speaking)]",
};

const MIN_HEIGHT_PX = 3;
const MAX_HEIGHT_PX = 24;

function barEnvelope(index: number): number {
  const dist = Math.abs(index - CENTER) / CENTER;
  return 1 - dist * 0.65;
}

function barHeightFor(level: number, index: number): number {
  const env = barEnvelope(index);
  const scaled = MIN_HEIGHT_PX + level * env * (MAX_HEIGHT_PX - MIN_HEIGHT_PX);
  return Math.min(MAX_HEIGHT_PX, Math.max(MIN_HEIGHT_PX, scaled));
}

export function VoiceDock({
  state,
  connected,
  audio,
  mic,
  onAudioControl,
  onMicControl,
}: VoiceDockProps): ReactElement {
  const audioLevel = useCharlieStore((s) => s.audioLevel);
  const toggleSpeakerMute = () => onAudioControl({ muted: !audio.muted });
  const setVolume = (value: number) =>
    onAudioControl({ volume: value, muted: value === 0 ? audio.muted : false });
  const toggleMic = () => onMicControl({ mic_muted: !mic.mic_muted });

  const bars = useMemo(
    () => Array.from({ length: BAR_COUNT }).map((_, i) => i),
    []
  );

  const barColor = STATE_COLOR[state] ?? STATE_COLOR.idle;
  const labelColor = STATE_TEXT[state] ?? STATE_TEXT.idle;
  const effectiveVolume = audio.muted ? 0 : audio.volume;
  const liveMic = connected && !mic.mic_muted;
  const liveAudio = !audio.muted && (state === "speaking" || state === "listening");
  const showLevel = connected && (liveMic || liveAudio);

  return (
    <div
      data-state={state}
      className={`mx-4 mb-4 p-3 rounded-xl flex items-center justify-between gap-6 border bg-[var(--color-glass-bg)] z-20 select-none transition-colors duration-200 ${
        !connected
          ? "border-status-error/50"
          : mic.mic_muted
          ? "border-status-idle/45"
          : audio.muted
          ? "border-status-error/45"
          : "border-[var(--color-glass-border)]"
      }`}
    >
      <div className="flex-1 flex items-center justify-center gap-[3px] h-7">
        {!connected ? (
          <span className="text-[11px] font-bold uppercase tracking-[0.18em] text-status-error animate-pulse">
            Audio offline
          </span>
        ) : (
          bars.map((i) => {
            let animClass = "";
            if (connected) {
              if (state === "thinking") animClass = "animate-wave-thinking";
              else if (state === "listening") animClass = "animate-wave-listening";
              else if (state === "speaking") animClass = "animate-wave-speaking";
            }
            return (
              <div
                key={i}
                className={`w-[3px] rounded-full ${barColor} ${animClass}`}
                style={{
                  height: `${MAX_HEIGHT_PX}px`,
                  animationDelay: animClass ? `${i * 0.04}s` : undefined,
                  transform: !animClass
                    ? `scaleY(${MIN_HEIGHT_PX / MAX_HEIGHT_PX})`
                    : undefined,
                  transition: "transform 200ms ease-out",
                }}
                aria-hidden="true"
              />
            );
          })
        )}
      </div>

      <span
        className={`text-[11px] font-bold uppercase tracking-[0.18em] min-w-[88px] text-center ${labelColor}`}
        aria-live="polite"
      >
        {!connected ? "offline" : state}
      </span>

      <div className="flex items-center gap-3 border-l border-[var(--color-glass-border)] pl-6">
        <div className="flex items-center gap-2">
          <button
            onClick={toggleSpeakerMute}
            aria-label={audio.muted ? "Unmute speaker" : "Mute speaker"}
            aria-pressed={audio.muted}
            className={`rounded-xl w-9 h-9 grid place-items-center cursor-pointer transition ${
              audio.muted
                ? "bg-status-error/20 text-status-error"
                : "text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)]"
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
            className="w-24 accent-[var(--color-accent-teal)] cursor-pointer"
          />
          <span className="text-[10px] font-mono text-[var(--color-text-muted)] w-7 text-right">
            {Math.round(effectiveVolume * 100)}
          </span>
        </div>

        <div className="flex items-center gap-2 pl-1">
          <button
            onClick={toggleMic}
            aria-label={mic.mic_muted ? "Unmute microphone" : "Mute microphone"}
            aria-pressed={mic.mic_muted}
            className={`rounded-xl w-9 h-9 grid place-items-center cursor-pointer transition ${
              !connected
                ? "bg-status-error/20 text-status-error animate-pulse"
                : mic.mic_muted
                ? "bg-status-idle/20 text-status-idle"
                : "text-status-speaking hover:text-[var(--color-text-primary)]"
            }`}
          >
            {mic.mic_muted || !connected ? <MicOff className="w-4 h-4" /> : <Mic className="w-4 h-4" />}
          </button>
          <div className="flex flex-col text-right">
            <span className="text-[9px] font-mono font-bold tracking-widest uppercase text-[var(--color-text-muted)]">
              Voice Link
            </span>
            <span
              className={`text-xs font-bold uppercase ${
                !connected
                  ? "text-status-error"
                  : mic.mic_muted
                  ? "text-status-idle"
                  : "text-status-speaking"
              }`}
            >
              {!connected ? "offline" : mic.mic_muted ? "muted" : "live"}
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}
