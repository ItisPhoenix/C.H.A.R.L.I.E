import { describe, expect, it, vi } from "vitest";
import { fixtureNames, makeFixture } from "./fixtures";

const actions = {
  expanded: false, extraTask: false, approved: false,
  expand: vi.fn(), toggleTask: vi.fn(), approve: vi.fn(),
};

describe("visual fixture coverage", () => {
  it("keeps fixture states on the same projection/core path", () => {
    for (const name of fixtureNames) {
      const fixture = makeFixture(name, actions);
      expect(Array.isArray(fixture.items)).toBe(true);
      expect(fixture.state).toBeTruthy();
    }
    expect(makeFixture("chat", actions).items[0].content).toBeTruthy();
    expect(makeFixture("media", actions).items[0].content).toBeTruthy();
    expect(makeFixture("media", { ...actions, paired: true }).items).toHaveLength(2);
    expect(makeFixture("research", actions).items[0].immersive).toBe(true);
    expect(fixtureNames).toEqual(expect.arrayContaining([
      "research-starting", "research-planning", "research-searching", "research-reading",
      "research-synthesis", "research-complete", "research-failed",
    ]));
    expect(makeFixture("research-failed", actions).state).toBe("degraded");
    expect(makeFixture("vision", actions).items[0].immersive).toBe(true);
    expect(makeFixture("terminal", actions).items[0].immersive).toBe(true);
  });
});
