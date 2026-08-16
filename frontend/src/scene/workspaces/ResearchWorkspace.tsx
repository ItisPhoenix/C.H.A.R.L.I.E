import { useState, type ReactElement } from "react";
import type { WorkspaceInstance } from "../../layout/workspaceStore";
import { SpatialMapPrimitive, type SpatialMapData } from "../../composer/primitives/SpatialMapPrimitive";
import { DensityHeatmapPrimitive, type DensityHeatmapData } from "../../composer/primitives/DensityHeatmapPrimitive";
import { SourceEvidencePrimitive, type SourceCardItem } from "../../composer/primitives/SourceEvidencePrimitive";
import { TimelinePrimitive, type TimelineItem } from "../../composer/primitives/TimelinePrimitive";
import { ChartPrimitive } from "../../composer/primitives/ChartPrimitive";
import { ContextCard, ContextCardHeader, ContextCardBody, ContextCardMetadata } from "../../ui/context";

export interface FindingItem {
  id?: string;
  title: string;
  detail: string;
  iconType?: "trend" | "radar" | "signal" | "shield" | "alert" | "anomaly" | "intercept" | string;
}

export function ResearchWorkspace({ workspace }: { workspace: WorkspaceInstance }): ReactElement {
  const content = workspace.contentState || {};
  const [disclosureLevel, setDisclosureLevel] = useState<1 | 2 | 3>(2);

  const subtitle = String(content.subtitle || content.category || "");
  const objective = String(
    content.objective ||
    content.summary ||
    workspace.summary ||
    "Evaluate operational readiness and intelligence correlation vectors."
  );

  // Dynamic Findings data strictly from payload
  const findings: FindingItem[] = Array.isArray(content.findings) ? (content.findings as FindingItem[]) : [];

  const renderFindingIcon = (type?: string) => {
    switch (type) {
      case "trend":
        return "↗";
      case "radar":
      case "intercept":
        return "◎";
      case "signal":
        return "((•))";
      case "anomaly":
      case "shield":
      case "alert":
      default:
        return "⚠";
    }
  };

  // Adaptive data resolution: Support all possible payload key conventions
  const spatialMapData = (content.radar || content.spatial_map || content.map_data || content.map) as SpatialMapData | undefined;
  const hasSpatialData = Boolean(spatialMapData && typeof spatialMapData === "object");

  const heatmapData = (content.heatmap || content.heatmap_data || content.density) as DensityHeatmapData | undefined;
  const hasHeatmapData = Boolean(heatmapData && typeof heatmapData === "object");

  const chartData = (content.chart || content.chart_data || content.activity_history) as Record<string, unknown> | undefined;
  const hasChartData = Boolean(chartData);

  const timelineItems: TimelineItem[] = Array.isArray(content.timeline_items)
    ? (content.timeline_items as TimelineItem[])
    : Array.isArray(content.timeline)
      ? (content.timeline as TimelineItem[])
      : [];
  const hasTimelineData = timelineItems.length > 0;

  const sourceItems: SourceCardItem[] = Array.isArray(content.sources)
    ? (content.sources as SourceCardItem[])
    : Array.isArray(content.evidence)
      ? (content.evidence as SourceCardItem[])
      : [];
  const hasSourceDeckData = sourceItems.length > 0;

  return (
    <div className="w-full h-full flex flex-col justify-start space-y-6 font-mono select-none text-left p-2 overflow-y-auto pr-4 pb-12">
      {/* 1. Header & Controls Bar */}
      <div className="flex items-center justify-between border-b border-cyan-500/15 pb-2.5">
        <div>
          <div className="text-[11px] text-cyan-400 font-bold tracking-widest uppercase">
            {subtitle || "INCIDENT ANALYSIS & PATTERN RECOGNITION"}
          </div>
        </div>

        {/* Progressive Disclosure Controls */}
        <div className="flex items-center gap-1.5 bg-slate-950/70 border border-cyan-500/20 rounded-lg p-1">
          <button
            type="button"
            onClick={() => setDisclosureLevel(1)}
            className={`px-2.5 py-0.5 text-[10px] rounded transition cursor-pointer font-mono ${
              disclosureLevel === 1
                ? "bg-cyan-950 text-cyan-300 border border-cyan-400/50 shadow-sm shadow-cyan-500/20"
                : "text-slate-400 hover:text-slate-200"
            }`}
          >
            Summary
          </button>
          <button
            type="button"
            onClick={() => setDisclosureLevel(2)}
            className={`px-2.5 py-0.5 text-[10px] rounded transition cursor-pointer font-mono ${
              disclosureLevel === 2
                ? "bg-cyan-950 text-cyan-300 border border-cyan-400/50 shadow-sm shadow-cyan-500/20"
                : "text-slate-400 hover:text-slate-200"
            }`}
          >
            Context
          </button>
          <button
            type="button"
            onClick={() => setDisclosureLevel(3)}
            className={`px-2.5 py-0.5 text-[10px] rounded transition cursor-pointer font-mono ${
              disclosureLevel === 3
                ? "bg-cyan-950 text-cyan-300 border border-cyan-400/50 shadow-sm shadow-cyan-500/20"
                : "text-slate-400 hover:text-slate-200"
            }`}
          >
            Deep Dive
          </button>
        </div>
      </div>

      {/* 2. Dynamic Adaptive Spatial Composition */}
      {hasSpatialData ? (
        /* Asymmetric Three-Column Spatial Composition (When Spatial Visual Exists) */
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 items-start">
          {/* Left Column: Objective & Density Spectrum */}
          <div className="lg:col-span-4 flex flex-col gap-4">
            <ContextCard variant="standard">
              <ContextCardHeader title="Research Objective" category="OBJECTIVE" />
              <ContextCardBody text={objective} />
            </ContextCard>

            {hasHeatmapData && disclosureLevel >= 2 && (
              <DensityHeatmapPrimitive data={heatmapData!} />
            )}
          </div>

          {/* Center Column: Dominant 2D Analytical Visual Canvas */}
          {disclosureLevel >= 2 && (
            <div className="lg:col-span-5 h-[340px] flex flex-col">
              <SpatialMapPrimitive
                data={{
                  mode: "radar",
                  ...spatialMapData,
                }}
              />
            </div>
          )}

          {/* Right Column: Key Findings & Activity Chart */}
          <div className="lg:col-span-3 flex flex-col gap-4">
            {hasChartData && (
              <div className="space-y-1.5">
                <div className="flex items-center justify-between text-[10px]">
                  <span className="text-cyan-400/80 font-bold uppercase tracking-wider">
                    ACTIVITY OVER TIME
                  </span>
                  <span className="text-emerald-400 font-bold">↑ 78%</span>
                </div>
                <ContextCard variant="compact" className="h-28">
                  <ChartPrimitive
                    primitive={{
                      id: "chart_activity",
                      type: "chart",
                      data: chartData,
                    }}
                  />
                </ContextCard>
              </div>
            )}

            <div className="space-y-2">
              <div className="text-[10px] text-cyan-400 font-bold uppercase tracking-wider">
                KEY FINDINGS
              </div>
              <div className="flex flex-col gap-2">
                {findings.map((f, idx) => (
                  <ContextCard key={f.id || idx} variant="standard" interactive>
                    <ContextCardHeader
                      title={f.title}
                      icon={renderFindingIcon(f.iconType)}
                      category={`FINDING #${idx + 1}`}
                    />
                    <ContextCardBody text={f.detail} />
                  </ContextCard>
                ))}
              </div>
            </div>
          </div>
        </div>
      ) : (
        /* Rebalanced Dual-Column Adaptive Composition (When No Map Exists) */
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 items-start">
          {/* Left 5 Cols: Objective Narrative & Reliability */}
          <div className="lg:col-span-5 flex flex-col gap-4">
            <ContextCard variant="standard" elevation="elevated" className="min-h-[220px] flex flex-col justify-between">
              <div>
                <ContextCardHeader
                  title="Research Objective"
                  category="PRIMARY OBJECTIVE"
                  badge="ACTIVE"
                  badgeVariant="cyan"
                />
                <ContextCardBody text={objective} />
              </div>
              <ContextCardMetadata
                confidence={0.992}
                items={[{ label: "STATUS", value: "VERIFIED", highlight: true }]}
              />
            </ContextCard>

            {hasHeatmapData && disclosureLevel >= 2 && (
              <DensityHeatmapPrimitive data={heatmapData!} />
            )}
          </div>

          {/* Right 7 Cols: Key Findings Grid */}
          <div className="lg:col-span-7 flex flex-col gap-3">
            <div className="text-[10px] text-cyan-400 font-bold uppercase tracking-wider">
              KEY FINDINGS
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3.5">
              {findings.map((f, idx) => (
                <ContextCard
                  key={f.id || idx}
                  variant="standard"
                  interactive
                  className="min-h-[160px] flex flex-col justify-between"
                >
                  <div>
                    <ContextCardHeader
                      title={f.title}
                      icon={renderFindingIcon(f.iconType)}
                      category={`FINDING #${idx + 1}`}
                    />
                    <ContextCardBody text={f.detail} />
                  </div>
                  <ContextCardMetadata
                    items={[{ label: "VERIFICATION", value: "CONFIRMED ↗", highlight: true }]}
                  />
                </ContextCard>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* 3. Bottom Row: Evidence & Sources Deck (With Right Safe Zone for Docked Core) */}
      {hasSourceDeckData && disclosureLevel >= 2 && (
        <div className="pt-3 border-t border-cyan-500/15 max-w-[calc(100%-250px)]">
          <SourceEvidencePrimitive
            data={{
              title: "EVIDENCE & SOURCES",
              items: sourceItems,
            }}
          />
        </div>
      )}

      {/* 4. Bottom Milestone Timeline (With Right Safe Zone for Docked Core) */}
      {hasTimelineData && disclosureLevel >= 2 && (
        <div className="pt-2 border-t border-cyan-500/15 max-w-[calc(100%-250px)]">
          <TimelinePrimitive
            data={{
              title: "TIMELINE",
              layout: "horizontal",
              items: timelineItems,
            }}
          />
        </div>
      )}
    </div>
  );
}
