'use client'

import { useState } from 'react'
import { motion } from 'framer-motion'
import { Check, X, Loader2, Key, Cpu } from 'lucide-react'

import { type SetupData } from '../types'

export function LLMStep({ data, onChange }: { data: SetupData; onChange: (d: SetupData) => void }) {
  const [testing, setTesting] = useState(false)
  const [testResult, setTestResult] = useState<'success' | 'error' | null>(null)

  async function testConnection() {
    setTesting(true)
    setTestResult(null)
    try {
      const resp = await fetch('http://localhost:3005/api/settings')
      if (resp.ok) setTestResult('success')
      else setTestResult('error')
    } catch {
      setTestResult('error')
    }
    setTesting(false)
  }

  return (
    <div className="space-y-8">
      <div className="text-center">
        <h2 className="font-display text-2xl text-charlie-cyan tracking-wide mb-2">LLM Configuration</h2>
        <p className="text-charlie-dim text-sm font-body">Connect to your AI model provider</p>
      </div>

      <div className="space-y-4">
        {/* Provider */}
        <div>
          <label className="text-charlie-dim text-xs font-display tracking-wider uppercase mb-2 block">
            Provider
          </label>
          <div className="grid grid-cols-2 gap-3">
            {[
              { id: 'google', label: 'Google Gemini', desc: 'Gemini 2.5 Flash' },
              { id: 'openai', label: 'OpenAI', desc: 'GPT-4o' },
              { id: 'anthropic', label: 'Anthropic', desc: 'Claude Opus' },
              { id: 'nvidia', label: 'NVIDIA NIM', desc: 'Cloud LLM' },
            ].map((p) => (
              <button
                key={p.id}
                onClick={() => onChange({ ...data, llm_provider: p.id })}
                className={`p-3 rounded-lg border text-left transition-all cursor-pointer ${
                  data.llm_provider === p.id
                    ? 'border-charlie-cyan/50 bg-charlie-cyan/10 shadow-neon-cyan-sm'
                    : 'border-charlie-border hover:border-charlie-cyan/20'
                }`}
              >
                <div className="font-body text-sm text-charlie-text">{p.label}</div>
                <div className="text-charlie-dim text-xs mt-0.5">{p.desc}</div>
              </button>
            ))}
          </div>
        </div>

        {/* API Key */}
        <div>
          <label className="text-charlie-dim text-xs font-display tracking-wider uppercase mb-2 flex items-center gap-2">
            <Key size={12} /> API Key
          </label>
          <input
            type="password"
            value={data.llm_api_key}
            onChange={(e) => onChange({ ...data, llm_api_key: e.target.value })}
            placeholder="sk-..."
            className="w-full bg-charlie-card border border-charlie-border rounded-lg px-4 py-2.5 text-sm font-mono text-charlie-text placeholder-charlie-dim/50 focus:border-charlie-cyan/50 focus:shadow-neon-cyan-sm transition-all"
          />
        </div>

        {/* Model */}
        <div>
          <label className="text-charlie-dim text-xs font-display tracking-wider uppercase mb-2 flex items-center gap-2">
            <Cpu size={12} /> Model
          </label>
          <select
            value={data.llm_model}
            onChange={(e) => onChange({ ...data, llm_model: e.target.value })}
            className="w-full bg-charlie-card border border-charlie-border rounded-lg px-4 py-2.5 text-sm font-body text-charlie-text focus:border-charlie-cyan/50 transition-all"
          >
            <option value="gemini-2.5-flash">Gemini 2.5 Flash (fast)</option>
            <option value="gemini-2.5-pro">Gemini 2.5 Pro (capable)</option>
            <option value="gpt-4o">GPT-4o</option>
            <option value="claude-opus-4">Claude Opus 4</option>
          </select>
        </div>

        {/* Test connection */}
        <div className="flex items-center gap-3">
          <button
            onClick={testConnection}
            disabled={testing || !data.llm_api_key}
            className="flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-body border border-charlie-border hover:border-charlie-cyan/30 disabled:opacity-50 transition-all cursor-pointer"
          >
            {testing ? <Loader2 size={14} className="animate-spin" /> : null}
            Test Connection
          </button>
          {testResult === 'success' && (
            <motion.span initial={{ opacity: 0 }} className="flex items-center gap-1 text-charlie-green text-sm">
              <Check size={14} /> Connected
            </motion.span>
          )}
          {testResult === 'error' && (
            <motion.span initial={{ opacity: 0 }} className="flex items-center gap-1 text-charlie-red text-sm">
              <X size={14} /> Failed
            </motion.span>
          )}
        </div>
      </div>
    </div>
  )
}
