import { useEffect, useState, type ReactElement } from "react";
import { Panel } from "./Panel";

interface MediaSnapshot {
  available: boolean;
  title: string;
  artist: string;
  album: string;
  status: string;
  position_seconds: number;
  duration_seconds: number;
  art_uri: string | null;
  volume_percent: number | null;
  muted: boolean | null;
}

export function MediaPlayer(): ReactElement {
  const [media, setMedia] = useState<MediaSnapshot | null>(null);

  useEffect(() => {
    let active = true;
    const load = async (): Promise<void> => {
      try {
        const response = await fetch("/api/media");
        if (response.ok && active) setMedia(await response.json() as MediaSnapshot);
      } catch {
        if (active) setMedia({ available: false, title: "", artist: "", album: "", status: "unavailable", position_seconds: 0, duration_seconds: 0, art_uri: null, volume_percent: null, muted: null });
      }
    };
    void load();
    const timer = window.setInterval(() => void load(), 2000);
    return () => { active = false; window.clearInterval(timer); };
  }, []);

  async function control(action: string): Promise<void> {
    await fetch("/api/media/control", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action }) });
  }

  return (
    <Panel id="media" title="Media Player">
      {!media?.available ? <p className="media-unavailable">No controllable Windows media session.</p> : <div className="media-workspace"><div className="media-main"><div className="media-art">{media.art_uri ? <img src={media.art_uri} alt="" /> : <span>{media.title.slice(0, 1) || "♪"}</span>}</div><div className="media-info"><strong>{media.title}</strong><span>{media.artist || "Unknown artist"}</span><small>{media.album || "Unknown album"}</small></div></div><div className="media-track"><i style={{ width: String(media.duration_seconds > 0 ? Math.min(100, media.position_seconds / media.duration_seconds * 100) : 0) + "%" }} /></div><div className="media-times"><span>{formatSeconds(media.position_seconds)}</span><span>{formatSeconds(media.duration_seconds)}</span></div><div className="media-controls"><button type="button" aria-label="Previous track" onClick={() => void control("prev_track")}>◀</button><button type="button" aria-label="Play or pause" className="is-playing" onClick={() => void control("play_pause")}>{media.status.includes("playing") ? "Ⅱ" : "▶"}</button><button type="button" aria-label="Next track" onClick={() => void control("next_track")}>▶</button><button type="button" aria-label="Volume down" onClick={() => void control("volume_down")}>−</button><span className="media-volume-label">{media.volume_percent === null ? "—" : media.volume_percent + "%"}</span><button type="button" aria-label="Volume up" onClick={() => void control("volume_up")}>+</button><button type="button" aria-label="Mute" onClick={() => void control("mute")}>{media.muted ? "Unmute" : "Mute"}</button></div></div>}
    </Panel>
  );
}

function formatSeconds(value: number): string {
  const seconds = Math.max(0, Math.floor(value));
  return String(Math.floor(seconds / 60)).padStart(2, "0") + ":" + String(seconds % 60).padStart(2, "0");
}
