import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";
import { Toaster } from "@/components/ui/toaster";
import { ThemeProvider } from "@/components/theme-provider";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "GitCurator v0.0.6 — Release & Audit Dashboard",
  description:
    "Telegram → Ollama → Obsidian pipeline. GitCurator v0.0.6 UI/UX Overhaul: fixed 1000×750 window, per-tab scroll, WCAG-AA tokens, 3-variant button hierarchy + More menu, batch confirmations — plus VaultSeal: every curation run backs the whole Obsidian vault up to a private GitHub repository. 45/45 tests, live verification gate with persisted history, drift detection, repository & release console with CI history.",
  keywords: ["GitCurator", "GitHub", "Obsidian", "Ollama", "Telegram", "audit", "reliability", "Python", "PyQt6", "vault backup", "VaultSeal"],
  authors: [{ name: "Z.ai Team" }],
  icons: {
    icon: "https://z-cdn.chatglm.cn/z-ai/static/logo.svg",
  },
  openGraph: {
    title: "GitCurator v0.0.6 — Release & Audit Dashboard",
    description: "13 fixes shipped, 45/45 tests, live verification gate, VaultSeal automatic vault backup to a private GitHub repo, repository & release console with CI history",
    siteName: "Z.ai",
    type: "website",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body
        className={`${geistSans.variable} ${geistMono.variable} antialiased bg-background text-foreground`}
      >
        <ThemeProvider
          attribute="class"
          defaultTheme="dark"
          enableSystem
          disableTransitionOnChange
        >
          {children}
          <Toaster />
        </ThemeProvider>
      </body>
    </html>
  );
}
