# Charlie Frontend

Next.js 16 + React 19 + Zustand dashboard for the Charlie voice-first AI assistant.

## Stack

- **Next.js 16** with `output: "export"` (static site)
- **React 19** with TypeScript 5
- **Zustand** for state management
- **Tailwind CSS v4** for styling
- **WebSocket** for real-time sync with `web_server.py`

## Architecture

```
src/
  app/page.tsx          # Main page - WebSocket, layout orchestration
  components/
    VoiceDock.tsx       # Bottom dock with voice state and waveform
    Sidebar.tsx         # Session list with active session sync
    SmartPanel.tsx      # Activity feed, tools, memory, knowledge graph
    MainWorkspace.tsx   # Chat display and message input
    WaveformBar.tsx     # Audio waveform visualization
    ErrorBoundary.tsx   # Production error boundary with fallback UI
  store/
    useCharlieStore.ts  # Zustand store (sessions, messages, blackboard, voice)
```

## Development

```bash
# Install dependencies
npm install

# Type-check
npx tsc --noEmit

# Dev server (port 3000)
npm run dev

# Test
npm test
```

## Key Features

- **Real-time WebSocket sync** with Charlie's backend (`web_server.py`)
- **Three-column layout**: Sidebar (sessions), Main (chat), Smart Panel (activity)
- **Voice dock** with waveform visualization and voice state display
- **Session management**: Create, switch, rename, delete sessions
- **Blackboard HUD**: Live view of swarm agents, tasks, and findings
- **Activity feed**: Real-time tool calls and thinking updates
- **Glassmorphism design**: Frosted glass panels with electric blue accent (#0066ff)

## WebSocket Events

The frontend listens for these events from `web_server.py`:

| Event | Purpose |
|-------|---------|
| `chat_response` | LLM streaming tokens |
| `blackboard_update` | Swarm state sync |
| `thinking_update` | Agent thinking updates |
| `system_status` | Health and diagnostics |
| `session_active` | Active session sync (frontend -> backend) |
