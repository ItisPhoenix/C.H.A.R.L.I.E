import { type ReactElement } from "react";
import type { WorkspaceInstance } from "../../layout/workspaceStore";
import { SpatialMapPrimitive, type SpatialMapData } from "../../composer/primitives/SpatialMapPrimitive";
import { DensityHeatmapPrimitive, type DensityHeatmapData } from "../../composer/primitives/DensityHeatmapPrimitive";
import { SourceEvidencePrimitive, type SourceCardItem } from "../../composer/primitives/SourceEvidencePrimitive";
import { TimelinePrimitive, type TimelineItem } from "../../composer/primitives/TimelinePrimitive";
import { ChartPrimitive } from "../../composer/primitives/ChartPrimitive";
import { normalizeResearchWorkspacePayload, type ResearchFinding } from "../../presentation/workspacePayloads";
import { ResearchRichText } from "./ResearchRichText";

export interface FindingItem extends Partial<ResearchFinding> {
  iconType?: string;
  text?: string;
  label?: string;
}

function compactResearchText(value: string, maxLength: number): string {
  const normalized = value.replace(/[#*_]/g, "").replace(/\s+/g, " ").trim();
  const sentences = normalized.split(/(?<=[.!?])\s+/).slice(0, 2).join(" ");
  if (sentences.length <= maxLength) return sentences;
  return `${sentences.slice(0, Math.max(0, maxLength - 1)).trimEnd()}…`;
}

export function ResearchWorkspace({ workspace }: { workspace: WorkspaceInstance }): ReactElement {
  const content = workspace.contentState || {};
  const payload = normalizeResearchWorkspacePayload(content);
  const disclosureLevel = 3;
  const title = String(payload.title || workspace.title || "RESEARCH & SYNTHESIS").replace(/^WORKSPACE\s*\/\/\s*/i, "");
  const objective = compactResearchText(String(content.objective || payload.query || "No research objective reported."), 260);
  const findings = payload.findings as (ResearchFinding & FindingItem)[];
  const map = (content.radar || content.spatial_map || content.map_data || content.map) as SpatialMapData | undefined;
  const heatmap = (content.heatmap || content.heatmap_data || content.density) as DensityHeatmapData | undefined;
  const chart = (content.chart || content.chart_data || content.activity_history) as Record<string, unknown> | undefined;
  const timeline: TimelineItem[] = payload.timeline_items.map((item) => ({
    time: item.time || item.timestamp || "",
    title: item.title,
    summary: item.summary,
    status: item.status as TimelineItem["status"],
  }));
  const sources = payload.sources as SourceCardItem[];

  return (
    <div className="charlie-spatial-composition research-composition" data-disclosure-level={disclosureLevel}>
      <header className="spatial-heading research-heading">
        <div className="spatial-kicker">RESEARCH WORKSPACE</div>
        <h1>{title}</h1>
        {payload.query && <p className="spatial-subtitle">{payload.query}</p>}
        <div className="spatial-objective">
          <span>RESEARCH OBJECTIVE</span>
          <ResearchRichText text={objective} />
        </div>
      </header>
      <section className="research-visual" aria-label="Research visualization">
        {map ? (
          <SpatialMapPrimitive data={{ ...map, mode: content.geo_data || content.geography || content.geoData ? "geo" : map.mode || "radar" }} />
        ) : chart ? (
          <ChartPrimitive primitive={{ id: "research-chart", type: "chart", data: chart }} />
        ) : heatmap ? (
          <DensityHeatmapPrimitive data={heatmap} />
        ) : (
          <div className="spatial-empty">NO GROUNDED SPATIAL OR TEMPORAL VISUAL AVAILABLE</div>
        )}
      </section>
      {heatmap && map && <section className="research-density"><div className="spatial-kicker">ACTIVITY DENSITY</div><DensityHeatmapPrimitive data={heatmap} /></section>}
      {chart && map && <section className="research-chart"><div className="spatial-kicker">ACTIVITY OVER TIME</div><ChartPrimitive primitive={{ id: "research-activity", type: "chart", data: chart }} /></section>}
      <section className="research-findings spatial-rail-section">
        <div className="spatial-kicker">KEY FINDINGS &amp; ANALYTICAL SIGNALS</div>
        {findings.length ? findings.slice(0, 5).map((finding, index) => (
          <article className="finding-signal" key={finding.id || index}>
            <span className="finding-glyph">{finding.contradiction ? "△" : "◈"}</span>
            <div>
              <strong>{compactResearchText(String(finding.title || "Finding"), 78)}</strong>
              <p>{compactResearchText(String(finding.detail || "No detail reported."), 150)}</p>
            </div>
          </article>
        )) : <div className="spatial-empty">NO GROUNDED FINDINGS REPORTED</div>}
      </section>
      {sources.length > 0 && <section className="research-evidence"><SourceEvidencePrimitive data={{ title: "EVIDENCE & SOURCES", items: sources }} /></section>}
      {timeline.length > 0 && <section className="research-timeline"><TimelinePrimitive data={{ title: "TIMELINE", layout: "horizontal", items: timeline }} /></section>}
    </div>
  );
}
