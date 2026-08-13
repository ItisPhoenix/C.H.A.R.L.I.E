import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, test, vi } from "vitest";
import { useCharlieStore } from "../store/charlie";
import { Chat } from "./Chat";

beforeEach(() => {
  useCharlieStore.setState({ chatMessages: [] });
  sessionStorage.clear();
  vi.stubGlobal("fetch", vi.fn(async (input: string) => {
    if (input === "/api/session/active") return new Response(JSON.stringify({ active_session: "primary-session" }));
    return new Response(JSON.stringify({ messages: [] }));
  }));
});

describe("Chat", () => {
  test("uses dashboard panel chrome so it can be managed like other workspaces", () => {
    render(<Chat />);

    expect(screen.getByRole("heading", { name: "Conversation" })).toBeInTheDocument();
  });

  test("shows an empty conversation instead of a demo exchange", () => {
    render(<Chat />);

    expect(screen.getByText("No messages in this session.")).toBeInTheDocument();
    expect(screen.queryByText("analyze the latest cyber threats")).not.toBeInTheDocument();
  });

  test("hydrates the active session from Charlie's persisted history", async () => {
    sessionStorage.setItem("charlie.active-session-id", "saved-session");
    vi.stubGlobal("fetch", vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ active_session: "saved-session" })))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        messages: [
          { role: "user", content: "Earlier question" },
          { role: "assistant", content: "Earlier answer" },
        ],
      }))));

    render(<Chat />);

    expect(await screen.findByText("Earlier question")).toBeInTheDocument();
    expect(screen.getByText("Earlier answer")).toBeInTheDocument();
    expect(fetch).toHaveBeenLastCalledWith("/api/sessions/saved-session/messages");
  });

  test("reports when a chat session cannot be created", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));

    render(<Chat />);

    expect(await screen.findByText("Chat session is unavailable.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Send message" })).toBeDisabled();
  });
});
