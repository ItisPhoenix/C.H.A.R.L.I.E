import type { ResearchResultData } from "./runtime";

export function ResearchTabs({
  results,
  selectedId,
  onSelect,
  onDismiss,
  placement = "dock",
}: {
  results: ResearchResultData[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  onDismiss: (id: string) => void;
  placement?: "dock" | "panel";
}) {
  if (!results.length || (placement === "panel" && results.length < 2)) return null;
  return (
    <nav className={`research-tabs research-tabs--${placement}`} aria-label="Research results">
      {results.map((result) => (
        <div className="research-tab" data-result-id={result.id} key={result.id}>
          <button
            type="button"
            className="research-tab__open"
            onClick={() => onSelect(result.id)}
            aria-pressed={selectedId === result.id}
            aria-label={`Open research answer: ${result.query}`}
          >
            <span className="research-reopen-tag">Research</span>
            <span className="research-reopen-query">{result.query}</span>
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
