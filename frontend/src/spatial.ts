export interface Size { width: number; height: number }
export interface Rect extends Size { x: number; y: number; scale?: number }
export interface Point { x: number; y: number }
export interface SpatialItem extends Size {
  id: string;
  importance: number;
  interactionPriority?: number;
  relatedTo?: string;
  core?: boolean;
  immersive?: boolean;
  preferredZone?: "center" | "around-core" | "bottom-right";
  pinnedPosition?: Point;
}
export interface Layout { positions: Record<string, Rect>; height: number }
export interface LayoutOptions { gap: number }

/** Contact counts as safe; the gap is reserved separately by the compositor. */
export function intersects(a: Rect, b: Rect, gap = 0) {
  return a.x < b.x + b.width + gap && a.x + a.width + gap > b.x &&
    a.y < b.y + b.height + gap && a.y + a.height + gap > b.y;
}

const distance = (a: Rect, b: Rect) =>
  Math.hypot(a.x + a.width / 2 - b.x - b.width / 2,
    a.y + a.height / 2 - b.y - b.height / 2);

/** Measured rectangles in, positions out. No scene names or content categories. */
function composeAtScale(
  viewport: Size, input: SpatialItem[], previous: Layout | undefined,
  { gap }: LayoutOptions,
): Layout {
  const interruption = input.find(item => (item.interactionPriority ?? 0) > 0);
  const priority = (item: SpatialItem) =>
    item.importance + (item.interactionPriority ?? 0) + (item.core && interruption ? 35 : 0);
  const immersive = input.find(item => !item.core && item.immersive);
  const centerBiased = input.some(item => !item.core &&
    (item.preferredZone === "around-core" || item.relatedTo === "charlie"));
  const keepCoreCentered = !immersive && centerBiased;
  const items = [...input].sort((a, b) => {
    // Charlie is the home anchor. Measure it first so compact content composes
    // around it instead of displacing it merely because it has higher priority.
    if (keepCoreCentered && a.core !== b.core) return a.core ? -1 : 1;
    if (immersive && a.immersive !== b.immersive) return a.immersive ? -1 : 1;
    if (Boolean(a.pinnedPosition) !== Boolean(b.pinnedPosition)) return a.pinnedPosition ? -1 : 1;
    return priority(b) - priority(a) || a.id.localeCompare(b.id);
  });
  const positions: Record<string, Rect> = {};
  const center = { x: viewport.width / 2, y: viewport.height / 2, width: 0, height: 0 };
  const coreHome = (core: SpatialItem) => ({
    x: (viewport.width - core.width) / 2,
    y: (viewport.height - core.height) / 2,
    width: core.width,
    height: core.height,
  });
  const coreDock = (core: SpatialItem) => ({
    x: Math.max(0, viewport.width - core.width),
    y: Math.max(0, viewport.height - core.height),
    width: core.width,
    height: core.height,
  });
  const lead = items.find(item => !item.core && item.importance >= 70);
  const coreItem = items.find(item => item.core);
  const coreClearance = coreItem ? Math.max(gap, coreItem.width * .6) : gap;
  const crowded = input.reduce((area, item) => area + item.width * item.height, 0) >
    viewport.width * viewport.height * 0.45;
  // Low-priority additions must avoid higher-priority existing positions as well.
  for (const item of items) {
    const old = previous?.positions[item.id];
    const occupiedEntries = Object.entries(positions);
    const occupied = occupiedEntries.map(([, rect]) => rect);
    const pending = items.filter(other => !positions[other.id] && other.id !== item.id)
      .flatMap(other => {
        const p = previous?.positions[other.id];
        return p ? [{ ...p, width: other.width, height: other.height }] : [];
      });
    const candidates: Rect[] = [];
    const add = (x: number, y: number) => candidates.push({
      x: Math.max(0, Math.min(viewport.width - item.width, x)),
      y: Math.max(0, y), width: item.width, height: item.height,
    });
    if (item.pinnedPosition) {
      add(item.pinnedPosition.x, item.pinnedPosition.y);
    } else {
      if (old) add(old.x, old.y);
      add((viewport.width - item.width) / 2, (viewport.height - item.height) / 2);
      if (!item.core && coreItem && !lead) {
        add((viewport.width - item.width) / 2,
          viewport.height / 2 - coreItem.height / 2 - item.height - gap);
      }
      if (item.core) {
        const target = immersive ? coreDock(item) : coreHome(item);
        add(target.x, target.y);
      }
      if (!item.core && keepCoreCentered && coreItem) {
        const home = coreHome(coreItem);
        add(home.x - item.width - coreClearance, home.y + (coreItem.height - item.height) / 2);
        add(home.x + coreItem.width + coreClearance, home.y + (coreItem.height - item.height) / 2);
        add(home.x + (coreItem.width - item.width) / 2, home.y - item.height - coreClearance);
        add(home.x + (coreItem.width - item.width) / 2, home.y + coreItem.height + coreClearance);
      }
    }
    if (!item.pinnedPosition) {
      // Candidate edges are driven by measured rectangles, including previous neighbors.
      const neighbors = [...occupied, ...pending];
      for (const r of neighbors) {
        for (const y of [r.y, r.y + r.height - item.height, r.y + (r.height - item.height) / 2]) {
          add(r.x + r.width + gap, y);
          add(r.x - item.width - gap, y);
        }
        for (const x of [r.x, r.x + r.width - item.width, r.x + (r.width - item.width) / 2]) {
          add(x, r.y + r.height + gap);
          add(x, r.y - item.height - gap);
        }
      }
      // A sparse viewport lattice supplies free regions when neighbors are absent.
      for (const fx of [0, 0.25, 0.5, 0.75, 1])
        for (const fy of [0, 0.25, 0.5, 0.75, 1])
          add((viewport.width - item.width) * fx, (viewport.height - item.height) * fy);
      add(0, Math.max(0, ...occupied.map(r => r.y + r.height + gap)));
    }

    const related = positions[item.relatedTo ?? ""] ??
      (item.core && immersive ? positions[immersive.id] : undefined);
    const score = (r: Rect) => {
      // Hard safety costs outrank continuity and optical preferences.
      if (occupiedEntries.some(([id, p]) => intersects(r, p, id === coreItem?.id ? coreClearance : gap))) return Infinity;
      const overflow = Math.max(0, r.y + r.height - viewport.height);
      const movement = old ? Math.hypot(r.x - old.x, r.y - old.y) : 0;
      const disrupt = pending.filter(p => intersects(r, p, gap)).length * 20000;
      const proximity = related ? distance(r, related) : distance(r, center);
      const coreTarget = item.core ? (immersive ? coreDock(item) : coreHome(item)) : undefined;
      const coreHomePenalty = coreTarget ? distance(r, coreTarget) * 500 : 0;
      const zoneTarget = item.preferredZone === "center"
        ? center
        : item.preferredZone === "bottom-right"
          ? { x: viewport.width, y: viewport.height, width: 0, height: 0 }
          : undefined;
      const zonePenalty = zoneTarget && !item.core ? distance(r, zoneTarget) * .06 : 0;
      const priorityConflict = !item.core && item.importance < 30 && lead && positions[lead.id] &&
        r.y < positions[lead.id].y ? 20000 : 0;
      const initialDensity = !old && crowded && occupied.length === 0 ? r.y * 1000 : 0;
      const coreConflict = !item.core && coreItem && !lead &&
        intersects(r, { x: (viewport.width - coreItem.width) / 2,
          y: (viewport.height - coreItem.height) / 2, ...coreItem }, gap) ? 100000 : 0;
      return overflow * 100000 + disrupt + coreConflict + priorityConflict + initialDensity + coreHomePenalty +
        zonePenalty + movement * 200 + proximity * (related ? 3 : 1) + r.y * 0.015;
    };
    let best = candidates[0];
    let bestScore = Infinity;
    for (const candidate of candidates) {
      const cost = score(candidate);
      if (cost < bestScore) { best = candidate; bestScore = cost; }
    }
    positions[item.id] = best;
  }
  return { positions, height: Math.max(viewport.height, ...Object.values(positions).map(r => r.y + r.height)) };
}

const fitsViewport = (layout: Layout, viewport: Size, gap: number) => {
  const rects = Object.values(layout.positions);
  return rects.every(rect => rect.x >= 0 && rect.y >= 0 &&
    rect.x + rect.width <= viewport.width + 0.01 &&
    rect.y + rect.height <= viewport.height + 0.01) &&
    rects.every((rect, index) => rects.slice(index + 1).every(other =>
      !intersects(rect, other, gap)));
};

const scaledLayout = (layout: Layout, scale: number, height: number): Layout => ({
  height,
  positions: Object.fromEntries(Object.entries(layout.positions).map(([id, rect]) => [id, {
    ...rect,
    scale,
  }])),
});

/**
 * Measured rectangles in, visible-viewport positions out.
 * Normal composition keeps intrinsic sizes; dense composition scales measured content
 * only when a visible, non-scrolling placement cannot be found.
 */
export function compose(
  viewport: Size, input: SpatialItem[], previous: Layout | undefined,
  options: LayoutOptions,
): Layout {
  const { gap } = options;
  if (!Number.isFinite(viewport.width) || !Number.isFinite(viewport.height) ||
    viewport.width <= 0 || viewport.height <= 0 || !Number.isFinite(gap) || gap < 0)
    throw new RangeError("Invalid spatial viewport");
  const ids = new Set<string>();
  for (const item of input) {
    if (ids.has(item.id) || !Number.isFinite(item.width) || !Number.isFinite(item.height) ||
      item.width <= 0 || item.height <= 0)
      throw new RangeError("Invalid or unmeasured spatial item");
    ids.add(item.id);
  }

  const initial = composeAtScale(viewport, input, previous, options);
  if (fitsViewport(initial, viewport, gap)) return scaledLayout(initial, 1, viewport.height);

  const maxWidth = Math.max(0, ...input.map(item => item.width));
  const totalHeight = input.reduce((total, item) => total + item.height, 0);
  const stackGap = Math.min(gap, viewport.height / Math.max(1, input.length + 1));
  const stackCapacity = Math.max(1, viewport.height - stackGap * Math.max(0, input.length - 1));
  const guaranteedScale = Math.min(1, viewport.width / Math.max(1, maxWidth),
    stackCapacity / Math.max(1, totalHeight)) * .98;

  const minimumSearchScale = Math.max(.2, Math.min(.95, guaranteedScale));
  for (let scale = .95; scale >= minimumSearchScale; scale -= .05) {
    const scaledInput = input.map(item => ({
      ...item,
      width: item.width * scale,
      height: item.height * scale,
    }));
    const compact = composeAtScale(viewport, scaledInput, previous, { gap: gap * scale });
    if (fitsViewport(compact, viewport, gap * scale)) return scaledLayout(compact, scale, viewport.height);
  }

  const scale = Math.max(.02, Math.min(1, guaranteedScale));
  const scaledInput = input.map(item => ({
    ...item,
    width: item.width * scale,
    height: item.height * scale,
  }));
  const compact = composeAtScale(viewport, scaledInput, previous, { gap: stackGap });
  if (fitsViewport(compact, viewport, stackGap)) return scaledLayout(compact, scale, viewport.height);

  // ponytail: impossible-density fallback stacks scaled content; replace with semantic collapse if runtime adds unbounded items.
  const positions: Record<string, Rect> = {};
  let y = 0;
  for (const item of scaledInput) {
    const pinned = item.pinnedPosition;
    positions[item.id] = {
      x: pinned ? Math.max(0, Math.min(viewport.width - item.width, pinned.x)) : 0,
      y: pinned ? Math.max(0, Math.min(viewport.height - item.height, pinned.y)) : y,
      width: item.width, height: item.height, scale,
    };
    y += item.height + stackGap;
  }
  return { positions, height: viewport.height };
}
