import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

describe("research scroll boundary", () => {
  it("keeps page scrolling disabled while allowing Research to scroll internally", () => {
    const css = readFileSync(resolve(process.cwd(), "src/styles.css"), "utf8");
    expect(css).toMatch(/html, body, #root[^\n]*overflow:\s*hidden/);
    expect(css).toMatch(/\.research-surface\s*\{[^}]*overflow:\s*auto/);
  });
});
