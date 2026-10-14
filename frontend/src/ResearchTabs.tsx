import type { ResearchResultData } from "./runtime";

export function researchDisplayTitle(query: string) {
  const title = query.trim()
    .replace(/^(?:please\s+)?(?:research|find|search(?:\s+for)?|look\s+up|check)\s+/i, "")
    .replace(/\s+(?:and\s+)?(?:summari[sz]e|explain|show|include|provide)\b[\s\S]*$/i, "")
    .replace(/\s+(?:with|including)\s+(?:sources?|citations?)\b[\s\S]*$/i, "")
    .replace(/^(?:current|latest|recent)\s+/i, "");
  const words = title.split(/\s+/).filter((word) => !/^(?:a|an|the|and|for|on|of|to|with|from|in|at|by)$/i.test(word));
  const selected: string[] = [];
  for (const word of words) {
    if ([...selected, word].join(" ").length > 30) break;
    selected.push(word);
  }
  const displayTitle = selected.join(" ") || title.slice(0, 30).trimEnd();
  const formatted = displayTitle ? displayTitle[0].toLocaleUpperCase() + displayTitle.slice(1) : query.trim();
  return formatted;
}

export function ResearchTabs({
  results,
  selectedId,
  onSelect,
  onDismiss,
}: {
  results: ResearchResultData[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  onDismiss: (id: string) => void;
}) {
  if (!results.length) return null;
  return (
    <nav className="research-tabs research-tabs--dock" aria-label="Research results">
      {results.map((result) => (
        <div className={`research-tab ${result.channel === "voice" ? "research-tab--voice" : ""}`} data-result-id={result.id} key={result.id}>
          <button
            type="button"
            className="research-tab__open"
            onClick={() => onSelect(result.id)}
            aria-pressed={selectedId === result.id}
            aria-label={`Open research answer: ${result.query}`}
          >
            <span className="research-reopen-tag">Research</span>
            <span className="research-reopen-query" title={result.query}>{result.channel === "voice" ? researchDisplayTitle(result.query) : result.query}</span>
          </button>
          <button
            type="button"
            className="research-tab__dismiss"
            onClick={() => onDismiss(result.id)}
            aria-label={`Dismiss research: ${result.query}`}
            title="Dismiss research"
          >×</button>
        </div>
      ))}
    </nav>
  );
}
