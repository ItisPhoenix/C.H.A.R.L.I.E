import type { SettingsSectionId } from "./SettingsSectionNav";

export type DashboardViewState = {
  chatOpen: boolean;
  drawerOpen: boolean;
  settingsOpen: boolean;
  activeSettingsSection: SettingsSectionId;
  researchOpen: boolean;
  selectedResearchResultId: string | null;
  dismissedResearchResultIds: string[];
};

const emptyDashboardViewState: DashboardViewState = {
  chatOpen: false,
  drawerOpen: false,
  settingsOpen: false,
  activeSettingsSection: "presence",
  researchOpen: false,
  selectedResearchResultId: null,
  dismissedResearchResultIds: [],
};

type ViewStorage = Pick<Storage, "getItem" | "setItem">;

function sessionKey(sessionId: string): string {
  return `charlie-dashboard-view:${sessionId}`;
}

export function readDashboardViewState(sessionId: string, storage: ViewStorage = window.localStorage): DashboardViewState {
  if (!sessionId) return { ...emptyDashboardViewState };
  try {
    const saved: unknown = JSON.parse(storage.getItem(sessionKey(sessionId)) ?? "null");
    if (!saved || typeof saved !== "object") return { ...emptyDashboardViewState };
    const value = saved as Partial<DashboardViewState>;
    return {
      chatOpen: value.chatOpen === true,
      drawerOpen: value.drawerOpen === true,
      settingsOpen: value.settingsOpen === true,
      activeSettingsSection: ["presence", "voice", "runtime", "privacy"].includes(value.activeSettingsSection ?? "")
        ? value.activeSettingsSection as SettingsSectionId
        : "presence",
      researchOpen: value.researchOpen === true,
      selectedResearchResultId: typeof value.selectedResearchResultId === "string" ? value.selectedResearchResultId : null,
      dismissedResearchResultIds: Array.isArray(value.dismissedResearchResultIds)
        ? value.dismissedResearchResultIds.filter((item): item is string => typeof item === "string").slice(-10)
        : [],
    };
  } catch {
    return { ...emptyDashboardViewState };
  }
}

export function writeDashboardViewState(
  sessionId: string,
  state: DashboardViewState,
  storage: ViewStorage = window.localStorage,
): void {
  if (!sessionId) return;
  try {
    storage.setItem(sessionKey(sessionId), JSON.stringify(state));
  } catch {
    // The runtime remains authoritative if browser storage is unavailable.
  }
}
