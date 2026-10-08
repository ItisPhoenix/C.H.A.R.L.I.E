import { describe, expect, it } from "vitest";
import { readDashboardViewState, writeDashboardViewState } from "./sessionView";

describe("session-scoped dashboard view state", () => {
  it("restores a closed research panel and dismissed tabs only for their Charlie session", () => {
    const values = new Map<string, string>();
    const storage: Pick<Storage, "getItem" | "setItem"> = {
      getItem: (key) => values.get(key) ?? null,
      setItem: (key, value) => { values.set(key, value); },
    };
    const view = {
      chatOpen: true,
      drawerOpen: true,
      settingsOpen: true,
      activeSettingsSection: "privacy" as const,
      researchOpen: false,
      selectedResearchResultId: "result-1",
      dismissedResearchResultIds: ["result-2"],
    };

    writeDashboardViewState("voice_launch-a", view, storage);

    expect(readDashboardViewState("voice_launch-a", storage)).toEqual(view);
    expect(readDashboardViewState("voice_launch-b", storage)).toEqual({
      chatOpen: false,
      drawerOpen: false,
      settingsOpen: false,
      activeSettingsSection: "presence",
      researchOpen: false,
      selectedResearchResultId: null,
      dismissedResearchResultIds: [],
    });
  });
});
