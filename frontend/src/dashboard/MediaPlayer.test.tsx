import { render, screen } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import { MediaPlayer } from "./MediaPlayer";

describe("MediaPlayer", () => {
  test("does not fabricate playback data without a media contract", () => {
    render(<MediaPlayer />);

    expect(screen.getByText("Media playback is unavailable.")).toBeInTheDocument();
    expect(screen.queryByText("Echoes of Tomorrow")).not.toBeInTheDocument();
  });
});
