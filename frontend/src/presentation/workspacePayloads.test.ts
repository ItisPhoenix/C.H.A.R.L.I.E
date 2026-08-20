import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import {
  normalizeBriefingWorkspacePayload,
  normalizeResearchWorkspacePayload,
} from "./workspacePayloads";

describe("workspace payload normalization", () => {
  it("conforms canonical schema/version to shared payload contract", () => {
    const contract = JSON.parse(readFileSync(resolve(process.cwd(), "../shared/workspace_payload_contract.json"), "utf8"));
    const research = normalizeResearchWorkspacePayload({});
    const briefing = normalizeBriefingWorkspacePayload({});
    expect(research.schema).toBe(contract.payloads.research.schema);
    expect(research.version).toBe(contract.payloads.research.version);
    expect(briefing.schema).toBe(contract.payloads.briefing.schema);
    expect(briefing.version).toBe(contract.payloads.briefing.version);
  });

  it("keeps canonical research fields and supports bounded legacy aliases", () => {
    const payload = normalizeResearchWorkspacePayload({
      schema: "charlie.research_workspace",
      version: 1,
      query: "research query",
      mode: "standard",
      summary: "clean summary",
      status: "complete",
      confidence: 0.8,
      findings: [{ id: "F1", title: "Finding", detail: "Evidence", source_ids: ["S1"] }],
      sources: [{ id: "S1", title: "Source", domain: "example.com", url: "https://example.com", snippet: "Snippet" }],
      timeline_items: [],
    });

    expect(payload.schema).toBe("charlie.research_workspace");
    expect(payload.findings[0]?.source_ids).toEqual(["S1"]);
    expect(payload.sources[0]?.id).toBe("S1");
  });

  it("uses real briefing story titles and published timeline metadata", () => {
    const payload = normalizeBriefingWorkspacePayload({
      schema: "charlie.briefing_workspace",
      version: 1,
      headline: "Real headline",
      summary: "Real summary",
      stories: [{ id: "ST1", title: "Story", summary: "Details", source_ids: ["S1"], published_at: "2026-08-20" }],
      summaries: ["Details"],
      timeline_items: [{ id: "ST1", kind: "published", timestamp: "2026-08-20", title: "Story" }],
      sources: [{ id: "S1", title: "Story source", url: "https://example.com" }],
      status: "complete",
      confidence: 0.7,
    });

    expect(payload.headline).toBe("Real headline");
    expect(payload.stories[0]?.source_ids).toEqual(["S1"]);
    expect(payload.timeline_items[0]?.kind).toBe("published");
  });

  it("drops dangling provenance references", () => {
    const payload = normalizeResearchWorkspacePayload({
      findings: [{ id: "F1", detail: "Evidence", source_ids: ["missing"] }],
      sources: [{ id: "S1", title: "Known", url: "https://example.com" }],
    });
    expect(payload.findings[0]?.source_ids).toEqual([]);
  });

  it.each([
    ["future version", { schema: "charlie.research_workspace", version: 99, findings: [{ detail: "future" }] }],
    ["wrong schema", { schema: "charlie.briefing_workspace", version: 1, stories: [{ title: "briefing" }] }],
    ["missing canonical field", {
      schema: "charlie.research_workspace",
      version: 1,
      query: "q",
      mode: "quick",
      summary: "summary",
      status: "complete",
      confidence: 0,
      sources: [],
    }],
  ])("fails safe for %s instead of interpreting unknown research content", (_label, input) => {
    const payload = normalizeResearchWorkspacePayload(input);

    expect(payload.status).toBe("unsupported");
    expect(payload.findings).toEqual([]);
    expect(payload.sources).toEqual([]);
    expect(payload.summary).toContain("Unsupported");
  });

  it.each([
    ["future version", { schema: "charlie.briefing_workspace", version: 99, stories: [{ title: "future" }] }],
    ["wrong schema", { schema: "charlie.research_workspace", version: 1, findings: [{ title: "research" }] }],
    ["missing canonical field", {
      schema: "charlie.briefing_workspace",
      version: 1,
      headline: "Headline",
      summary: "Summary",
      stories: [],
      sources: [],
      status: "complete",
      confidence: 0,
    }],
  ])("fails safe for %s instead of interpreting unknown briefing content", (_label, input) => {
    const payload = normalizeBriefingWorkspacePayload(input);

    expect(payload.status).toBe("unsupported");
    expect(payload.stories).toEqual([]);
    expect(payload.sources).toEqual([]);
    expect(payload.summary).toContain("Unsupported");
  });

  it("keeps unversioned legacy briefing payloads bounded and compatible", () => {
    const payload = normalizeBriefingWorkspacePayload({
      title: "Legacy briefing",
      summary: "Legacy summary",
      stories: [{ title: "Legacy story", summary: "Legacy detail" }],
    });

    expect(payload.version).toBe(1);
    expect(payload.stories).toHaveLength(1);
    expect(payload.status).toBe("partial");
  });
});
