import { describe, expect, test, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { WidgetContainer } from "./WidgetContainer";
import type { WidgetInstance } from "./widgetStore";

const widget = (widgetType: string): WidgetInstance => ({
  id: `widget-${widgetType}`,
  presentationIntentId: `intent-${widgetType}`,
  widgetType,
  taskId: null,
  title: "Test widget",
  summary: "Test summary",
  content: {},
  position: { x: 10, y: 10 },
  size: { width: 320, height: 190 },
  zone: "top_right",
  focused: true,
  minimized: false,
  pinned: false,
  autoDismissMs: 5000,
  expiresAt: Date.now() + 5000,
  pausedExpiry: false,
  createdAt: new Date().toISOString(),
  updatedAt: new Date().toISOString(),
  replaceKey: null,
  zIndex: 10,
});

const callbacks = {
  onFocus: vi.fn(), onDrag: vi.fn(), onResize: vi.fn(), onPin: vi.fn(), onUnpin: vi.fn(),
  onPauseExpiry: vi.fn(), onResumeExpiry: vi.fn(), onDismiss: vi.fn(),
};

describe("WidgetContainer renderer resolution", () => {
  test("marks media widget unavailable until semantic renderer exists", () => {
    render(<WidgetContainer widget={widget("media_control")} {...callbacks} />);
    expect(screen.getByText("WIDGET UNAVAILABLE")).toBeDefined();
    expect(screen.getByText(/not implemented/i)).toBeDefined();
  });

  test("uses shared unavailable renderer for unknown widget type", () => {
    render(<WidgetContainer widget={widget("future_metric")} {...callbacks} />);
    expect(screen.getByText("WIDGET UNAVAILABLE")).toBeDefined();
    expect(screen.getAllByText("future_metric")).toHaveLength(2);
  });
});
