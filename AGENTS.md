# AGENTS.md — Charlie Agent System

## Overview

Charlie uses 7 manifest-driven agents loaded from `charlie/agents/*/AGENT.md`. Each agent has a YAML frontmatter header defining its name, description, tools, skills, triggers, and config.

## Agent Routing

- **LLM-powered routing** — queries sent to best-fit agent via LLM
- **Keyword fallback** — matches query against agent trigger keywords
- **@agent override** — users can force a specific agent with `@agent_name`

## Coordinator Pattern

Complex goals are decomposed and dispatched across multiple agents:

1. **Decompose** — LLM breaks goal into 2-4 sub-tasks
2. **Route** — each sub-task assigned to specialist agent
3. **Execute** — agents run in parallel (asyncio.gather)
4. **Merge** — results combined via LLM into unified response

## Agents

| Agent | Tools | Purpose |
|-------|-------|---------|
| research | 18 | Web research, browser control, news analysis |
| coding | 11 | Code analysis, debugging, browser for docs |
| comms | 5 | Email, notifications, calendar |
| system | 10 | PC control, processes, shell commands |
| vision | 5 | Screen analysis, OCR, visual inspection |
| writer | 5 | File editing, documentation |
| redteam | 12 | Penetration testing, security analysis |

## AGENT.md Format

```yaml
---
name: research
description: Web research, search engines, browser fetching, news analysis
version: "1.0.0"
enabled: true
tools: ["search", "browser_fetch", "get_news", ...]
skills: ["deep-research", "source-verification"]
triggers:
  keywords: ["search", "find", "research", "look up"]
  intent_description: "Information gathering, web research"
config:
  max_chain_depth: 8
  timeout_seconds: 120
  priority: NORMAL
---
```

## Task Chain Execution

When a complex goal is decomposed, each sub-task is executed sequentially via the ReAct loop, with previous results fed as context into subsequent steps.

## Browser Tools

Research and Coding agents have full browser control:
- `browser_navigate`, `browser_click`, `browser_type`, `browser_scroll`
- `browser_screenshot`, `browser_new_tab`, `browser_close_tab`
- `browser_go_back`, `browser_go_forward`, `browser_control`
