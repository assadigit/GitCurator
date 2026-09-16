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
  title: "GitHub Curator v30.4 — Release & Audit Dashboard",
  description:
    "Telegram → Ollama → Obsidian pipeline. v30.4 Timing Insights & Scheduled Verification: 13 fixes shipped, 45/45 tests, live verification gate with persisted history, per-file drift detail, per-case timing leaderboard, 6-hour scheduled verification, and a dark/light/system theme.",
  keywords: ["GitHub", "Obsidian", "Ollama", "Telegram", "audit", "reliability", "Python", "PyQt6"],
  authors: [{ name: "Z.ai Team" }],
  icons: {
    icon: "https://z-cdn.chatglm.cn/z-ai/static/logo.svg",
  },
  openGraph: {
    title: "GitHub Curator v30.4 — Release & Audit Dashboard",
    description: "13 fixes shipped, 45/45 tests, live verification gate, persisted history, drift detection, light/dark theme",
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
