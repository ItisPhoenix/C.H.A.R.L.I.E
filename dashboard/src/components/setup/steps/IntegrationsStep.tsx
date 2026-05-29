'use client'

import { Check, ExternalLink } from 'lucide-react'
import { type SetupData } from '../types'

const INTEGRATIONS = [
  { id: 'gmail', name: 'Gmail', desc: 'Read and send emails', icon: '📧' },
  { id: 'calendar', name: 'Google Calendar', desc: 'Schedule and reminders', icon: '📅' },
  { id: 'github', name: 'GitHub', desc: 'Issues, PRs, notifications', icon: '🐙' },
  { id: 'notion', name: 'Notion', desc: 'Pages and databases', icon: '📝' },
]

export function IntegrationsStep({ data, onChange }: { data: SetupData; onChange: (d: SetupData) => void }) {
  function toggle(id: string) {
    onChange({
      ...data,
      integrations: { ...data.integrations, [id]: !data.integrations[id] },
    })
  }

  return (
    <div className="space-y-8">
      <div className="text-center">
        <h2 className="font-display text-2xl text-charlie-cyan tracking-wide mb-2">Integrations</h2>
        <p className="text-charlie-dim text-sm font-body">Connect your services. You can always add more later.</p>
      </div>

      <div className="space-y-3">
        {INTEGRATIONS.map((int) => {
          const connected = data.integrations[int.id]
          return (
            <button
              key={int.id}
              onClick={() => toggle(int.id)}
              className={`w-full flex items-center gap-4 p-4 rounded-lg border text-left transition-all cursor-pointer ${
                connected
                  ? 'border-charlie-cyan/50 bg-charlie-cyan/10 shadow-neon-cyan-sm'
                  : 'border-charlie-border hover:border-charlie-cyan/20'
              }`}
            >
              <span className="text-2xl">{int.icon}</span>
              <div className="flex-1">
                <div className="font-body text-sm text-charlie-text">{int.name}</div>
                <div className="text-charlie-dim text-xs mt-0.5">{int.desc}</div>
              </div>
              {connected ? (
                <span className="flex items-center gap-1 text-charlie-green text-xs">
                  <Check size={14} /> Connected
                </span>
              ) : (
                <span className="text-charlie-dim text-xs flex items-center gap-1">
                  <ExternalLink size={12} /> Connect
                </span>
              )}
            </button>
          )
        })}
      </div>
    </div>
  )
}
