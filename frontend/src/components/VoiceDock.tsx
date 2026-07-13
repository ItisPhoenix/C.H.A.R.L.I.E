"use client";

import { useMemo, type ReactElement } from "react";
import { Mic, MicOff, Volume2, VolumeX } from "lucide-react";
import { useCharlieStore, type VoiceState, type AudioState, type MicState, rgba, lighten } from "../store/useCharlieStore";

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

const MIN_HEIGHT_PX = 3;
const MAX_HEIGHT_PX = 22;

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

  const bars = useMemo(
    () => Array.from({ length: BAR_COUNT }).map((_, i) => i),
    []
  );

  const stateColor = {
    idle: "#4b5563",
    listening: accentColor,
    thinking: lighten(accentColor, 0.25),
    speaking: lighten(accentColor, 0.5),
  }[state] || "#4b5563";

  const effectiveVolume = audio.muted ? 0 : audio.volume;

  const voiceDockBorder = !connected 
    ? "rgba(239, 68, 68, 0.4)" 
    : mic.mic_muted 
    ? "rgba(75, 85, 99, 0.4)" 
    : audio.muted 
    ? "rgba(239, 68, 68, 0.35)" 
    : "rgba(255, 255, 255, 0.07)";

  return (
    <div
      data-state={state}
      style={{
        border: `1px solid ${voiceDockBorder}`,
      }}
      className="mx-4 mb-4 p-3 rounded-2xl flex items-center justify-between gap-6 bg-black/60 backdrop-blur-[20px] z-20 select-none transition-all duration-200"
    >
      <div className="flex-1 flex items-center justify-center gap-[3px] h-[26px]">
        {!connected ? (
          <span className="text-[11px] font-bold uppercase tracking-[0.18em] text-status-error animate-pulse font-mono">
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
                className={`w-[3px] rounded-full ${animClass}`}
                style={{
                  backgroundColor: stateColor,
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
        style={{
          color: !connected ? "#ef4444" : stateColor,
        }}
        className={`text-[11px] font-bold uppercase tracking-[0.18em] min-w-[88px] text-center font-mono`}
        aria-live="polite"
      >
        {!connected ? "offline" : state}
      </span>

      <div className="flex items-center gap-3 border-l border-[var(--color-glass-border)] pl-6">
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
            aria-pressed={audio.muted}
            className={`rounded-xl w-[34px] h-[34px] grid place-items-center cursor-pointer transition ${
              audio.muted
                ? "bg-[#ef4444]/16 text-[#ef4444]"
                : "text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)]"
            }`}
          >
            {audio.muted ? <VolumeX className="w-[15px] h-[15px]" /> : <Volume2 className="w-[15px] h-[15px]" />}
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
            className="w-22 cursor-pointer"
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
            className={`rounded-xl w-[34px] h-[34px] grid place-items-center cursor-pointer transition ${
              !connected
                ? "bg-[#ef4444]/16 text-[#ef4444] animate-pulse"
                : mic.mic_muted
                ? "bg-[rgba(75,85,99,0.2)] text-[#6b7280]"
                : "text-[var(--color-accent-teal)] hover:text-[var(--color-text-primary)]"
            }`}
          >
            {mic.mic_muted || !connected ? <MicOff className="w-[15px] h-[15px]" /> : <Mic className="w-[15px] h-[15px]" />}
          </button>
          <div className="flex flex-col text-right">
            <span className="text-[9px] font-mono font-bold tracking-widest uppercase text-[var(--color-text-muted)]">
              Voice Link
            </span>
            <span
              style={{
                color: !connected ? "#ef4444" : mic.mic_muted ? "#6b7280" : "var(--color-accent-teal)",
              }}
              className={`text-xs font-bold uppercase`}
            >
              {!connected ? "offline" : mic.mic_muted ? "muted" : "live"}
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}
