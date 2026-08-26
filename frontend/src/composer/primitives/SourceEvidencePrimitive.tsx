import { useState, type ReactElement } from "react";
import type { PrimitiveSpec } from "../surfaceSchema";
import {
  ContextCard,
  ContextCardHeader,
  ContextCardBody,
  ContextCardMetadata,
  ContextCardActions,
} from "../../ui/context";

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

      {/* Card Deck Grid using canonical ContextCard */}
      {items.length === 0 ? (
        <div className="text-xs text-slate-500 italic py-2">No evidence sources available.</div>
      ) : (
        <div className="charlie-evidence-deck grid grid-cols-1 sm:grid-cols-2 md:grid-cols-4 gap-3">
          {items.slice(0, 4).map((item, idx) => {
            const pub = item.publisher || item.domain || "INTELLIGENCE_SOURCE";
            const timeTag = item.time || item.timestamp || "RECENT";

            return (
              <ContextCard
                key={item.id || idx}
                variant="source"
                interactive
                onClick={() => setActiveModalItem(item)}
                className="charlie-evidence-card flex flex-col justify-between group"
              >
                <div>
                  <ContextCardHeader
                    title={item.title}
                    category={pub}
                    icon={renderSourceGlyph(item.type)}
                    timestamp={timeTag}
                    className="charlie-card-header-compact"
                  />
                  {item.snippet && (
                    <ContextCardBody>
                      <p className="text-[11.5px] text-slate-300 line-clamp-2 mt-1">
                        {item.snippet}
                      </p>
                    </ContextCardBody>
                  )}
                </div>

                <ContextCardMetadata
                  confidence={item.confidence}
                  items={[
                    {
                      label: "TYPE",
                      value: item.type ? item.type.toUpperCase() : "VERIFIED_DOC",
                      highlight: true,
                    },
                  ]}
                />
              </ContextCard>
            );
          })}
        </div>
      )}

      {/* Expanded Modal Viewer using canonical ContextCard floating */}
      {activeModalItem && (
        <div
          className="fixed inset-0 z-50 bg-black/80 backdrop-blur-md flex items-center justify-center p-4"
          onClick={() => setActiveModalItem(null)}
        >
          <div className="max-w-lg w-full" onClick={(e) => e.stopPropagation()}>
            <ContextCard variant="standard" elevation="floating">
              <ContextCardHeader
                title={activeModalItem.title}
                category={activeModalItem.publisher || activeModalItem.domain || "INTELLIGENCE_SOURCE"}
                icon={renderSourceGlyph(activeModalItem.type)}
                onClose={() => setActiveModalItem(null)}
              />
              <ContextCardBody>
                <p className="text-xs text-slate-300 leading-relaxed font-sans mt-2">
                  {activeModalItem.snippet || "Evidence telemetry snapshot verified during task execution."}
                </p>
              </ContextCardBody>

              <ContextCardMetadata
                confidence={activeModalItem.confidence}
                items={[
                  {
                    label: "CATEGORY",
                    value: activeModalItem.type?.toUpperCase() || "DOCUMENT",
                  },
                ]}
              />

              {activeModalItem.url && (
                <ContextCardActions
                  actions={[
                    {
                      id: "open-source",
                      label: "Open External Source ↗",
                      variant: "primary",
                      onClick: () => {
                        window.open(activeModalItem.url, "_blank", "noopener,noreferrer");
                      },
                    },
                  ]}
                />
              )}
            </ContextCard>
          </div>
        </div>
      )}
    </div>
  );
}
