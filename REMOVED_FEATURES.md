# Removed Features Registry

This file tracks the features and modules removed during the "Charlie Strip-Down" on 2026-06-13. 

## 1. Front-ends
- **Next.js Dashboard** (`dashboard/`): The web UI on port 3000.
- **Tauri Companion** (`charlie-companion/`): The desktop app shell.
- **Control Server API** (`charlie/watchdog/control_server.py`): The REST/WS API used by the dashboard.

## 2. Integrations
- **Telegram Bot** (`charlie/telegram/`): Complete Telegram bridge, voice reply, and "Jarvis" mode.
- **MCP Gateway** (`charlie/mcp/`, `docker-compose.yml`): The Model Context Protocol bridge and SearXNG search engine.

## 3. Advanced AI & Autonomous Engines
- **Self-Modification** (`charlie/self_mod/`): "Soul editor" and ability for the agent to edit its own core.
- **Intelligence Stack** (`charlie/intelligence/`): Evolution engine, skill synthesizer, graph scheduler, and timeline management.
- **Automation Loops** (`charlie/automation/`): Autonomy loops and rule-based risk gates.
- **Brain Learning** (`charlie/brain/learning.py`, `skill_loader.py`): The mechanisms for persistent learning from interactions.
- **Agent Manifests** (`charlie/agents/`): Directory containing specialized agent definitions (coding, research, etc.).
- **Orchestrator** (`charlie/brain/orchestrator.py`): High-level goal decomposition and multi-agent coordination logic.

## 4. Automation & Tooling
- **Browser Automation** (`charlie/browser/`, `browser_tools.py`): Headless Chromium control via Playwright/CloakBrowser.
- **Desktop Automation** (`charlie/tools/desktop_automation.py`, `gui_agent.py`): Control of mouse/keyboard, screen understanding, and Android device control.
- **GUI Agent** (`charlie/tools/gui_agent.py`): Visual agent for interacting with desktop applications.
- **Report Tools** (`charlie/tools/report_tools.py`): Automated report generation.
- **App Controller** (`charlie/tools/app_controller.py`): Control of external applications.
- **Vision Context** (`charlie/tools/vision_context.py`): Metadata handling for visual tasks.
- **Messenger** (`charlie/tools/messenger.py`): Abstraction layer for Telegram/IPC messaging.

## 5. System Infrastructure
- **Watchdog Supervisor** (`charlie/watchdog/phoenix.py`, `daemon_supervisor.py`): The multi-process supervisor that auto-restarts crashed subsystems.
- **System Tray Icon** (`charlie/watchdog/tray.py`): The Windows tray icon for background operation.
- **IPC Manager** (`charlie/ipc/`): Cross-process communication bus (simplified to direct brain run).
- **Daemon Mode**: The `--daemon` flag and background process state management.
- **Launcher Scripts** (`start-charlie.ps1`, `*.bat`, `*.sh`): Automated startup scripts for multi-process mode.
- **Dashboard Type Generation** (`scripts/generate_ts_types.py`): Synchronization logic between Python and TypeScript.
- **Extended Diagnostic Checks** (`charlie/utils/doctor.py`): Logic for checking Browser, MCP, and Automation health.
- **UI Automation Constants** (`charlie/brain/constants.py`): Keywords for triggering GUI/PyAutoGUI actions.
