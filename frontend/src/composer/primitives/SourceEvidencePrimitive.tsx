import { useState, type ReactElement } from "react";
import type { PrimitiveSpec } from "../surfaceSchema";

export function SourceEvidencePrimitive({ primitive }: { primitive: PrimitiveSpec }): ReactElement {
  const data = primitive.data || {};
  const title = String(data.title ?? "Source Citation");
  const domain = data.domain ? String(data.domain) : null;
  const url = data.url ? String(data.url) : null;
  const snippet = data.snippet ? String(data.snippet) : null;
  const confidence = typeof data.confidence === "number" ? Math.round(data.confidence * 100) : null;

  const [expanded, setExpanded] = useState(false);

  // Validate URL scheme for security
  const safeUrl = url && (url.startsWith("https://") || url.startsWith("http://")) ? url : null;

  return (
    <div className="w-full my-1.5 p-3 rounded-xl bg-slate-950/70 border border-cyan-500/20 text-left transition-all">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 overflow-hidden">
          <span className="text-cyan-400 font-mono text-[10px] uppercase">
            {domain || "SOURCE"}
          </span>
          <span className="text-xs font-semibold text-slate-200 truncate">{title}</span>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {confidence !== null && (
            <span className="text-[10px] font-mono text-cyan-300 bg-cyan-950/80 px-1.5 py-0.5 rounded border border-cyan-500/30">
              {confidence}% match
            </span>
          )}
          {snippet && (
            <button
              type="button"
              onClick={() => setExpanded((prev) => !prev)}
              className="text-[10px] text-slate-400 hover:text-cyan-200 cursor-pointer font-mono"
            >
              {expanded ? "Less ▲" : "More ▼"}
            </button>
          )}
        </div>
      </div>

      {expanded && snippet && (
        <p className="mt-2 text-xs text-slate-300 leading-relaxed border-t border-cyan-500/10 pt-2 italic">
          "{snippet}"
        </p>
      )}

      {safeUrl && (
        <div className="mt-2 pt-1 border-t border-cyan-500/10 flex justify-end">
          <a
            href={safeUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="text-[10px] font-mono text-cyan-400 hover:underline inline-flex items-center gap-1"
          >
            Visit Source ↗
          </a>
        </div>
      )}
    </div>
  );
}
