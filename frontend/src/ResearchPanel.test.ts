import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { expect, it, vi } from "vitest";
import { ChatBubbles, ResearchProgressWidget } from "./App";
import { ResearchPanel } from "./ResearchPanel";

vi.mock("voice-glow", () => ({ VoiceBeam: () => null }));

it("keeps research tables and source links inline in conversation", () => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  const host = document.createElement("div");
  const root = createRoot(host);
  const result = {
    id: "report", query: "Compare options", text: "",
    answer: "### Answer\n\n**Finding:** *supported*\n\n| Option | Finding |\n| --- | --- |\n| A | Supported [S1] |",
    sources: [{ id: "S1", title: "Evidence", url: "https://example.com/evidence" }],
  };
  try {
    act(() => root.render(createElement(ChatBubbles, {
      bubbles: [{ id: "question", role: "you", text: result.query }],
      researchResults: [result],
    })));
    expect(host.querySelector(".chat-bubbles table")).not.toBeNull();
    expect(host.querySelector(".research-panel--inline")).not.toBeNull();
    expect(host.querySelector(".research-body h2")).toBeNull();
    expect(host.querySelector("strong")?.textContent).toContain("Finding:");
    expect(host.querySelector("em")?.textContent).toBe("supported");
    expect(host.querySelector('[aria-label="Close research answer"]')).toBeNull();
    expect(host.querySelector('.research-citation')?.getAttribute("href")).toBe("https://example.com/evidence");
  } finally {
    act(() => root.unmount());
    vi.unstubAllGlobals();
  }
});

it("labels quick web lookups as Search and explicit reports as Research", () => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  const host = document.createElement("div");
  const root = createRoot(host);
  const result = { id: "report", query: "Current release", text: "A grounded answer.", answer: "A grounded answer." };
  try {
    act(() => root.render(createElement(ResearchPanel, {
      result: { ...result, mode: "quick" }, onClose: vi.fn(),
    })));
    expect(host.querySelector(".research-label")?.textContent).toBe("Search");
    expect(host.querySelector('[aria-label="Search answer"]')).not.toBeNull();

    act(() => root.render(createElement(ResearchPanel, {
      result: { ...result, mode: "standard" }, onClose: vi.fn(),
    })));
    expect(host.querySelector(".research-label")?.textContent).toBe("Research");
    expect(host.querySelector('[aria-label="Research answer"]')).not.toBeNull();
  } finally {
    act(() => root.unmount());
    vi.unstubAllGlobals();
  }
});

it("joins streamed answers by turn identity and preserves distinct same-text reports", () => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  const host = document.createElement("div");
  const root = createRoot(host);
  const query = "Research the official guidance";
  const answer = "Use approved protection.";
  const results = [
    { id: "report-1", turnId: "turn-1", query, text: answer, answer },
    { id: "report-2", turnId: "turn-2", query, text: answer, answer },
  ];
  try {
    act(() => root.render(createElement(ChatBubbles, {
      bubbles: [
        { id: "turn:turn-1:you", role: "you", text: query, turnId: "turn-1" },
        { id: "research:report-1", role: "charlie", text: answer, turnId: "turn-1" },
        { id: "turn:turn-1", role: "charlie", text: answer, turnId: "turn-1" },
        { id: "turn:turn-2:you", role: "you", text: query, turnId: "turn-2" },
        { id: "turn:turn-2", role: "charlie", text: answer, turnId: "turn-2" },
      ],
      researchResults: results,
    })));
    expect(host.querySelectorAll(".research-panel--inline")).toHaveLength(2);
    expect(host.querySelectorAll(".chat-bubble--research")).toHaveLength(2);
  } finally {
    act(() => root.unmount());
    vi.unstubAllGlobals();
  }
});

it("shows actual research work in a small widget and reports disconnection truthfully", () => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  const host = document.createElement("div");
  const root = createRoot(host);
  const progress = { type: "research_progress", query: "Compare API clients", text: "Reading source 2/8", stage: "reading" };
  try {
    act(() => root.render(createElement(ResearchProgressWidget, { progress, steps: [progress], connected: true })));
    expect(host.querySelector('[role="status"]')?.textContent).toContain("Reading source 2/8");
    expect(host.querySelector('.research-progress-steps li[aria-current="step"] span')?.textContent).toBe("reading");
    expect(host.textContent).toContain("Compare API clients");
    act(() => root.render(createElement(ResearchProgressWidget, { progress, connected: false })));
    expect(host.textContent).toContain("Connection lost. Last update: Reading source 2/8");
    act(() => root.render(null));
    expect(host.querySelector('[role="status"]')).toBeNull();
  } finally {
    act(() => root.unmount());
    vi.unstubAllGlobals();
  }
});

it("styles opened voice reports with the real acquisition and citation counts", () => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  const host = document.createElement("div");
  const root = createRoot(host);
  try {
    act(() => root.render(createElement(ResearchPanel, {
      result: {
        id: "voice-report", query: "Compare two approaches", text: "### Answer\n\nA supported finding.", channel: "voice",
        searchResultCount: 32, documentCount: 30,
        sources: [{ id: "S1", title: "Evidence", url: "https://example.com/evidence" }],
      },
      onClose: vi.fn(),
    })));
    expect(host.querySelector(".research-panel--voice")).not.toBeNull();
    expect(host.querySelector(".research-header-badges")?.textContent).toContain("Research");
    expect(host.querySelector(".research-header-badges")?.textContent).not.toContain("Voice research");
    expect(host.querySelector(".research-body h2")).toBeNull();
    expect([...host.querySelectorAll(".research-resource-count")].map((item) => item.textContent?.replace(/\s+/g, " ").trim()))
      .toEqual(["32 links found", "30 pages read"]);
    expect(host.querySelector(".research-acquisition-details > details summary")?.textContent?.replace(/\s+/g, " ").trim()).toBe("1 source cited");
    expect(host.querySelectorAll(".research-resource-items")).toHaveLength(0);
  } finally {
    act(() => root.unmount());
    vi.unstubAllGlobals();
  }
});

it("shows distinct expandable search, read-page, and citation records in voice reports", () => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  const host = document.createElement("div");
  const root = createRoot(host);
  try {
    act(() => root.render(createElement(ResearchPanel, {
      result: {
        id: "voice-report", query: "Research disclosure guidance", text: "A finding [S1].", channel: "voice",
        searchResultCount: 2, documentCount: 1,
        searchResults: [
          { title: "Standards guidance", url: "https://w3.org/guidance", domain: "w3.org" },
          { title: "Access board guidance", url: "https://access-board.example/disclosures", domain: "access-board.example" },
        ],
        readPages: [{ title: "Disclosure guidance", url: "https://w3.org/guidance", domain: "w3.org" }],
        sources: [{ id: "S1", title: "Disclosure guidance", url: "https://w3.org/guidance", class: "official" }],
      },
      onClose: vi.fn(),
    })));
    expect([...host.querySelectorAll(".research-acquisition-details > details summary")].map((item) => item.textContent?.replace(/\s+/g, " ").trim())).toEqual([
      "2 links found", "1 page read", "1 source cited",
    ]);
    expect(host.querySelector('.research-resource-items [href="https://access-board.example/disclosures"] strong')?.textContent).toBe("Access board guidance");
    expect(host.querySelector('.research-resource-items [href="https://w3.org/guidance"] small')?.textContent).toBe("https://w3.org/guidance");
    expect(host.querySelectorAll(".research-acquisition-details details")).toHaveLength(3);
  } finally {
    act(() => root.unmount());
    vi.unstubAllGlobals();
  }
});

it("shows only an answer-linked source excerpt and labels unknown publishers", () => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  const host = document.createElement("div");
  const root = createRoot(host);
  try {
    act(() => root.render(createElement(ResearchPanel, {
      result: {
        id: "voice-report", query: "A relevant finding", text: "A relevant finding [S1].", channel: "voice",
        sources: [{ id: "S1", title: "Publisher page", url: "https://publisher.example/page", class: "unknown" }],
        passages: [
          { id: "p1", sourceId: "S1", sourceUrl: "https://publisher.example/page", canonicalUrl: "https://publisher.example/page", documentHash: "a", text: "Orders ship in recyclable cardboard packaging.", startOffset: 0, endOffset: 46, retrievedAt: "now", publishedAt: null, sourceClass: "unknown" },
          { id: "p2", sourceId: "S1", sourceUrl: "https://publisher.example/page", canonicalUrl: "https://publisher.example/page", documentHash: "a", text: "A relevant finding appears in this source.", startOffset: 50, endOffset: 91, retrievedAt: "now", publishedAt: null, sourceClass: "unknown" },
        ],
        claims: [{ id: "c1", text: "A relevant finding", subject: "", predicate: "", value: "", units: "", timeScope: "", evidencePassageIds: ["p2"], relationship: "supports", verificationStatus: "supported" }],
      },
      onClose: vi.fn(),
    })));
    expect(host.querySelector(".research-source-chip")?.textContent).toBe("Unverified");
    expect(host.querySelector(".research-source-excerpt")?.textContent).toBe("A relevant finding appears in this source.");
    expect(host.textContent).not.toContain("recyclable cardboard packaging");
  } finally {
    act(() => root.unmount());
    vi.unstubAllGlobals();
  }
});

it("renders adjacent headings and findings as real lists, with one concise coverage section", () => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  const host = document.createElement("div");
  const root = createRoot(host);
  const query = "Compare these products. Check their exact variants and memory.";
  const result = {
    id: "result", query, text: "", partial: true,
    answer: "## Verified findings\n### Widget ZX1234AB [S1]\n- **RAM:** 16 GB [S1]\n- **VRAM:** 8 GB [S1]\n\n## Coverage gaps\nUnresolved: upgradeability",
    sources: [
      { id: "S1", title: "Widget specifications", url: "https://vendor.example/item", class: "retailer" },
      { id: "S2", title: "Official memory specifications", url: "https://maker.example/specs", class: "official" },
    ],
    passages: [
      { id: "p1", sourceId: "S1", sourceUrl: "https://vendor.example/item", canonicalUrl: "https://vendor.example/item", documentHash: "a", text: "Verified source excerpt for the vendor page.", startOffset: 0, endOffset: 45, retrievedAt: "now", publishedAt: null, sourceClass: "retailer" },
      { id: "p2", sourceId: "S2", sourceUrl: "https://maker.example/specs", canonicalUrl: "https://maker.example/specs", documentHash: "b", text: "Actual manufacturer excerpt with the supported specification.", startOffset: 0, endOffset: 59, retrievedAt: "now", publishedAt: null, sourceClass: "official" },
    ],
    gaps: [`Unresolved: ${query}: ram_upgradeable`],
    coverage: [{ id: "q1", question: `${query}: ram_upgradeable`, requiredFields: ["ram_upgradeable"],
      status: "unresolved" as const, supportingClaimIds: [], refutingClaimIds: [], conflict: false, missingEvidence: [] }],
  };
  try {
    act(() => root.render(createElement(ResearchPanel, {
      result, onClose: vi.fn(),
    })));
    expect(host.querySelector("header h2")?.textContent).toBe("Compare these products");
    expect(host.querySelector("header h2")?.getAttribute("title")).toBe(query);
    expect(host.querySelectorAll(".research-body > ul li")).toHaveLength(2);
    expect(host.querySelector(".research-body h2")?.textContent).toBe("Verified findings");
    expect(host.querySelector(".research-body h3")?.textContent).toContain("Widget ZX1234AB");
    expect(host.querySelector(".research-gaps li")?.textContent).toBe("ram upgradeable");
    expect(host.querySelectorAll(".research-citation")).toHaveLength(3);
    expect(host.textContent).not.toContain("Coverage gaps");
    expect(host.querySelector(".research-tabs--panel")).toBeNull();
    expect(host.querySelector(".research-sources a")?.getAttribute("href")).toBe("https://vendor.example/item");
    expect(host.querySelector(".research-source-url")?.textContent).toBe("https://vendor.example/item");
    expect(host.querySelector(".research-source-chip")?.textContent).toBe("Retailer");
    expect(host.querySelectorAll(".research-source-excerpt")).toHaveLength(2);
    expect(host.querySelector('[href="https://maker.example/specs"] .research-source-domain')?.textContent).toBe("maker.example");
    expect(host.querySelector('[href="https://maker.example/specs"] .research-source-excerpt')?.textContent).toBe("Actual manufacturer excerpt with the supported specification.");
    expect(host.textContent).not.toContain("description");
  } finally {
    act(() => root.unmount());
    vi.unstubAllGlobals();
  }
});

it("keeps quick Search concise while retaining expandable source details", () => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  const host = document.createElement("div");
  const root = createRoot(host);
  const result = {
    id: "quick", query: "What is the latest stable Node.js LTS release?", text: "",
    mode: "quick" as const, partial: true,
    answer: "**Node.js v24.21.0** is the latest LTS release [S1].",
    gaps: ["Unresolved: release date"],
    sources: [{ id: "S1", title: "Node.js downloads", url: "https://nodejs.org/en/download/current" }],
    searchResultCount: 1, documentCount: 1,
  };
  try {
    act(() => root.render(createElement(ResearchPanel, { result, onClose: vi.fn() })));
    expect(host.querySelector(".research-label")?.textContent).toBe("Search");
    expect(host.querySelector(".research-answer-list")).toBeNull();
    expect(host.querySelector(".research-body p")?.textContent).toBe("Node.js v24.21.0 is the latest LTS release 1.");
    expect(host.querySelector(".research-body p strong")?.textContent).toBe("Node.js v24.21.0");
    expect(host.querySelectorAll(".research-citation")).toHaveLength(1);
    expect(host.textContent).not.toContain("*");
    expect(host.querySelector(".research-gaps")).toBeNull();
    expect(host.querySelector(".research-acquisition-details")).not.toBeNull();
  } finally {
    act(() => root.unmount());
    vi.unstubAllGlobals();
  }
});
