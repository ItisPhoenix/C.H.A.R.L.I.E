import { describe, expect, test } from "vitest";
import { reconnectDelayMs } from "./bridge";

describe("reconnectDelayMs", () => {
  test("starts at 3000ms on the first attempt", () => {
    expect(reconnectDelayMs(0)).toBe(3000);
  });

  test("doubles each attempt", () => {
    expect(reconnectDelayMs(1)).toBe(6000);
    expect(reconnectDelayMs(2)).toBe(12000);
  });

  test("caps at 30000ms", () => {
    expect(reconnectDelayMs(10)).toBe(30000);
  });
});
