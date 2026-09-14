import { useEffect, useState, type ReactElement, type ReactNode } from "react";

interface SceneTransitionControllerProps {
  transitionKey: string;
  children: ReactNode;
}

export function SceneTransitionController({ transitionKey, children }: SceneTransitionControllerProps): ReactElement {
  const [settledKey, setSettledKey] = useState(transitionKey);
  const [transitioning, setTransitioning] = useState(false);

  useEffect(() => {
    if (settledKey === transitionKey) return;
    setSettledKey(transitionKey);
    setTransitioning(true);
    const timeout = window.setTimeout(() => setTransitioning(false), 420);
    return () => window.clearTimeout(timeout);
  }, [settledKey, transitionKey]);

  return (
    <div
      className="spatial-transition-controller"
      data-transitioning={transitioning ? "true" : "false"}
      data-transition-key={transitionKey}
    >
      {children}
    </div>
  );
}
