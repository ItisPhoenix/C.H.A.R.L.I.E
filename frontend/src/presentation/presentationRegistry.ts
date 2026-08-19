import contract from "../../../shared/presentation_contract.json";
import {
  CONTRACT_VERSION,
  SURFACE_SCHEMA_VERSION,
  type PresentationKind,
  type PrimitiveType,
  type LayoutType,
  type DismissPolicy,
  type PreferredZone,
  type AnchorTarget,
  type CoreState,
  type CorePosition,
  type PresentationAction,
  type WorkspaceType,
  type WidgetType,
  type OverlayType,
} from "./presentationContract.generated";

export const PRESENTATION_CONTRACT = contract;
export { CONTRACT_VERSION, SURFACE_SCHEMA_VERSION };

export type {
  PresentationKind,
  PrimitiveType,
  LayoutType,
  DismissPolicy,
  PreferredZone,
  AnchorTarget,
  CoreState,
  CorePosition,
  PresentationAction,
  WorkspaceType,
  WidgetType,
  OverlayType,
};

export interface WorkspaceDefinition {
  name: string;
  aliases: string[];
  implemented: boolean;
  renderer: string;
  renderer_module?: string;
  renderer_export?: string;
  spatial: boolean;
  core_position: string;
  dismiss_policy: string;
  description: string;
}

export interface WidgetDefinition {
  name: string;
  aliases: string[];
  implemented: boolean;
  renderer: string;
  renderer_module?: string;
  renderer_export?: string;
  default_dismiss_policy: string;
  default_auto_dismiss_ms: number;
  default_zone: string;
  supports: {
    drag: boolean;
    resize: boolean;
    pin: boolean;
    auto_dismiss: boolean;
  };
  description: string;
}

export interface OverlayDefinition {
  name: string;
  aliases: string[];
  implemented: boolean;
  renderer: string;
  renderer_module?: string;
  renderer_export?: string;
  dismiss_policy: string;
  anchor: string;
  description: string;
}

// Build alias resolution maps
const WORKSPACE_ALIAS_MAP: Record<string, string> = {};
const WORKSPACES: Record<string, WorkspaceDefinition> = {};

for (const [wsKey, wsVal] of Object.entries(contract.workspaces)) {
  const def: WorkspaceDefinition = {
    name: wsKey,
    aliases: [...wsVal.aliases],
    implemented: wsVal.implemented,
    renderer: wsVal.renderer,
    renderer_module: (wsVal as Record<string, unknown>).renderer_module as string | undefined,
    renderer_export: (wsVal as Record<string, unknown>).renderer_export as string | undefined,
    spatial: wsVal.spatial,
    core_position: wsVal.core_position,
    dismiss_policy: wsVal.dismiss_policy,
    description: wsVal.description,
  };
  WORKSPACES[wsKey] = def;
  WORKSPACE_ALIAS_MAP[wsKey.toLowerCase()] = wsKey;
  for (const alias of wsVal.aliases) {
    WORKSPACE_ALIAS_MAP[alias.toLowerCase()] = wsKey;
  }
}

const WIDGET_ALIAS_MAP: Record<string, string> = {};
const WIDGETS: Record<string, WidgetDefinition> = {};

for (const [wKey, wVal] of Object.entries(contract.widgets)) {
  const def: WidgetDefinition = {
    name: wKey,
    aliases: [...wVal.aliases],
    implemented: wVal.implemented,
    renderer: wVal.renderer,
    renderer_module: (wVal as Record<string, unknown>).renderer_module as string | undefined,
    renderer_export: (wVal as Record<string, unknown>).renderer_export as string | undefined,
    default_dismiss_policy: wVal.default_dismiss_policy,
    default_auto_dismiss_ms: wVal.default_auto_dismiss_ms,
    default_zone: wVal.default_zone,
    supports: { ...wVal.supports },
    description: wVal.description,
  };
  WIDGETS[wKey] = def;
  WIDGET_ALIAS_MAP[wKey.toLowerCase()] = wKey;
  for (const alias of wVal.aliases) {
    WIDGET_ALIAS_MAP[alias.toLowerCase()] = wKey;
  }
}

const OVERLAY_ALIAS_MAP: Record<string, string> = {};
const OVERLAYS: Record<string, OverlayDefinition> = {};

for (const [oKey, oVal] of Object.entries(contract.overlays || {})) {
  const def: OverlayDefinition = {
    name: oKey,
    aliases: [...((oVal as Record<string, unknown>).aliases as string[] || [])],
    implemented: Boolean((oVal as Record<string, unknown>).implemented),
    renderer: String((oVal as Record<string, unknown>).renderer || ""),
    renderer_module: (oVal as Record<string, unknown>).renderer_module as string | undefined,
    renderer_export: (oVal as Record<string, unknown>).renderer_export as string | undefined,
    dismiss_policy: String((oVal as Record<string, unknown>).dismiss_policy || "manual"),
    anchor: String((oVal as Record<string, unknown>).anchor || "screen"),
    description: String((oVal as Record<string, unknown>).description || ""),
  };
  OVERLAYS[oKey] = def;
  OVERLAY_ALIAS_MAP[oKey.toLowerCase()] = oKey;
  for (const alias of def.aliases) {
    OVERLAY_ALIAS_MAP[alias.toLowerCase()] = oKey;
  }
}

const PRIMITIVE_SET = new Set<string>(contract.surface_primitives);

/**
 * Canonicalize a workspace type or alias to its canonical name.
 */
export function resolveWorkspaceType(name?: string | null): string | null {
  if (!name) return null;
  return WORKSPACE_ALIAS_MAP[name.trim().toLowerCase()] ?? null;
}

/**
 * Canonicalize a widget type or alias to its canonical name.
 */
export function resolveWidgetType(name?: string | null): string | null {
  if (!name) return null;
  return WIDGET_ALIAS_MAP[name.trim().toLowerCase()] ?? null;
}

/**
 * Canonicalize an overlay type or alias to its canonical name.
 */
export function resolveOverlayType(name?: string | null): string | null {
  if (!name) return null;
  return OVERLAY_ALIAS_MAP[name.trim().toLowerCase()] ?? null;
}

/**
 * Check if a workspace type (canonical or alias) exists.
 */
export function hasWorkspace(name: string): boolean {
  return resolveWorkspaceType(name) !== null;
}

/**
 * Check if a widget type (canonical or alias) exists.
 */
export function hasWidget(name: string): boolean {
  return resolveWidgetType(name) !== null;
}

/**
 * Check if an overlay type (canonical or alias) exists.
 */
export function hasOverlay(name: string): boolean {
  return resolveOverlayType(name) !== null;
}

/**
 * Check if a surface primitive type exists.
 */
export function hasPrimitive(name: string): boolean {
  return PRIMITIVE_SET.has(name);
}

/**
 * Retrieve canonical WorkspaceDefinition by name or alias.
 */
export function getWorkspaceDefinition(name: string): WorkspaceDefinition | null {
  const canonical = resolveWorkspaceType(name);
  if (!canonical) return null;
  return WORKSPACES[canonical] ?? null;
}

/**
 * Retrieve canonical WidgetDefinition by name or alias.
 */
export function getWidgetDefinition(name: string): WidgetDefinition | null {
  const canonical = resolveWidgetType(name);
  if (!canonical) return null;
  return WIDGETS[canonical] ?? null;
}

/**
 * Retrieve canonical OverlayDefinition by name or alias.
 */
export function getOverlayDefinition(name: string): OverlayDefinition | null {
  const canonical = resolveOverlayType(name);
  if (!canonical) return null;
  return OVERLAYS[canonical] ?? null;
}

/**
 * List all canonical workspace names.
 */
export function listWorkspaces(): string[] {
  return Object.keys(WORKSPACES);
}

/**
 * List all canonical widget names.
 */
export function listWidgets(): string[] {
  return Object.keys(WIDGETS);
}

/**
 * List all canonical overlay names.
 */
export function listOverlays(): string[] {
  return Object.keys(OVERLAYS);
}

/**
 * List all supported surface primitives.
 */
export function listSurfacePrimitives(): string[] {
  return [...contract.surface_primitives];
}

/**
 * List all supported layout types.
 */
export function listLayoutTypes(): string[] {
  return [...contract.layout_types];
}

/**
 * List all supported dismiss policies.
 */
export function listDismissPolicies(): string[] {
  return [...contract.dismiss_policies];
}

/**
 * List all preferred placement zones.
 */
export function listPreferredZones(): string[] {
  return [...contract.preferred_zones];
}

/**
 * List all presentation kinds.
 */
export function listPresentationKinds(): string[] {
  return [...contract.presentation_kinds];
}

/**
 * Get core state names.
 */
export function getCoreStates(): string[] {
  return [...contract.core.states];
}

/**
 * Get core position names.
 */
export function getCorePositions(): string[] {
  return [...contract.core.positions];
}

/**
 * Get authoritative core positioning rules.
 */
export function getCoreRules(): typeof contract.core.rules {
  return contract.core.rules;
}
