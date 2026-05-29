'use client'

import { useState, useEffect } from 'react'
import { PageHeader } from '@/components/layout/PageHeader'
import { GlassCard } from '@/components/ui/GlassCard'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { EmptyState } from '@/components/ui/EmptyState'
import { LoadingSpinner } from '@/components/ui/LoadingSpinner'
import { HudCorners } from '@/components/background/HudCorners'
import { Sparkles, Check, X, Plus } from 'lucide-react'
import { fetchSkills } from '@/lib/api'

interface Skill {
  name: string
  description: string
  status: 'active' | 'pending' | 'disabled'
  category: string
  lastUsed?: string
  triggerCount: number
}

export default function SkillsPage() {
  const [skills, setSkills] = useState<Skill[]>([])
  const [loading, setLoading] = useState(true)
  const [filter, setFilter] = useState<'all' | 'active' | 'pending'>('all')

  useEffect(() => {
    async function loadSkills() {
      try {
        const data = await fetchSkills()
        const mapped = (data.skills || []).map((s) => ({
          name: s.name,
          description: s.description,
          status: (s.enabled ? 'active' : 'disabled') as 'active' | 'pending' | 'disabled',
          category: s.tags?.[0] || 'general',
          lastUsed: (s as Record<string, unknown>).last_used as string | undefined,
          triggerCount: ((s as Record<string, unknown>).trigger_count as number) || 0,
        }))
        setSkills(mapped)
      } catch {} finally {
        setLoading(false)
      }
    }
    loadSkills()
  }, [])

  const filtered = filter === 'all' ? skills : skills.filter((s) => s.status === filter)
  const pendingCount = skills.filter((s) => s.status === 'pending').length

  return (
    <div>
      <PageHeader
        title="Skills"
        subtitle="Manage installed skills and pending approvals"
        actions={
          <Button variant="primary">
            <Plus size={16} className="mr-1" />
            Create Skill
          </Button>
        }
      />

      {/* Filter tabs */}
      <div className="flex gap-2 mb-4">
        {(['all', 'active', 'pending'] as const).map((f) => (
          <button
            key={f}
            onClick={() => setFilter(f)}
            className={`px-3 py-1.5 rounded-lg text-sm transition-colors cursor-pointer ${
              filter === f
                ? 'bg-charlie-cyan/15 text-charlie-cyan border border-charlie-cyan/30'
                : 'bg-charlie-card/50 text-charlie-dim border border-charlie-border hover:text-charlie-text'
            }`}
          >
            {f.charAt(0).toUpperCase() + f.slice(1)}
            {f === 'pending' && pendingCount > 0 && (
              <span className="ml-1.5 text-xs">({pendingCount})</span>
            )}
          </button>
        ))}
      </div>

      {loading ? (
        <div className="flex justify-center py-12">
          <LoadingSpinner label="Loading skills..." />
        </div>
      ) : filtered.length > 0 ? (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {filtered.map((skill) => (
            <HudCorners key={skill.name}>
              <GlassCard className="p-4">
                <div className="flex items-start justify-between mb-2">
                  <div className="flex items-center gap-2">
                    <Sparkles size={16} className="text-charlie-cyan" />
                    <span className="font-semibold text-charlie-text text-sm">{skill.name}</span>
                  </div>
                  <Badge
                    variant={
                      skill.status === 'active'
                        ? 'green'
                        : skill.status === 'pending'
                          ? 'amber'
                          : 'dim'
                    }
                  >
                    {skill.status}
                  </Badge>
                </div>
                <p className="text-charlie-dim text-sm mb-3 line-clamp-2">{skill.description}</p>
                {skill.category && (
                  <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-charlie-cyan/5 text-charlie-dim border border-charlie-border">
                    {skill.category}
                  </span>
                )}
                {skill.status === 'pending' && (
                  <div className="flex gap-2 mt-3 pt-3 border-t border-charlie-border">
                    <Button variant="primary" className="flex-1" onClick={() => {
                      window.dispatchEvent(new CustomEvent('charlie-notification', {
                        detail: { type: 'info', title: 'Skill', message: `Approved: ${skill.name}` },
                      }))
                    }}>
                      <Check size={14} className="mr-1" />
                      Approve
                    </Button>
                    <Button variant="danger" className="flex-1" onClick={() => {
                      window.dispatchEvent(new CustomEvent('charlie-notification', {
                        detail: { type: 'info', title: 'Skill', message: `Rejected: ${skill.name}` },
                      }))
                    }}>
                      <X size={14} className="mr-1" />
                      Reject
                    </Button>
                  </div>
                )}
              </GlassCard>
            </HudCorners>
          ))}
        </div>
      ) : (
        <EmptyState
          icon={<Sparkles size={32} />}
          title="No skills found"
          description={filter === 'pending' ? 'No pending skills to review' : 'No skills installed yet'}
          terminal={filter !== 'pending'}
          action={filter === 'pending' ? undefined : { label: 'Create Skill', onClick: () => {
            window.dispatchEvent(new CustomEvent('charlie-notification', {
              detail: { type: 'info', title: 'Skills', message: 'Skill creation coming soon' },
            }))
          } }}
        />
      )}
    </div>
  )
}
