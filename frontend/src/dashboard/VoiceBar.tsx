import type { ReactElement } from "react";
import { MicrophoneIcon, MicrophoneSlashIcon, SpeakerHighIcon, SpeakerSlashIcon, WaveformIcon } from "@phosphor-icons/react";
import { sendCommand } from "../runtime/bridge";
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
  const speakerMuted = audioState?.muted ?? true;

  return (
    <section className="voice-shell" aria-label={label}>
      <button type="button" className={`voice-mic${micMuted ? " is-muted" : ""}`} aria-label={micMuted ? "Unmute microphone" : "Mute microphone"} title={micMuted ? "Unmute microphone" : "Mute microphone"} onClick={() => sendCommand("mic_control", { mic_muted: !micMuted })}>
        {micMuted ? <MicrophoneSlashIcon aria-hidden="true" weight="duotone" /> : <MicrophoneIcon aria-hidden="true" weight="duotone" />}
      </button>
      <div className="voice-content"><div className="voice-title-row"><span className="voice-label">{label}</span><WaveformIcon aria-hidden="true" weight="duotone" /></div><div className="voice-wave" aria-hidden="true">{Array.from({ length: 24 }, (_, index) => <i key={index} />)}</div></div>
      <button type="button" className="voice-speaker" aria-label={speakerMuted ? "Unmute speaker" : "Mute speaker"} title={speakerMuted ? "Unmute speaker" : "Mute speaker"} onClick={() => sendCommand("audio_control", { muted: !speakerMuted })}>
        {speakerMuted ? <SpeakerSlashIcon aria-hidden="true" weight="duotone" /> : <SpeakerHighIcon aria-hidden="true" weight="duotone" />}
      </button>
    </section>
  );
}
