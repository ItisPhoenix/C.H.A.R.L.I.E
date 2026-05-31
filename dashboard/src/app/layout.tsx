import type { Metadata } from 'next'
import { Inter, Fira_Code } from 'next/font/google'
import './globals.css'
import { DashboardShell } from './shell'

const inter = Inter({
  subsets: ['latin'],
  variable: '--font-sans',
  display: 'swap',
})

const firacode = Fira_Code({
  subsets: ['latin'],
  variable: '--font-mono',
  display: 'swap',
  weight: ['400', '500'],
})
export const metadata: Metadata = {
  title: 'CHARLIE Dashboard',
  description: 'CHARLIE AI Assistant Command Center',
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`dark ${inter.variable} ${firacode.variable}`}>
      <body className="min-h-screen bg-black text-zinc-300 font-sans selection:bg-white/20">
        <DashboardShell>{children}</DashboardShell>
      </body>
    </html>
  )
}
