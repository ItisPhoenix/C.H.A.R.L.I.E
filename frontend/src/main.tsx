import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { useCharlieStore } from './store/charlie'
import { useWorkspaceStore } from './layout/workspaceStore'
import { useWidgetStore } from './layout/widgetStore'
import { useMapStore } from './map/mapStore'

if (typeof window !== 'undefined') {
  (window as unknown as { __CHARLIE_STORES__: unknown }).__CHARLIE_STORES__ = {
    charlie: useCharlieStore,
    workspace: useWorkspaceStore,
    widget: useWidgetStore,
    map: useMapStore,
  };
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
