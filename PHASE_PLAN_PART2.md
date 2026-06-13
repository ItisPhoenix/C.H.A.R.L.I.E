## Phase 2: Orchestrator as Main Entry (Week 2)
**Goal**: All input routes through Orchestrator; agents work

### 2.1 Route All Text Through Orchestrator
- ChainExecutor.execute_chain -> Orchestrator.route_goal()
- Orchestrator decides: single agent / multi-agent / direct tool

### 2.2 Direct Agent Routing
- @agent_name <query> prefix -> routes to that agent
- Natural language: "ask research to..." -> LLM detects intent

### 2.3 AgentRuntime Tool Access
- Share brain.tool_registry with AgentRuntime
- Agents filter by agent_spec.tools list
- MCP tools via MCPManager

### 2.4 Decomposition for Complex Goals
- Orchestrator._decompose_goal_llm() -> subtasks
- Parallel execution via asyncio.gather()
- Merge results with LLM

### 2.5 Test Multi-Agent
- "Research X and write summary" -> research + writer agents
- @coding fix bug in auth.py -> coding agent directly

## Phase 3: MCP Gateway + Custom Servers (Week 2-3)
**Goal**: MCP tools discoverable and callable

### 3.1 docker-compose.yml for mcp/gateway
- Official mcp/gateway image on port 8080
- SSE endpoint, token from env

### 3.2 DaemonSupervisor Starts Gateway
- Start container on daemon startup
- Track container ID for cleanup

### 3.3 MCPManager Initialization
- Initialize at Brain startup
- Connect to gateway, discover tools
- Register as native_mcp_* tools

### 3.4 Custom MCP Servers (Config + UI)
- Static: charlie_config.json + docker-compose override
- Dynamic: Dashboard "Add MCP Server" -> spawns container

### 3.5 Test MCP Tools
- Orchestrator routes to MCP tools
- Dashboard shows servers, tools, can toggle

## Phase 4: Full Dashboard (Week 3)
**Goal**: All 7 MVP pages show real data

### 4.1 BrainRPC Endpoints
- GET_AGENT_STATUS -> agent list + status
- GET_AUTOMATION_RULES -> rules from Brain
- GET_TASKS -> task queue status
- GET_MEMORY_SEARCH -> ChromaDB query

### 4.2 MVP Pages (7)
1. Voice - orb, transcript, mute, status
2. Chat - history, send message
3. Status - subsystem health, uptime
4. Logs - orchestrator trace, filter
5. Approvals - pending Tier 2+ actions
6. Settings - config editor, audio devices
7. Skills - filesystem scan, enable/disable

### 4.3 WebSocket Reconnection
- Auto-reconnect on disconnect
- Token refresh

## Phase 5: Tauri Companion App (Week 3-4)
**Goal**: Floating orb on desktop

### 5.1 Tauri v2 Project
- charlie-companion/ separate app
- Transparent, always-on-top, frameless

### 5.2 Rive Integration
- .riv file with 9-state face machine
- States: idle, listening, thinking, speaking, error, happy, confused, surprised, sleepy

### 5.3 WebSocket to :8090
- VOICE_ACTIVITY, THINKING_STATUS, ORCHESTRATOR_UPDATE
- Status toast component (floating, non-blocking)

### 5.4 Interactions
- Drag orb anywhere
- Click -> expand mini-chat (300x400)
- Right-click -> Mute/Standby/Settings/Quit

### 5.5 Lip-sync
- Rive AI Agent from TTS audio stream

## Phase 6: Browser Automation (Phase 2+)
**Goal**: Playwright MCP for full web control

### 6.1 Playwright MCP Server (stdio)
- Local Chromium, 40+ tools
- Expose as MCP tools + internal browser_agent tool

### 6.2 Orchestrator Integration
- Tool: browser_agent (navigate, click, type, screenshot, etc.)
- Routes when JS rendering needed

### 6.3 SearXNG (Optional)
- Docker service on :8888
- Replace DDG for private search