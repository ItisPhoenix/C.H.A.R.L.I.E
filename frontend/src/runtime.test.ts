import { afterEach, describe, expect, it, vi } from "vitest";
import { loadScene, orbStateForEvent, parseRuntimeEvent, parseSnapshot, postCommand } from "./runtime";

afterEach(() => vi.restoreAllMocks());

describe("live runtime boundary", () => {
  it("maps canonical runtime events to truthful orb states", () => {
    expect(orbStateForEvent("transcript")).toBe("listening");
    expect(orbStateForEvent("thinking")).toBe("shaping");
    expect(orbStateForEvent({ type: "thinking", stage: "planning" })).toBe("weaving");
    expect(orbStateForEvent("speaking_start")).toBe("composing");
    expect(orbStateForEvent("response_done")).toBe("breathing");
    expect(orbStateForEvent("unrelated_event")).toBeNull();
  });

  it("accepts a valid snapshot and rejects malformed content", () => {
    expect(parseSnapshot({ revision: 3, title: "System status", details: [{ label: "CPU", value: "18%" }] })?.details).toHaveLength(1);
    expect(parseSnapshot({ revision: -1, title: "invalid" })).toBeNull();
    expect(parseSnapshot({ revision: 1, title: "invalid", details: [{ label: "CPU", value: 18 }] })).toBeNull();
  });

  it("hydrates the current session's chat and durable activity from a scene snapshot", () => {
    const snapshot = parseSnapshot({
      revision: 4,
      title: "Charlie is ready",
      session_id: "voice_launch-a",
      conversation_history: [
        { id: "message-1", role: "you", text: "Keep this chat visible." },
        { id: "message-2", role: "charlie", text: "I will." },
      ],
      activity: [{
        id: "activity-1",
        type: "tool_result",
        timestamp: "2026-10-08T12:00:00Z",
        payload: { message: "Completed the task." },
      }],
    });

    expect(snapshot?.sessionId).toBe("voice_launch-a");
    expect(snapshot?.conversationHistory).toEqual([
      { id: "message-1", role: "you", text: "Keep this chat visible." },
      { id: "message-2", role: "charlie", text: "I will." },
    ]);
    expect(snapshot?.activity).toMatchObject([
      { id: "activity-1", type: "tool_result", timestamp: "2026-10-08T12:00:00Z", summary: "Completed the task." },
    ]);
    expect(parseSnapshot({ revision: 5, title: "Malformed", session_id: 42 })).toBeNull();
  });

  it("reads the live scene endpoint and reports unsupported payloads", async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({ revision: 1, title: "Ready" }), { status: 200 }));
    await expect(loadScene(fetcher)).resolves.toMatchObject({ title: "Ready" });
    fetcher.mockResolvedValueOnce(new Response("{}", { status: 200 }));
    await expect(loadScene(fetcher)).rejects.toThrow("unsupported scene snapshot");
  });

  it("projects scene updates from SSE and ignores malformed events", () => {
    expect(parseRuntimeEvent({ type: "scene.updated", payload: { snapshot: { revision: 4, title: "Updated" }, text: "Hello there" } })?.snapshot?.title).toBe("Updated");
    expect(parseRuntimeEvent({ type: "transcript", payload: { text: "Hello there" } })?.text).toBe("Hello there");
    expect(parseRuntimeEvent({ type: "token", payload: { text: "world" } })?.text).toBe("world");
    expect(parseRuntimeEvent({ type: "audio_level", payload: { level: 1.4 } })?.level).toBe(1);
    expect(parseRuntimeEvent({ nope: true })).toBeNull();
  });

  it("retains validated research provenance and completion metrics from scene and SSE payloads", () => {
    const researchResult = {
      result_id: "result-1",
      text: "Alpha is licensed under MIT [S1].",
      query: "Is Alpha licensed under GPL-3.0?",
      answer: "Alpha is licensed under MIT [S1].",
      partial: false,
      completeness: "complete",
      termination_reason: "completed",
      stop_reason: "evidence-sufficient",
      source_urls: ["https://alpha.example/license", "file:///private/source"],
      search_result_count: 3,
      document_count: 1,
      evidence_count: 1,
      passage_count: 1,
      fact_count: 1,
      duration_ms: 12.5,
      coverage: [{
        id: "q1",
        question: "Is Alpha licensed under GPL-3.0?",
        required_fields: ["license_proposition:GPL-3.0"],
        supporting_claim_ids: [],
        refuting_claim_ids: ["p1"],
        status: "contradicted",
        conflict: false,
        missing_evidence: [],
      }],
      passages: [{
        id: "p1",
        source_id: "S1",
        source_url: "https://alpha.example/license",
        canonical_url: "https://alpha.example/license",
        document_hash: "abc123",
        text: "Alpha is licensed under MIT.",
        start_offset: 0,
        end_offset: 29,
        retrieved_at: "2026-10-08T00:00:00Z",
        published_at: null,
        source_class: "official",
      }, {
        id: "bad",
        source_id: "S1",
        source_url: "https://alpha.example/license",
        canonical_url: "https://alpha.example/license",
        document_hash: "abc123",
        text: "bad offsets",
        start_offset: -1,
        end_offset: 3,
        retrieved_at: "now",
        published_at: null,
        source_class: "official",
      }],
      claims: [{
        id: "answer-1",
        text: "Alpha is licensed under MIT.",
        subject: "Alpha",
        predicate: "license_proposition",
        value: "Alpha is licensed under MIT.",
        units: "",
        time_scope: "",
        evidence_passage_ids: ["p1"],
        relationship: "refutes",
        verification_status: "bound_to_validated_passage",
      }],
      verified_facts: [{
        candidate: "Alpha",
        aspect: "license",
        value: "MIT",
        quote: "Alpha is licensed under MIT.",
        source_id: "S1",
        url: "https://alpha.example/license",
      }],
      sources: [{ id: "S1", title: "Alpha license", url: "https://alpha.example/license", class: "official" }],
      completed_at: "2026-10-08T00:00:01Z",
    };

    const secondResearchResult = { ...researchResult, result_id: "result-2", query: "A second research task" };
    const snapshot = parseSnapshot({
      revision: 1,
      title: "Ready",
      research_result: secondResearchResult,
      research_results: [researchResult, secondResearchResult],
    });
    const event = parseRuntimeEvent({ type: "research_result", payload: researchResult });

    expect(snapshot?.researchResult).toMatchObject({
      documentCount: 1,
      evidenceCount: 1,
      passageCount: 1,
      durationMs: 12.5,
      terminationReason: "completed",
      coverage: [{ status: "contradicted", refutingClaimIds: ["p1"] }],
      claims: [{ relationship: "refutes", evidencePassageIds: ["p1"] }],
      passages: [{ id: "p1", sourceUrl: "https://alpha.example/license" }],
      sourceUrls: ["https://alpha.example/license"],
    });
    expect(snapshot?.researchResult?.passages).toHaveLength(1);
    expect(snapshot?.researchResults?.map((item) => item.id)).toEqual(["result-1", "result-2"]);
    expect(snapshot?.researchResult?.id).toBe("result-2");
    expect(event?.researchResult?.claims?.[0]?.evidencePassageIds).toEqual(["p1"]);
    expect(event?.researchResult?.verifiedFacts?.[0]?.value).toBe("MIT");
  });

  it("only reports command acceptance when the server explicitly confirms it", async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({ accepted: true }), { status: 200 }));
    await expect(postCommand("Check status", fetcher)).resolves.toBe(true);
    expect(JSON.parse(fetcher.mock.calls[0][1]?.body as string)).toEqual({ type: "submit_text", text: "Check status" });
    fetcher.mockResolvedValueOnce(new Response(JSON.stringify({ ok: true }), { status: 200 }));
    await expect(postCommand("Check status", fetcher)).resolves.toBe(false);
  });

  it("keeps gateway error details available to the chat surface", async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({ error: "All connection attempts failed" }), { status: 400 }));
    await expect(postCommand("Say hello", fetcher)).rejects.toThrow("All connection attempts failed");
  });
});
