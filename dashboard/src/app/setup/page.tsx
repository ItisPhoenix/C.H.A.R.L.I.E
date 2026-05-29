'use client'

import { useRouter } from 'next/navigation'
import { SetupWizard } from '@/components/setup/SetupWizard'
import { type SetupData } from '@/components/setup/types'
import { submitSetup } from '@/lib/api'

export default function SetupPage() {
  const router = useRouter()

  async function handleComplete(data: SetupData) {
    try {
      await submitSetup({ ...data, setup_complete: true })
    } catch {
      // Config will be written on next daemon restart
    }
    router.push('/')
  }

  return <SetupWizard onComplete={handleComplete} />
}
