import { useEffect, useState, type FormEvent } from "react";
import type { RuntimeConversation, RuntimeMediaSnapshot, RuntimeResearch, RuntimeTask, MediaAction } from "../runtime/types";
import { HudAction, HudHeading, HudIconButton, HudLabel, HudMedia, HudTextInput, HudTimeline } from "./HudPrimitives";

export function ChatWidget({ conversation, thinking, onDismiss, onSend }: {
  conversation: RuntimeConversation;
  thinking: string;
  onDismiss?: () => void;
  onSend?: (text: string) => boolean;
}) {
  const [draft, setDraft] = useState("");
  const [sendError, setSendError] = useState("");
  const submit = (event: FormEvent) => {
    event.preventDefault();
    const message = draft.trim();
    if (!message || !onSend) return;
    if (onSend(message)) {
      setDraft("");
      setSendError("");
    } else {
      setSendError("Message not sent.");
    }
  };
  return <div className="runtime-widget runtime-chat-widget" data-widget="conversation">
    {conversation.userText && <>
      <HudLabel className="runtime-widget__sender">YOU</HudLabel>
      <p className="runtime-chat-widget__user">{conversation.userText}</p>
    </>}
    <HudLabel className="runtime-widget__sender runtime-widget__sender--charlie">CHARLIE</HudLabel>
    {thinking && !conversation.responseText && <p className="runtime-thinking">{thinking}</p>}
    {conversation.responseText && <p className="runtime-response">{conversation.responseText}
      {conversation.streaming && <span className="runtime-cursor" aria-hidden="true">▋</span>}
    </p>}
    {!thinking && !conversation.responseText && <p className="metadata">Conversation ready.</p>}
    {onSend && <form className="runtime-chat-composer" onSubmit={submit}>
      <HudTextInput aria-label="Message Charlie" placeholder="Message Charlie" value={draft}
        onChange={event => setDraft(event.target.value)} />
      <HudAction type="submit" className="text-action" disabled={!draft.trim()}>Send</HudAction>
      {sendError && <span className="runtime-error" role="status">{sendError}</span>}
    </form>}
    {onDismiss && <HudAction className="text-action runtime-widget__action" onClick={onDismiss}>
      Close chat <span aria-hidden="true">×</span>
    </HudAction>}
  </div>;
}

export function TaskWidget({ tasks, onFocus, onDismiss }: {
  tasks: RuntimeTask[];
  onFocus?: (taskId: string) => void;
  onDismiss?: () => void;
}) {
  return <div className="runtime-widget runtime-task-widget" data-widget="tasks">
    <HudLabel>TASK{tasks.length === 1 ? "" : "S"}</HudLabel>
    <HudHeading className="runtime-widget__heading">{tasks.length} active task{tasks.length === 1 ? "" : "s"}</HudHeading>
    <ol className="runtime-list runtime-task-list">
      {tasks.slice(0, 3).map(task => <li key={task.id}>
        <span className="status-dot" />
        <div>
          <strong>{task.title || `Task ${task.id}`}</strong>
          <p className="metadata">{task.status || "active"}{task.current_action || task.currentAction
            ? ` · ${task.current_action || task.currentAction}` : ""}</p>
          {onFocus && <HudAction className="text-action runtime-widget__action"
            onClick={() => onFocus(task.id)} aria-label={`Open task ${task.title || task.id}`}>
            Open task <span aria-hidden="true">↗</span>
          </HudAction>}
        </div>
      </li>)}
    </ol>
    {onDismiss && <HudAction className="text-action runtime-widget__action" onClick={onDismiss}>
      Hide tasks <span aria-hidden="true">×</span>
    </HudAction>}
  </div>;
}

export function ResearchWidget({ research, onDismiss }: {
  research: RuntimeResearch;
  onDismiss?: () => void;
}) {
  if (research.progress) {
    const { stage, message, current, total } = research.progress;
    return <div className="runtime-widget runtime-research-widget" data-widget="research">
      <HudLabel>RESEARCH</HudLabel>
      <HudHeading className="runtime-widget__heading">{stage}</HudHeading>
      <p>{message}</p>
      {current !== undefined && total !== undefined && <p className="metadata">Reading source {current} / {total}</p>}
      {onDismiss && <HudAction className="text-action runtime-widget__action" onClick={onDismiss}>
        Hide research <span aria-hidden="true">×</span>
      </HudAction>}
    </div>;
  }
  const result = research.result;
  if (!result) return null;
  const findings = Array.isArray(result.findings) ? result.findings : [];
  const sources = Array.isArray(result.sources) ? result.sources.length : undefined;
  return <div className="runtime-widget runtime-research-widget" data-widget="research">
    <HudLabel>RESEARCH COMPLETE</HudLabel>
    <HudHeading className="runtime-widget__heading">{findings.length} finding{findings.length === 1 ? "" : "s"}
      {sources !== undefined ? ` · ${sources} source${sources === 1 ? "" : "s"}` : ""}</HudHeading>
    <p>{typeof result.summary === "string" ? result.summary : "Research result ready."}</p>
    {onDismiss && <HudAction className="text-action runtime-widget__action" onClick={onDismiss}>
      Hide research <span aria-hidden="true">×</span>
    </HudAction>}
  </div>;
}

const formatTime = (value: unknown) => {
  const seconds = typeof value === "number" && Number.isFinite(value) ? Math.max(0, Math.floor(value)) : 0;
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
};

export function MediaWidget({ media, pending, onControl, onDismiss, localControls = false }: {
  media: RuntimeMediaSnapshot;
  pending?: boolean;
  onControl?: (action: MediaAction) => void;
  onDismiss?: () => void;
  localControls?: boolean;
}) {
  const duration = media.duration_seconds ?? 0;
  const mediaPosition = media.position_seconds ?? 0;
  const [localStatus, setLocalStatus] = useState(media.status);
  const [localPosition, setLocalPosition] = useState(mediaPosition);
  useEffect(() => {
    if (!localControls) return;
    setLocalStatus(media.status);
    setLocalPosition(mediaPosition);
  }, [localControls, media.status, mediaPosition]);
  const status = localControls ? localStatus : media.status;
  const position = localControls ? localPosition : mediaPosition;
  const handleControl = (action: MediaAction) => {
    if (localControls) {
      if (action === "play_pause") setLocalStatus(status === "playing" ? "paused" : "playing");
      if (action === "prev_track") setLocalPosition(0);
      if (action === "next_track") setLocalPosition(Math.min(duration, position + 30));
    }
    onControl?.(action);
  };
  return <div className="runtime-widget runtime-media-widget" data-widget="media" data-local-controls={localControls || undefined}>
    {media.art_uri && <img src={media.art_uri} alt={`${media.title || "Media"} artwork`} className="runtime-media-widget__art" />}
    <HudMedia title={media.title || "Current media"} artist={media.artist}>
      <HudTimeline position={position} duration={duration} label={`${formatTime(position)} of ${formatTime(duration)}`}
        onSeek={localControls ? setLocalPosition : undefined} />
      <p className="metadata">{formatTime(position)} / {formatTime(duration)} · {status || "unknown"}</p>
      {(onControl || localControls) && <div className="runtime-media-widget__controls" aria-label="Media controls">
        <HudIconButton label="Previous track" disabled={pending} onClick={() => handleControl("prev_track")}>◀</HudIconButton>
        <HudIconButton label={status === "playing" ? "Pause" : "Play"} disabled={pending}
          pressed={status === "playing"} onClick={() => handleControl("play_pause")}>
          {status === "playing" ? "Ⅱ" : "▶"}
        </HudIconButton>
        <HudIconButton label="Next track" disabled={pending} onClick={() => handleControl("next_track")}>▶</HudIconButton>
      </div>}
      {onDismiss && <HudAction className="text-action runtime-widget__action" onClick={onDismiss}>
        Hide media <span aria-hidden="true">×</span>
      </HudAction>}
    </HudMedia>
  </div>;
}

export interface SystemIssue {
  label: string;
  detail?: string;
}

export function SystemWidget({ issues, onDismiss }: { issues: SystemIssue[]; onDismiss?: () => void }) {
  const visibleIssues = issues.slice(0, 3);
  return <div className="runtime-widget runtime-system-widget" data-widget="system">
    <HudLabel>SYSTEM</HudLabel>
    {visibleIssues.map(issue => <p className="runtime-system-widget__issue" key={issue.label}>
      <strong>{issue.label}</strong>{issue.detail && <span>{issue.detail}</span>}
    </p>)}
    {issues.length > visibleIssues.length && <p className="metadata">{issues.length - visibleIssues.length} more service{issues.length - visibleIssues.length === 1 ? "" : "s"} need attention</p>}
    {onDismiss && <HudAction className="text-action runtime-widget__action" onClick={onDismiss}>
      Hide status <span aria-hidden="true">×</span>
    </HudAction>}
  </div>;
}
