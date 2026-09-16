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
  title: "GitCurator v0.0.7 — Release & Audit Dashboard",
  description:
    "Telegram → Ollama → Obsidian pipeline. GitCurator v0.0.7 Modular Core & Good Repos: the app became the gitcurator package (core/integrations/cloud/gui/tools), every curation run now also publishes an emoji-rich PUBLIC curated directory (github.com/assadigit/good-repos), and the UI speaks pastel — cream day / plum night with AA-verified tokens. 73/73 tests, live verification gate with persisted history, drift detection, repository & release console with CI history.",
  keywords: ["GitCurator", "GitHub", "Obsidian", "Ollama", "Telegram", "audit", "reliability", "Python", "PyQt6", "vault backup", "VaultSeal"],
  authors: [{ name: "Z.ai Team" }],
  icons: {
    icon: "https://z-cdn.chatglm.cn/z-ai/static/logo.svg",
  },
  openGraph: {
    title: "GitCurator v0.0.7 — Release & Audit Dashboard",
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
