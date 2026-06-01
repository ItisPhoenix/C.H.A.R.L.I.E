'use client'

import { useState, useEffect, useCallback } from 'react'
import { PageHeader } from '@/components/layout/PageHeader'
import { GlassCard } from '@/components/ui/GlassCard'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { EmptyState } from '@/components/ui/EmptyState'
import { LoadingSpinner } from '@/components/ui/LoadingSpinner'
import { ErrorState } from '@/components/ui/ErrorState'
import { Modal } from '@/components/ui/Modal'
import { Sparkles, Plus, Trash2, Pencil } from 'lucide-react'
import { FilterBar } from '@/components/ui/FilterBar'
import { fetchSkills, createSkill, updateSkill, deleteSkill } from '@/lib/api'
import { cn } from '@/lib/utils'

interface Skill {
  name: string
  description: string
  status: 'active' | 'pending' | 'disabled'
  category: string
  lastUsed?: string
  triggerCount: number
}

function CreateSkillModal({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleSave = async () => {
    if (!name.trim()) { setError('Name required'); return }
    setSaving(true)
    setError(null)
    try {
      const res = await createSkill(name.trim(), description.trim())
      if (res.ok) { onCreated(); onClose() }
      else { setError(res.error || 'Failed to create skill') }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to create skill')
    } finally { setSaving(false) }
  }

  return (
    <Modal open onClose={onClose} title="Create Skill">
      <div className="space-y-4">
        {error && <p className="text-sm text-charlie-red">{error}</p>}
        <div>
          <label className="text-xs text-charlie-dim mb-1 block">Name</label>
          <input value={name} onChange={(e) => setName(e.target.value)}
            className="w-full bg-charlie-dark border border-charlie-border rounded-lg p-2 text-sm text-charlie-text focus:border-charlie-cyan focus:outline-none"
            placeholder="my-skill" />
        </div>
        <div>
          <label className="text-xs text-charlie-dim mb-1 block">Description</label>
          <textarea value={description} onChange={(e) => setDescription(e.target.value)}
            className="w-full bg-charlie-dark border border-charlie-border rounded-lg p-2 text-sm text-charlie-text focus:border-charlie-cyan focus:outline-none min-h-[80px]"
            placeholder="What does this skill do?" />
        </div>
        <div className="flex gap-2 justify-end">
          <Button variant="ghost" size="sm" onClick={onClose}>Cancel</Button>
          <Button size="sm" loading={saving} onClick={handleSave}>Create</Button>
        </div>
      </div>
    </Modal>
  )
}

function EditSkillModal({ skill, onClose, onUpdated }: { skill: Skill; onClose: () => void; onUpdated: () => void }) {
  const [name, setName] = useState(skill.name)
  const [description, setDescription] = useState(skill.description)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleSave = async () => {
    if (!name.trim()) { setError('Name required'); return }
    setSaving(true)
    setError(null)
    try {
      const res = await updateSkill(skill.name, { name: name.trim(), description: description.trim() })
      if (res.ok) { onUpdated(); onClose() }
      else { setError(res.error || 'Failed to update skill') }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to update skill')
    } finally { setSaving(false) }
  }

  return (
    <Modal open onClose={onClose} title="Edit Skill">
      <div className="space-y-4">
        {error && <p className="text-sm text-charlie-red">{error}</p>}
        <div>
          <label className="text-xs text-charlie-dim mb-1 block">Name</label>
          <input value={name} onChange={(e) => setName(e.target.value)}
            className="w-full bg-charlie-dark border border-charlie-border rounded-lg p-2 text-sm text-charlie-text focus:border-charlie-cyan focus:outline-none"
            placeholder="my-skill" />
        </div>
        <div>
          <label className="text-xs text-charlie-dim mb-1 block">Description</label>
          <textarea value={description} onChange={(e) => setDescription(e.target.value)}
            className="w-full bg-charlie-dark border border-charlie-border rounded-lg p-2 text-sm text-charlie-text focus:border-charlie-cyan focus:outline-none min-h-[80px]"
            placeholder="What does this skill do?" />
        </div>
        <div className="flex gap-2 justify-end">
          <Button variant="ghost" size="sm" onClick={onClose}>Cancel</Button>
          <Button size="sm" loading={saving} onClick={handleSave}>Save</Button>
        </div>
      </div>
    </Modal>
  )
}

export default function SkillsPage() {
  const [skills, setSkills] = useState<Skill[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [filter, setFilter] = useState<'all' | 'active' | 'pending'>('all')
  const [showCreate, setShowCreate] = useState(false)
  const [deleting, setDeleting] = useState<string | null>(null)
  const [editingSkill, setEditingSkill] = useState<Skill | null>(null)

  const loadSkills = useCallback(async () => {
    try {
      setError(null)
      setLoading(true)
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
    } catch (e) {
      console.error('Failed to load skills:', e)
      setError('Failed to load skills')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { loadSkills() }, [loadSkills])

  const handleDelete = async (name: string) => {
    setDeleting(name)
    try {
      await deleteSkill(name)
      await loadSkills()
    } catch (e) {
      console.error('Failed to delete skill:', e)
    } finally { setDeleting(null) }
  }

  const filtered = filter === 'all' ? skills : skills.filter((s) => s.status === filter)
  const pendingCount = skills.filter((s) => s.status === 'pending').length

  return (
    <div className="max-w-6xl mx-auto space-y-6">
      <PageHeader
        title="Skills"
        subtitle="Manage installed skills and pending approvals"
        actions={<Button size="sm" onClick={() => setShowCreate(true)}><Plus size={14} className="mr-1" />Create Skill</Button>}
      />

      <FilterBar
        options={['all', 'active', 'pending'] as const}
        value={filter}
        onChange={setFilter}
        badge={(key) => key === 'pending' ? pendingCount : undefined}
      />

      {loading ? (
        <div className="flex items-center justify-center h-[60vh]">
          <LoadingSpinner label="Loading skills..." />
        </div>
      ) : error ? (
        <ErrorState error={error} onRetry={loadSkills} />
      ) : filtered.length > 0 ? (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {filtered.map((skill) => (
            <GlassCard key={skill.name} className="p-4">
              <div className="flex items-start justify-between mb-2">
                <div className="flex items-center gap-2">
                  <Sparkles size={16} className="text-charlie-cyan" />
                  <span className="font-semibold text-charlie-text text-sm">{skill.name}</span>
                </div>
                <div className="flex items-center gap-1">
                  <Badge variant={skill.status === 'active' ? 'green' : skill.status === 'pending' ? 'amber' : 'dim'}>
                    {skill.status}
                  </Badge>
                  <Button variant="ghost" size="xs"
                    onClick={() => setEditingSkill(skill)} title="Edit skill">
                    <Pencil size={12} />
                  </Button>
                  <Button variant="danger" size="xs" loading={deleting === skill.name}
                    onClick={() => handleDelete(skill.name)} title="Delete skill">
                    <Trash2 size={12} />
                  </Button>
                </div>
              </div>
              <p className="text-charlie-dim text-sm mb-3 line-clamp-2">{skill.description}</p>
              {skill.category && (
                <span className="text-xs font-mono px-1.5 py-0.5 rounded-lg bg-charlie-cyan/5 text-charlie-dim">
                  {skill.category}
                </span>
              )}
            </GlassCard>
          ))}
        </div>
      ) : (
        <EmptyState
          icon={<Sparkles size={32} />}
          title="No skills found"
          description={filter === 'pending' ? 'No pending skills to review' : 'No skills installed yet'}
          terminal={filter !== 'pending'}
          action={filter === 'pending' ? undefined : { label: 'Create Skill', onClick: () => setShowCreate(true) }}
        />
      )}

      {showCreate && <CreateSkillModal onClose={() => setShowCreate(false)} onCreated={loadSkills} />}
      {editingSkill && <EditSkillModal skill={editingSkill} onClose={() => setEditingSkill(null)} onUpdated={loadSkills} />}
    </div>
  )
}
