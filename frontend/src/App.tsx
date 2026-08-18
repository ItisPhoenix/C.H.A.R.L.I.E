import { useEffect, type ReactElement } from "react";
import { BrowserRouter, Routes, Route } from "react-router-dom";
import { connectBridge } from "./runtime/bridge";
import { CharlieScene } from "./scene/CharlieScene";

export default function App(): ReactElement {
  useEffect(() => connectBridge(), []);

  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<CharlieScene />} />
      </Routes>
    </BrowserRouter>
  );
}
