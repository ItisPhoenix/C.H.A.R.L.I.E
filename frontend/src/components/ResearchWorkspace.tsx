import { useEffect, useMemo, useState, type FormEvent, type ReactNode } from "react";
import type { RuntimeResearch } from "../runtime/types";
import {
  normalizeResearchPayload,
  type EvidenceClass,
  type ResearchFinding,
  type ResearchPayload,
  type ResearchSource,
} from "../research/payload";

function text(value: unknown, fallback = ""): string {
  return typeof value === "string" && value.trim() ? value.trim() : fallback;
}

function inlineMarkdown(value: string): ReactNode[] {
  const parts: ReactNode[] = [];
  const pattern = /(\*\*[^*\n]+\*\*|`[^`\n]+`)/g;
  let cursor = 0;
  for (const match of value.matchAll(pattern)) {
    const token = match[0];
    const index = match.index ?? 0;
    if (index > cursor) parts.push(value.slice(cursor, index));
    if (token.startsWith("**")) {
      parts.push(<strong key={`${index}-${token}`}>{token.slice(2, -2)}</strong>);
    } else {
      parts.push(<code key={`${index}-${token}`}>{token.slice(1, -1)}</code>);
    }
    cursor = index + token.length;
  }
  if (cursor < value.length) parts.push(value.slice(cursor));
  return parts;
}

function ResearchSummary({ value }: { value: string }) {
  const lines = value.replace(/\s+-\s+(?=(?:\*\*|[A-Z0-9]))/g, "\n- ").split(/\r?\n/);
  return <div className="research-summary" aria-label="Research answer">
    {lines.map((line, index) => {
      const trimmed = line.trim();
      if (!trimmed) return <span className="research-summary__break" key={`break-${index}`} aria-hidden="true" />;
      const item = trimmed.match(/^[-*]\s+(.+)$/);
      return <p className={item ? "research-summary__item" : undefined} key={`${index}-${trimmed}`}>
        {inlineMarkdown(item ? item[1] : trimmed)}
      </p>;
    })}
  </div>;
}

function planItems(payload: ResearchPayload | null): Array<{ id: string; title: string; status?: string }> {
  if (!payload || !Array.isArray(payload.plan)) return [];
  return payload.plan.flatMap((item, index) => {
    if (typeof item === "string" && item.trim()) return [{ id: `plan-${index + 1}`, title: item.trim() }];
    if (!item || typeof item !== "object" || Array.isArray(item)) return [];
    const value = item as Record<string, unknown>;
    const title = text(value.title ?? value.question ?? value.detail);
    return title ? [{ id: text(value.id, `plan-${index + 1}`), title, status: text(value.status) || undefined }] : [];
  });
}

function sourceLabel(source: ResearchSource): string {
  return source.domain || source.url || "Source origin not reported";
}

function truthFor(research: RuntimeResearch, payload: ResearchPayload | null, override?: EvidenceClass): EvidenceClass {
  if (override) return override;
  if (research.truth) return research.truth;
  if (payload && payload.sources.length > 0 && !["error", "timeout", "no_results", "cancelled"].includes(payload.status)) {
    return "REAL RUNTIME";
  }
  return payload ? "SANDBOXED RUNTIME" : "NOT VERIFIED";
}

function CitationChips({ finding, onSource }: { finding: ResearchFinding; onSource: (sourceId: string) => void }) {
  return <div className="research-citations" aria-label={`Sources supporting ${finding.title}`}>
    {finding.source_ids.map(sourceId => <button key={sourceId} type="button" className="research-citation"
      onClick={() => onSource(sourceId)}>{sourceId}</button>)}
    {finding.source_ids.length === 0 && <span className="research-empty">No supporting source reported</span>}
  </div>;
}

function Finding({ finding, onSource }: { finding: ResearchFinding; onSource: (sourceId: string) => void }) {
  return <article className="research-finding" data-finding-id={finding.id}>
    <div className="research-finding__marker" aria-hidden="true">{finding.contradiction ? "△" : "◈"}</div>
    <div>
      <h3>{finding.title}</h3>
      <p>{finding.detail}</p>
      <CitationChips finding={finding} onSource={onSource} />
    </div>
  </article>;
}

function SourceCard({ source, selected, onSelect }: { source: ResearchSource; selected: boolean; onSelect: () => void }) {
  return <article className="research-source" data-source-id={source.id} data-selected={selected || undefined}>
    <button type="button" className="research-source__select" onClick={onSelect} aria-pressed={selected}>
      <span className="research-source__id">{source.id}</span>
      <span>
        <strong>{source.title}</strong>
        <small>{sourceLabel(source)}</small>
      </span>
    </button>
    {source.snippet && <p>{source.snippet}</p>}
    {source.url && <a href={source.url} target="_blank" rel="noreferrer">Open source ↗</a>}
  </article>;
}

function ResearchPulse({ research, payload, truth, onDismiss }: {
  research: RuntimeResearch;
  payload: ResearchPayload | null;
  truth: EvidenceClass;
  onDismiss?: () => void;
}) {
  const message = text(
    research.error || research.progress?.message || payload?.summary,
    payload?.stop_reason ? `Research stopped: ${payload.stop_reason}.` : "Researching…",
  );
  return <aside className="research-pulse" data-research-stage={research.progress?.stage ?? payload?.status ?? "starting"}
    data-truth={truth} aria-live="polite">
    <div className="research-pulse__topline">
      <span className="research-kicker">RESEARCH</span>
      <span className="research-truth">{truth}</span>
    </div>
    <p className="research-pulse__message">{message}</p>
    {onDismiss && <button type="button" className="research-pulse__close" onClick={onDismiss}>Hide ×</button>}
  </aside>;
}

export function ResearchWorkspace({ research, onDismiss, onSend, truth, compact = false }: {
  research: RuntimeResearch;
  onDismiss?: () => void;
  onSend?: (text: string) => boolean;
  truth?: EvidenceClass;
  compact?: boolean;
}) {
  const payload = useMemo(() => normalizeResearchPayload(research.result), [research.result]);
  const [selectedSourceId, setSelectedSourceId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");

  const objective = text(payload?.objective ?? payload?.query ?? research.objective, "Research objective not reported by runtime.");
  const stage = text(research.progress?.stage ?? payload?.status, "starting");
  const status = text(payload?.status ?? research.progress?.stage, "not reported");
  const plan = planItems(payload);
  const activities = research.activity ?? [];
  const sources = payload?.sources ?? [];
  const findings = payload?.findings ?? [];
  const evidenceClass = truthFor(research, payload, truth);
  const selectedSource = selectedSourceId ? sources.find(source => source.id === selectedSourceId) : undefined;
  const leadFinding = status === "reading" ? findings[0] : undefined;
  const payloadTitle = text(payload?.title);
  const hasUsefulTitle = Boolean(payloadTitle && !/^research\s*(?:&|and)\s*synthesis$/i.test(payloadTitle));
  const resultTitle = leadFinding?.title ?? (hasUsefulTitle ? payloadTitle : "Research result");
  const sourceProgress = research.progress?.current !== undefined && research.progress.total !== undefined
    ? `${research.progress.current} of ${research.progress.total} sources processed`
    : null;
  const [sourcesOpen, setSourcesOpen] = useState(false);

  const selectSource = (sourceId: string) => {
    setSelectedSourceId(sourceId);
    setSourcesOpen(true);
  };

  useEffect(() => {
    setSelectedSourceId(null);
    setSourcesOpen(false);
  }, [research.result]);

  if (compact) return <ResearchPulse research={research} payload={payload} truth={evidenceClass} onDismiss={onDismiss} />;

  const submitFollowUp = (event: FormEvent) => {
    event.preventDefault();
    const message = draft.trim();
    if (!message || !onSend || !onSend(message)) return;
    setDraft("");
  };

  return <section className="research-surface" data-research-stage={stage} data-truth={evidenceClass}
    aria-label="Research workspace">
    <header className="research-header">
      <div className="research-header__topline">
        <span className="research-kicker">RESEARCH</span>
        <span className="research-stage">{stage}</span>
        <span className={`research-truth research-truth--${evidenceClass.toLowerCase().replace(/[^a-z]+/g, "-")}`}>{evidenceClass}</span>
      </div>
      <h1>{resultTitle}</h1>
      {payload && !leadFinding && <ResearchSummary value={payload.summary} />}
      {leadFinding && <div className="research-lead-support">
        <p>{leadFinding.detail}</p>
        <CitationChips finding={leadFinding} onSource={selectSource} />
      </div>}
      <p className="research-objective"><span>QUESTION</span>{objective}</p>
    </header>

    <div className="research-flow">
      <div className="research-primary">
        {research.error && <section className="research-failure" role="status">
          <span className="research-kicker">RESEARCH PAYLOAD UNAVAILABLE</span>
          <p>{research.error}</p>
        </section>}
        {!research.error && !payload && <section className="research-investigation" aria-live="polite">
          <span className="research-kicker">ACTIVE INVESTIGATION</span>
          <h2>{research.progress?.message || "Research is preparing."}</h2>
          <p>Charlie is collecting runtime evidence. No final answer has been reported yet.</p>
        </section>}
        {payload && <section className="research-synthesis" aria-label="Research synthesis">
          {payload.stop_reason && <p className="research-stop">STOP // {payload.stop_reason}</p>}
          {findings.slice(leadFinding ? 1 : 0).length > 0 && <div className="research-findings" aria-label="Research findings">
            {findings.slice(leadFinding ? 1 : 0).map(finding => <Finding key={finding.id} finding={finding} onSource={selectSource} />)}
          </div>}
        </section>}
      </div>

      {(plan.length > 0 || activities.length > 0 || selectedSource || sourceProgress) && <details className="research-context" open={Boolean(selectedSource)}>
        <summary>Research context</summary>
        <div className="research-context__body">
          {sourceProgress && <section className="research-context__section">
            <span className="research-kicker">STATUS</span>
            <p className="research-context__status">{text(research.progress?.message, status)}. {sourceProgress}.</p>
          </section>}
          {plan.length > 0 && <section className="research-context__section">
            <span className="research-kicker">PLAN</span>
            <ol className="research-plan">{plan.map(item => <li key={item.id}>
              <span>{item.status || "queued"}</span><strong>{item.title}</strong>
            </li>)}</ol>
          </section>}
          {activities.length > 0 && <section className="research-context__section">
            <span className="research-kicker">LIVE ACTIVITY</span>
            <ol className="research-activity">{activities.slice(-5).map(item => <li key={item.id}>
              <span className="research-activity__dot" aria-hidden="true" /><div><strong>{item.stage}</strong><p>{item.message}</p></div>
            </li>)}</ol>
          </section>}
          {selectedSource && <section className="research-context__section research-context__selected" aria-live="polite">
            <span className="research-kicker">SELECTED SOURCE</span>
            <strong>{selectedSource.id} · {selectedSource.title}</strong>
            <p>{selectedSource.snippet || "No extracted evidence reported."}</p>
          </section>}
        </div>
      </details>}
    </div>

    {sources.length > 0 && <details className="research-evidence-disclosure" open={sourcesOpen}
      onToggle={event => setSourcesOpen(event.currentTarget.open)}>
      <summary className="research-evidence__heading">
        <span className="research-kicker">SOURCES / CITATIONS</span>
        <span className="research-evidence__note">Citations stay tied to source IDs.</span>
      </summary>
      <div className="research-source-list">
        {sources.map(source => <SourceCard key={source.id} source={source}
          selected={source.id === selectedSourceId} onSelect={() => selectSource(source.id)} />)}
      </div>
    </details>}

    {(onSend || onDismiss) && <footer className="research-actions">
      {onSend && <form onSubmit={submitFollowUp} className="research-follow-up">
        <label htmlFor="research-follow-up">FOLLOW UP</label>
        <input id="research-follow-up" value={draft} onChange={event => setDraft(event.target.value)}
          placeholder="Ask Charlie about this evidence" />
        <button type="submit" disabled={!draft.trim()}>Send</button>
      </form>}
      {onDismiss && <button type="button" className="research-close" onClick={onDismiss}>Close research ×</button>}
    </footer>}
  </section>;
}
