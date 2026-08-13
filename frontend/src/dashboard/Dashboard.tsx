import { useEffect, useRef, useState, type ReactElement } from "react";
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
import { Settings } from "./Settings";
import { layoutProfileForWidth, type LayoutProfile } from "./layoutProfile";
import { useLayoutStore } from "./layoutStore";
import { useCharlieStore } from "../store/charlie";
import "./dashboard.css";

function useLayoutProfile(): LayoutProfile {
  const [profile, setProfile] = useState<LayoutProfile>(() => layoutProfileForWidth(window.innerWidth));
  useEffect(() => {
    const update = () => setProfile(layoutProfileForWidth(window.innerWidth));
    window.addEventListener("resize", update);
    return () => window.removeEventListener("resize", update);
  }, []);
  return profile;
}

export function Dashboard(): ReactElement {
  const profile = useLayoutProfile();
  const stageRef = useRef<HTMLDivElement>(null);
  const panelIntent = useCharlieStore((state) => state.dashboardPanelIntent);
  const dashboardVisible = useCharlieStore((state) => state.dashboardVisible);
  const open = useLayoutStore((state) => state.open);
  const close = useLayoutStore((state) => state.close);
  const setProfile = useLayoutStore((state) => state.setProfile);

  useEffect(() => {
    setProfile(profile);
  }, [profile, setProfile]);

  useEffect(() => {
    const stage = stageRef.current;
    if (!stage) return;
    const updateScale = () => {
      const scale = Math.min(1, window.innerWidth / 1536, window.innerHeight / 1024);
      stage.style.setProperty("--hud-scale", String(Math.max(0.55, scale)));
    };
    updateScale();
    window.addEventListener("resize", updateScale);
    return () => window.removeEventListener("resize", updateScale);
  }, []);

  useEffect(() => {
    if (!panelIntent) return;
    if (panelIntent.action === "show") open(panelIntent.panelId);
    else close(panelIntent.panelId);
  }, [close, open, panelIntent]);

  return (
    <main className={dashboardVisible ? "hud-viewport" : "hud-viewport is-dashboard-hidden"}>
      <div ref={stageRef} className="hud-stage" data-layout-profile={profile}>
        <Background />
        <Topbar />
        <div className="hud-ring-wrap" aria-hidden="true"><Ring /></div>
        <Chat />
        <ToolsGrid />
        <Terminal />
        <div className="hud-voice"><VoiceBar /></div>
        <Notification />
        <Calendar />
        <MediaPlayer />
        <Settings />
        <Tasks />
        <SystemMonitor />
        <McpConnections />
        <StatStrip />
      </div>
    </main>
  );
}
