import { describe, expect, test, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { SettingsModal } from "./SettingsModal";

describe("SettingsModal Component", () => {
  test("renders nothing when isOpen is false", () => {
    const { container } = render(<SettingsModal isOpen={false} onClose={() => {}} />);
    expect(container.firstChild).toBeNull();
  });

  test("renders modal overlay and handles close click", () => {
    const onClose = vi.fn();
    render(<SettingsModal isOpen={true} onClose={onClose} />);

    expect(screen.getByText(/C.H.A.R.L.I.E. CONFIGURATION & SYSTEM SETTINGS/i)).toBeDefined();

    const closeBtn = screen.getByText("✕ CLOSE");
    fireEvent.click(closeBtn);
    expect(onClose).toHaveBeenCalled();
  });

  test("renders 15 settings categories in modal sidebar", () => {
    render(<SettingsModal isOpen={true} onClose={() => {}} />);

    const categories = [
      "All",
      "General",
      "Audio",
      "Appearance",
      "HUD",
      "Map",
      "Pet",
      "Models",
      "Memory",
      "Automation",
      "Privacy",
      "Tools / MCP",
      "Integrations",
      "System",
      "Developer",
      "Audit & Diagnostics",
    ];

    for (const cat of categories) {
      expect(screen.getByRole("button", { name: new RegExp(`^${cat.replace("/", "\\/")}$`, "i") })).toBeDefined();
    }
  });
});
