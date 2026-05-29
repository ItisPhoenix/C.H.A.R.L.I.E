import type { Metadata } from 'next'
import { Orbitron, Exo_2, Fira_Code } from 'next/font/google'
import './globals.css'
import { DashboardShell } from './shell'

const orbitron = Orbitron({
  subsets: ['latin'],
  variable: '--font-display',
  display: 'swap',
  weight: ['400', '500', '600', '700', '800', '900'],
})

const exo2 = Exo_2({
  subsets: ['latin'],
  variable: '--font-body',
  display: 'swap',
  weight: ['300', '400', '500', '600', '700'],
})

const firacode = Fira_Code({
  subsets: ['latin'],
  variable: '--font-mono',
  display: 'swap',
  weight: ['300', '400', '500', '600', '700'],
})

export const metadata: Metadata = {
  title: 'CHARLIE Dashboard',
  description: 'CHARLIE AI Assistant Command Center',
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`dark ${orbitron.variable} ${exo2.variable} ${firacode.variable}`}>
      <body className="min-h-screen bg-charlie-dark text-charlie-text font-body">
        <DashboardShell>{children}</DashboardShell>
      </body>
    </html>
  )
}
