import { describe, expect, it } from "vitest";
import { compose, intersects, type SpatialItem, type Layout } from "./spatial";

const viewport = { width: 1280, height: 800 };
const items: SpatialItem[] = [
  { id: "finding", width: 530, height: 290, importance: 90 },
  { id: "charlie", width: 220, height: 240, importance: 65, core: true },
  { id: "evidence", width: 340, height: 220, importance: 45, relatedTo: "finding" },
  { id: "tasks", width: 280, height: 90, importance: 10 },
];
const options = { gap: 24 };
function expectSafe(layout: Layout, width: number) {
  const rects = Object.values(layout.positions);
  for (const [index, rect] of rects.entries()) {
    expect(rect.x).toBeGreaterThanOrEqual(0);
    expect(rect.x + rect.width).toBeLessThanOrEqual(width);
    expect(rect.y).toBeGreaterThanOrEqual(0);
    expect(rect.y + rect.height).toBeLessThanOrEqual(layout.height);
    for (const other of rects.slice(index + 1)) expect(intersects(rect, other)).toBe(false);
  }
}
describe("measured spatial placement", () => {
  it("keeps surviving projections and Charlie still when adding and removing a background task", () => {
    const first = compose(viewport, items, undefined, options);
    const added = compose(viewport, [...items,
      { id: "export", width: 230, height: 35, importance: 5, relatedTo: "tasks" },
    ], first, options);
    for (const item of items) expect(added.positions[item.id]).toEqual(first.positions[item.id]);
    const removed = compose(viewport, items, added, options);
    expect(removed.positions).toEqual(first.positions);
    expectSafe(added, viewport.width);
  });
  it("uses changed DOM-sized dimensions to resolve new collisions", () => {
    const first = compose(viewport, items, undefined, options);
    const grown = items.map(item => item.id === "finding" ? { ...item, height: 590 } : item);
    const next = compose(viewport, grown, first, options);
    expect(next.positions.finding.height).toBe(590);
    expectSafe(next, viewport.width);
  });
  it("keeps urgent content visible in dense viewport space without extending layout height", () => {
    const narrow = { width: 335, height: 590 };
    const data = [...items.map(item => ({ ...item, width: Math.min(item.width, narrow.width) })),
      { id: "approval", width: 335, height: 210, importance: 90, interactionPriority: 30 }];
    const layout = compose(narrow, data, undefined, { gap: 20 });
    expect(Object.keys(layout.positions)).toHaveLength(data.length);
    expect(layout.height).toBe(narrow.height);
    expect(layout.positions.approval.y + layout.positions.approval.height).toBeLessThanOrEqual(narrow.height);
    expect(Math.min(...Object.values(layout.positions).map(rect => rect.scale ?? 1))).toBeLessThan(1);
    expectSafe(layout, narrow.width);
  });
  it("is independent of input order and fixture identifiers", () => {
    expect(compose(viewport, [...items].reverse(), undefined, options))
      .toEqual(compose(viewport, items, undefined, options));
  });
  it("rejects invalid measurements and duplicate identities", () => {
    expect(() => compose(viewport, [{ ...items[0], width: NaN }], undefined, options)).toThrow();
    expect(() => compose(viewport, [items[0], items[0]], undefined, options)).toThrow();
  });

  it("keeps Charlie at center while compact content composes around it", () => {
    const layout = compose(viewport, [
      { id: "chat", width: 420, height: 180, importance: 86, relatedTo: "charlie" },
      { id: "charlie", width: 220, height: 220, importance: 65, core: true },
      { id: "tasks", width: 250, height: 90, importance: 62, relatedTo: "charlie" },
    ], undefined, options);
    expect(layout.positions.charlie.x + layout.positions.charlie.width / 2).toBeCloseTo(viewport.width / 2);
    expect(layout.positions.charlie.y + layout.positions.charlie.height / 2).toBeCloseTo(viewport.height / 2);
    expect(intersects(layout.positions.chat, layout.positions.charlie, options.gap)).toBe(false);
    expect(intersects(layout.positions.tasks, layout.positions.charlie, options.gap)).toBe(false);
  });

  it("docks Charlie only for an immersive surface", () => {
    const layout = compose(viewport, [
      { id: "research-workspace", width: 760, height: 480, importance: 92, immersive: true, preferredZone: "center" },
      { id: "charlie", width: 220, height: 220, importance: 65, core: true },
    ], undefined, options);
    expect(layout.positions["research-workspace"].x).toBe((viewport.width - 760) / 2);
    expect(layout.positions.charlie.x).toBeGreaterThan(viewport.width / 2);
    expect(layout.positions.charlie.y).toBeGreaterThan(viewport.height / 2);
    expect(layout.positions.charlie.x + layout.positions.charlie.width).toBe(viewport.width);
    expect(layout.positions.charlie.y + layout.positions.charlie.height).toBe(viewport.height);
    expect(intersects(layout.positions["research-workspace"], layout.positions.charlie, options.gap)).toBe(false);
  });

  it("keeps a manually pinned widget at its position and composes other content around it", () => {
    const pinned = { x: 18, y: 22 };
    const layout = compose(viewport, [
      { id: "chat", width: 300, height: 130, importance: 86, relatedTo: "charlie", pinnedPosition: pinned },
      { id: "charlie", width: 220, height: 220, importance: 65, core: true },
      { id: "tasks", width: 250, height: 90, importance: 62, relatedTo: "charlie" },
    ], undefined, options);
    expect(layout.positions.chat.x).toBe(pinned.x);
    expect(layout.positions.chat.y).toBe(pinned.y);
    expect(intersects(layout.positions.chat, layout.positions.tasks)).toBe(false);
  });
});
