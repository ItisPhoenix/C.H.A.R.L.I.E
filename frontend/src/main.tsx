import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { useCharlieStore } from './store/charlie'
import { useWorkspaceStore } from './layout/workspaceStore'
import { useWidgetStore } from './layout/widgetStore'
import { useMapStore } from './map/mapStore'

export interface CharlieBuildIdentity {
  build_id: string
  git_sha: string | null
  dirty: boolean | null
  built_at: string
}

declare global {
  interface Window {
    __CHARLIE_BUILD__?: CharlieBuildIdentity
  }
}

if (typeof window !== 'undefined') {
  (window as unknown as { __CHARLIE_STORES__: unknown }).__CHARLIE_STORES__ = {
    charlie: useCharlieStore,
    workspace: useWorkspaceStore,
    widget: useWidgetStore,
    map: useMapStore,
  };
  (window as unknown as { useWorkspaceStore: unknown }).useWorkspaceStore = useWorkspaceStore;
  (window as unknown as { useCharlieStore: unknown }).useCharlieStore = useCharlieStore;
  (window as unknown as { useMapStore: unknown }).useMapStore = useMapStore;
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
