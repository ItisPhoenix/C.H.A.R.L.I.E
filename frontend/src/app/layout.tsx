import type { Metadata } from "next";
import { Space_Grotesk } from "next/font/google";
import "./globals.css";

const spaceGrotesk = Space_Grotesk({
  variable: "--font-display",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Charlie Dashboard",
  description: "Next-gen Assistant Control Center",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      className={`${spaceGrotesk.variable} h-full antialiased`}
    >
      <body className="h-full w-full overflow-hidden relative bg-[var(--color-canvas)] text-[var(--color-text-primary)]">
        {children}
      </body>
    </html>
  );
}
