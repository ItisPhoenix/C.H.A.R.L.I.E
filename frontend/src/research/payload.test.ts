import { describe, expect, it } from "vitest";
import { normalizeResearchPayload } from "./payload";

const validPayload = {
  schema: "charlie.research_workspace",
  version: 1,
  query: "q",
  mode: "standard",
  summary: "summary",
  status: "complete",
  confidence: 0.7,
  findings: [{ id: "F1", title: "Finding", detail: "Evidence", source_ids: ["S1"] }],
  sources: [{ id: "S1", title: "Source", domain: "example.test", url: "https://example.test", snippet: "Snippet" }],
};

describe("research workspace payload", () => {
  it("keeps source IDs as the citation relationship", () => {
    const payload = normalizeResearchPayload(validPayload);
    expect(payload?.findings[0]?.source_ids).toEqual(["S1"]);
    expect(payload?.sources[0]?.id).toBe("S1");
  });

  it("fails closed on dangling evidence and future schema versions", () => {
    expect(normalizeResearchPayload({ ...validPayload, version: 2 })).toBeNull();
    expect(normalizeResearchPayload({
      ...validPayload,
      findings: [{ id: "F1", title: "Finding", detail: "Evidence", source_ids: ["missing"] }],
    })).toBeNull();
  });
});
