import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, test } from "vitest";
import { Panel } from "./Panel";
import { useLayoutStore } from "./layoutStore";

describe("Panel", () => {
  beforeEach(() => useLayoutStore.getState().resetAll());

  test("supports keyboard movement from its focused header", () => {
    useLayoutStore.getState().open("chat");
    render(<Panel id="chat" title="Conversation"><p>Body</p></Panel>);
    const heading = screen.getByRole("heading", { name: "Conversation" });
    const header = heading.parentElement as HTMLElement;

    fireEvent.keyDown(header, { key: "ArrowRight" });

    expect(useLayoutStore.getState().panels.chat.x).toBe(82);
  });

  test("panel action buttons minimize, reset, and close without starting a drag", () => {
    useLayoutStore.getState().open("chat");
    render(<Panel id="chat" title="Conversation"><p>Body</p></Panel>);

    fireEvent.click(screen.getByRole("button", { name: "Minimize" }));
    expect(useLayoutStore.getState().panels.chat.minimized).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Restore" }));
    fireEvent.click(screen.getByRole("button", { name: "Reset position" }));
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(useLayoutStore.getState().panels.chat.open).toBe(false);
  });
});
