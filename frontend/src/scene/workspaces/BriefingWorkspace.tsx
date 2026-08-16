import { useState, type ReactElement } from "react";
import type { WorkspaceInstance } from "../../layout/workspaceStore";
import { SpatialMapPrimitive, type SpatialMapData } from "../../composer/primitives/SpatialMapPrimitive";
import { SourceEvidencePrimitive, type SourceCardItem } from "../../composer/primitives/SourceEvidencePrimitive";
import type { TimelineItem } from "../../composer/primitives/TimelinePrimitive";

export function BriefingWorkspace({ workspace }: { workspace: WorkspaceInstance }): ReactElement {
  const content = workspace.contentState || {};
  const [activeStoryPage, setActiveStoryPage] = useState(0);

  const headline = String(
    content.headline ||
    content.title ||
    workspace.title ||
    "DAILY INTELLIGENCE BRIEFING"
  ).replace(/^WORKSPACE\s*\/\/\s*/i, "");

  const summaries: string[] = Array.isArray(content.summaries) && content.summaries.length > 0
    ? (content.summaries as string[])
    : content.summary
      ? [String(content.summary)]
      : workspace.summary
        ? [workspace.summary]
        : ["No briefing text recorded for active operational window."];

  const currentSummary = summaries[activeStoryPage] || summaries[0];

  const rawTimeline = Array.isArray(content.timeline_items)
    ? content.timeline_items
    : Array.isArray(content.timeline)
      ? content.timeline
      : [];

  const timelineItems: TimelineItem[] = rawTimeline.map((it: any, idx: number) => ({
    time: it.time || it.timestamp || `STEP 0${idx + 1}`,
    title: it.title || it.event || it.description || "",
    summary: it.summary,
    status: it.status,
  }));

  const sourceItems: SourceCardItem[] = Array.isArray(content.sources)
    ? (content.sources as SourceCardItem[])
    : Array.isArray(content.evidence)
      ? (content.evidence as SourceCardItem[])
      : [];

  // Support geographic map data conventions
  const geoMapData = (content.geo_data || content.map_data || content.map || content.spatial_map) as SpatialMapData | undefined;
  const hasGeoData = Boolean(geoMapData && typeof geoMapData === "object");
  const hasTimeline = timelineItems.length > 0;
  const hasSources = sourceItems.length > 0;

  return (
    <div className="w-full h-full flex flex-col justify-start space-y-6 font-mono select-none text-left p-2 sm:p-4 overflow-y-auto pr-4 pb-16">
      {/* 1. Briefing Header Bar */}
      <div className="flex items-center justify-between border-b border-cyan-500/15 pb-3">
        <div className="flex items-center gap-2">
          <span className="w-2 h-2 rounded-full bg-cyan-400 animate-pulse" />
          <span className="text-xs text-cyan-400 font-bold tracking-widest uppercase">
            OPERATIONAL INTELLIGENCE BRIEFING
          </span>
        </div>
        <div className="text-[10px] text-slate-400 font-mono">
          {new Date().toISOString().slice(0, 10)} // CHARLIE V1
        </div>
      </div>

      {/* 2. Primary Layout: Map-Centric or Editorial Narrative */}
      {hasGeoData ? (
        /* Geographic Briefing: Dominant Visual Map Left (~65%), Synthesis & Signals Right (~35%) */
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 items-start">
          {/* Dominant World Map Surface (Expanding to full visual anchor) */}
          <div className="lg:col-span-8 h-[440px] sm:h-[500px] flex flex-col rounded-lg overflow-hidden border border-cyan-500/15 bg-transparent relative">
            <SpatialMapPrimitive
              data={{
                mode: "geo",
                title: "GLOBAL OPERATIONS THEATER",
                subtitle: "REAL-TIME GEOGRAPHIC CORRELATION",
                ...geoMapData,
              }}
            />
          </div>

          {/* Right Rail: Editorial Headline & Key Developments */}
          <div className="lg:col-span-4 flex flex-col gap-4">
            <div className="p-4 rounded-lg border border-cyan-500/15 bg-slate-950/50 backdrop-blur-sm space-y-2">
              <div className="text-[10px] font-bold text-cyan-400 uppercase tracking-widest">
                KEY DEVELOPMENT
              </div>
              <h2 className="text-sm sm:text-base font-bold text-slate-100 font-sans leading-snug">
                {headline}
              </h2>
              <p className="text-[12.5px] text-slate-300 font-sans leading-relaxed pt-1">
                {currentSummary}
              </p>

              {/* Story Pagination */}
              {summaries.length > 1 && (
                <div className="flex items-center gap-1.5 pt-2 border-t border-cyan-500/10">
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

            {/* Vertical Timeline Signal Feed */}
            {hasTimeline && (
              <div className="p-3.5 rounded-lg border border-cyan-500/10 bg-slate-950/30 space-y-2">
                <div className="text-[10px] font-bold text-cyan-400/80 uppercase tracking-widest">
                  INCIDENT TIMELINE
                </div>
                <div className="space-y-2">
                  {timelineItems.slice(0, 4).map((item, idx) => (
                    <div key={idx} className="flex items-start gap-2.5 text-xs">
                      <span className="text-cyan-400 font-mono text-[10px] mt-0.5 flex-shrink-0">
                        {item.time}
                      </span>
                      <span className="text-slate-200 font-sans leading-snug">
                        {item.title}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>
      ) : (
        /* Non-Map Editorial Briefing: High-Impact Editorial Synthesis */
        <div className="space-y-6 max-w-5xl">
          {/* Large Headline & Executive Synthesis (Typography-driven) */}
          <div className="space-y-3 border-b border-cyan-500/15 pb-5">
            <div className="text-[10px] font-bold text-cyan-400 uppercase tracking-widest">
              EXECUTIVE SYNTHESIS
            </div>
            <h1 className="text-xl sm:text-2xl font-bold text-slate-100 font-sans tracking-tight leading-tight">
              {headline}
            </h1>
            <div className="space-y-2 pt-2">
              {summaries.map((summary, idx) => (
                <div key={idx} className="flex items-start gap-3 text-sm text-slate-200 font-sans leading-relaxed">
                  <span className="text-cyan-400 font-mono mt-0.5 text-xs font-bold">0{idx + 1} //</span>
                  <span>{summary}</span>
                </div>
              ))}
            </div>
          </div>

          {/* Sequence of Events as sparse rows with hairlines */}
          {hasTimeline && (
            <div className="space-y-3">
              <div className="text-[10px] font-bold text-cyan-400/80 uppercase tracking-widest">
                SEQUENCE OF EVENTS
              </div>
              <div className="divide-y divide-cyan-500/10 border-y border-cyan-500/10 py-1">
                {timelineItems.map((item, idx) => (
                  <div key={idx} className="py-2.5 px-1 flex items-baseline gap-4 hover:bg-cyan-950/20 transition rounded">
                    <span className="text-xs text-cyan-400 font-mono flex-shrink-0 w-24">
                      {item.time}
                    </span>
                    <span className="text-xs text-slate-200 font-sans">
                      {item.title}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {/* 3. Bottom Evidence & Sources Strip (Safe Margins from Docked Core) */}
      {hasSources && (
        <div className="pt-4 border-t border-cyan-500/15 max-w-[calc(100%-250px)]">
          <SourceEvidencePrimitive
            data={{
              title: "VERIFIED SOURCES",
              items: sourceItems,
            }}
          />
        </div>
      )}
    </div>
  );
}
