"use client";

import { type ReactElement } from "react";
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
}

/** Sidebar nav item -- one definition instead of the same block copy-pasted
 * per page, and wired to the live --accent token so the active-state color
 * actually follows the user's chosen accent instead of a hardcoded teal. */
function NavButton({ icon: Icon, label, active, onClick }: NavButtonProps): ReactElement {
  return (
    <button
      onClick={onClick}
      aria-current={active ? "page" : undefined}
      className={`w-full text-left rounded-lg px-2.5 py-2 text-xs flex items-center gap-2.5 font-medium cursor-pointer transition ${
        active
          ? "bg-white/5 text-accent font-semibold border-l-2 border-accent"
          : "text-slate-400 hover:text-slate-200 hover:bg-white/5 active:scale-[0.98]"
      }`}
    >
      <Icon className="w-4 h-4 shrink-0" />
      {label}
    </button>
  );
}

interface SidebarProps {
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

/** Left navigation sidebar: Chats accordion, Tools/System nav, accent picker.
 * Extracted out of page.tsx (was ~150 lines inline) so page.tsx only owns layout/routing. */
export function Sidebar(props: SidebarProps): ReactElement {
  const {
    mobileMenuOpen, onToggleMobileMenu, activePage, onSelectPage, searchedSessions,
    currentSessionId, onSelectSession, onCreateSession, onRenameSession, onDeleteSession,
    onExportHistory, accentColor, onSetAccentColor,
  } = props;

  return (
    <nav
      className={`shrink-0 border-r border-[var(--color-glass-border)] bg-zinc-950/20 p-4 flex flex-col justify-between select-none overflow-y-auto scrollbar transition-[width] duration-200 ${
        mobileMenuOpen ? "w-72" : "w-52"
      }`}
    >
      <div className="space-y-6">
        {/* Category: MAIN */}
        <div className="space-y-1.5">
          <h3 className="px-2 text-[10px] font-bold text-slate-500 uppercase tracking-widest">
            Main
          </h3>
          <div className="space-y-0.5">
            <NavButton
              icon={LayoutDashboard}
              label="Control Center"
              active={activePage === "controlCenter"}
              onClick={() => onSelectPage("controlCenter")}
            />
            <button
              onClick={() => {
                onSelectPage("chats");
                onToggleMobileMenu();
              }}
              aria-expanded={mobileMenuOpen}
              className={`w-full text-left rounded-lg px-2.5 py-2 text-xs flex items-center justify-between gap-2.5 font-medium cursor-pointer transition ${
                activePage === "chats" ? "bg-white/5 text-slate-100" : "text-slate-400 hover:text-slate-200 hover:bg-white/5"
              }`}
            >
              <span className="flex items-center gap-2.5">
                <MessageSquare className="w-4 h-4 shrink-0" />
                Chats
              </span>
              <ChevronDown
                className={`w-3.5 h-3.5 shrink-0 transition-transform ${mobileMenuOpen ? "rotate-180" : ""}`}
              />
            </button>
            {mobileMenuOpen && (
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
            />
          </div>
        </div>

        {/* Category: TOOLS */}
        <div className="space-y-1.5">
          <h3 className="px-2 text-[10px] font-bold text-slate-500 uppercase tracking-widest">
            Tools
          </h3>
          <div className="space-y-0.5">
            <NavButton icon={Monitor} label="Desktop" active={activePage === "desktop"} onClick={() => onSelectPage("desktop")} />
            <NavButton icon={FolderGit} label="Files" active={activePage === "files"} onClick={() => onSelectPage("files")} />
            <NavButton icon={Server} label="Services" active={activePage === "docker"} onClick={() => onSelectPage("docker")} />
            <NavButton icon={Network} label="Local Models" active={activePage === "ollama"} onClick={() => onSelectPage("ollama")} />
            <NavButton icon={Puzzle} label="Extensions" active={activePage === "extensions"} onClick={() => onSelectPage("extensions")} />
            <NavButton icon={Sparkles} label="Skills" active={activePage === "skills"} onClick={() => onSelectPage("skills")} />
            <NavButton icon={GitBranch} label="Agents" active={activePage === "agents"} onClick={() => onSelectPage("agents")} />
            <NavButton icon={Cable} label="MCP Servers" active={activePage === "mcp"} onClick={() => onSelectPage("mcp")} />
          </div>
        </div>

        {/* Category: SYSTEM */}
        <div className="space-y-1.5">
          <h3 className="px-2 text-[10px] font-bold text-slate-500 uppercase tracking-widest">
            System
          </h3>
          <div className="space-y-0.5">
            <NavButton icon={Cpu} label="Hardware" active={activePage === "hardware"} onClick={() => onSelectPage("hardware")} />
            <Link
              href="/settings"
              className="w-full text-left rounded-lg px-2.5 py-2 text-xs flex items-center gap-2.5 font-medium cursor-pointer text-slate-400 hover:text-slate-200 hover:bg-white/5 active:scale-[0.98] transition"
            >
              <Settings className="w-4 h-4 shrink-0" />
              Settings
            </Link>
          </div>
        </div>
      </div>

      {/* Sidebar Footer Accent Dot Pickers */}
      <div className="border-t border-white/5 pt-4 flex flex-col gap-2">
        <span className="px-2 text-[10px] font-mono font-bold tracking-widest text-slate-500 uppercase">
          ACCENT THEME
        </span>
        <div className="flex gap-2 px-2">
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
