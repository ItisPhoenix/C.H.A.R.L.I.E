import { useState, type ReactElement } from "react";
import type { WorkspaceInstance } from "../../layout/workspaceStore";

export function DocumentWorkspace({ workspace }: { workspace: WorkspaceInstance }): ReactElement {
  const content = workspace.contentState || {};
  const title = String(content.title || workspace.title || "DOCUMENT VIEWER").replace(/^WORKSPACE\s*\/\/\s*/i, "");
  const textContent = [content.text, content.markdown]
    .find((value): value is string => typeof value === "string" && value.trim().length > 0) || "";
  const summary = workspace.summary.trim();
  const status = String(content.status || content.state || "").toLowerCase();
  const message = content.loading === true || ["loading", "pending", "processing"].includes(status)
    ? "LOADING AUTHORITATIVE DOCUMENT..."
    : content.available === false || ["unavailable", "offline", "error", "failed"].includes(status)
      ? "DOCUMENT DATA UNAVAILABLE."
      : summary
        ? "NO DOCUMENT BODY AVAILABLE."
        : "NO DOCUMENT SELECTED.";

  const [searchQuery, setSearchQuery] = useState("");

  return (
    <div className="w-full h-full flex flex-col justify-between font-mono select-none text-left p-2 overflow-y-auto space-y-4">
      {/* Header & Controls */}
      <div className="flex items-start justify-between border-b border-cyan-500/20 pb-3">
        <div>
          <div className="text-[10px] text-cyan-400 font-bold tracking-widest uppercase mb-0.5 flex items-center gap-2">
            <span className="w-1.5 h-1.5 rounded-full bg-cyan-400 animate-pulse" />
            DOCUMENTATION & REPORT WORKSPACE
          </div>
          <h1 className="text-xl font-bold text-slate-100 uppercase tracking-tight font-sans">
            {title}
          </h1>
        </div>

        {/* Quick Search */}
        <div className="flex items-center gap-2">
          <input
            type="text"
            placeholder="Search document..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="px-3 py-1 text-xs rounded-lg bg-slate-900 border border-cyan-500/30 text-slate-200 placeholder-slate-500 focus:outline-none focus:border-cyan-400 font-mono"
          />
        </div>
      </div>

      {/* Main Document Body */}
      {!textContent ? (
        <div className="p-6 border border-cyan-500/20 bg-slate-950/80 text-slate-400 font-sans text-sm leading-relaxed" role="status">
          <div className="text-xs font-mono uppercase tracking-wider text-cyan-400/80">{message}</div>
          {summary && <p className="mt-3 text-slate-300">{summary}</p>}
        </div>
      ) : <div className="p-6 rounded-xl border border-cyan-500/20 bg-slate-950/80 backdrop-blur-md overflow-y-auto max-h-[600px] text-slate-200 font-sans text-sm leading-relaxed space-y-4">
        {textContent.split("\n\n").map((para, idx) => {
          if (para.startsWith("# ")) {
            return (
              <h2 key={idx} className="text-xl font-bold text-cyan-300 font-mono uppercase tracking-tight">
                {para.replace("# ", "")}
              </h2>
            );
          }
          if (para.startsWith("## ")) {
            return (
              <h3 key={idx} className="text-base font-bold text-cyan-200 font-mono uppercase tracking-tight pt-2">
                {para.replace("## ", "")}
              </h3>
            );
          }
          if (para.startsWith("- ")) {
            return (
              <ul key={idx} className="list-disc list-inside space-y-1 pl-2 text-slate-300">
                {para.split("\n").map((line, lIdx) => (
                  <li key={lIdx}>{line.replace(/^- /, "")}</li>
                ))}
              </ul>
            );
          }
          return <p key={idx} className="text-slate-300">{para}</p>;
        })}
      </div>}
    </div>
  );
}
