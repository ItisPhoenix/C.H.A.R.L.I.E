import { beforeEach, describe, expect, it } from "vitest";
import { useLayoutStore } from "./layoutStore";

describe("layout store", () => {
  beforeEach(() => {
    useLayoutStore.getState().resetAll();
  });

  it("resizes a panel without letting it exceed the HUD bounds", () => {
    useLayoutStore.getState().resize("chat", 9_000, 9_000);

    const chat = useLayoutStore.getState().panels.chat;
    expect(chat.w).toBe(1_464);
    expect(chat.h).toBe(926);
  });

  it("keeps layouts separate for each display profile", () => {
    useLayoutStore.getState().setProfile("laptop");
    useLayoutStore.getState().move("chat", 210, 150);
    useLayoutStore.getState().setProfile("desktop");

    expect(useLayoutStore.getState().panels.chat.x).toBe(72);

    useLayoutStore.getState().setProfile("laptop");
    expect(useLayoutStore.getState().panels.chat.x).toBe(210);
  });

  it("stores panel layouts in browser storage", () => {
    useLayoutStore.getState().move("chat", 300, 120);

    expect(window.localStorage.getItem("charlie.dashboard.layouts.v1")).toContain('"x":300');
  });
});
