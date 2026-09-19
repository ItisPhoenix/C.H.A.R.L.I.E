import { fireEvent, render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ChatWidget, MediaWidget, ResearchWidget, TaskWidget } from "./RuntimeWidgets";

describe("runtime widgets", () => {
  it("renders conversation as sender-led text and supports dismissal", () => {
    const onDismiss = vi.fn();
    const onSend = vi.fn(() => true);
    const view = render(<ChatWidget conversation={{ userText: "Show chat", responseText: "Ready.", streaming: false, turnId: "t1" }}
      thinking="" onDismiss={onDismiss} onSend={onSend} />);
    expect(view.getByText("YOU")).toBeTruthy();
    expect(view.getByText("CHARLIE")).toBeTruthy();
    expect(view.getByText("Ready.")).toBeTruthy();
    expect(view.queryByText(/bubble/i)).toBeNull();
    fireEvent.change(view.getByRole("textbox", { name: "Message Charlie" }), { target: { value: "Follow up" } });
    fireEvent.click(view.getByRole("button", { name: "Send" }));
    expect(onSend).toHaveBeenCalledWith("Follow up");
    fireEvent.click(view.getByRole("button", { name: /close chat/i }));
    expect(onDismiss).toHaveBeenCalledOnce();
  });

  it("keeps task and research summaries bounded and actionable", () => {
    const onFocus = vi.fn();
    const taskView = render(<TaskWidget tasks={[{ id: "task-1", title: "Index notes", status: "running" }]}
      onFocus={onFocus} />);
    fireEvent.click(taskView.getByRole("button", { name: /open task index notes/i }));
    expect(onFocus).toHaveBeenCalledWith("task-1");
    const researchView = render(<ResearchWidget research={{ progress: null, result: {
      summary: "Three findings.", findings: [{ title: "One" }], sources: [{ url: "https://example.test" }],
    } }} />);
    expect(researchView.getByText("RESEARCH COMPLETE")).toBeTruthy();
    expect(researchView.getByText(/1 finding · 1 source/)).toBeTruthy();
  });

  it("keeps fixture media controls local while exposing real control callbacks", () => {
    const onControl = vi.fn();
    const view = render(<MediaWidget localControls onControl={onControl}
      media={{ available: true, title: "Night Drive", artist: "Fixture Session", status: "playing",
        position_seconds: 42, duration_seconds: 214, art_uri: "data:image/svg+xml,test" }} />);
    expect(view.container.querySelector("img.runtime-media-widget__art")).toBeTruthy();
    const timeline = view.getByRole("slider", { name: "0:42 of 3:34" });
    fireEvent.change(timeline, { target: { value: "72" } });
    expect(view.getByRole("slider", { name: "1:12 of 3:34" })).toBeTruthy();
    fireEvent.click(view.getByRole("button", { name: "Pause" }));
    expect(view.getByRole("button", { name: "Play" })).toBeTruthy();
    fireEvent.click(view.getByRole("button", { name: "Next track" }));
    expect(onControl).toHaveBeenNthCalledWith(1, "play_pause");
    expect(onControl).toHaveBeenNthCalledWith(2, "next_track");
  });
});
