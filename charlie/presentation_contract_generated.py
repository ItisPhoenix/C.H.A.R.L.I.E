# AUTO-GENERATED — DO NOT EDIT
# Source: shared/presentation_contract.json
"""Authoritative generated presentation enums, constants, and metadata."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Dict, Tuple

CONTRACT_VERSION: int = 1
SURFACE_SCHEMA_VERSION: int = 1


class PresentationKind(StrEnum):
    SILENT = "silent"
    CAPTION = "caption"
    NOTIFICATION = "notification"
    WIDGET = "widget"
    COMPOSED_SURFACE = "composed_surface"
    WORKSPACE = "workspace"
    OVERLAY = "overlay"
    ATTENTION = "attention"


class DismissPolicy(StrEnum):
    IMMEDIATE = "immediate"
    TIMED = "timed"
    MANUAL = "manual"
    PERSISTENT = "persistent"
    TASK_LIFETIME = "task_lifetime"


class PreferredZone(StrEnum):
    CONTEXTUAL = "contextual"
    TOP_RIGHT = "top_right"
    BOTTOM_RIGHT = "bottom_right"
    TOP_LEFT = "top_left"
    BOTTOM_LEFT = "bottom_left"
    CENTER = "center"


class AnchorTarget(StrEnum):
    CORE = "core"
    WORKSPACE = "workspace"
    SCREEN = "screen"
    WIDGET = "widget"


class PrimitiveType(StrEnum):
    HEADING = "heading"
    TEXT = "text"
    METRIC = "metric"
    PROGRESS = "progress"
    LIST = "list"
    TABLE = "table"
    CHART = "chart"
    TIMELINE = "timeline"
    IMAGE = "image"
    SOURCE = "source"
    STATUS = "status"
    BADGE = "badge"
    DIVIDER = "divider"
    ACTION = "action"
    LAYOUT = "layout"
    SPATIAL_MAP = "spatial_map"
    DENSITY_HEATMAP = "density_heatmap"
    TELEMETRY_GAUGES = "telemetry_gauges"
    PROCESS_TELEMETRY = "process_telemetry"
    MAP_PLACEHOLDER = "map_placeholder"


class LayoutType(StrEnum):
    STACK = "stack"
    ROW = "row"
    GRID = "grid"
    COLUMNS = "columns"
    SECTION = "section"


PRESENTATION_KINDS: Tuple[str, ...] = ('silent', 'caption', 'notification', 'widget', 'composed_surface', 'workspace', 'overlay', 'attention')
SURFACE_PRIMITIVES: Tuple[str, ...] = ('heading', 'text', 'metric', 'progress', 'list', 'table', 'chart', 'timeline', 'image', 'source', 'status', 'badge', 'divider', 'action', 'layout', 'spatial_map', 'density_heatmap', 'telemetry_gauges', 'process_telemetry', 'map_placeholder')
LAYOUT_TYPES: Tuple[str, ...] = ('stack', 'row', 'grid', 'columns', 'section')
DISMISS_POLICIES: Tuple[str, ...] = ('immediate', 'timed', 'manual', 'persistent', 'task_lifetime')
PREFERRED_ZONES: Tuple[str, ...] = ('contextual', 'top_right', 'bottom_right', 'top_left', 'bottom_left', 'center')
ANCHOR_TARGETS: Tuple[str, ...] = ('core', 'workspace', 'screen', 'widget')
CORE_STATES: Tuple[str, ...] = ('idle', 'listening', 'thinking', 'speaking', 'working')
CORE_POSITIONS: Tuple[str, ...] = ('center', 'dock_bottom_right')
PRESENTATION_ACTIONS: Tuple[str, ...] = ('open_workspace', 'focus_workspace', 'minimize_workspace', 'restore_workspace', 'close_workspace', 'clear_workspaces', 'upsert_widget', 'focus_widget', 'drag_widget', 'resize_widget', 'pin_widget', 'unpin_widget', 'pause_expiry', 'resume_expiry', 'dismiss_widget', 'tick_auto_dismiss', 'clear_screen', 'clear_everything', 'restore_everything', 'focused_escape', 'surface_action')

CORE_RULES: Dict[str, Any] = {'no_workspace': {'position': 'center', 'show_status_bar': True, 'show_indicator_dots': True, 'show_state_label': True, 'show_subtext': True}, 'active_workspace': {'position': 'dock_bottom_right', 'show_status_bar': False, 'show_indicator_dots': False, 'show_state_label': False, 'show_subtext': False}}
WORKSPACES_METADATA: Dict[str, Any] = {'research': {'aliases': [], 'implemented': True, 'renderer': 'ResearchWorkspace', 'renderer_module': 'frontend/src/scene/workspaces/ResearchWorkspace.tsx', 'renderer_export': 'ResearchWorkspace', 'spatial': False, 'core_position': 'dock_bottom_right', 'dismiss_policy': 'persistent', 'description': 'Web research findings, citations, and synthesized intelligence'}, 'briefing': {'aliases': [], 'implemented': True, 'renderer': 'BriefingWorkspace', 'renderer_module': 'frontend/src/scene/workspaces/BriefingWorkspace.tsx', 'renderer_export': 'BriefingWorkspace', 'spatial': False, 'core_position': 'dock_bottom_right', 'dismiss_policy': 'persistent', 'description': 'Daily intelligence briefing, calendar, and summary digest'}, 'system': {'aliases': ['telemetry'], 'implemented': True, 'renderer': 'SystemWorkspace', 'renderer_module': 'frontend/src/scene/workspaces/SystemWorkspace.tsx', 'renderer_export': 'SystemWorkspace', 'spatial': False, 'core_position': 'dock_bottom_right', 'dismiss_policy': 'persistent', 'description': 'Comprehensive system telemetry, process health, and subsystem status'}, 'tasks': {'aliases': ['task', 'plans'], 'implemented': True, 'renderer': 'TasksWorkspace', 'renderer_module': 'frontend/src/scene/workspaces/TasksWorkspace.tsx', 'renderer_export': 'TasksWorkspace', 'spatial': False, 'core_position': 'dock_bottom_right', 'dismiss_policy': 'persistent', 'description': 'Runtime task journal, execution plans, and progress tracking'}, 'map': {'aliases': ['spatial'], 'implemented': True, 'renderer': 'MapWorkspace', 'renderer_module': 'frontend/src/scene/workspaces/MapWorkspace.tsx', 'renderer_export': 'MapWorkspace', 'spatial': True, 'core_position': 'dock_bottom_right', 'dismiss_policy': 'persistent', 'description': 'Edge-to-edge geospatial map and spatial intelligence canvas'}, 'vision': {'aliases': ['camera'], 'implemented': True, 'renderer': 'VisionWorkspace', 'renderer_module': 'frontend/src/scene/workspaces/VisionWorkspace.tsx', 'renderer_export': 'VisionWorkspace', 'spatial': False, 'core_position': 'dock_bottom_right', 'dismiss_policy': 'persistent', 'description': 'Visual perception, screen grounding, and camera feed'}, 'document': {'aliases': ['report', 'file'], 'implemented': True, 'renderer': 'DocumentWorkspace', 'renderer_module': 'frontend/src/scene/workspaces/DocumentWorkspace.tsx', 'renderer_export': 'DocumentWorkspace', 'spatial': False, 'core_position': 'dock_bottom_right', 'dismiss_policy': 'persistent', 'description': 'Document viewer, report reader, and file artifact browser'}, 'terminal': {'aliases': [], 'implemented': True, 'renderer': 'TerminalWorkspace', 'renderer_module': 'frontend/src/scene/workspaces/TerminalWorkspace.tsx', 'renderer_export': 'TerminalWorkspace', 'spatial': False, 'core_position': 'dock_bottom_right', 'dismiss_policy': 'persistent', 'description': 'Interactive Windows PTY shell and terminal session'}, 'conversation': {'aliases': ['chat'], 'implemented': True, 'renderer': 'ConversationWorkspace', 'renderer_module': 'frontend/src/scene/workspaces/ConversationWorkspace.tsx', 'renderer_export': 'ConversationWorkspace', 'spatial': False, 'core_position': 'dock_bottom_right', 'dismiss_policy': 'persistent', 'description': 'Full-session conversation history and dialogue stream'}, 'composed_surface': {'aliases': [], 'implemented': True, 'renderer': 'SurfaceComposer', 'renderer_module': 'frontend/src/composer/SurfaceComposer.tsx', 'renderer_export': 'SurfaceComposer', 'spatial': False, 'core_position': 'dock_bottom_right', 'dismiss_policy': 'persistent', 'description': 'Dynamic schema-driven composed workspace canvas'}}
WIDGETS_METADATA: Dict[str, Any] = {'system_metric': {'aliases': ['system'], 'implemented': True, 'renderer': 'SystemWidget', 'renderer_module': 'frontend/src/scene/widgets/SystemWidget.tsx', 'renderer_export': 'SystemWidget', 'default_dismiss_policy': 'timed', 'default_auto_dismiss_ms': 5000, 'default_zone': 'top_right', 'supports': {'drag': True, 'resize': True, 'pin': True, 'auto_dismiss': True}, 'description': 'System hardware telemetry (CPU, RAM, GPU, Disk, Network, Battery)'}, 'composed_surface': {'aliases': [], 'implemented': True, 'renderer': 'SurfaceComposer', 'renderer_module': 'frontend/src/composer/SurfaceComposer.tsx', 'renderer_export': 'SurfaceComposer', 'default_dismiss_policy': 'timed', 'default_auto_dismiss_ms': 8000, 'default_zone': 'top_right', 'supports': {'drag': True, 'resize': True, 'pin': True, 'auto_dismiss': True}, 'description': 'Dynamic schema-driven composed widget surface'}, 'media_control': {'aliases': ['media', 'media player', 'music', 'music player'], 'implemented': True, 'renderer': 'GenericWidget', 'renderer_module': 'frontend/src/scene/widgets/GenericWidget.tsx', 'default_dismiss_policy': 'timed', 'default_auto_dismiss_ms': 6000, 'default_zone': 'bottom_right', 'supports': {'drag': True, 'resize': True, 'pin': True, 'auto_dismiss': True}, 'description': 'Media playback and audio volume controller'}, 'file_viewer': {'aliases': ['file'], 'implemented': True, 'renderer': 'GenericWidget', 'renderer_module': 'frontend/src/scene/widgets/GenericWidget.tsx', 'default_dismiss_policy': 'timed', 'default_auto_dismiss_ms': 5000, 'default_zone': 'top_right', 'supports': {'drag': True, 'resize': True, 'pin': True, 'auto_dismiss': True}, 'description': 'Directory listing and file inspection widget'}}
OVERLAYS_METADATA: Dict[str, Any] = {'settings': {'implemented': True, 'renderer': 'SettingsModal', 'renderer_module': 'frontend/src/scene/SettingsModal.tsx', 'renderer_export': 'SettingsModal', 'dismiss_policy': 'manual', 'anchor': 'screen', 'description': 'Configuration, voice settings, appearance, and doctor diagnostics'}}
SEMANTIC_TARGETS: Dict[str, Any] = {'system_metrics': {'taxonomy': 'widget', 'surface': 'system_metric'}, 'media_control': {'taxonomy': 'widget', 'surface': 'media_control'}, 'research_result': {'taxonomy': 'workspace', 'surface': 'research'}, 'daily_briefing': {'taxonomy': 'workspace', 'surface': 'briefing'}, 'geospatial': {'taxonomy': 'workspace', 'surface': 'map'}, 'terminal': {'taxonomy': 'workspace', 'surface': 'terminal'}, 'file_viewer': {'taxonomy': 'widget', 'surface': 'file_viewer'}, 'composed_widget': {'taxonomy': 'widget', 'surface': 'composed_surface'}, 'composed_workspace': {'taxonomy': 'workspace', 'surface': 'composed_surface'}}

