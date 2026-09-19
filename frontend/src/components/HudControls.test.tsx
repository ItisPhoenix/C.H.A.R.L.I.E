import { fireEvent, render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { HudControls } from "./HudControls";

describe("HudControls", () => {
  it("sends PTT lifecycle commands and opens conversation", () => {
    const onPttStart = vi.fn();
    const onPttStop = vi.fn();
    const onPttCancel = vi.fn();
    const onOpenConversation = vi.fn();
    const view = render(
      <HudControls
        pttDisabled={false}
        conversationDisabled={false}
        pressed={false}
        onPttStart={onPttStart}
        onPttStop={onPttStop}
        onPttCancel={onPttCancel}
        onOpenConversation={onOpenConversation}
      />,
    );

    const ptt = view.getByRole("button", { name: "Hold to talk" });
    fireEvent.pointerDown(ptt);
    fireEvent.pointerUp(ptt);
    expect(onPttStart).toHaveBeenCalledOnce();
    expect(onPttStop).toHaveBeenCalledOnce();
    fireEvent.click(view.getByRole("button", { name: "Open conversation" }));
    expect(onOpenConversation).toHaveBeenCalledOnce();
    expect(onPttCancel).not.toHaveBeenCalled();
  });

  it("truthfully disables controls while runtime is unavailable", () => {
    const view = render(
      <HudControls
        pttDisabled
        conversationDisabled
        pressed={false}
        onPttStart={vi.fn()}
        onPttStop={vi.fn()}
        onPttCancel={vi.fn()}
        onOpenConversation={vi.fn()}
      />,
    );
    expect(view.getByRole("button", { name: "Push to talk unavailable" }).getAttribute("disabled")).not.toBeNull();
    expect(view.getByRole("button", { name: "Open conversation unavailable" }).getAttribute("disabled")).not.toBeNull();
  });
});
