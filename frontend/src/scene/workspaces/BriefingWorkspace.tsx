import { useState, type ReactElement } from "react";
import type { WorkspaceInstance } from "../../layout/workspaceStore";
import { SpatialMapPrimitive, type SpatialMapData } from "../../composer/primitives/SpatialMapPrimitive";
import { SourceEvidencePrimitive, type SourceCardItem } from "../../composer/primitives/SourceEvidencePrimitive";
import type { TimelineItem } from "../../composer/primitives/TimelinePrimitive";
import { normalizeBriefingWorkspacePayload } from "../../presentation/workspacePayloads";

export function BriefingWorkspace({ workspace }: { workspace: WorkspaceInstance }): ReactElement {
  const content = workspace.contentState || {};
  const payload = normalizeBriefingWorkspacePayload(content);
  const [activeStoryPage, setActiveStoryPage] = useState(0);

  const headline = String(
    payload.headline ||
    payload.title ||
    workspace.title ||
    "DAILY INTELLIGENCE BRIEFING"
  ).replace(/^WORKSPACE\s*\/\/\s*/i, "");

  const summaries: string[] = payload.summaries.length > 0
    ? payload.summaries
    : payload.summary
      ? [payload.summary]
      : workspace.summary
        ? [workspace.summary]
        : ["No briefing text recorded for active operational window."];

  const currentSummary = summaries[activeStoryPage] || summaries[0];

  const timelineItems: TimelineItem[] = payload.timeline_items.map((item, idx) => ({
    time: item.time || item.timestamp || `STEP 0${idx + 1}`,
    title: item.title,
    summary: item.summary,
    status: item.status as TimelineItem["status"],
  }));

  const sourceItems: SourceCardItem[] = payload.sources as SourceCardItem[];

  // Support geographic map data conventions
  const geoMapData = (content.geo_data || content.map_data || content.map || content.spatial_map) as SpatialMapData | undefined;
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

      {/* 2. Primary Layout: the world map is context even without geo events. */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 items-start">
          {/* Dominant World Map Surface (Expanding to full visual anchor) */}
          <div className="lg:col-span-8 h-[440px] sm:h-[500px] flex flex-col overflow-hidden bg-transparent relative charlie-map-immersive">
            <SpatialMapPrimitive
              data={{
                mode: "geo",
                title: "GLOBAL OPERATIONS THEATER",
                subtitle: "REAL-TIME GEOGRAPHIC CORRELATION",
                ...(geoMapData && typeof geoMapData === "object" ? geoMapData : {}),
              }}
            />
          </div>

          {/* Right Rail: Editorial Headline & Key Developments */}
          <div className="lg:col-span-4 flex flex-col gap-4">
            <div className="briefing-rail-section space-y-2">
              <div className="text-[10px] font-bold text-cyan-400 uppercase tracking-widest">
                TOP HEADLINE
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
              <div className="briefing-rail-section space-y-2">
                <div className="text-[10px] font-bold text-cyan-400/80 uppercase tracking-widest">
                  KEY TIMELINE
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
      </div>

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
