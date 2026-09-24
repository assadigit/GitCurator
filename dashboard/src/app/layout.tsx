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
  title: "GitCurator v0.0.10 — Release & Audit Dashboard",
  description:
    "Telegram → Ollama → Obsidian pipeline. GitCurator v0.0.10 Security Hygiene & Deploy Kit: the git tree is credential-free (session files untracked + gitignored, configs are templates, docs use YOUR_BOT_TOKEN placeholders) and deploy-latest.ps1/.sh ships a two-minute Cloudflare update path (idempotent D1 schema → wrangler deploy → live health check, optional WEBHOOK_SECRET anti-impersonation). Modular package lineage, GoodRepos public directory, 73/73 tests, live verification gate with persisted history, drift detection, repository & release console with CI history.",
  keywords: ["GitCurator", "GitHub", "Obsidian", "Ollama", "Telegram", "audit", "reliability", "Python", "PyQt6", "vault backup", "VaultSeal"],
  authors: [{ name: "Z.ai Team" }],
  icons: {
    icon: "https://z-cdn.chatglm.cn/z-ai/static/logo.svg",
  },
  openGraph: {
    title: "GitCurator v0.0.10 — Release & Audit Dashboard",
    description: "23 fixes shipped, 73/73 tests, credential-free git tree, two-minute Cloudflare deploy kit, live verification gate, VaultSeal automatic vault backup to a private GitHub repo, repository & release console with CI history",
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
