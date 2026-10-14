import { researchDisplayTitle } from "./ResearchTabs";
import type { ResearchResultData, ResearchSource } from "./runtime";

// Search stays result-first; source detail follows Arc's progressive disclosure and assistant-ui's Sources pill pattern.
export function ResearchPanel({
  result,
  onClose,
  inline: inChat = false,
}: {
  result: ResearchResultData;
  onClose: () => void;
  inline?: boolean;
}) {
  const reportLabel = result.mode === "quick" ? "Search" : "Research";
  const marker = result.text.lastIndexOf("\n\nSources:\n");
  const answer = result.answer || (marker >= 0 ? result.text.slice(0, marker) : result.text);
  const rawAnswer = result.gaps?.length
    ? answer.replace(/\n\s*(?:\*{0,2}Gaps and Notes\*{0,2}\s*:|#{1,6}\s+Coverage gaps)\s*[\s\S]*$/i, "").trim()
    : answer;
  const title = researchDisplayTitle(!inChat && result.channel === "voice"
    ? result.query
    : result.query.split(/[.!?]\s+/)[0]);
  const displayGap = (gap: string) => {
    let label = gap.replace(/^Unresolved:\s*/i, "");
    for (const question of result.coverage ?? []) {
      if (label.includes(question.question) && question.requiredFields.length === 1) {
        const field = question.requiredFields[0];
        if (!field.includes(":")) label = label.replace(question.question, field.replaceAll("_", " "));
      }
    }
    if (/^(?:answer|response|result)$/i.test(label.trim())) return "The available sources did not support a complete answer.";
    return label;
  };
  const sources: ResearchSource[] = result.sources && result.sources.length > 0 ? result.sources : (
    marker >= 0 ? result.text.slice(marker + "\n\nSources:\n".length).split("\n").flatMap((line) => {
      const match = line.match(/^\[(S\d+)\] (.*): (https?:\/\/\S+)$/);
      if (!match) return [];
      try {
        const url = new URL(match[3]);
        return url.hostname ? [{ id: match[1], title: match[2], url: url.href }] : [];
      } catch { return []; }
    }) : []
  );

  const formatSourceClass = (cls?: string) => {
    switch (cls) {
      case "official": return "Official";
      case "official_store": return "Official Store";
      case "retailer": return "Retailer";
      case "official_unverified": return "Unverified Official";
      case "unknown": return "Unverified";
      case "review": return "Review";
      case "news": return "News";
      default: return "";
    }
  };

  const renderResourceDetails = (id: string, singular: string, plural: string, count: number | undefined, resources: ResearchResultData["searchResults"]) => {
    const itemCount = count ?? resources?.length ?? 0;
    const label = itemCount === 1 ? singular : plural;
    if (resources?.length) return <details key={id} className="research-resource-disclosure">
      <summary><strong>{itemCount}</strong> {label}</summary>
      <ol className="research-resource-items">
        {resources.map((resource, index) => (
          <li key={`${resource.url}-${index}`}>
            <a href={resource.url} target="_blank" rel="noreferrer">
              <strong>{resource.title}</strong>
              <span>{resource.domain}</span>
              <small>{resource.url}</small>
            </a>
          </li>
        ))}
      </ol>
    </details>;
    return count !== undefined ? <span key={id} className="research-resource-count"><strong>{count}</strong> {label}</span> : null;
  };
  const normalizedAnswer = answer.replace(/\s+/g, " ").toLowerCase();
  const answerSupportedPassageIds = new Set((result.claims ?? [])
    .filter((claim) => claim.relationship === "supports"
      && Boolean(claim.text.trim())
      && normalizedAnswer.includes(claim.text.replace(/\s+/g, " ").trim().toLowerCase()))
    .flatMap((claim) => claim.evidencePassageIds));

  const inline = (text: string) => text.split(/(\*\*[^*]+\*\*|\*[^*]+\*|_[^_]+_|`[^`]+`|\[S\d+\]|https?:\/\/[^\s]+)/g).map((part, index) => {
    if (part.startsWith("**")) return <strong key={index}>{part.slice(2, -2)}</strong>;
    if (part.startsWith("*") || part.startsWith("_")) return <em key={index}>{part.slice(1, -1)}</em>;
    if (part.startsWith("`")) return <code key={index}>{part.slice(1, -1)}</code>;
    if (/^https?:\/\//.test(part)) return <a key={index} href={part} target="_blank" rel="noreferrer">{part}</a>;
    const source = sources.find((item) => `[${item.id}]` === part);
    return source ? <a className="research-citation" key={index} href={source.url} target="_blank" rel="noreferrer" aria-label={`Source ${source.id}: ${source.title}`}>{source.id.replace(/^S/i, "")}</a> : part;
  });

  const markdownBlocks: string[] = [];
  let pending: string[] = [];
  const flush = () => { if (pending.length) markdownBlocks.push(pending.join("\n")); pending = []; };
  const isList = (line: string) => /^\s*(?:[-*]|\d+[.)])\s+/.test(line);
  for (const line of rawAnswer.split("\n")) {
    if (!line.trim()) { flush(); continue; }
    if (/^#{1,6}\s+/.test(line)) { flush(); markdownBlocks.push(line); continue; }
    if (pending.length && isList(line) !== isList(pending[0])) flush();
    pending.push(line);
  }
  flush();
  const visibleMarkdownBlocks = markdownBlocks.filter((block) => !/^#{1,6}\s+(?:answer|response)\s*$/i.test(block.trim()));
  const blocks = visibleMarkdownBlocks.map((block, index) => {
    const lines = block.trim().split("\n");
    if (!block.trim() || /^[-*_]{3,}$/.test(block.trim())) return null;
    if (lines.length > 1 && lines[0].includes("|") && /^[\s|:-]+$/.test(lines[1])) {
      const cells = (line: string) => line.replace(/^\s*\||\|\s*$/g, "").split("|").map((cell) => cell.trim());
      const rows = lines.slice(2).map(cells).filter((row) => row.some((cell) => cell && cell !== "—" && cell !== "-"));
      return <div className="research-table" key={index}><table><thead><tr>{cells(lines[0]).map((cell, i) => <th key={i}>{inline(cell)}</th>)}</tr></thead><tbody>{rows.map((row, rowIndex) => <tr key={rowIndex}>{row.map((cell, i) => <td key={i}>{inline(cell)}</td>)}</tr>)}</tbody></table></div>;
    }
    if (lines.every((line) => /^\s*(?:[-*]|\d+[.)])\s+/.test(line))) {
      const ordered = lines.every((line) => /^\s*\d+[.)]\s+/.test(line));
      const List = ordered ? "ol" : "ul";
      return <List className="research-answer-list" key={index}>{lines.map((line, i) => <li key={i}>{inline(line.replace(/^\s*(?:[-*]|\d+[.)])\s+/, ""))}</li>)}</List>;
    }
    const recommendation = /^\s*\*{0,2}Recommendation\*{0,2}\s*:/i.test(block);
    return <div className={recommendation ? "research-recommendation" : undefined} key={index}>{lines.map((line, i) => /^#{1,6}\s+/.test(line)
      ? /^#{1,2}\s+/.test(line) ? <h2 key={i}>{inline(line.replace(/^#{1,6}\s+/, ""))}</h2> : <h3 key={i}>{inline(line.replace(/^#{1,6}\s+/, ""))}</h3>
      : <p key={i}>{inline(line)}</p>)}</div>;
  });
  const sourceDetails = sources.length > 0 && <details key="sources" className="research-sources research-resource-disclosure">
    <summary><strong>{sources.length}</strong> {sources.length === 1 ? "source cited" : "sources cited"}</summary>
    <ol>
      {sources.map((source) => {
        const chip = formatSourceClass(source.class);
        const passage = result.channel === "voice" && !inChat
          ? result.passages?.find((item) => item.sourceId === source.id && answerSupportedPassageIds.has(item.id))?.text.trim().replace(/\s+/g, " ")
          : result.passages?.find((item) => item.sourceId === source.id)?.text.trim().replace(/\s+/g, " ");
        const factQuote = result.channel === "voice" && !inChat
          ? result.verifiedFacts?.find((item) => item.sourceId === source.id
            && normalizedAnswer.includes(item.quote.replace(/\s+/g, " ").trim().toLowerCase()))?.quote.trim().replace(/\s+/g, " ")
          : undefined;
        const supportedExcerpt = passage || factQuote;
        const excerpt = supportedExcerpt && (supportedExcerpt.length > 220 ? `${supportedExcerpt.slice(0, 220).trimEnd()}…` : supportedExcerpt);
        const domain = new URL(source.url).hostname;
        return (
          <li key={source.id}>
            <a href={source.url} target="_blank" rel="noreferrer">
              <span className="research-source-id">{source.id}</span>
              <span className="research-source-heading">
                <strong className="research-source-title">{source.title}</strong>
                {chip && <span className={`research-source-chip research-source-chip--${source.class}`}>{chip}</span>}
              </span>
              <span className="research-source-domain">{domain}</span>
              <small className="research-source-url">{source.url}</small>
              {excerpt && <span className="research-source-excerpt">{excerpt}</span>}
            </a>
          </li>
        );
      })}
    </ol>
  </details>;
  const sourceCountDetails = sourceDetails || (result.sources !== undefined && <span key="sources" className="research-resource-count"><strong>{sources.length}</strong> {sources.length === 1 ? "source cited" : "sources cited"}</span>);
  const acquisitionRows = [
    renderResourceDetails("links", "link found", "links found", result.searchResultCount, result.searchResults),
    renderResourceDetails("pages", "page read", "pages read", result.documentCount, result.readPages),
    sourceCountDetails,
  ].filter(Boolean);
  const providerOutcomes = result.providerOutcomes ?? [];
  const providerChallenge = providerOutcomes.some((outcome) => outcome.status === "challenge");
  const providerFailure = providerOutcomes.some((outcome) => outcome.status === "failed" || outcome.status === "unavailable");
  const providerCompleted = providerOutcomes.some((outcome) => outcome.status === "ok" || outcome.status === "empty");
  const availableResults = (result.searchResultCount ?? 0) > 0
    || Boolean(result.searchResults?.length)
    || Boolean(result.sources?.length);
  let outcomeMessage = "";
  if (availableResults && (providerChallenge || providerFailure)) {
    outcomeMessage = providerChallenge
      ? "Some search providers returned access challenges; the available results may not cover the full search."
      : "Some search providers failed or were unavailable; the available results may not cover the full search.";
  } else if (providerChallenge) {
    outcomeMessage = providerCompleted
      ? "Some providers returned access challenges, and completed searches found no relevant results."
      : "Search could not be completed because providers returned access challenges.";
  } else if (providerFailure) {
    outcomeMessage = providerCompleted
      ? "Some providers failed or were unavailable; completed searches found no relevant results."
      : "Search could not be completed because providers failed or were unavailable.";
  } else if (result.stopReason === "provider-challenge") {
    outcomeMessage = "Search could not be completed because providers returned access challenges.";
  } else if (result.stopReason === "provider-exhausted") {
    outcomeMessage = "Search could not be completed because providers failed or were unavailable.";
  } else if (result.stopReason === "provider-unconfigured") {
    outcomeMessage = "No search provider was available, so nothing was searched.";
  } else if (result.stopReason === "no-results") {
    outcomeMessage = "No relevant search results were available to verify.";
  } else if (result.stopReason === "search-snippets-only") {
    outcomeMessage = "Search results were found, but their source pages were not verified.";
  } else if (result.stopReason === "insufficient-evidence" || result.completeness === "none") {
    outcomeMessage = "The available sources did not verify enough of the requested evidence.";
  } else if (result.stopReason === "partial-evidence" || result.completeness === "partial" || result.partial) {
    outcomeMessage = result.gaps?.length
      ? "Some requested points remain unverified; see the gaps below."
      : "Some requested points remain unverified.";
  }
  const acquisitionDetails = (acquisitionRows.length > 0 || outcomeMessage) && (
    <div className="research-acquisition-details" aria-label={result.mode === "quick" ? "Search links and sources" : "Research activity and sources"}>
      {outcomeMessage && <p role="status">{outcomeMessage}</p>}
      {acquisitionRows}
    </div>
  );

  return (
    <article className={`research-panel ${inChat ? "research-panel--inline" : ""} ${!inChat && result.channel === "voice" ? "research-panel--voice" : ""}`} aria-label={result.query ? `${reportLabel} answer` : "Charlie answer"}>
      {(!inChat || result.query) && <header>
        <div>
          <div className="research-header-badges">
            <span className="research-label">{reportLabel}</span>
            {result.partial && <span className="research-badge research-badge--partial">Partial</span>}
          </div>
          <h2 title={result.query}>{title}</h2>
        </div>
        {!inChat && <button type="button" onClick={onClose} aria-label="Close research answer">×</button>}
      </header>}
      <div className="research-body">
        {blocks}
        {acquisitionDetails}
        {result.mode !== "quick" && result.gaps && result.gaps.length > 0 && (
          <div className="research-gaps">
            <h4>What could not be confirmed</h4>
            <ul>
              {result.gaps.map((gap, i) => <li key={i}>{displayGap(gap)}</li>)}
            </ul>
          </div>
        )}
      </div>
    </article>
  );
}
