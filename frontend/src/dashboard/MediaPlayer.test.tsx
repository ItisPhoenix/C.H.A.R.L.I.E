import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";
import { MediaPlayer } from "./MediaPlayer";
import { useLayoutStore } from "./layoutStore";

describe("MediaPlayer", () => {
  test("renders real metadata and an honest unavailable state", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ available: true, title: "Echoes", artist: "Charlie", album: "Tomorrow", status: "playing", position_seconds: 12, duration_seconds: 120, art_uri: null }), { status: 200 })));
    useLayoutStore.getState().open("media");
    render(<MediaPlayer />);

    await waitFor(() => expect(screen.getByText("Echoes")).toBeInTheDocument());
    expect(screen.getByText("Charlie")).toBeInTheDocument();
  });
});
