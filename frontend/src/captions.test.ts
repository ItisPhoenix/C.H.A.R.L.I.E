import { describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { createElement } from "react";
import { ApprovalPopup, LiveCaption } from "./App";
import { parseRuntimeEvent, parseSnapshot } from "./runtime";

vi.mock("./CrispThinkingOrb", () => ({ CrispThinkingOrb: () => null }));
vi.mock("voice-glow", () => ({ VoiceBeam: () => null }));

describe("live captions", () => {
  const lines = [
    { id: "stt", text: "Close calculator", kind: "stt" as const },
    { id: "tts", text: "Calculator is closed", kind: "tts" as const },
  ];

  it("shows full text immediately and alternates brightness without visible labels", () => {
    for (const speaker of ["stt", "tts"] as const) {
      const host = document.createElement("div");
      host.innerHTML = renderToStaticMarkup(createElement(LiveCaption, { lines, speaker }));
      expect(host.textContent).toBe("Close calculatorCalculator is closed");
      expect(host.querySelector(".is-current")?.textContent).toBe(speaker === "stt" ? lines[0].text : lines[1].text);
      expect(host.querySelectorAll(".live-caption__line")).toHaveLength(2);
    }
  });

  it("preserves interim recognition flag at the event boundary", () => {
    expect(parseRuntimeEvent({ type: "transcript", payload: { text: "close", partial: true } })?.partial).toBe(true);
  });
});

describe("approval popup", () => {
  it("restores only text-origin approvals from the canonical scene", () => {
    const scene = { revision: 1, title: "Waiting", approval: {
      request_id: "approval-1", channel: "web", message: "Close the app", operation_preview: "Close Calculator",
    } };
    expect(parseSnapshot(scene)?.pendingApproval?.requestId).toBe("approval-1");
    expect(parseSnapshot({ ...scene, approval: { ...scene.approval, channel: "telegram" } })?.pendingApproval).toBeUndefined();
    expect(parseSnapshot({ ...scene, approval: null })?.pendingApproval).toBeUndefined();
  });
  it("shows the pending action and accessible decisions without technical tooling text", () => {
    const host = document.createElement("div");
    host.innerHTML = renderToStaticMarkup(createElement(ApprovalPopup, {
      title: "Close Calculator", reason: "Closing the app needs your approval.",
      busy: false, error: "", onDecision: () => {},
    }));
    expect(host.querySelector('[role="dialog"]')).not.toBeNull();
    expect(host.querySelector("h2")?.textContent).toBe("Close Calculator");
    expect([...host.querySelectorAll("button")].map((button) => button.textContent)).toEqual(["Decline", "Approve"]);
  });
  it("disables both decisions while acknowledgement is pending", () => {
    const host = document.createElement("div");
    host.innerHTML = renderToStaticMarkup(createElement(ApprovalPopup, {
      title: "Close Calculator", reason: "Approval needed", busy: true, error: "", onDecision: () => {},
    }));
    expect(host.querySelectorAll("button:disabled")).toHaveLength(2);
  });
});
