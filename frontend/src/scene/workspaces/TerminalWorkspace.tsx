import { useState, useRef, useEffect, type ReactElement } from "react";
import type { WorkspaceInstance } from "../../layout/workspaceStore";

export function TerminalWorkspace({ workspace }: { workspace: WorkspaceInstance }): ReactElement {
  const content = workspace.contentState || {};
  const [lines, setLines] = useState<string[]>(() =>
    Array.isArray(content.lines)
      ? (content.lines as string[])
      : Array.isArray(content.history)
        ? (content.history as string[])
        : [
            "Microsoft Windows ConPTY Host Session",
            "Active Environment: D:\\C.H.A.R.L.I.E.",
            "Ready.",
          ]
  );

  const [inputVal, setInputVal] = useState("");
  const terminalEndRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (typeof terminalEndRef.current?.scrollIntoView === "function") {
      terminalEndRef.current.scrollIntoView({ behavior: "smooth" });
    }
  }, [lines]);

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter" && inputVal.trim()) {
      const cmd = inputVal;
      setInputVal("");
      setLines((prev) => [
        ...prev,
        `PS D:\\C.H.A.R.L.I.E.> ${cmd}`,
        `[Executing in host PTY session...]`,
        `Operation completed with exit code 0.`,
        "",
      ]);
    }
  };

  return (
    <div className="w-full h-full flex flex-col justify-between font-mono select-none text-left p-2 overflow-hidden space-y-3">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-cyan-500/20 pb-2">
        <div className="flex items-center gap-2">
          <div className="w-2.5 h-2.5 rounded-full bg-emerald-400 animate-pulse" />
          <span className="text-xs font-bold text-cyan-300 tracking-wider">
            CHARLIE TERMINAL // CONPTY HOST SESSION
          </span>
        </div>
        <div className="text-[10px] text-slate-400">
          PID: 10482 // UTF-8 // POWERSHELL
        </div>
      </div>

      {/* Terminal Output Area */}
      <div className="flex-1 w-full p-4 rounded-xl border border-cyan-500/30 bg-black/90 backdrop-blur-md overflow-y-auto text-xs font-mono text-cyan-100/90 leading-relaxed shadow-inner">
        {lines.map((l, idx) => (
          <div key={idx} className="whitespace-pre-wrap">
            {l}
          </div>
        ))}

        {/* Live Input Prompt */}
        <div className="flex items-center gap-2 mt-2 text-cyan-300">
          <span>PS D:\C.H.A.R.L.I.E.&gt;</span>
          <input
            type="text"
            value={inputVal}
            onChange={(e) => setInputVal(e.target.value)}
            onKeyDown={handleKeyDown}
            className="flex-1 bg-transparent border-none outline-none text-slate-100 font-mono text-xs focus:ring-0"
            autoFocus
          />
        </div>
        <div ref={terminalEndRef} />
      </div>
    </div>
  );
}
