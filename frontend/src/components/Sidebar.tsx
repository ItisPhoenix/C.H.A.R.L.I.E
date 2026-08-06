"use client";

import { useState, type ReactElement } from "react";
import Link from "next/link";
import {
  MessageSquare, Monitor, Database, Cpu, Settings, FolderGit, Network,
  Server, Puzzle, GitBranch, ChevronDown, Cable, Sparkles, LayoutDashboard, type LucideIcon,
} from "lucide-react";
import { SessionRail } from "./SessionRail";
import { lighten, type Session } from "../store/useCharlieStore";

interface NavButtonProps {
  icon: LucideIcon;
  label: string;
  active: boolean;
  onClick: () => void;
  collapsed?: boolean;
}

// Applied to headers/text blocks (not nav labels -- see HIDDEN_WHEN_COLLAPSED
// below) when the sidebar is in auto-collapse mode: invisible at icon-only
// width, fades in with the hover-expand instead of clipping mid-word.
const HIDE_WHEN_COLLAPSED = "opacity-0 group-hover:opacity-100 transition-opacity duration-150";

// Nav-item labels use display:none rather than opacity so they take zero
// layout space while collapsed -- opacity alone still reserves the label's
// full text width in the flex row, which pushes the icon off-center against
// the left edge instead of centering it in the icon-only rail.
const HIDDEN_WHEN_COLLAPSED = "hidden group-hover:inline";

/** Sidebar nav item -- one definition, wired to the live --accent token so the active color follows the user's chosen accent. */
function NavButton({ icon: Icon, label, active, onClick, collapsed }: NavButtonProps): ReactElement {
  return (
    <button
      onClick={onClick}
      aria-current={active ? "page" : undefined}
      className={`w-full text-left rounded-lg px-2.5 py-2 text-xs flex items-center gap-2.5 font-medium cursor-pointer transition ${
        collapsed ? "justify-center group-hover:justify-start" : ""
      } ${
        active
          ? `bg-white/5 text-accent font-semibold ${collapsed ? "" : "border-l-2 border-accent"}`
          : "text-slate-400 hover:text-slate-200 hover:bg-white/5 active:scale-[0.98]"
      }`}
    >
      <Icon className="w-4 h-4 shrink-0" />
      <span className={`truncate whitespace-nowrap ${collapsed ? HIDDEN_WHEN_COLLAPSED : ""}`}>{label}</span>
    </button>
  );
}

interface SidebarProps {
  autoCollapse?: boolean;
  mobileMenuOpen: boolean;
  onToggleMobileMenu: () => void;
  activePage: string;
  onSelectPage: (page: string) => void;
  searchedSessions: Session[];
  currentSessionId: string;
  onSelectSession: (id: string) => void;
  onCreateSession: () => void;
  onRenameSession: (id: string, title: string) => void;
  onDeleteSession: (id: string) => void;
  onExportHistory: () => void;
  accentColor: string;
  onSetAccentColor: (color: string) => void;
}

const ACCENT_SWATCHES = ["#a855f7", "#3b82f6", "#ef4444", "#f59e0b", "#06b6d4"];

/** Left navigation sidebar: Chats accordion, Tools/System nav, accent picker. Extracted out of page.tsx. */
export function Sidebar(props: SidebarProps): ReactElement {
  const {
    autoCollapse, mobileMenuOpen, onToggleMobileMenu, activePage, onSelectPage, searchedSessions,
    currentSessionId, onSelectSession, onCreateSession, onRenameSession, onDeleteSession,
    onExportHistory, accentColor, onSetAccentColor,
  } = props;

  // The Chats accordion's content assumes a full-width sidebar. autoCollapse's
  // hover-to-expand is pure CSS, so if the mouse leaves before React knows,
  // the accordion can stay "open" (mobileMenuOpen=true) while the nav is
  // back at icon-only width -- rendering the session list garbled into 56px.
  // Track real hover in JS so the accordion only ever renders when there's
  // room, and auto-close it when the mouse leaves a collapsed sidebar.
  const [isHovering, setIsHovering] = useState(false);
  const accordionHasRoom = !autoCollapse || isHovering;

  return (
    <nav
      onMouseEnter={() => setIsHovering(true)}
      onMouseLeave={() => {
        setIsHovering(false);
        if (autoCollapse && mobileMenuOpen) onToggleMobileMenu();
      }}
      className={`group shrink-0 border-r border-[var(--color-glass-border)] bg-zinc-950/20 p-4 flex flex-col justify-between select-none overflow-y-auto overflow-x-hidden scrollbar transition-[width] duration-200 ${
        autoCollapse ? "w-14 hover:w-56" : mobileMenuOpen ? "w-72" : "w-52"
      }`}
    >
      <div className="space-y-6">
        {/* Category: MAIN */}
        <div className="space-y-1.5">
          <h2 className={`px-2 text-xs font-bold text-slate-500 uppercase tracking-widest whitespace-nowrap ${autoCollapse ? HIDE_WHEN_COLLAPSED : ""}`}>
            Main
          </h2>
          <div className="space-y-0.5">
            <NavButton
              icon={LayoutDashboard}
              label="Control Center"
              active={activePage === "controlCenter"}
              onClick={() => onSelectPage("controlCenter")}
              collapsed={autoCollapse}
            />
            <button
              onClick={() => {
                onSelectPage("chats");
                onToggleMobileMenu();
              }}
              aria-expanded={mobileMenuOpen}
              className={`w-full text-left rounded-lg px-2.5 py-2 text-xs flex items-center gap-2.5 font-medium cursor-pointer transition ${
                autoCollapse ? "justify-center group-hover:justify-between" : "justify-between"
              } ${
                activePage === "chats" ? "bg-white/5 text-accent font-semibold" : "text-slate-400 hover:text-slate-200 hover:bg-white/5"
              }`}
            >
              {/* inline-flex, not flex -- a block-level flex span fills the button's
                  full width by default, which defeats the button's own justify-center
                  (there's nothing left to center once this span already spans 100%). */}
              <span className="inline-flex items-center gap-2.5 whitespace-nowrap">
                <MessageSquare className="w-4 h-4 shrink-0" />
                <span className={autoCollapse ? HIDDEN_WHEN_COLLAPSED : ""}>Chats</span>
              </span>
              <ChevronDown
                className={`w-3.5 h-3.5 shrink-0 transition-transform ${mobileMenuOpen ? "rotate-180" : ""} ${autoCollapse ? HIDDEN_WHEN_COLLAPSED : ""}`}
              />
            </button>
            {mobileMenuOpen && accordionHasRoom && (
              <div className="rounded-lg border border-white/5 bg-zinc-900/40 overflow-hidden">
                <SessionRail
                  variant="accordion"
                  sessions={searchedSessions}
                  currentId={currentSessionId}
                  onSelect={onSelectSession}
                  onCreate={onCreateSession}
                  onRename={onRenameSession}
                  onDelete={onDeleteSession}
                  onExport={onExportHistory}
                />
              </div>
            )}
            <NavButton
              icon={Database}
              label="Memories"
              active={activePage === "memories"}
              onClick={() => onSelectPage("memories")}
              collapsed={autoCollapse}
            />
          </div>
        </div>

        {/* Category: TOOLS */}
        <div className="space-y-1.5">
          <h2 className={`px-2 text-xs font-bold text-slate-500 uppercase tracking-widest whitespace-nowrap ${autoCollapse ? HIDE_WHEN_COLLAPSED : ""}`}>
            Tools
          </h2>
          <div className="space-y-0.5">
            <NavButton icon={Monitor} label="Desktop" active={activePage === "desktop"} onClick={() => onSelectPage("desktop")} collapsed={autoCollapse} />
            <NavButton icon={FolderGit} label="Files" active={activePage === "files"} onClick={() => onSelectPage("files")} collapsed={autoCollapse} />
            <NavButton icon={Server} label="Services" active={activePage === "docker"} onClick={() => onSelectPage("docker")} collapsed={autoCollapse} />
            <NavButton icon={Network} label="Local Models" active={activePage === "ollama"} onClick={() => onSelectPage("ollama")} collapsed={autoCollapse} />
            <NavButton icon={Puzzle} label="Extensions" active={activePage === "extensions"} onClick={() => onSelectPage("extensions")} collapsed={autoCollapse} />
            <NavButton icon={Sparkles} label="Skills" active={activePage === "skills"} onClick={() => onSelectPage("skills")} collapsed={autoCollapse} />
            <NavButton icon={GitBranch} label="Agents" active={activePage === "agents"} onClick={() => onSelectPage("agents")} collapsed={autoCollapse} />
            <NavButton icon={Cable} label="MCP Servers" active={activePage === "mcp"} onClick={() => onSelectPage("mcp")} collapsed={autoCollapse} />
          </div>
        </div>

        {/* Category: SYSTEM */}
        <div className="space-y-1.5">
          <h2 className={`px-2 text-xs font-bold text-slate-500 uppercase tracking-widest whitespace-nowrap ${autoCollapse ? HIDE_WHEN_COLLAPSED : ""}`}>
            System
          </h2>
          <div className="space-y-0.5">
            <NavButton icon={Cpu} label="Hardware" active={activePage === "hardware"} onClick={() => onSelectPage("hardware")} collapsed={autoCollapse} />
            <Link
              href="/settings"
              className={`w-full text-left rounded-lg px-2.5 py-2 text-xs flex items-center gap-2.5 font-medium cursor-pointer text-slate-400 hover:text-slate-200 hover:bg-white/5 active:scale-[0.98] transition whitespace-nowrap overflow-hidden ${
                autoCollapse ? "justify-center group-hover:justify-start" : ""
              }`}
            >
              <Settings className="w-4 h-4 shrink-0" />
              <span className={autoCollapse ? HIDDEN_WHEN_COLLAPSED : ""}>Settings</span>
            </Link>
          </div>
        </div>
      </div>

      {/* Sidebar Footer Accent Dot Pickers */}
      <div className="border-t border-white/5 pt-4 flex flex-col gap-2">
        <span className={`px-2 text-xs font-mono font-bold tracking-widest text-slate-500 uppercase whitespace-nowrap overflow-hidden block ${autoCollapse ? HIDE_WHEN_COLLAPSED : ""}`}>
          ACCENT THEME
        </span>
        <div className={`flex gap-2 px-2 ${autoCollapse ? HIDE_WHEN_COLLAPSED : ""}`}>
          {ACCENT_SWATCHES.map((color) => (
            <button
              key={color}
              onClick={() => onSetAccentColor(color)}
              className="w-3.5 h-3.5 rounded-full border border-white/20 transition hover:scale-110 cursor-pointer active:scale-90"
              style={{
                background: color,
                outline: accentColor === color ? `1.5px solid ${lighten(color, 0.35)}` : "none",
                outlineOffset: "1px",
              }}
              aria-label={`Set accent to ${color}`}
            />
          ))}
        </div>
      </div>
    </nav>
  );
}
