import type {
  DaemonStatus,
  Approval,
  TimelineEntry,
  IntegrationHealth,
  AutomationRule,
  BriefingData,
  CharlieSettings,
  ChatMessage,
  Task,
  MCPServer,
  AgentStatus,
  ToolExecution,
} from './types'

const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8090'

class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public body?: string,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

async function apiFetch<T>(path: string, options?: RequestInit): Promise<T> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
  }
  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), 10000)
  try {
    const res = await fetch(`${API_BASE}${path}`, {
      headers,
      signal: controller.signal,
      cache: 'no-store',
      ...options,
    })
    if (!res.ok) {
      const body = await res.text().catch(() => '')
      throw new ApiError(res.status, `HTTP ${res.status}: ${res.statusText}`, body)
    }
    return res.json()
  } finally {
    clearTimeout(timeout)
  }
}

// === Existing endpoints ===

export function fetchStatus(): Promise<DaemonStatus> {
  return apiFetch('/api/status')
}

export async function checkBrainStatus(): Promise<boolean> {
  try {
    const status = await apiFetch<DaemonStatus>('/api/status')
    return status.subsystems?.Brain?.status === 'running'
  } catch {
    return false
  }
}

export function fetchApprovals(): Promise<{ pending: Approval[] }> {
  return apiFetch('/api/approvals')
}

export function approveAction(id: string): Promise<{ ok: boolean }> {
  return apiFetch(`/api/approvals/${id}/approve`, { method: 'POST' })
}

export function denyAction(id: string): Promise<{ ok: boolean }> {
  return apiFetch(`/api/approvals/${id}/deny`, { method: 'POST' })
}

export function searchMemory(query: string): Promise<{ results: TimelineEntry[] }> {
  return apiFetch(`/api/memory/search?q=${encodeURIComponent(query)}`)
}

export interface UnifiedSearchResult {
  source: 'chat' | 'memory' | 'tools' | 'tasks'
  category: string
  content: string
  timestamp?: number
}

export function searchAll(query: string): Promise<{ results: UnifiedSearchResult[]; count: number }> {
  return safeFetch(`/api/search?q=${encodeURIComponent(query)}`, { results: [], count: 0 })
}

export function toggleAutomationRule(name: string): Promise<{ ok: boolean; name: string; enabled: boolean }> {
  return apiFetch(`/api/automation/rules/${encodeURIComponent(name)}/toggle`, { method: 'POST' })
}

export function fetchIntegrations(): Promise<{ integrations: IntegrationHealth[] }> {
  return apiFetch('/api/integrations')
}

export function fetchAutomationRules(): Promise<{ rules: AutomationRule[] }> {
  return apiFetch('/api/automation/rules')
}

export function fetchBriefing(): Promise<BriefingData> {
  return apiFetch('/api/briefing').then((d: any) => d.briefing ?? d)
}

export function runBriefing(): Promise<BriefingData> {
  return apiFetch('/api/briefing/run', { method: 'POST' }).then((d: any) => d.briefing ?? d)
}

export function fetchSettings(): Promise<CharlieSettings> {
  return apiFetch('/api/settings')
}

export function saveSettings(diff: Record<string, Record<string, unknown>>): Promise<{ ok: boolean; error?: string }> {
  return apiFetch('/api/settings', {
    method: 'POST',
    body: JSON.stringify(diff),
  })
}

export function restartSubsystem(name: string): Promise<{ ok: boolean }> {
  return apiFetch(`/api/subsystems/${name}/restart`, { method: 'POST' })
}

export function stopSubsystem(name: string): Promise<{ ok: boolean }> {
  return apiFetch(`/api/subsystems/${name}/stop`, { method: 'POST' })
}

export function shutdownDaemon(): Promise<{ ok: boolean }> {
  return apiFetch('/api/control/shutdown', { method: 'POST' })
}

export function rebootDaemon(): Promise<{ ok: boolean }> {
  return apiFetch('/api/control/reboot', { method: 'POST' })
}

// === Safe fetch — returns fallback on 404/501/503 (but NOT brain_disconnected) ===

export class BrainDisconnectedError extends ApiError {
  constructor(message: string, body?: string) {
    super(503, message, body)
    this.name = 'BrainDisconnectedError'
  }
}

async function safeFetch<T>(path: string, fallback: T, options?: RequestInit): Promise<T> {
  try {
    return await apiFetch(path, options)
  } catch (e) {
    if (e instanceof ApiError) {
      // Do NOT swallow brain_disconnected 503 — let callers handle it
      if (e.status === 503 && e.body) {
        try {
          const parsed = JSON.parse(e.body)
          if (parsed.error === 'brain_disconnected') {
            throw new BrainDisconnectedError(parsed.message || 'Brain process is not running', e.body)
          }
        } catch (parseErr) {
          if (parseErr instanceof BrainDisconnectedError) throw parseErr
          // JSON parse failed — fall through to normal fallback
        }
      }
      if (e.status === 404 || e.status === 501 || e.status === 503) return fallback
    }
    throw e
  }
}

export function fetchChatHistory(): Promise<{ messages: ChatMessage[] }> {
  return safeFetch('/api/chat/history', { messages: [] })
}

export function sendMessage(content: string): Promise<{ ok: boolean }> {
  return safeFetch('/api/chat/send', { ok: false }, {
    method: 'POST',
    body: JSON.stringify({ content }),
  })
}

export function fetchTasks(): Promise<{ tasks: Task[] }> {
  return safeFetch('/api/tasks', { tasks: [] })
}

export function cancelTask(id: string): Promise<{ ok: boolean }> {
  return safeFetch(`/api/tasks/${id}/cancel`, { ok: false }, { method: 'POST' })
}

export function fetchMCPServers(): Promise<{ servers: MCPServer[] }> {
  return safeFetch('/api/mcp/servers', { servers: [] })
}

export function toggleMCPServer(id: string): Promise<{ ok: boolean; enabled?: boolean }> {
  return safeFetch(`/api/mcp/${id}/toggle`, { ok: false }, { method: 'POST' })
}

export function connectMCPServer(id: string): Promise<{ ok: boolean; connected?: boolean; tools?: unknown[]; error?: string }> {
  return safeFetch(`/api/mcp/${id}/connect`, { ok: false }, { method: 'POST' })
}

export function disconnectMCPServer(id: string): Promise<{ ok: boolean; connected?: boolean }> {
  return safeFetch(`/api/mcp/${id}/disconnect`, { ok: false }, { method: 'POST' })
}

export function callMCPTool(serverId: string, toolName: string, args: Record<string, unknown> = {}): Promise<{ ok: boolean; result?: string; error?: string }> {
  return safeFetch(`/api/mcp/${serverId}/tools/${toolName}/call`, { ok: false }, { method: 'POST', body: JSON.stringify({ arguments: args }) })
}

export function addMCPServer(name: string, config: Record<string, unknown>): Promise<{ ok: boolean; error?: string }> {
  return safeFetch('/api/mcp/servers', { ok: false }, { method: 'POST', body: JSON.stringify({ name, config }) })
}

export function deleteMCPServer(id: string): Promise<{ ok: boolean }> {
  return apiFetch(`/api/mcp/${id}`, { method: 'DELETE' })
}

// Docker MCP gateway lifecycle (manual start/stop)
export function fetchDockerGatewayStatus(): Promise<{ ok: boolean; reachable: boolean; managed_here: boolean; container_id?: string; port: number }> {
  return safeFetch('/api/control/docker/gateway/status', { ok: true, reachable: false, managed_here: false, port: 8080 })
}

export function startDockerGateway(): Promise<{ ok: boolean; reachable?: boolean; container_id?: string; already_running?: boolean; error?: string }> {
  return safeFetch('/api/control/docker/gateway/start', { ok: false }, { method: 'POST' })
}

export function stopDockerGateway(): Promise<{ ok: boolean; warning?: string; error?: string }> {
  return safeFetch('/api/control/docker/gateway/stop', { ok: false }, { method: 'POST' })
}

// Skills CRUD
export function createSkill(name: string, description: string, manifest?: Record<string, unknown>): Promise<{ ok: boolean; error?: string }> {
  return safeFetch('/api/skills', { ok: false }, { method: 'POST', body: JSON.stringify({ name, description, manifest }) })
}

export function updateSkill(name: string, data: Record<string, unknown>): Promise<{ ok: boolean; error?: string }> {
  return safeFetch(`/api/skills/${name}`, { ok: false }, { method: 'PUT', body: JSON.stringify(data) })
}

export function deleteSkill(name: string): Promise<{ ok: boolean }> {
  return apiFetch(`/api/skills/${name}`, { method: 'DELETE' })
}

// Agents CRUD
export function createAgent(data: Record<string, unknown>): Promise<{ ok: boolean; error?: string }> {
  return safeFetch('/api/agents', { ok: false }, { method: 'POST', body: JSON.stringify(data) })
}

export function updateAgent(name: string, data: Record<string, unknown>): Promise<{ ok: boolean; error?: string }> {
  return safeFetch(`/api/agents/${name}`, { ok: false }, { method: 'PUT', body: JSON.stringify(data) })
}

export function deleteAgent(name: string): Promise<{ ok: boolean }> {
  return apiFetch(`/api/agents/${name}`, { method: 'DELETE' })
}

// Orchestrator
export function planOrchestrator(goal: string): Promise<{ ok: boolean; subtasks?: Array<{ id: string; description: string; suggested_agent: string; dependencies: string[]; status: string }>; error?: string }> {
  return safeFetch('/api/orchestrator/plan', { ok: false, subtasks: [] }, { method: 'POST', body: JSON.stringify({ goal }) })
}

export function executeOrchestrator(goal: string): Promise<{ ok: boolean; results?: Array<{ subtask_id: string; description: string; agent: string; status: string; output: string; duration_ms: number }>; error?: string }> {
  return safeFetch('/api/orchestrator/execute', { ok: false, results: [] }, { method: 'POST', body: JSON.stringify({ goal }) })
}

export function fetchOrchestratorLearning(): Promise<{ ok: boolean; stats?: { total_records: number; overall_success_rate: number; agents: Record<string, { total: number; successes: number; success_rate: number }> }; history?: Array<{ agent: string; keywords: string[]; success: boolean; duration_ms: number; timestamp: number }>; error?: string }> {
  return safeFetch('/api/orchestrator/learning', { ok: false, stats: { total_records: 0, overall_success_rate: 0, agents: {} }, history: [] })
}

// Automation Rules CRUD
export function createAutomationRule(data: Record<string, unknown>): Promise<{ ok: boolean; error?: string }> {
  return safeFetch('/api/automation/rules', { ok: false }, { method: 'POST', body: JSON.stringify(data) })
}

export function updateAutomationRule(name: string, data: Record<string, unknown>): Promise<{ ok: boolean; error?: string }> {
  return safeFetch(`/api/automation/rules/${name}`, { ok: false }, { method: 'PUT', body: JSON.stringify(data) })
}

export function deleteAutomationRule(name: string): Promise<{ ok: boolean }> {
  return apiFetch(`/api/automation/rules/${name}`, { method: 'DELETE' })
}

export function fetchAgentStatus(): Promise<AgentStatus> {
  return safeFetch('/api/agents/status', {
    orchestrator: { status: 'idle', active_agents: 0 },
    agents: [],
  })
}

export function fetchToolLog(): Promise<{ executions: ToolExecution[] }> {
  return safeFetch('/api/tools/log', { executions: [] })
}

export function fetchLogs(processFilter?: string, levelFilter?: string): Promise<{ logs: Array<{ timestamp: string; process: string; level: string; message: string }> }> {
  const params = new URLSearchParams()
  if (processFilter) params.set('process', processFilter)
  if (levelFilter) params.set('level', levelFilter)
  return safeFetch(`/api/logs?${params}`, { logs: [] })
}

export function fetchEvolution(): Promise<{ entries: Array<{ id: string; skillName: string; type: string; status: string; description: string; diff?: string; timestamp: string; confidence: number }> }> {
  return safeFetch('/api/evolution', { entries: [] })
}

export function fetchSkills(): Promise<{ skills: Array<{ name: string; description: string; tags: string[]; enabled: boolean; inject_mode: string }> }> {
  return safeFetch('/api/skills', { skills: [] })
}

export interface VoiceStatus {
  stt_model: string
  tts_model: string
  tts_speed: number
  is_listening: boolean
  is_speaking: boolean
}

export function fetchVoiceStatus(): Promise<VoiceStatus> {
  return safeFetch('/api/voice/status', {
    stt_model: 'unknown',
    tts_model: 'unknown',
    tts_speed: 1.0,
    is_listening: false,
    is_speaking: false,
  })
}

