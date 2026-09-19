import { describe, expect, it } from "vitest";
import { createFrontendBuildIdentity, createFrontendBuildIdentityPlugin } from "../vite.config";

describe("frontend build identity", () => {
  it("creates a non-empty build identity with runtime metadata", () => {
    const identity = createFrontendBuildIdentity();
    expect(identity.build_id).toMatch(/^frontend-/);
    expect(identity.built_at).not.toBe("");
    expect(identity).toHaveProperty("git_sha");
    expect(identity).toHaveProperty("dirty");
    expect(identity.authority).toBe("vite_build");
  });

  it("marks the production root with the same build id emitted in the manifest", () => {
    const plugin = createFrontendBuildIdentityPlugin({
      build_id: "build-test",
      git_sha: "sha-test",
      dirty: false,
      built_at: "2026-09-17T00:00:00.000Z",
      authority: "vite_build",
    });
    const transform = plugin.transformIndexHtml as ((html: string) => string);
    const html = transform('<div id="root"></div>');
    expect(html).toContain('data-charlie-build-id="build-test"');
    expect(html).toContain('data-charlie-build-status="valid"');
  });
});
