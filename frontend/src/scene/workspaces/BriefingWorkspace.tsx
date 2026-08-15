import { useState, type ReactElement } from "react";
import type { WorkspaceInstance } from "../../layout/workspaceStore";
import { SpatialMapPrimitive, type SpatialMapData } from "../../composer/primitives/SpatialMapPrimitive";
import { TimelinePrimitive, type TimelineItem } from "../../composer/primitives/TimelinePrimitive";
import { SourceEvidencePrimitive, type SourceCardItem } from "../../composer/primitives/SourceEvidencePrimitive";

export function BriefingWorkspace({ workspace }: { workspace: WorkspaceInstance }): ReactElement {
  const content = workspace.contentState || {};
  const [activeStoryPage, setActiveStoryPage] = useState(0);

  const headline = String(
    content.headline ||
    content.title ||
    workspace.title ||
    "GLOBAL INTELLIGENCE & SITUATION BRIEFING"
  ).replace(/^WORKSPACE\s*\/\/\s*/i, "");

  const summaries: string[] = Array.isArray(content.summaries) && content.summaries.length > 0
    ? (content.summaries as string[])
    : content.summary
      ? [String(content.summary)]
      : workspace.summary
        ? [workspace.summary]
        : [];

  const currentSummary = summaries[activeStoryPage] || summaries[0] || "No briefing text recorded.";

  const timelineItems: TimelineItem[] = Array.isArray(content.timeline_items)
    ? (content.timeline_items as TimelineItem[])
    : Array.isArray(content.timeline)
      ? (content.timeline as TimelineItem[])
      : [];

  const sourceItems: SourceCardItem[] = Array.isArray(content.sources)
    ? (content.sources as SourceCardItem[])
    : Array.isArray(content.evidence)
      ? (content.evidence as SourceCardItem[])
      : [];

  // Support all geographic map data conventions
  const geoMapData = (content.geo_data || content.map_data || content.map || content.spatial_map) as SpatialMapData | undefined;
  const hasGeoData = Boolean(geoMapData && typeof geoMapData === "object");
  const hasTimeline = timelineItems.length > 0;
  const hasSources = sourceItems.length > 0;

  return (
    <div className="w-full h-full flex flex-col justify-start space-y-6 font-mono select-none text-left p-2 overflow-y-auto pr-4 pb-12">
      {/* 1. Dynamic Top Section */}
      {hasGeoData ? (
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 items-start">
          {/* Left: Dominant World Map Canvas (~60%) */}
          <div className="lg:col-span-7 h-[360px] flex flex-col">
            <SpatialMapPrimitive
              data={{
                mode: "geo",
                title: "GLOBAL COMPUTE & HUB INFRASTRUCTURE",
                subtitle: "REAL-TIME INFRASTRUCTURE & HUBS",
                ...geoMapData,
              }}
            />
          </div>

          {/* Right: Floating Headline, Synthesis, and Timeline (~40%) */}
          <div className="lg:col-span-5 flex flex-col gap-4">
            {/* Top Headline Block */}
            <div className="space-y-2.5 p-4 rounded-xl border border-cyan-500/15 bg-slate-950/50 backdrop-blur-md">
              <div className="text-[10px] text-cyan-400 font-bold uppercase tracking-widest flex items-center justify-between">
                <span>BRIEFING / NEWS</span>
                <span className="text-cyan-400/60">TOP HEADLINE</span>
              </div>

              <h2 className="text-base font-bold text-slate-100 uppercase tracking-tight leading-snug font-sans">
                {headline}
              </h2>

              <div className="pt-2 border-t border-cyan-500/10">
                <div className="text-[10px] text-cyan-400/70 font-semibold uppercase tracking-wider mb-1">
                  SUMMARY
                </div>
                <p className="text-[13px] text-slate-200 font-sans leading-relaxed">
                  {currentSummary}
                </p>

                {/* Story Pagination Dots */}
                {summaries.length > 1 && (
                  <div className="flex items-center gap-1.5 mt-2.5">
                    {summaries.map((_, idx) => (
                      <button
                        key={idx}
                        type="button"
                        onClick={() => setActiveStoryPage(idx)}
                        className={`w-2 h-2 rounded-full transition cursor-pointer ${
                          activeStoryPage === idx
                            ? "bg-cyan-400 shadow-sm shadow-cyan-400"
                            : "bg-slate-700 hover:bg-slate-500"
                        }`}
                        aria-label={`View summary point ${idx + 1}`}
                      />
                    ))}
                  </div>
                )}
              </div>
            </div>

            {/* Key Timeline */}
            {hasTimeline && (
              <div className="p-3 rounded-xl border border-cyan-500/15 bg-slate-950/50 backdrop-blur-md">
                <TimelinePrimitive
                  data={{
                    title: "KEY TIMELINE",
                    layout: "vertical",
                    items: timelineItems,
                  }}
                />
              </div>
            )}
          </div>
        </div>
      ) : (
        /* Adaptive Rebalanced Layout (When No Map Exists) */
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 items-start">
          {/* Left 7 Cols: Dominant Headline & All Story Bullet Highlights */}
          <div className="lg:col-span-7 flex flex-col gap-4">
            <div className="space-y-4 p-6 rounded-2xl border border-cyan-500/20 bg-slate-950/60 backdrop-blur-md shadow-xl shadow-cyan-950/20 min-h-[240px] flex flex-col justify-between">
              <div>
                <div className="text-[10px] text-cyan-400 font-bold uppercase tracking-widest flex items-center justify-between mb-2">
                  <span>BRIEFING / NEWS</span>
                  <span className="text-cyan-400/60">INTELLIGENCE SYNTHESIS</span>
                </div>

                <h2 className="text-lg sm:text-xl font-bold text-slate-100 uppercase tracking-tight leading-snug font-sans">
                  {headline}
                </h2>
              </div>

              <div className="pt-3 border-t border-cyan-500/15 space-y-2.5">
                <div className="text-[10px] text-cyan-400/80 font-bold uppercase tracking-wider">
                  KEY INSIGHTS
                </div>
                {summaries.map((summary, idx) => (
                  <div key={idx} className="flex items-start gap-2.5 text-[13.5px] text-slate-200 font-sans leading-relaxed">
                    <span className="text-cyan-400 font-mono mt-0.5">▪</span>
                    <span>{summary}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>

          {/* Right 5 Cols: Key Timeline */}
          {hasTimeline && (
            <div className="lg:col-span-5 p-4 rounded-xl border border-cyan-500/15 bg-slate-950/50 backdrop-blur-md">
              <TimelinePrimitive
                data={{
                  title: "KEY TIMELINE",
                  layout: "vertical",
                  items: timelineItems,
                }}
              />
            </div>
          )}
        </div>
      )}

      {/* 2. Bottom Row: Visual Source Feed (With Safe Margin from Docked Core) */}
      {hasSources && (
        <div className="pt-3 border-t border-cyan-500/15 max-w-[calc(100%-250px)]">
          <SourceEvidencePrimitive
            data={{
              title: "SOURCE FEED",
              items: sourceItems,
            }}
          />
        </div>
      )}
    </div>
  );
}
