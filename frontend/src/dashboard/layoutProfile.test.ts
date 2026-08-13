import { describe, expect, test } from "vitest";
import { layoutProfileForWidth } from "./layoutProfile";

describe("layoutProfileForWidth", () => {
  test("uses independent compact, laptop, and desktop layout profiles", () => {
    expect(layoutProfileForWidth(640)).toBe("compact");
    expect(layoutProfileForWidth(1024)).toBe("laptop");
    expect(layoutProfileForWidth(1440)).toBe("desktop");
  });
});
