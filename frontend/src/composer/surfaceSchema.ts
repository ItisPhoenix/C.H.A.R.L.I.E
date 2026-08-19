/**
 * C.H.A.R.L.I.E. V1 — Frontend SurfaceComposer Schema & Validation Engine
 *
 * Enforces schema versioning, strict typing, complexity bounds, and sanitization.
 */

import {
  SURFACE_SCHEMA_VERSION,
  type PrimitiveType,
  type LayoutType,
} from "../presentation/presentationContract.generated";

export const SCHEMA_VERSION = SURFACE_SCHEMA_VERSION;

export const MAX_DEPTH = 5;
export const MAX_PRIMITIVES = 100;
export const MAX_TABLE_ROWS = 50;
export const MAX_CHART_POINTS = 60;
export const MAX_TEXT_LEN = 4000;
export const MAX_ACTIONS = 10;

export type { PrimitiveType, LayoutType };
export type TargetSurface = "widget" | "workspace";

export interface ActionSpec {
  id: string;
  label: string;
  action_id: string;
  payload?: Record<string, unknown>;
  variant?: "default" | "primary" | "danger" | "subtle";
  disabled?: boolean;
}

export interface PrimitiveSpec {
  type: PrimitiveType | string;
  id?: string;
  data?: Record<string, unknown>;
  children?: PrimitiveSpec[];
}

export interface LayoutSpec {
  type: LayoutType | string;
  gap?: number;
  columns?: number;
  align?: "start" | "center" | "end" | "stretch";
  justify?: "start" | "center" | "end" | "between";
}

export interface SurfaceSpec {
  schema_version: number;
  surface_id: string;
  title: string;
  target: TargetSurface | string;
  revision: number;
  surface_type?: string;
  summary?: string;
  layout?: LayoutSpec;
  primitives: PrimitiveSpec[];
  actions?: ActionSpec[];
  metadata?: Record<string, unknown>;
}

const DANGEROUS_PATTERNS = [
  /<\s*script/i,
  /javascript\s*:/i,
  /<\s*iframe/i,
  /<\s*style/i,
  /onload\s*=/i,
  /onerror\s*=/i,
  /onclick\s*=/i,
];

function checkStringSafety(val: string, path: string, errors: string[]) {
  if (val.length > MAX_TEXT_LEN) {
    errors.push(`Text length at ${path} (${val.length}) exceeds maximum limit of ${MAX_TEXT_LEN}`);
  }
  for (const pattern of DANGEROUS_PATTERNS) {
    if (pattern.test(val)) {
      errors.push(`Dangerous script/HTML pattern detected at ${path}`);
    }
  }
}

function validatePrimitiveNode(
  node: unknown,
  depth: number,
  counter: { count: number },
  errors: string[],
  path: string
) {
  counter.count += 1;
  if (counter.count > MAX_PRIMITIVES) {
    errors.push(`Surface primitive count exceeds maximum allowed limit (${MAX_PRIMITIVES})`);
    return;
  }

  if (depth > MAX_DEPTH) {
    errors.push(`Nesting depth at ${path} (${depth}) exceeds maximum limit (${MAX_DEPTH})`);
    return;
  }

  if (!node || typeof node !== "object") {
    errors.push(`Primitive node must be an object at ${path}`);
    return;
  }

  const p = node as Record<string, unknown>;
  if (!p.type || typeof p.type !== "string") {
    errors.push(`Missing or invalid primitive type at ${path}`);
    return;
  }

  if (p.data && typeof p.data === "object") {
    for (const [k, v] of Object.entries(p.data as Record<string, unknown>)) {
      if (typeof v === "string") {
        checkStringSafety(v, `${path}.data.${k}`, errors);
      }
    }

    if (p.type === "table" && Array.isArray((p.data as Record<string, unknown>).rows)) {
      const rows = (p.data as Record<string, unknown>).rows as unknown[];
      if (rows.length > MAX_TABLE_ROWS) {
        errors.push(`Table rows at ${path} (${rows.length}) exceeds limit (${MAX_TABLE_ROWS})`);
      }
    }

    if (p.type === "chart" && Array.isArray((p.data as Record<string, unknown>).data)) {
      const dataPoints = (p.data as Record<string, unknown>).data as unknown[];
      if (dataPoints.length > MAX_CHART_POINTS) {
        errors.push(`Chart data points at ${path} (${dataPoints.length}) exceeds limit (${MAX_CHART_POINTS})`);
      }
    }

    if (p.type === "image" && typeof (p.data as Record<string, unknown>).src === "string") {
      const src = ((p.data as Record<string, unknown>).src as string).toLowerCase();
      if (src.startsWith("javascript:")) {
        errors.push(`Unsafe image URL scheme detected at ${path}.data.src`);
      }
    }
  }

  if (Array.isArray(p.children)) {
    p.children.forEach((child, idx) => {
      validatePrimitiveNode(child, depth + 1, counter, errors, `${path}.children[${idx}]`);
    });
  }
}

export function validateSurfaceSpec(raw: unknown): {
  valid: boolean;
  errors: string[];
  spec?: SurfaceSpec;
} {
  const errors: string[] = [];

  if (!raw || typeof raw !== "object") {
    return { valid: false, errors: ["Surface specification must be a non-null object"] };
  }

  const data = raw as Record<string, unknown>;

  // 1. Schema version check
  if (data.schema_version !== SCHEMA_VERSION) {
    return {
      valid: false,
      errors: [`Unsupported schema_version: ${data.schema_version}. Expected ${SCHEMA_VERSION}.`],
    };
  }

  // 2. Root fields
  if (!data.surface_id || typeof data.surface_id !== "string") {
    errors.push("surface_id is required and must be a non-empty string");
  }

  if (!data.title || typeof data.title !== "string") {
    errors.push("title is required and must be a string");
  } else {
    checkStringSafety(data.title, "title", errors);
  }

  if (data.summary && typeof data.summary === "string") {
    checkStringSafety(data.summary, "summary", errors);
  }

  const target = data.target ?? "widget";
  if (target !== "widget" && target !== "workspace") {
    errors.push(`target must be 'widget' or 'workspace', got '${target}'`);
  }

  // 3. Actions
  if (data.actions) {
    if (!Array.isArray(data.actions)) {
      errors.push("actions must be an array");
    } else {
      if (data.actions.length > MAX_ACTIONS) {
        errors.push(`Actions count (${data.actions.length}) exceeds limit of ${MAX_ACTIONS}`);
      }
      data.actions.forEach((a, idx) => {
        if (!a || typeof a !== "object") {
          errors.push(`Action at index ${idx} must be an object`);
        } else {
          const act = a as Record<string, unknown>;
          if (!act.action_id || typeof act.action_id !== "string") {
            errors.push(`Action at index ${idx} missing required action_id`);
          }
          if (typeof act.label === "string") {
            checkStringSafety(act.label, `actions[${idx}].label`, errors);
          }
        }
      });
    }
  }

  // 4. Primitives
  if (!data.primitives || !Array.isArray(data.primitives)) {
    errors.push("primitives must be an array");
  } else {
    const counter = { count: 0 };
    data.primitives.forEach((p, idx) => {
      validatePrimitiveNode(p, 1, counter, errors, `primitives[${idx}]`);
    });
  }

  if (errors.length > 0) {
    return { valid: false, errors };
  }

  const spec: SurfaceSpec = {
    schema_version: SCHEMA_VERSION,
    surface_id: String(data.surface_id),
    title: String(data.title),
    target: String(target) as TargetSurface,
    revision: Number(data.revision ?? 1),
    surface_type: String(data.surface_type ?? "custom"),
    summary: String(data.summary ?? ""),
    layout: (data.layout as LayoutSpec) ?? { type: "stack", gap: 12 },
    primitives: (data.primitives as PrimitiveSpec[]) ?? [],
    actions: (data.actions as ActionSpec[]) ?? [],
    metadata: (data.metadata as Record<string, unknown>) ?? {},
  };

  return { valid: true, errors: [], spec };
}
