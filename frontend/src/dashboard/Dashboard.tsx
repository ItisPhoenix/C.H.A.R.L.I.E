import { useEffect, useState, type CSSProperties, type ReactElement } from "react";
import { Background } from "./Background";
import { Chat } from "./Chat";
import { McpConnections } from "./McpConnections";
import { Ring } from "./Ring";
import { StatStrip } from "./StatStrip";
import { SystemMonitor } from "./SystemMonitor";
import { Tasks } from "./Tasks";
import { Topbar } from "./Topbar";
import { ToolsGrid } from "./ToolsGrid";
import { Terminal } from "./Terminal";
import { VoiceBar } from "./VoiceBar";
import { Notification } from "./Notification";
import { Calendar } from "./Calendar";
import { MediaPlayer } from "./MediaPlayer";
import "./dashboard.css";

const STAGE_WIDTH = 1536;
const STAGE_HEIGHT = 1024;

function useStageScale(): number {
  const [scale, setScale] = useState(1);

  useEffect(() => {
    const update = () => setScale(Math.min(window.innerWidth / STAGE_WIDTH, window.innerHeight / STAGE_HEIGHT));
    update();
    window.addEventListener("resize", update);
    return () => window.removeEventListener("resize", update);
  }, []);

  return scale;
}

export function Dashboard(): ReactElement {
  const scale = useStageScale();
  const stageStyle = { "--hud-scale": scale } as CSSProperties;

  return (
    <main className="hud-viewport">
      <div className="hud-stage" style={stageStyle}>
        <Background />
        <Topbar />
        <div className="hud-ring-wrap" aria-hidden="true"><Ring /></div>
        <div className="hud-chat"><Chat /></div>
        <ToolsGrid />
        <Terminal />
        <VoiceBar />
        <Notification />
        <Calendar />
        <MediaPlayer />
        <Tasks />
        <SystemMonitor />
        <McpConnections />
        <StatStrip />
      </div>
    </main>
  );
}
