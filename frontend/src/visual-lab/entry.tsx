import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import "../index.css";
import { VisualLabHarness, type VisualLabScenario } from "./VisualLabHarness";

const scenario = (new URLSearchParams(window.location.search).get("scenario") || "idle") as VisualLabScenario;
createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <MemoryRouter>
      <VisualLabHarness scenario={scenario} />
    </MemoryRouter>
  </StrictMode>,
);
