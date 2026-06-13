'use client'

import { useEffect, useState, useCallback } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { X, Info, CheckCircle, AlertTriangle, AlertCircle } from 'lucide-react'
import { useWSEvent } from '@/lib/ws'

interface Toast {
  id: string
  type: 'info' | 'success' | 'warning' | 'error'
  title?: string
  message: string
}

const icons = { info: Info, success: CheckCircle, warning: AlertTriangle, error: AlertCircle }
const colors = {
  info: 'border-charlie-cyan/30 text-charlie-cyan',
  success: 'border-charlie-green/30 text-charlie-green',
  warning: 'border-charlie-amber/30 text-charlie-amber',
  error: 'border-charlie-red/30 text-charlie-red',
}

// Global toast function — allows other components to trigger toasts
let globalAddToast: ((toast: Omit<Toast, 'id'>) => void) | null = null
export function addToast(toast: Omit<Toast, 'id'>) {
  globalAddToast?.(toast)
}

export function ToastContainer() {
  const [toasts, setToasts] = useState<Toast[]>([])
  const alert = useWSEvent<{ type?: string; message?: string }>('phoenix_alert')

  const handleAddToast = useCallback((toast: Omit<Toast, 'id'>) => {
    const id = Date.now().toString()
    setToasts((prev) => [...prev, { ...toast, id }])
    setTimeout(() => setToasts((prev) => prev.filter((t) => t.id !== id)), 5000)
  }, [])

  // Register global add function
  useEffect(() => {
    globalAddToast = handleAddToast
    return () => { globalAddToast = null }
  }, [handleAddToast])

  useEffect(() => {
    if (alert?.message) {
      handleAddToast({ type: (alert.type as Toast['type']) || 'info', message: alert.message })
    }
  }, [alert, handleAddToast])

  return (
    <div className="fixed top-4 right-4 z-[100] flex flex-col gap-2 w-80">
      <AnimatePresence>
        {toasts.map((toast) => {
          const Icon = icons[toast.type]
          return (
            <motion.div
              key={toast.id}
              initial={{ opacity: 0, x: 20, scale: 0.95 }}
              animate={{ opacity: 1, x: 0, scale: 1 }}
              exit={{ opacity: 0, x: 20, scale: 0.95 }}
              className={`glass-card p-4 flex items-start gap-3 border rounded-xl ${colors[toast.type]}`}
            >
              <Icon size={16} className="mt-0.5 flex-shrink-0" />
              <div className="flex-1 min-w-0">
                {toast.title && (
                  <p className="text-sm font-display font-semibold text-charlie-text mb-0.5">{toast.title}</p>
                )}
                <span className="text-sm font-body text-charlie-text">{toast.message}</span>
              </div>
              <button onClick={() => setToasts((prev) => prev.filter((t) => t.id !== toast.id))} className="text-charlie-dim hover:text-charlie-text cursor-pointer" aria-label="Dismiss notification">
                <X size={14} />
              </button>
            </motion.div>
          )
        })}
      </AnimatePresence>
    </div>
  )
}
