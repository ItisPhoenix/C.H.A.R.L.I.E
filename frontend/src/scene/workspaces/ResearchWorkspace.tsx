import { useState, type ReactElement } from "react";
import type { WorkspaceInstance } from "../../layout/workspaceStore";
import { SpatialMapPrimitive, type SpatialMapData } from "../../composer/primitives/SpatialMapPrimitive";
import { DensityHeatmapPrimitive, type DensityHeatmapData } from "../../composer/primitives/DensityHeatmapPrimitive";
import { SourceEvidencePrimitive, type SourceCardItem } from "../../composer/primitives/SourceEvidencePrimitive";
import { TimelinePrimitive, type TimelineItem } from "../../composer/primitives/TimelinePrimitive";
import { ChartPrimitive } from "../../composer/primitives/ChartPrimitive";
import { normalizeResearchWorkspacePayload, type ResearchFinding } from "../../presentation/workspacePayloads";
import { ResearchRichText } from "./ResearchRichText";

export interface FindingItem {
  id?: string;
  title?: string;
  label?: string;
  detail?: string;
  text?: string;
  iconType?: "trend" | "radar" | "signal" | "shield" | "alert" | "anomaly" | "intercept" | string;
}

export function ResearchWorkspace({ workspace }: { workspace: WorkspaceInstance }): ReactElement {
  const content = workspace.contentState || {};
  const payload = normalizeResearchWorkspacePayload(content);
  const [disclosureLevel, setDisclosureLevel] = useState<1 | 2 | 3>(2);

  const title = String(payload.title || workspace.title || "RESEARCH & SYNTHESIS").replace(/^WORKSPACE\s*\/\/\s*/i, "");
  const subtitle = String(payload.query || "");
  const summary = String(
    payload.summary ||
    workspace.summary ||
    "Synthesizing active research streams and correlating contextual intelligence."
  );

  // Dynamic Findings from payload (supporting multiple payload formats)
  const findings: (ResearchFinding & FindingItem)[] = payload.findings.map((finding) => ({
    ...finding,
    iconType: "signal",
  }));

  // Resolve analytical visuals from payload
  const spatialMapData = (content.radar || content.spatial_map || content.map_data || content.map) as SpatialMapData | undefined;
  const hasSpatial = Boolean(spatialMapData && typeof spatialMapData === "object");
  const isGeographicSpatial = Boolean(
    spatialMapData &&
      (spatialMapData.mode === "geo" || content.geo_data || content.geography || content.geoData),
  );

  const heatmapData = (content.heatmap || content.heatmap_data || content.density) as DensityHeatmapData | undefined;
  const hasHeatmap = Boolean(heatmapData && typeof heatmapData === "object");

  const chartData = (content.chart || content.chart_data || content.activity_history) as Record<string, unknown> | undefined;
  const hasChart = Boolean(chartData);

  const timelineItems: TimelineItem[] = payload.timeline_items.map((item) => ({
    time: item.time || item.timestamp || "",
    title: item.title,
    summary: item.summary,
    status: item.status as TimelineItem["status"],
  }));
  const hasTimeline = timelineItems.length > 0;

  const sourceItems: SourceCardItem[] = payload.sources as SourceCardItem[];
  const hasSources = sourceItems.length > 0;

  // Real payload confidence / status (never fabricated)
  const payloadConfidence = payload.confidence;
  const payloadStatus = payload.status;

  return (
    <div className="w-full h-full flex flex-col justify-start space-y-6 font-mono select-none text-left p-2 sm:p-4 overflow-y-auto pr-4 pb-16">
      {/* 1. Header & Technical HUD Controls */}
      <div className="flex items-start justify-between border-b border-cyan-500/15 pb-3 gap-4">
        <div>
          <div className="text-xs text-cyan-400 font-bold tracking-widest uppercase flex items-center gap-2">
            <span className="w-1.5 h-1.5 rounded-full bg-cyan-400 animate-pulse" />
            <span>{title}</span>
          </div>
          {subtitle && (
            <div className="text-[11px] text-slate-400 font-sans tracking-wide mt-0.5">
              Query: <span className="text-cyan-200">{subtitle}</span>
            </div>
          )}
        </div>

        {/* Progressive Disclosure Selector */}
        <div className="flex items-center gap-1 bg-slate-950/80 border border-cyan-500/20 rounded-lg p-0.5 flex-shrink-0">
          <button
            type="button"
            onClick={() => setDisclosureLevel(1)}
            className={`px-2.5 py-1 text-[10px] rounded transition cursor-pointer font-mono ${
              disclosureLevel === 1
                ? "bg-cyan-950 text-cyan-300 border border-cyan-400/50 shadow-sm"
                : "text-slate-400 hover:text-slate-200"
            }`}
          >
            Summary
          </button>
          <button
            type="button"
            onClick={() => setDisclosureLevel(2)}
            className={`px-2.5 py-1 text-[10px] rounded transition cursor-pointer font-mono ${
              disclosureLevel === 2
                ? "bg-cyan-950 text-cyan-300 border border-cyan-400/50 shadow-sm"
                : "text-slate-400 hover:text-slate-200"
            }`}
          >
            Synthesis
          </button>
          <button
            type="button"
            onClick={() => setDisclosureLevel(3)}
            className={`px-2.5 py-1 text-[10px] rounded transition cursor-pointer font-mono ${
              disclosureLevel === 3
                ? "bg-cyan-950 text-cyan-300 border border-cyan-400/50 shadow-sm"
                : "text-slate-400 hover:text-slate-200"
            }`}
          >
            Deep Dive
          </button>
        </div>
      </div>

      {/* 2. Primary Focal Composition */}
      {hasSpatial ? (
        /* Rich Spatial Research: Dominant Visual Left/Center (~65%), Asymmetric Synthesis Rail (~35%) */
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 items-start">
          {/* Dominant Analytical Visual Surface (Merging into dark environment) */}
          <div className="lg:col-span-8 h-[420px] sm:h-[500px] flex flex-col overflow-hidden bg-transparent relative charlie-map-immersive">
            <SpatialMapPrimitive
              data={{
                ...spatialMapData,
                mode: isGeographicSpatial ? "geo" : "radar",
              }}
            />
          </div>

          {/* Asymmetric Synthesis & Findings Rail */}
          <div className="lg:col-span-4 flex flex-col gap-4">
            {/* Primary Synthesis Statement */}
            <div className="p-4 rounded-lg border border-cyan-500/15 bg-slate-950/50 backdrop-blur-sm space-y-2">
              <div className="text-[10px] font-bold text-cyan-400/80 uppercase tracking-widest flex items-center justify-between">
                <span>SYNTHESIS SUMMARY</span>
                {payloadStatus && (
                  <span className="text-[9px] px-1.5 py-0.2 rounded bg-cyan-950 border border-cyan-500/30 text-cyan-300">
                    {payloadStatus}
                  </span>
                )}
              </div>
              <p className="text-[13px] text-slate-200 font-sans leading-relaxed">
                <ResearchRichText text={summary} />
              </p>
              {payloadConfidence !== null && (
                <div className="pt-2 border-t border-cyan-500/10 text-[10px] text-slate-400 flex justify-between">
                  <span>CONFIDENCE</span>
                  <span className="text-cyan-300 font-bold">{Math.round(payloadConfidence * 100)}%</span>
                </div>
              )}
            </div>

            {/* Supporting Findings */}
            {findings.length > 0 && disclosureLevel >= 2 && (
              <div className="space-y-2">
                <div className="text-[10px] font-bold text-cyan-400/70 uppercase tracking-widest">
                  SUPPORTING EVIDENCE
                </div>
                <div className="space-y-2">
                  {findings.map((f, idx) => (
                    <div
                      key={f.id || idx}
                      className="p-3 rounded-lg border border-cyan-500/10 bg-slate-950/30 hover:border-cyan-500/30 transition space-y-1"
                    >
                      <div className="text-xs font-semibold text-cyan-200 font-sans">
                        {f.title}
                      </div>
                      <div className="text-[12px] text-slate-300 font-sans leading-relaxed">
                        {f.detail}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>
      ) : (
        /* Non-Spatial Research: Editorial Typography-Driven Synthesis */
        <div className="space-y-6 max-w-none">
          {/* Dominant Executive Conclusion / Synthesis (Typography-driven, minimal chrome) */}
          <div className="space-y-2 border-b border-cyan-500/15 pb-5">
            <div className="text-[10px] font-bold text-cyan-400 uppercase tracking-widest flex items-center justify-between">
              <span>PRIMARY RESEARCH SYNTHESIS</span>
              {payloadStatus && (
                <span className="text-[10px] px-2 py-0.5 rounded bg-cyan-950 border border-cyan-500/40 text-cyan-200 font-bold">
                  {payloadStatus}
                </span>
              )}
            </div>
            <p className="text-base sm:text-lg text-slate-100 font-sans font-normal leading-relaxed">
              <ResearchRichText text={summary} />
            </p>
            {payloadConfidence !== null && (
              <div className="pt-2 text-xs text-slate-400 flex items-center gap-2 font-mono">
                <span>CORRELATION CONFIDENCE:</span>
                <span className="text-cyan-300 font-bold">{Math.round(payloadConfidence * 100)}%</span>
              </div>
            )}
          </div>

          {/* Key Findings as Analytical Signals separated by hairlines */}
          {findings.length > 0 && disclosureLevel >= 2 && (
            <div className="space-y-3">
              <div className="text-[10px] font-bold text-cyan-400/80 uppercase tracking-widest">
                KEY FINDINGS & ANALYTICAL SIGNALS
              </div>
              <div className="divide-y divide-cyan-500/10 border-y border-cyan-500/10 py-1">
                {findings.map((f, idx) => (
                  <div
                    key={f.id || idx}
                    className="py-3.5 px-2 flex items-start gap-3 hover:bg-cyan-950/20 transition rounded"
                  >
                    <span className="text-[10px] text-cyan-400 font-mono font-bold mt-0.5 flex-shrink-0">
                      0{idx + 1} //
                    </span>
                    <div className="space-y-1">
                      <div className="text-sm font-semibold text-cyan-200 font-sans">
                        {f.title}
                      </div>
                      <p className="text-xs text-slate-300 font-sans leading-relaxed max-w-3xl">
                        {f.detail}
                      </p>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Optional Heatmap or Chart embedded cleanly if present in deep dive */}
          {(hasHeatmap || hasChart || hasTimeline) && disclosureLevel >= 2 && (
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-5 pt-2 max-w-6xl">
              {hasChart && (
                <div className="border-t border-cyan-500/15 pt-3">
                  <div className="text-[10px] font-bold text-cyan-400/80 uppercase tracking-widest mb-1">
                    ANALYTICAL SIGNALS
                  </div>
                  <ChartPrimitive
                    primitive={{
                      id: "chart_activity",
                      type: "chart",
                      data: chartData,
                    }}
                  />
                </div>
              )}
              {hasHeatmap && <DensityHeatmapPrimitive data={heatmapData!} />}
              {hasTimeline && (
                <div className="border-t border-cyan-500/15 pt-3">
                  <TimelinePrimitive
                    data={{
                      title: "EVIDENCE TIMELINE",
                      layout: "vertical",
                      items: timelineItems,
                    }}
                  />
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* 3. Bottom Evidence & Sources Strip (Safe Margins from Docked Core) */}
      {hasSources && disclosureLevel >= 2 && (
        <div className="pt-4 border-t border-cyan-500/15 max-w-[calc(100%-250px)]">
          <SourceEvidencePrimitive
            data={{
              title: "VERIFIED EVIDENCE & SOURCES",
              items: sourceItems,
            }}
          />
        </div>
      )}

      {/* 4. Bottom Milestone Timeline if provided */}
      {hasTimeline && disclosureLevel >= 3 && (
        <div className="pt-2 border-t border-cyan-500/15 max-w-[calc(100%-250px)]">
          <TimelinePrimitive
            data={{
              title: "EVENT TIMELINE",
              layout: "horizontal",
              items: timelineItems,
            }}
          />
        </div>
      )}
    </div>
  );
}
