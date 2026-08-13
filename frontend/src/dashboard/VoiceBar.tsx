import type { ReactElement } from "react";
import { useCharlieStore } from "../store/charlie";

export function VoiceBar(): ReactElement {
  const connected = useCharlieStore((state) => state.connected);
  const coreState = useCharlieStore((state) => state.coreState);
  const audioState = useCharlieStore((state) => state.audioState);
  const micMuted = useCharlieStore((state) => state.micMuted);
  const voiceRunning = useCharlieStore((state) => state.subsystemHealth.voice?.status === "running");
  const label = !connected || !voiceRunning || !audioState || micMuted === null
    ? "Voice unavailable."
    : micMuted ? "Microphone muted"
    : audioState.muted ? "Speaker muted"
    : coreState === "speaking" ? "Speaking..."
    : coreState === "listening" ? "Listening..."
    : "Voice online";

  return (
    <section className="voice-shell" aria-label={label}>
      <span className="voice-mic" aria-hidden="true">mic</span>
      <div className="voice-content"><span className="voice-label">{label}</span></div>
    </section>
  );
}
