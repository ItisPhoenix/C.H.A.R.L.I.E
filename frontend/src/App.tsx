import { useEffect, type ReactElement } from "react";
import { BrowserRouter, Routes, Route } from "react-router-dom";
import { connectBridge } from "./runtime/bridge";
import { SurfaceRoute } from "./surfaces";

export default function App(): ReactElement {
  useEffect(() => connectBridge(), []);

  return (
    <BrowserRouter>
      <Routes>
        <Route path="/surface/:surfaceId" element={<SurfaceRoute />} />
      </Routes>
    </BrowserRouter>
  );
}
