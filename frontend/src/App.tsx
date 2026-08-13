import { useEffect, type ReactElement } from "react";
import { BrowserRouter, Routes, Route } from "react-router-dom";
import { connectBridge } from "./runtime/bridge";
import { SurfaceRoute } from "./surfaces";
import { Dashboard } from "./dashboard/Dashboard";

export default function App(): ReactElement {
  useEffect(() => connectBridge(), []);

  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Dashboard />} />
        <Route path="/dashboard" element={<Dashboard />} />
        <Route path="/surface/:surfaceId" element={<SurfaceRoute />} />
      </Routes>
    </BrowserRouter>
  );
}
