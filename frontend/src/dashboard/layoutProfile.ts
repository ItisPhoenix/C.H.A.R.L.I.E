export type LayoutProfile = "compact" | "laptop" | "desktop";

const COMPACT_MAX_WIDTH = 767;
const LAPTOP_MAX_WIDTH = 1439;

export function layoutProfileForWidth(width: number): LayoutProfile {
  if (width <= COMPACT_MAX_WIDTH) return "compact";
  if (width <= LAPTOP_MAX_WIDTH) return "laptop";
  return "desktop";
}
