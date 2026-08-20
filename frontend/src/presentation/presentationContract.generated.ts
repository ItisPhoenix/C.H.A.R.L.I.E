// AUTO-GENERATED — DO NOT EDIT
// Source: shared/presentation_contract.json

export const CONTRACT_VERSION = 1 as const;
export const SURFACE_SCHEMA_VERSION = 1 as const;

export const PRESENTATION_KINDS = [
  "silent",
  "caption",
  "notification",
  "widget",
  "composed_surface",
  "workspace",
  "overlay",
  "attention"
] as const;
export type PresentationKind = (typeof PRESENTATION_KINDS)[number];

export const SURFACE_PRIMITIVES = [
  "heading",
  "text",
  "metric",
  "progress",
  "list",
  "table",
  "chart",
  "timeline",
  "image",
  "source",
  "status",
  "badge",
  "divider",
  "action",
  "layout",
  "spatial_map",
  "density_heatmap",
  "telemetry_gauges",
  "process_telemetry",
  "map_placeholder"
] as const;
export type PrimitiveType = (typeof SURFACE_PRIMITIVES)[number];

export const LAYOUT_TYPES = [
  "stack",
  "row",
  "grid",
  "columns",
  "section"
] as const;
export type LayoutType = (typeof LAYOUT_TYPES)[number];

export const DISMISS_POLICIES = [
  "immediate",
  "timed",
  "manual",
  "persistent",
  "task_lifetime"
] as const;
export type DismissPolicy = (typeof DISMISS_POLICIES)[number];

export const PREFERRED_ZONES = [
  "contextual",
  "top_right",
  "bottom_right",
  "top_left",
  "bottom_left",
  "center"
] as const;
export type PreferredZone = (typeof PREFERRED_ZONES)[number];

export const ANCHOR_TARGETS = [
  "core",
  "workspace",
  "screen",
  "widget"
] as const;
export type AnchorTarget = (typeof ANCHOR_TARGETS)[number];

export const CORE_STATES = [
  "idle",
  "listening",
  "thinking",
  "speaking",
  "working"
] as const;
export type CoreState = (typeof CORE_STATES)[number];

export const CORE_POSITIONS = [
  "center",
  "dock_bottom_right"
] as const;
export type CorePosition = (typeof CORE_POSITIONS)[number];

export const PRESENTATION_ACTIONS = [
  "open_workspace",
  "focus_workspace",
  "minimize_workspace",
  "restore_workspace",
  "close_workspace",
  "clear_workspaces",
  "upsert_widget",
  "focus_widget",
  "drag_widget",
  "resize_widget",
  "pin_widget",
  "unpin_widget",
  "pause_expiry",
  "resume_expiry",
  "dismiss_widget",
  "tick_auto_dismiss",
  "clear_screen",
  "clear_everything",
  "restore_everything",
  "focused_escape",
  "surface_action"
] as const;
export type PresentationAction = (typeof PRESENTATION_ACTIONS)[number];

export const CORE_RULES = {
  "no_workspace": {
    "position": "center",
    "show_status_bar": true,
    "show_indicator_dots": true,
    "show_state_label": true,
    "show_subtext": true
  },
  "active_workspace": {
    "position": "dock_bottom_right",
    "show_status_bar": false,
    "show_indicator_dots": false,
    "show_state_label": false,
    "show_subtext": false
  }
} as const;

export const WORKSPACES_METADATA = {
  "research": {
    "aliases": [],
    "implemented": true,
    "renderer": "ResearchWorkspace",
    "renderer_module": "frontend/src/scene/workspaces/ResearchWorkspace.tsx",
    "renderer_export": "ResearchWorkspace",
    "spatial": false,
    "core_position": "dock_bottom_right",
    "dismiss_policy": "persistent",
    "description": "Web research findings, citations, and synthesized intelligence"
  },
  "briefing": {
    "aliases": [],
    "implemented": true,
    "renderer": "BriefingWorkspace",
    "renderer_module": "frontend/src/scene/workspaces/BriefingWorkspace.tsx",
    "renderer_export": "BriefingWorkspace",
    "spatial": false,
    "core_position": "dock_bottom_right",
    "dismiss_policy": "persistent",
    "description": "Daily intelligence briefing, calendar, and summary digest"
  },
  "system": {
    "aliases": [
      "telemetry"
    ],
    "implemented": true,
    "renderer": "SystemWorkspace",
    "renderer_module": "frontend/src/scene/workspaces/SystemWorkspace.tsx",
    "renderer_export": "SystemWorkspace",
    "spatial": false,
    "core_position": "dock_bottom_right",
    "dismiss_policy": "persistent",
    "description": "Comprehensive system telemetry, process health, and subsystem status"
  },
  "tasks": {
    "aliases": [
      "task",
      "plans"
    ],
    "implemented": true,
    "renderer": "TasksWorkspace",
    "renderer_module": "frontend/src/scene/workspaces/TasksWorkspace.tsx",
    "renderer_export": "TasksWorkspace",
    "spatial": false,
    "core_position": "dock_bottom_right",
    "dismiss_policy": "persistent",
    "description": "Runtime task journal, execution plans, and progress tracking"
  },
  "map": {
    "aliases": [
      "spatial"
    ],
    "implemented": true,
    "renderer": "MapWorkspace",
    "renderer_module": "frontend/src/scene/workspaces/MapWorkspace.tsx",
    "renderer_export": "MapWorkspace",
    "spatial": true,
    "core_position": "dock_bottom_right",
    "dismiss_policy": "persistent",
    "description": "Edge-to-edge geospatial map and spatial intelligence canvas"
  },
  "vision": {
    "aliases": [
      "camera"
    ],
    "implemented": true,
    "renderer": "VisionWorkspace",
    "renderer_module": "frontend/src/scene/workspaces/VisionWorkspace.tsx",
    "renderer_export": "VisionWorkspace",
    "spatial": false,
    "core_position": "dock_bottom_right",
    "dismiss_policy": "persistent",
    "description": "Visual perception, screen grounding, and camera feed"
  },
  "document": {
    "aliases": [
      "report",
      "file"
    ],
    "implemented": true,
    "renderer": "DocumentWorkspace",
    "renderer_module": "frontend/src/scene/workspaces/DocumentWorkspace.tsx",
    "renderer_export": "DocumentWorkspace",
    "spatial": false,
    "core_position": "dock_bottom_right",
    "dismiss_policy": "persistent",
    "description": "Document viewer, report reader, and file artifact browser"
  },
  "terminal": {
    "aliases": [],
    "implemented": true,
    "renderer": "TerminalWorkspace",
    "renderer_module": "frontend/src/scene/workspaces/TerminalWorkspace.tsx",
    "renderer_export": "TerminalWorkspace",
    "spatial": false,
    "core_position": "dock_bottom_right",
    "dismiss_policy": "persistent",
    "description": "Interactive Windows PTY shell and terminal session"
  },
  "conversation": {
    "aliases": [
      "chat"
    ],
    "implemented": true,
    "renderer": "ConversationWorkspace",
    "renderer_module": "frontend/src/scene/workspaces/ConversationWorkspace.tsx",
    "renderer_export": "ConversationWorkspace",
    "spatial": false,
    "core_position": "dock_bottom_right",
    "dismiss_policy": "persistent",
    "description": "Full-session conversation history and dialogue stream"
  },
  "composed_surface": {
    "aliases": [],
    "implemented": true,
    "renderer": "SurfaceComposer",
    "renderer_module": "frontend/src/composer/SurfaceComposer.tsx",
    "renderer_export": "SurfaceComposer",
    "spatial": false,
    "core_position": "dock_bottom_right",
    "dismiss_policy": "persistent",
    "description": "Dynamic schema-driven composed workspace canvas"
  }
} as const;
export type WorkspaceType = keyof typeof WORKSPACES_METADATA;

export const WIDGETS_METADATA = {
  "system_metric": {
    "aliases": [
      "system"
    ],
    "implemented": true,
    "renderer": "SystemWidget",
    "renderer_module": "frontend/src/scene/widgets/SystemWidget.tsx",
    "renderer_export": "SystemWidget",
    "default_dismiss_policy": "timed",
    "default_auto_dismiss_ms": 5000,
    "default_zone": "top_right",
    "supports": {
      "drag": true,
      "resize": true,
      "pin": true,
      "auto_dismiss": true
    },
    "description": "System hardware telemetry (CPU, RAM, GPU, Disk, Network, Battery)"
  },
  "composed_surface": {
    "aliases": [],
    "implemented": true,
    "renderer": "SurfaceComposer",
    "renderer_module": "frontend/src/composer/SurfaceComposer.tsx",
    "renderer_export": "SurfaceComposer",
    "default_dismiss_policy": "timed",
    "default_auto_dismiss_ms": 8000,
    "default_zone": "top_right",
    "supports": {
      "drag": true,
      "resize": true,
      "pin": true,
      "auto_dismiss": true
    },
    "description": "Dynamic schema-driven composed widget surface"
  },
  "media_control": {
    "aliases": [
      "media",
      "media player",
      "music",
      "music player"
    ],
    "implemented": true,
    "renderer": "WidgetContainer.generic_fallback",
    "renderer_module": "frontend/src/layout/WidgetContainer.tsx",
    "default_dismiss_policy": "timed",
    "default_auto_dismiss_ms": 6000,
    "default_zone": "bottom_right",
    "supports": {
      "drag": true,
      "resize": true,
      "pin": true,
      "auto_dismiss": true
    },
    "description": "Media playback and audio volume controller"
  },
  "file_viewer": {
    "aliases": [
      "file"
    ],
    "implemented": true,
    "renderer": "WidgetContainer.generic_fallback",
    "renderer_module": "frontend/src/layout/WidgetContainer.tsx",
    "default_dismiss_policy": "timed",
    "default_auto_dismiss_ms": 5000,
    "default_zone": "top_right",
    "supports": {
      "drag": true,
      "resize": true,
      "pin": true,
      "auto_dismiss": true
    },
    "description": "Directory listing and file inspection widget"
  }
} as const;
export type WidgetType = keyof typeof WIDGETS_METADATA;

export const OVERLAYS_METADATA = {
  "settings": {
    "implemented": true,
    "renderer": "SettingsModal",
    "renderer_module": "frontend/src/scene/SettingsModal.tsx",
    "renderer_export": "SettingsModal",
    "dismiss_policy": "manual",
    "anchor": "screen",
    "description": "Configuration, voice settings, appearance, and doctor diagnostics"
  }
} as const;
export type OverlayType = keyof typeof OVERLAYS_METADATA;

