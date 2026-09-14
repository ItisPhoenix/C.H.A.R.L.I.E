import { useState, type ReactElement } from "react";
import type { WorkspaceInstance } from "../../layout/workspaceStore";
import { SpatialMapPrimitive, type SpatialMapData } from "../../composer/primitives/SpatialMapPrimitive";
import { SourceEvidencePrimitive, type SourceCardItem } from "../../composer/primitives/SourceEvidencePrimitive";
import { TimelinePrimitive, type TimelineItem } from "../../composer/primitives/TimelinePrimitive";
import { normalizeBriefingWorkspacePayload } from "../../presentation/workspacePayloads";
import "./BriefingWorkspace.css";
function compactBriefingText(value: string): string {
  const normalized = value.replace(/[#*_]/g, "").replace(/\s+/g, " ").trim();
  return normalized.length > 360 ? `${normalized.slice(0, 357).trimEnd()}…` : normalized;
}

export function BriefingWorkspace({ workspace }: { workspace: WorkspaceInstance }): ReactElement {
  const payload = normalizeBriefingWorkspacePayload(workspace.contentState || {});
  const [story, setStory] = useState(0);
  const content = workspace.contentState || {};
  const map = (content.geo_data || content.map_data || content.map || content.spatial_map) as SpatialMapData | undefined;
  const summaries = payload.summaries.length
    ? payload.summaries
    : [payload.summary || workspace.summary || "No grounded briefing stories were returned."];
  const timeline: TimelineItem[] = payload.timeline_items.map((item) => ({
    time: item.time || item.timestamp || "",
    title: item.title,
    summary: item.summary,
    status: item.status as TimelineItem["status"],
  }));
  const sources = payload.sources as SourceCardItem[];
  const stories = payload.stories.slice(0, 4);

  return (
    <div className={`charlie-spatial-composition briefing-composition ${map ? "briefing-composition--map" : "briefing-composition--signal"}`}>
      {map && (
        <section className="briefing-map">
          <SpatialMapPrimitive data={{ mode: "geo", title: "", ...map }} />
        </section>
      )}

      {!map && (
        <section className="briefing-signal-field" aria-label="Grounded briefing stories">
          <div className="spatial-kicker">GROUNDED NEWS SIGNAL</div>
          <div className="briefing-signal-rule" />
          {stories.length > 0 ? stories.map((item, index) => (
            <article className={`briefing-story-signal${index === story ? " is-active" : ""}`} key={item.id}>
              <div className="briefing-story-signal__index">0{index + 1}</div>
              <div>
                <h2>{item.title}</h2>
                <p>{compactBriefingText(item.summary)}</p>
                <div className="briefing-story-signal__meta">
                  {item.published_at && <span>{item.published_at}</span>}
                  {item.region && <span>{item.region}</span>}
                  <span>{item.source_ids.length} SOURCES</span>
                </div>
              </div>
            </article>
          )) : (
            <p className="briefing-signal-empty">{compactBriefingText(payload.summary || "NO GROUNDED BRIEFING STORIES AVAILABLE")}</p>
          )}
          <div className="briefing-signal-counts">
            <span><strong>{stories.length}</strong> STORIES</span>
            <span><strong>{sources.length}</strong> SOURCES</span>
            <span><strong>{timeline.length}</strong> TIMELINE EVENTS</span>
          </div>
        </section>
      )}

      <aside className="briefing-rail">
        <div className="spatial-kicker">BRIEFING / NEWS</div>
        <div className="hairline" />
        <div className="spatial-kicker rail-label">TOP HEADLINE</div>
        <h1>{payload.headline || payload.title || "DAILY INTELLIGENCE BRIEFING"}</h1>
        <div className="spatial-kicker rail-label">SUMMARY</div>
        <p className="briefing-summary">{compactBriefingText(summaries[story] || summaries[0])}</p>
        {summaries.length > 1 && (
          <div className="story-dots">
            {summaries.map((_, index) => (
              <button key={index} type="button" onClick={() => setStory(index)} aria-label={`View story ${index + 1}`} className={index === story ? "active" : ""} />
            ))}
          </div>
        )}
        {timeline.length > 0 && (
          <div className="briefing-timeline">
            <TimelinePrimitive data={{ title: "KEY TIMELINE", layout: "vertical", items: timeline.slice(0, 5) }} />
          </div>
        )}
      </aside>

      {sources.length > 0 && (
        <section className="briefing-sources">
          <SourceEvidencePrimitive data={{ title: "SOURCE FEED", items: sources.slice(0, 4) }} />
        </section>
      )}
    </div>
  );
}
