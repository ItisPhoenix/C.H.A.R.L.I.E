'use client'

import { useEffect, useRef } from 'react'
import { cn } from '@/lib/utils'
import { X } from 'lucide-react'

interface ModalProps {
  open: boolean
  onClose: () => void
  title: string
  children: React.ReactNode
  className?: string
}

export function Modal({ open, onClose, title, children, className }: ModalProps) {
  const closeRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    if (!open) return

    // Focus the close button when modal opens
    closeRef.current?.focus()

    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') {
        onClose()
      }
    }

    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [open, onClose])

  if (!open) return null
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center" role="dialog" aria-modal="true" aria-label={title}>
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={onClose} />
      <div
        className={cn(
          'relative glass-card p-6 max-w-md w-full mx-4 animate-slide-in rounded-2xl',
          'border-charlie-cyan/30 shadow-neon-glow',
          className,
        )}
      >
        <div className="relative flex items-center justify-between mb-4">
          <h2 className="font-display bg-gradient-to-r from-charlie-cyan to-charlie-teal bg-clip-text text-transparent font-bold tracking-[0.05em] uppercase text-sm">{title}</h2>
          <button
            ref={closeRef}
            onClick={onClose}
            className="text-charlie-dim hover:text-charlie-text transition-colors cursor-pointer"
            aria-label="Close modal"
          >
            <X size={20} />
          </button>
        </div>
        {children}
      </div>
    </div>
  )
}
