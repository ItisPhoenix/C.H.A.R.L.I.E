import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, test } from "vitest";
import { useCharlieStore } from "../store/charlie";
import { VoiceBar } from "./VoiceBar";

beforeEach(() => {
  useCharlieStore.setState({ connected: false, coreState: "idle", audioState: null, micMuted: null, subsystemHealth: {} });
});

describe("VoiceBar", () => {
  test("shows unavailable voice state before real audio data arrives", () => {
    render(<VoiceBar />);

    expect(screen.getByText("Voice unavailable.")).toBeInTheDocument();
    expect(screen.queryByText("Listening...")).not.toBeInTheDocument();
  });

  test("renders live muted microphone state", () => {
    useCharlieStore.setState({ connected: true, coreState: "listening", audioState: { muted: false, volume: 1 }, micMuted: true, subsystemHealth: { voice: { status: "running", detail: "Running" } } });
    render(<VoiceBar />);

    expect(screen.getByText("Microphone muted")).toBeInTheDocument();
  });

  test("renders the live speaking state from the voice event pipeline", () => {
    useCharlieStore.setState({ connected: true, coreState: "speaking", audioState: { muted: false, volume: 1 }, micMuted: false, subsystemHealth: { voice: { status: "running", detail: "Running" } } });
    render(<VoiceBar />);

    expect(screen.getByText("Speaking...")).toBeInTheDocument();
  });

  test("renders the live listening state after VAD detects speech", () => {
    useCharlieStore.setState({ connected: true, coreState: "listening", audioState: { muted: false, volume: 1 }, micMuted: false, subsystemHealth: { voice: { status: "running", detail: "Running" } } });
    render(<VoiceBar />);

    expect(screen.getByText("Listening...")).toBeInTheDocument();
  });

  test("does not infer voice availability from default audio values", () => {
    useCharlieStore.setState({ connected: true, audioState: { muted: false, volume: 1 }, micMuted: false, subsystemHealth: {} });
    render(<VoiceBar />);

    expect(screen.getByText("Voice unavailable.")).toBeInTheDocument();
  });
});
