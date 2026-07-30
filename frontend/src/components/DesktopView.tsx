"use client";

import { useState } from "react";
import type { ReactElement } from "react";
import { useCharlieStore } from "../store/useCharlieStore";

interface DesktopViewProps {
  onStartBackgroundTask: (text: string) => void;
  onCancelBackgroundTask: (taskId: string) => void;
  onApproveBackgroundTask: (taskId: string) => void;
  onRejectBackgroundTask: (taskId: string) => void;
}

function EmptyState({ text }: { text: string }): ReactElement {
  return (
    <div className="h-40 flex items-center justify-center px-6 text-center text-sm text-[var(--color-text-muted)]">
      {text}
    </div>
  );
}

function taskStatusColor(status: string): string {
  switch (status) {
    case "running":
      return "var(--color-accent-teal)";
    case "paused":
    case "awaiting_approval":
      return "var(--color-status-warning)";
    case "done":
      return "#9ca3af";
    case "failed":
      return "var(--color-status-error)";
    default:
      return "var(--color-text-muted)";
  }
}

function BackgroundTaskChip({
  onCancelBackgroundTask,
  onApproveBackgroundTask,
  onRejectBackgroundTask,
}: Omit<DesktopViewProps, "onStartBackgroundTask">): ReactElement | null {
  const task = useCharlieStore((s) => s.backgroundTask);
  if (!task || task.status === "done" || task.status === "failed" || task.status === "cancelled") return null;

  const totalSteps = task.steps.length;

  return (
    <div className="rounded-xl border border-[var(--color-glass-border)] bg-[var(--color-glass-bg-2)] p-3 space-y-2">
      <div className="flex items-center gap-2">
        <span
          className="w-2 h-2 rounded-full shrink-0"
          style={{ backgroundColor: taskStatusColor(task.status) }}
        />
        <p className="text-sm text-[var(--color-text-primary)] truncate flex-1" title={task.text}>
          {task.text}
        </p>
        <span className="text-[10px] uppercase tracking-wide text-[var(--color-text-muted)] shrink-0">
          {task.status.replace("_", " ")}
        </span>
      </div>
      {totalSteps > 0 && (
        <p className="text-xs text-[var(--color-text-muted)]">
          Step {Math.min(task.current_step + 1, totalSteps)}/{totalSteps}
        </p>
      )}
      {task.error && <p className="text-xs text-[var(--color-status-error)]">{task.error}</p>}
      <div className="flex items-center justify-end gap-1.5">
        {task.status === "awaiting_approval" ? (
          <>
            <button
              onClick={() => onRejectBackgroundTask(task.id)}
              className="text-xs px-2.5 py-1 rounded-lg border border-[var(--color-glass-border)] text-[var(--color-text-secondary)] hover:bg-[var(--color-glass-bg-2)]"
            >
              Decline
            </button>
            <button
              onClick={() => onApproveBackgroundTask(task.id)}
              className="text-xs px-2.5 py-1 rounded-lg bg-[var(--color-accent-teal)] text-black font-medium"
            >
              Approve
            </button>
          </>
        ) : (
          <button
            onClick={() => onCancelBackgroundTask(task.id)}
            className="text-xs px-2.5 py-1 rounded-lg border border-[var(--color-glass-border)] text-[var(--color-text-secondary)] hover:bg-[var(--color-glass-bg-2)]"
          >
            Cancel
          </button>
        )}
      </div>
    </div>
  );
}

function StartBackgroundTaskForm({ onStartBackgroundTask }: Pick<DesktopViewProps, "onStartBackgroundTask">): ReactElement {
  const [text, setText] = useState("");

  const submit = () => {
    const trimmed = text.trim();
    if (!trimmed) return;
    onStartBackgroundTask(trimmed);
    setText("");
  };

  return (
    <div className="flex items-center gap-2">
      <input
        type="text"
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => e.key === "Enter" && submit()}
        placeholder="Describe an unattended task to run in the background..."
        className="flex-1 text-sm px-3 py-2 rounded-lg bg-[var(--color-glass-bg-2)] border border-[var(--color-glass-border)] text-[var(--color-text-primary)] placeholder:text-[var(--color-text-muted)]"
      />
      <button
        onClick={submit}
        className="text-xs px-3 py-2 rounded-lg border border-[var(--color-glass-border)] text-[var(--color-text-secondary)] hover:bg-[var(--color-glass-bg-2)] shrink-0"
      >
        Start
      </button>
    </div>
  );
}

export function DesktopView({
  onStartBackgroundTask,
  onCancelBackgroundTask,
  onApproveBackgroundTask,
  onRejectBackgroundTask,
}: DesktopViewProps): ReactElement {
  const frame = useCharlieStore((s) => s.latestDesktopFrame);
  const toolActivity = useCharlieStore((s) => s.toolActivity);
  const backgroundTask = useCharlieStore((s) => s.backgroundTask);
  const taskActive = !!backgroundTask && !["done", "failed", "cancelled"].includes(backgroundTask.status);

  return (
    <div className="space-y-4">
      {taskActive ? (
        <BackgroundTaskChip
          onCancelBackgroundTask={onCancelBackgroundTask}
          onApproveBackgroundTask={onApproveBackgroundTask}
          onRejectBackgroundTask={onRejectBackgroundTask}
        />
      ) : (
        <StartBackgroundTaskForm onStartBackgroundTask={onStartBackgroundTask} />
      )}

      <div className="rounded-2xl overflow-hidden border border-[var(--color-glass-border)] bg-[var(--color-glass-bg-2)]">
        {frame ? (
          // eslint-disable-next-line @next/next/no-img-element -- base64 data URI, next/image doesn't optimize these
          <img
            src={`data:image/png;base64,${frame.imageB64}`}
            alt="Live view of Charlie's screen"
            className="w-full h-auto block"
          />
        ) : (
          <EmptyState text="No live view yet -- Charlie hasn't looked at the screen this session." />
        )}
      </div>

      <div>
        <p className="text-xs uppercase tracking-widest text-[var(--color-text-muted)] mb-2">
          Action log
        </p>
        {toolActivity.length === 0 ? (
          <p className="text-sm text-[var(--color-text-muted)]">No actions yet.</p>
        ) : (
          <div className="space-y-1.5 max-h-64 overflow-y-auto pr-1 scrollbar">
            {toolActivity
              .slice()
              .reverse()
              .map((e, i) => (
                <div
                  key={i}
                  className="text-[11px] font-mono px-2 py-1.5 rounded-lg bg-[var(--color-glass-bg-2)] border border-[var(--color-glass-border)] text-[var(--color-text-secondary)] break-all"
                >
                  <span className="text-[var(--color-accent-teal)]">{e.name}</span>{" "}
                  <span className="text-[var(--color-text-muted)]">{e.kind}</span>
                  {e.text && (
                    <span className="block text-[var(--color-text-muted)] mt-0.5">{e.text}</span>
                  )}
                </div>
              ))}
          </div>
        )}
      </div>
    </div>
  );
}
