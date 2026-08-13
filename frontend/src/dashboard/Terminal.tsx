import { useEffect, useState, type FormEvent, type ReactElement } from "react";
import { Panel } from "./Panel";

export function Terminal(): ReactElement {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [output, setOutput] = useState("");
  const [line, setLine] = useState("");
  const [status, setStatus] = useState("idle");
  const [message, setMessage] = useState("");

  async function start(): Promise<void> {
    setMessage("");
    try {
      const response = await fetch("/api/terminal/sessions", { method: "POST" });
      if (!response.ok) throw new Error("Terminal unavailable");
      const data = await response.json() as { session_id: string; status: string; output: string };
      setSessionId(data.session_id);
      setStatus(data.status);
      setOutput(data.output);
    } catch {
      setMessage("Terminal unavailable.");
    }
  }

  useEffect(() => {
    if (!sessionId) return undefined;
    let active = true;
    const poll = async (): Promise<void> => {
      try {
        const response = await fetch(`/api/terminal/sessions/${sessionId}`);
        if (!response.ok || !active) return;
        const data = await response.json() as { status: string; output: string };
        setStatus(data.status);
        setOutput(data.output);
      } catch {
        if (active) setMessage("Terminal connection lost.");
      }
    };
    const timer = window.setInterval(() => void poll(), 500);
    void poll();
    return () => { active = false; window.clearInterval(timer); };
  }, [sessionId]);

  async function submit(event: FormEvent): Promise<void> {
    event.preventDefault();
    if (!sessionId || !line.trim()) return;
    if (!window.confirm("Run this command in the local terminal?")) return;
    try {
      const response = await fetch(`/api/terminal/sessions/${sessionId}/input`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ line, confirmed: true }),
      });
      if (!response.ok) throw new Error("Command blocked");
      setLine("");
      setMessage("");
    } catch {
      setMessage("Command blocked or unavailable.");
    }
  }

  return (
    <Panel id="terminal" title="Terminal">
      <div className="terminal-workspace">
        {sessionId ? <pre className="terminal-output" aria-label="Terminal output">{output || "Connected.\n"}</pre> : <p className="terminal-unavailable">Start a local shell session when you need one.</p>}
        {message ? <p className="terminal-message">{message}</p> : null}
        {!sessionId ? <button type="button" className="terminal-start" onClick={() => void start()}>Start terminal</button> : <form className="terminal-form" onSubmit={(event) => void submit(event)}><input aria-label="Terminal command" value={line} onChange={(event) => setLine(event.target.value)} placeholder={status === "running" ? "Enter command" : "Session ended"} disabled={status !== "running"} /><button type="submit" disabled={status !== "running"}>Run</button></form>}
      </div>
    </Panel>
  );
}
