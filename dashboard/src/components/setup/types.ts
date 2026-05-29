export interface SetupData {
  llm_provider: string
  llm_api_key: string
  llm_model: string
  voice_enabled: boolean
  wake_word: string
  stt_model: string
  tts_model: string
  integrations: Record<string, boolean>
  risk_tier: number
  guardian_enabled: boolean
  auto_approve_threshold: number
  [key: string]: unknown
}

export const defaultSetupData: SetupData = {
  llm_provider: 'google',
  llm_api_key: '',
  llm_model: 'gemini-2.5-flash',
  voice_enabled: true,
  wake_word: 'CHARLIE',
  stt_model: 'faster-whisper',
  tts_model: 'kokoro',
  integrations: { gmail: false, calendar: false, github: false, notion: false },
  risk_tier: 1,
  guardian_enabled: true,
  auto_approve_threshold: 0.85,
}
