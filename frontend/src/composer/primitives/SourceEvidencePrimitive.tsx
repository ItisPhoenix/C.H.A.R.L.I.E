import { useState, type ReactElement } from "react";
import type { PrimitiveSpec } from "../surfaceSchema";

export interface SourceCardItem {
  id?: string;
  title: string;
  domain?: string;
  publisher?: string;
  timestamp?: string;
  time?: string;
  url?: string;
  snippet?: string;
  thumbnail?: string; // image or waveform indicator
  type?: "satellite" | "ais" | "sigint" | "report" | "news" | "article" | string;
  confidence?: number;
}

export interface SourceEvidenceData {
  title?: string;
  items?: SourceCardItem[];
  // single item fallback properties:
  domain?: string;
  url?: string;
  snippet?: string;
  confidence?: number;
}

export function SourceEvidencePrimitive({
  primitive,
  data,
}: {
  primitive?: PrimitiveSpec;
  data?: SourceEvidenceData;
}): ReactElement {
  const pData: SourceEvidenceData = data || primitive?.data || {};
  const title = pData.title || "SOURCE FEED";
  const [activeModalItem, setActiveModalItem] = useState<SourceCardItem | null>(null);

  const items: SourceCardItem[] = Array.isArray(pData.items)
    ? pData.items
    : pData.url || pData.domain || pData.snippet
      ? [
          {
            id: "s1",
            title: pData.domain || "SOURCE EVIDENCE",
            domain: pData.domain,
            url: pData.url,
            snippet: pData.snippet,
            confidence: pData.confidence,
          },
        ]
      : [];

  const renderSourceGlyph = (type?: string) => {
    switch (type) {
      case "satellite":
        return "🛰";
      case "ais":
        return "⚓";
      case "sigint":
        return "📡";
      case "news":
        return "📰";
      default:
        return "☷";
    }
  };

  return (
    <div className="w-full font-mono select-none flex flex-col gap-2 text-left">
      {/* Title Header */}
      {title && (
        <div className="text-[11px] font-bold text-cyan-200 tracking-wider uppercase flex items-center justify-between">
          <span>{title}</span>
          <span className="text-[10px] text-cyan-400/60 font-medium">[{items.length} VERIFIED]</span>
        </div>
      )}

      {/* Card Deck Grid (Pure clean typography and telemetry, NO fake skeleton lines) */}
      {items.length === 0 ? (
        <div className="text-xs text-slate-500 italic py-2">No evidence sources available.</div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-4 gap-3">
          {items.map((item, idx) => {
            const pub = item.publisher || item.domain || "INTELLIGENCE_SOURCE";
            const timeTag = item.time || item.timestamp || "RECENT";

            return (
              <div
                key={item.id || idx}
                onClick={() => setActiveModalItem(item)}
                className="p-3.5 rounded-xl border border-cyan-500/20 bg-slate-950/60 backdrop-blur-md hover:border-cyan-400/50 hover:bg-slate-950/85 transition cursor-pointer flex flex-col justify-between group shadow-sm"
              >
                {/* Top Badge & Glyphs */}
                <div className="flex items-center justify-between text-[10px] text-cyan-400/80 mb-2">
                  <div className="flex items-center gap-1.5 font-bold uppercase truncate max-w-[140px]">
                    <span className="text-xs text-cyan-300">{renderSourceGlyph(item.type)}</span>
                    <span className="truncate">{pub}</span>
                  </div>
                  <span className="text-slate-400 font-mono text-[9px]">{timeTag}</span>
                </div>

                {/* Main Headline / Title */}
                <div className="text-xs font-bold text-slate-100 group-hover:text-cyan-200 line-clamp-2 uppercase leading-snug font-sans my-1">
                  {item.title}
                </div>

                {/* Bottom Footer Metadata */}
                <div className="pt-2 border-t border-cyan-500/10 flex justify-between items-center text-[9px] text-slate-400 font-mono mt-1">
                  <span>{item.type ? item.type.toUpperCase() : "VERIFIED_DOC"}</span>
                  <span className="text-cyan-400 group-hover:translate-x-0.5 transition-transform">→</span>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Expanded Modal Viewer */}
      {activeModalItem && (
        <div
          className="fixed inset-0 z-50 bg-black/80 backdrop-blur-md flex items-center justify-center p-4"
          onClick={() => setActiveModalItem(null)}
        >
          <div
            className="p-6 rounded-2xl bg-slate-950 border border-cyan-400/60 shadow-2xl max-w-lg w-full text-left font-mono"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between border-b border-cyan-500/20 pb-3 mb-4">
              <div>
                <div className="text-xs text-cyan-400 font-bold uppercase">{activeModalItem.title}</div>
                <div className="text-[10px] text-slate-400 mt-0.5">{activeModalItem.publisher || activeModalItem.domain}</div>
              </div>
              <button
                type="button"
                onClick={() => setActiveModalItem(null)}
                className="text-slate-400 hover:text-cyan-200 text-sm cursor-pointer"
              >
                ✕
              </button>
            </div>

            <div className="text-xs text-slate-300 leading-relaxed font-sans">
              {activeModalItem.snippet || "Evidence telemetry snapshot verified during task execution."}
            </div>

            {activeModalItem.url && (
              <div className="mt-4 pt-3 border-t border-cyan-500/15 flex justify-end">
                <a
                  href={activeModalItem.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-xs text-cyan-400 hover:underline"
                >
                  Open External Source ↗
                </a>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
