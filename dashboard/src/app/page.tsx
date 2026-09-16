'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { animate, motion } from "framer-motion";
import { useTheme } from "next-themes";
import {
  Activity, AlertTriangle, Archive, ArrowDownRight, ArrowRight, ArrowUpRight, Brain, Bug, CheckCircle2, ChevronDown, ChevronRight,
  CircleDot, ClipboardCheck, Clock, Cpu, Download, ExternalLink, FileArchive, FileCode2, FileJson, FileText,
  FlaskConical, Fingerprint, Gauge, GitCommitHorizontal, Github, GitBranch, HardDrive, History, Layers, Lightbulb, Link2, ListChecks, Loader2,
  Lock, Monitor, Moon, MousePointerClick, Package, Play, RefreshCw, RotateCcw, Rocket, Search, Send, ServerCog, ShieldCheck,
  Sparkles, Sun, Tag, Target, TerminalSquare, TrendingUp, Trash2, Vault, Workflow, Wrench, X, Zap,
} from "lucide-react";
import {
  Area, AreaChart, CartesianGrid, ReferenceLine, ResponsiveContainer,
  Tooltip as RTooltip, XAxis, YAxis,
} from "recharts";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Progress } from "@/components/ui/progress";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Tooltip, TooltipContent, TooltipProvider, TooltipTrigger,
} from "@/components/ui/tooltip";
import { useToast } from "@/hooks/use-toast";
import type {
  ChecklistItem, Fix, PipelineStage, Priority, ReleaseReport, Severity, TestSuite,
} from "@/lib/report";
import type { VerifyResult } from "@/lib/verify-core";
import type { TimingInsight, TimingSample } from "@/lib/insights";
import type { HistoryData, HistoryRun } from "@/app/api/history/route";
import type { ReleasesData } from "@/app/api/releases/route";

/* ------------------------------------------------------------------ */
/* Helpers                                                             */
/* ------------------------------------------------------------------ */

const SEVERITY_STYLES: Record<Severity, { label: string; cls: string; dot: string }> = {
  critical: { label: "Critical", cls: "bg-rose-500/10 text-rose-700 dark:text-rose-300 border-rose-500/30", dot: "bg-rose-400" },
  high: { label: "High", cls: "bg-amber-500/10 text-amber-700 dark:text-amber-300 border-amber-500/30", dot: "bg-amber-400" },
  medium: { label: "Medium", cls: "bg-teal-500/10 text-teal-700 dark:text-teal-300 border-teal-500/30", dot: "bg-teal-400" },
};

const PRIORITY_STYLES: Record<Priority, string> = {
  P0: "bg-rose-500/15 text-rose-700 dark:text-rose-300 border-rose-500/40",
  P1: "bg-amber-500/15 text-amber-700 dark:text-amber-300 border-amber-500/40",
  P2: "bg-teal-500/15 text-teal-700 dark:text-teal-300 border-teal-500/40",
  P3: "bg-zinc-500/15 text-zinc-700 dark:text-zinc-300 border-zinc-500/40",
};

const STAGE_ICONS: Record<string, React.ComponentType<{ className?: string }>> = {
  send: Send, link: Link2, github: Github, brain: Brain, "file-text": FileText, vault: Vault,
};

const SEVERITY_ORDER: Severity[] = ["critical", "high", "medium"];

const CHECKLIST_STORAGE_KEY = "curator-deploy-checklist-v1";

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

function formatTimestamp(iso: string): string {
  return new Date(iso).toLocaleString("en-GB", {
    dateStyle: "short", timeStyle: "medium",
  });
}

function timeAgo(iso: string): string {
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 45) return "just now";
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.floor(h / 24);
  return `${d}d ago`;
}

function shortHash(hash: string): string {
  return hash.slice(0, 8);
}

function formatBytes(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
  if (bytes >= 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${bytes} B`;
}

/* ------------------------------------------------------------------ */
/* Motion presets                                                      */
/* ------------------------------------------------------------------ */

const stagger = {
  hidden: {},
  show: { transition: { staggerChildren: 0.07 } },
};
const fadeUp = {
  hidden: { opacity: 0, y: 14 },
  show: { opacity: 1, y: 0, transition: { duration: 0.45, ease: "easeOut" as const } },
};

/* ------------------------------------------------------------------ */
/* Theme toggle (v30.4 — dark → light → system, three-state cycle)      */
/* ------------------------------------------------------------------ */

const THEME_ORDER = ["dark", "light", "system"] as const;
type ThemePref = (typeof THEME_ORDER)[number];

function ThemeToggle() {
  const { theme, setTheme } = useTheme();
  const [mounted, setMounted] = useState(false);

  // next-themes resolves on the client only — render a stable placeholder
  // until mounted to avoid a hydration mismatch (rAF-deferred setState,
  // same pattern as the checklist loader).
  useEffect(() => {
    const raf = requestAnimationFrame(() => setMounted(true));
    return () => cancelAnimationFrame(raf);
  }, []);

  const current: ThemePref = THEME_ORDER.includes(theme as ThemePref)
    ? (theme as ThemePref)
    : "dark";
  const next = THEME_ORDER[(THEME_ORDER.indexOf(current) + 1) % THEME_ORDER.length];
  const label = `Theme: ${current} — switch to ${next}`;

  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>
          <Button
            variant="outline"
            size="sm"
            aria-label={label}
            onClick={() => setTheme(next)}
            className="h-7 w-7 border-zinc-200 bg-zinc-50 p-0 text-zinc-600 hover:bg-zinc-100 hover:text-zinc-900 dark:border-zinc-700 dark:bg-zinc-800/50 dark:text-zinc-300 dark:hover:bg-zinc-800 dark:hover:text-zinc-100"
          >
            {mounted ? (
              current === "dark" ? (
                <Moon className="h-3.5 w-3.5" />
              ) : current === "light" ? (
                <Sun className="h-3.5 w-3.5" />
              ) : (
                <Monitor className="h-3.5 w-3.5" />
              )
            ) : (
              <span className="h-3.5 w-3.5" aria-hidden="true" />
            )}
          </Button>
        </TooltipTrigger>
        <TooltipContent>
          <span className="text-xs">{label} · cycles dark → light → system</span>
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

/* ------------------------------------------------------------------ */
/* Count-up (framer-motion driven — no sync setState in effects)        */
/* ------------------------------------------------------------------ */

function CountUp({ value, format }: { value: number; format?: (v: number) => string }) {
  const [display, setDisplay] = useState(0);
  useEffect(() => {
    const controls = animate(0, value, {
      duration: 1.0,
      ease: [0.16, 1, 0.3, 1],
      onUpdate: (v) => setDisplay(v),
    });
    return () => controls.stop();
  }, [value]);
  return <>{format ? format(display) : Math.round(display)}</>;
}

/* ------------------------------------------------------------------ */
/* Loading skeleton                                                    */
/* ------------------------------------------------------------------ */

function DashboardSkeleton() {
  return (
    <div className="space-y-6" aria-busy="true" aria-label="Loading release report">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
        {Array.from({ length: 5 }).map((_, i) => (
          <Card key={i} className="border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900/60">
            <CardContent className="p-5 space-y-3">
              <div className="h-9 w-9 rounded-lg bg-zinc-200 dark:bg-zinc-800 animate-pulse" />
              <div className="h-7 w-16 rounded bg-zinc-200 dark:bg-zinc-800 animate-pulse" />
              <div className="h-3 w-24 rounded bg-zinc-200/70 dark:bg-zinc-800/70 animate-pulse" />
            </CardContent>
          </Card>
        ))}
      </div>
      <Card className="border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900/60">
        <CardContent className="p-6 space-y-4">
          <div className="h-4 w-40 rounded bg-zinc-200 dark:bg-zinc-800 animate-pulse" />
          <div className="grid grid-cols-2 gap-4 md:grid-cols-6">
            {Array.from({ length: 6 }).map((_, i) => (
              <div key={i} className="h-24 rounded-xl bg-zinc-200/70 dark:bg-zinc-800/60 animate-pulse" />
            ))}
          </div>
        </CardContent>
      </Card>
      <Card className="border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900/60">
        <CardContent className="p-6 space-y-3">
          <div className="h-4 w-56 rounded bg-zinc-200 dark:bg-zinc-800 animate-pulse" />
          {Array.from({ length: 5 }).map((_, i) => (
            <div key={i} className="h-12 rounded-lg bg-zinc-200/60 dark:bg-zinc-800/50 animate-pulse" />
          ))}
        </CardContent>
      </Card>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Error card                                                          */
/* ------------------------------------------------------------------ */

function ErrorCard({ onRetry }: { onRetry: () => void }) {
  return (
    <Card className="border-rose-500/30 bg-rose-950/20">
      <CardContent className="p-8 flex flex-col items-center gap-4 text-center">
        <AlertTriangle className="h-10 w-10 text-rose-600 dark:text-rose-400" aria-hidden="true" />
        <div>
          <h3 className="text-lg font-semibold text-rose-800 dark:text-rose-200">Could not load the release report</h3>
          <p className="text-sm text-rose-700/70 dark:text-rose-300/70 mt-1 max-w-md">
            The dashboard fetches its data from <code className="font-mono text-xs">/api/report</code>.
            Check the dev server log and retry.
          </p>
        </div>
        <Button variant="outline" onClick={onRetry} className="border-rose-500/40 text-rose-800 dark:text-rose-200 hover:bg-rose-500/10">
          <RefreshCw className="mr-2 h-4 w-4" /> Retry
        </Button>
      </CardContent>
    </Card>
  );
}

/** Retry click — resets state in a USER EVENT (allowed), then bumps the
 *  effect key so the fetch re-runs with a clean slate. */
function handleRetry(
  setReport: (v: ReleaseReport | null) => void,
  setError: (v: string | null) => void,
  setReloadKey: (fn: (k: number) => number) => void,
) {
  setReport(null);
  setError(null);
  setReloadKey((k) => k + 1);
}

/* ------------------------------------------------------------------ */
/* Metric cards                                                        */
/* ------------------------------------------------------------------ */

interface Metric {
  icon: React.ComponentType<{ className?: string }>;
  label: string;
  num: number;
  format?: (v: number) => string;
  sub: string;
  accent: string;
}

function MetricsRow({ report }: { report: ReleaseReport }) {
  const m = report.metrics;
  const metrics: Metric[] = [
    { icon: Bug, label: "Bugs fixed", num: m.bugsFixed, sub: "10 approved + model bug + 2 test-caught", accent: "text-emerald-600 dark:text-emerald-400 bg-emerald-500/10" },
    { icon: FlaskConical, label: "Tests passing", num: m.testsPassed, format: (v) => `${Math.round(v)}/${m.testsTotal}`, sub: "34 unit + 11 end-to-end — 0.22s, no network", accent: "text-teal-600 dark:text-teal-400 bg-teal-500/10" },
    { icon: FileCode2, label: "Lines audited", num: m.mainPyLines, format: (v) => `${(v / 1000).toFixed(1)}K`, sub: "main.py + 6 satellite modules", accent: "text-amber-600 dark:text-amber-400 bg-amber-500/10" },
    { icon: Layers, label: "Files touched", num: m.filesTouched, sub: "v30.1: +e2e suite, −2 stale artifacts", accent: "text-orange-600 dark:text-orange-400 bg-orange-500/10" },
    { icon: Sparkles, label: "New modules", num: m.newModules, sub: "4 core + 2 test suites, zero GUI deps", accent: "text-lime-600 dark:text-lime-400 bg-lime-500/10" },
  ];

  return (
    <motion.section
      variants={stagger}
      initial="hidden"
      animate="show"
      className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5"
      aria-label="Release metrics"
    >
      {metrics.map((mt) => (
        <motion.div key={mt.label} variants={fadeUp}>
          <Card className="group border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900/60 transition-all duration-200 hover:-translate-y-0.5 hover:border-zinc-300 dark:hover:border-zinc-700 hover:shadow-[0_8px_30px_-12px_rgba(0,0,0,0.7)]">
            <CardContent className="p-5">
              <div className="flex items-start justify-between">
                <div className={`flex h-9 w-9 items-center justify-center rounded-lg ${mt.accent}`} aria-hidden="true">
                  <mt.icon className="h-[18px] w-[18px]" />
                </div>
                <TrendingUp className="h-4 w-4 text-zinc-400 dark:text-zinc-700 opacity-0 group-hover:opacity-100 transition-opacity" aria-hidden="true" />
              </div>
              <p className="mt-4 font-mono text-2xl font-semibold tabular-nums text-zinc-900 dark:text-zinc-50">
                <CountUp value={mt.num} format={mt.format} />
              </p>
              <p className="text-[13px] font-medium text-zinc-500 dark:text-zinc-400">{mt.label}</p>
              <p className="mt-1 text-[11px] leading-snug text-zinc-500">{mt.sub}</p>
            </CardContent>
          </Card>
        </motion.div>
      ))}
    </motion.section>
  );
}

/* ------------------------------------------------------------------ */
/* Pipeline flow                                                       */
/* ------------------------------------------------------------------ */

function PipelineFlow({ stages }: { stages: PipelineStage[] }) {
  return (
    <section aria-label="Pipeline architecture">
      <div className="flex flex-wrap items-center gap-2 mb-4">
        <h2 className="text-base font-semibold text-zinc-900 dark:text-zinc-100">Pipeline architecture</h2>
        <Badge variant="outline" className="border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300 font-normal">
          <ShieldCheck className="mr-1 h-3 w-3" /> v30 hardening at every stage
        </Badge>
      </div>
      <motion.ol
        variants={stagger}
        initial="hidden"
        animate="show"
        className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6"
      >
        {stages.map((stage, i) => {
          const Icon = STAGE_ICONS[stage.icon] ?? CircleDot;
          return (
            <motion.li key={stage.id} variants={fadeUp} className="relative">
              <Card className="h-full border-zinc-200 dark:border-zinc-800 bg-gradient-to-b from-zinc-50 dark:from-zinc-900/80 to-white dark:to-zinc-950/80 transition-all duration-200 hover:-translate-y-0.5 hover:border-emerald-500/25">
                <CardContent className="p-4 flex h-full flex-col gap-2.5">
                  <div className="flex items-center justify-between">
                    <span className="flex h-8 w-8 items-center justify-center rounded-md bg-emerald-500/10 text-emerald-600 dark:text-emerald-400" aria-hidden="true">
                      <Icon className="h-4 w-4" />
                    </span>
                    <span className="font-mono text-[10px] text-zinc-500 dark:text-zinc-600">0{i + 1}</span>
                  </div>
                  <p className="text-sm font-semibold text-zinc-900 dark:text-zinc-100 leading-tight">{stage.name}</p>
                  <p className="text-[11.5px] leading-snug text-zinc-500">{stage.description}</p>
                  <p className="mt-auto pt-2 border-t border-zinc-200/80 dark:border-zinc-800/80 text-[11px] leading-snug text-emerald-700/80 dark:text-emerald-300/80">
                    <Zap className="inline mr-1 h-3 w-3 -translate-y-px" aria-hidden="true" />
                    {stage.hardening}
                  </p>
                </CardContent>
              </Card>
              {i < stages.length - 1 && (
                <ChevronRight
                  className="hidden xl:block absolute top-1/2 -right-[13px] -translate-y-1/2 h-4 w-4 text-zinc-400 dark:text-zinc-700 z-10"
                  aria-hidden="true"
                />
              )}
            </motion.li>
          );
        })}
      </motion.ol>
    </section>
  );
}

/* ------------------------------------------------------------------ */
/* Copy-to-clipboard button (evidence lines)                           */
/* ------------------------------------------------------------------ */

function CopyButton({ text, label }: { text: string; label: string }) {
  const { toast } = useToast();
  const [copied, setCopied] = useState(false);

  async function onCopy() {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      toast({ title: "Copied to clipboard", description: label });
      setTimeout(() => setCopied(false), 1600);
    } catch {
      toast({
        title: "Copy failed",
        description: "Clipboard access was blocked by the browser.",
        variant: "destructive",
      });
    }
  }

  return (
    <Button
      variant="ghost"
      size="sm"
      onClick={onCopy}
      aria-label={`Copy ${label}`}
      className="h-6 w-6 p-0 text-zinc-500 dark:text-zinc-600 hover:bg-zinc-200 dark:hover:bg-zinc-800 hover:text-zinc-700 dark:hover:text-zinc-300 focus-visible:ring-1 focus-visible:ring-emerald-500/50"
    >
      {copied ? <CheckCircle2 className="h-3.5 w-3.5 text-emerald-600 dark:text-emerald-400" /> : <FileJson className="h-3.5 w-3.5" />}
    </Button>
  );
}

/* ------------------------------------------------------------------ */
/* Fixes tab (search + severity filter)                                */
/* ------------------------------------------------------------------ */

type SeverityFilter = "all" | Severity;

function FixesTab({ fixes, severityBreakdown }: { fixes: Fix[]; severityBreakdown: Record<Severity, number> }) {
  const [query, setQuery] = useState("");
  const [sevFilter, setSevFilter] = useState<SeverityFilter>("all");
  const searchRef = useRef<HTMLInputElement>(null);

  // "/" focuses the search box (keyboard shortcut, no state needed here)
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "/" && document.activeElement?.tagName !== "INPUT") {
        e.preventDefault();
        searchRef.current?.focus();
      }
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return fixes.filter((fix) => {
      if (sevFilter !== "all" && fix.severity !== sevFilter) return false;
      if (!q) return true;
      const haystack = `${fix.id} ${fix.title} ${fix.summary} ${fix.detail} ${fix.files.join(" ")} ${fix.evidence ?? ""}`.toLowerCase();
      return haystack.includes(q);
    });
  }, [fixes, query, sevFilter]);

  const total = Object.values(severityBreakdown).reduce((a, b) => a + b, 0);
  const filterChips: { key: SeverityFilter; label: string; count: number }[] = [
    { key: "all", label: "All", count: total },
    ...SEVERITY_ORDER.map((sev) => ({
      key: sev as SeverityFilter, label: SEVERITY_STYLES[sev].label, count: severityBreakdown[sev],
    })),
  ];

  return (
    <div className="space-y-4">
      {/* Severity distribution + search */}
      <Card className="border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900/60">
        <CardContent className="p-4 space-y-3">
          <div className="flex flex-wrap items-center gap-x-6 gap-y-3">
            <span className="text-sm text-zinc-500 dark:text-zinc-400">Severity distribution</span>
            <div className="flex flex-1 min-w-[220px] items-center gap-2">
              {SEVERITY_ORDER.map((sev) => (
                <TooltipProvider key={sev}>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <div
                        className={`h-2.5 rounded-full ${SEVERITY_STYLES[sev].dot} cursor-default`}
                        style={{ width: `${Math.max((severityBreakdown[sev] / total) * 100, 6)}%` }}
                        role="presentation"
                      />
                    </TooltipTrigger>
                    <TooltipContent>
                      <span className="text-xs">{SEVERITY_STYLES[sev].label}: {severityBreakdown[sev]}</span>
                    </TooltipContent>
                  </Tooltip>
                </TooltipProvider>
              ))}
            </div>
          </div>
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
            <div className="relative flex-1">
              <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-zinc-500 dark:text-zinc-600" aria-hidden="true" />
              <Input
                ref={searchRef}
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Search fixes, files, evidence…  ( / )"
                aria-label="Search fixes"
                className="h-8 border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-950 pl-8 pr-8 text-[13px] text-zinc-800 dark:text-zinc-200 placeholder:text-zinc-400 dark:placeholder:text-zinc-600 focus-visible:ring-1 focus-visible:ring-emerald-500/50"
              />
              {query && (
                <button
                  type="button"
                  onClick={() => setQuery("")}
                  aria-label="Clear search"
                  className="absolute right-2 top-1/2 -translate-y-1/2 rounded-sm p-0.5 text-zinc-500 dark:text-zinc-600 hover:text-zinc-700 dark:hover:text-zinc-300 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-emerald-500/50"
                >
                  <X className="h-3.5 w-3.5" />
                </button>
              )}
            </div>
            <div className="flex flex-wrap gap-1.5" role="group" aria-label="Filter by severity">
              {filterChips.map((chip) => {
                const active = sevFilter === chip.key;
                return (
                  <button
                    key={chip.key}
                    type="button"
                    onClick={() => setSevFilter(chip.key)}
                    aria-pressed={active}
                    className={`rounded-md border px-2.5 py-1 font-mono text-[11px] transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-emerald-500/50 ${
                      active
                        ? "border-emerald-500/40 bg-emerald-500/15 text-emerald-700 dark:text-emerald-300"
                        : "border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-950 text-zinc-500 hover:border-zinc-300 dark:hover:border-zinc-700 hover:text-zinc-700 dark:hover:text-zinc-300"
                    }`}
                  >
                    {chip.label} <span className="opacity-60">{chip.count}</span>
                  </button>
                );
              })}
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Result count */}
      <p className="text-[12px] text-zinc-500" aria-live="polite">
        Showing <span className="font-mono text-zinc-500 dark:text-zinc-400">{filtered.length}</span> of{" "}
        <span className="font-mono text-zinc-500 dark:text-zinc-400">{fixes.length}</span> fixes
        {sevFilter !== "all" && ` · filtered by ${SEVERITY_STYLES[sevFilter].label.toLowerCase()}`}
        {query && ` · matching “${query.trim()}”`}
      </p>

      {/* List */}
      {filtered.length === 0 ? (
        <Card className="border-dashed border-zinc-200 dark:border-zinc-800 bg-zinc-50 dark:bg-zinc-900/40">
          <CardContent className="p-10 flex flex-col items-center gap-3 text-center">
            <Search className="h-8 w-8 text-zinc-400 dark:text-zinc-700" aria-hidden="true" />
            <p className="text-sm font-medium text-zinc-500 dark:text-zinc-400">No fixes match your search</p>
            <p className="text-xs text-zinc-500">Try a different term, or clear the filters.</p>
            <Button
              variant="outline" size="sm"
              onClick={() => { setQuery(""); setSevFilter("all"); }}
              className="mt-1 border-zinc-300 dark:border-zinc-700 bg-transparent text-zinc-700 dark:text-zinc-300 hover:bg-zinc-200 dark:hover:bg-zinc-800"
            >
              <RotateCcw className="mr-1.5 h-3.5 w-3.5" /> Reset filters
            </Button>
          </CardContent>
        </Card>
      ) : (
        <ScrollArea className="h-[62vh] max-h-[720px] pr-3 custom-scroll">
          <motion.ul
            key={`${sevFilter}:${query}`}
            variants={stagger}
            initial="hidden"
            animate="show"
            className="space-y-3 p-1"
            aria-label="Shipped fixes"
          >
            {filtered.map((fix) => (
              <motion.li key={fix.id} variants={fadeUp}>
                <Card className="group border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900/60 transition-all duration-200 hover:-translate-y-0.5 hover:border-zinc-400 dark:hover:border-zinc-600">
                  <CardContent className="p-5">
                    <div className="flex flex-wrap items-start gap-3">
                      <span className="flex h-9 w-11 shrink-0 items-center justify-center rounded-md bg-zinc-100 dark:bg-zinc-800 font-mono text-xs font-semibold text-zinc-700 dark:text-zinc-300 group-hover:bg-emerald-500/10 group-hover:text-emerald-700 dark:group-hover:text-emerald-300 transition-colors">
                        {fix.id}
                      </span>
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-2">
                          <h3 className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">{fix.title}</h3>
                          <Badge variant="outline" className={`text-[10px] ${SEVERITY_STYLES[fix.severity].cls}`}>
                            {SEVERITY_STYLES[fix.severity].label}
                          </Badge>
                          {fix.status === "quarantined" && (
                            <Badge variant="outline" className="text-[10px] border-zinc-400 dark:border-zinc-600 bg-zinc-500/10 dark:bg-zinc-700/20 text-zinc-500 dark:text-zinc-400">
                              quarantined
                            </Badge>
                          )}
                        </div>
                        <p className="mt-1.5 text-[13px] leading-relaxed text-zinc-500 dark:text-zinc-400">{fix.summary}</p>
                        <p className="mt-1.5 text-[12px] leading-relaxed text-zinc-500">{fix.detail}</p>
                        <div className="mt-3 flex flex-wrap items-center gap-1.5">
                          {fix.files.map((f) => (
                            <span
                              key={f}
                              className="rounded border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-950 px-2 py-0.5 font-mono text-[10.5px] text-zinc-500 dark:text-zinc-400"
                            >
                              {f}
                            </span>
                          ))}
                        </div>
                        {fix.evidence && (
                          <p className="mt-2 flex items-center gap-1.5 font-mono text-[10.5px] text-zinc-500">
                            <span className="text-zinc-500">evidence:</span>
                            <span className="min-w-0 truncate">{fix.evidence}</span>
                            <CopyButton text={fix.evidence} label={`${fix.id} evidence locations`} />
                          </p>
                        )}
                      </div>
                      <CheckCircle2 className="h-5 w-5 shrink-0 text-emerald-600/70 dark:text-emerald-500/70" aria-label="Fixed" />
                    </div>
                  </CardContent>
                </Card>
              </motion.li>
            ))}
          </motion.ul>
        </ScrollArea>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Audit tab (SWOT)                                                    */
/* ------------------------------------------------------------------ */

function SwotGrid({ swot }: { swot: ReleaseReport["swot"] }) {
  const quadrants = [
    {
      title: "Strengths",
      icon: ShieldCheck,
      items: swot.strengths,
      accent: "text-emerald-600 dark:text-emerald-400 bg-emerald-500/10 border-emerald-500/20",
      bar: "bg-emerald-400",
    },
    {
      title: "Weaknesses → fixed in v30",
      icon: Wrench,
      items: swot.weaknessesFixed,
      accent: "text-amber-600 dark:text-amber-400 bg-amber-500/10 border-amber-500/20",
      bar: "bg-amber-400",
    },
    {
      title: "Opportunities",
      icon: Lightbulb,
      items: swot.opportunities,
      accent: "text-teal-600 dark:text-teal-400 bg-teal-500/10 border-teal-500/20",
      bar: "bg-teal-400",
    },
    {
      title: "Threats",
      icon: AlertTriangle,
      items: swot.threats,
      accent: "text-rose-600 dark:text-rose-400 bg-rose-500/10 border-rose-500/20",
      bar: "bg-rose-400",
    },
  ];
  return (
    <div className="grid gap-4 md:grid-cols-2">
      {quadrants.map((q) => (
        <Card key={q.title} className="border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900/60">
          <CardHeader className="pb-3">
            <div className="flex items-center gap-2.5">
              <span className={`flex h-8 w-8 items-center justify-center rounded-lg border ${q.accent}`} aria-hidden="true">
                <q.icon className="h-4 w-4" />
              </span>
              <CardTitle className="text-sm font-semibold">{q.title}</CardTitle>
            </div>
          </CardHeader>
          <CardContent>
            <ul className="space-y-2.5">
              {q.items.map((item, i) => (
                <li key={i} className="flex items-start gap-2.5 text-[13px] leading-snug text-zinc-500 dark:text-zinc-400">
                  <span className={`mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full ${q.bar}`} aria-hidden="true" />
                  {item}
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Live verification card                                              */
/* ------------------------------------------------------------------ */

interface VerifyState {
  result: VerifyResult | null;
  running: boolean;
  error: string | null;
}

function SuiteRow({ suite }: { suite: VerifyResult["suites"][number] }) {
  const [open, setOpen] = useState(false);
  const healthy = suite.failed === 0 && suite.errors === 0;
  const timed = suite.cases.filter((c) => c.ms != null);
  const slowest = timed.length > 0 ? Math.max(...timed.map((c) => c.ms ?? 0)) : null;
  return (
    <div className="rounded-lg border border-zinc-200 dark:border-zinc-800 bg-zinc-100/80 dark:bg-zinc-950/60">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="flex w-full items-center gap-3 px-3.5 py-2.5 text-left transition-colors hover:bg-zinc-100 dark:hover:bg-zinc-900/60 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-emerald-500/50 rounded-lg"
      >
        <ChevronDown
          className={`h-3.5 w-3.5 shrink-0 text-zinc-500 dark:text-zinc-600 transition-transform ${open ? "" : "-rotate-90"}`}
          aria-hidden="true"
        />
        <span className="font-mono text-[12.5px] text-zinc-800 dark:text-zinc-200">{suite.suite}</span>
        {healthy ? (
          <Badge variant="outline" className="border-emerald-500/30 bg-emerald-500/10 font-mono text-[10.5px] text-emerald-700 dark:text-emerald-300">
            <CheckCircle2 className="mr-1 h-3 w-3" /> {suite.passed}/{suite.total}
          </Badge>
        ) : (
          <Badge variant="outline" className="border-rose-500/30 bg-rose-500/10 font-mono text-[10.5px] text-rose-700 dark:text-rose-300">
            <AlertTriangle className="mr-1 h-3 w-3" /> {suite.passed}/{suite.total}
          </Badge>
        )}
        <span className="ml-auto font-mono text-[10.5px] text-zinc-500">
          {suite.durationMs != null && `${formatDuration(suite.durationMs)} · `}
          {suite.failed > 0 && `${suite.failed} failed · `}
          {suite.errors > 0 && `${suite.errors} errors · `}
          {suite.cases.length} cases
          {slowest != null && slowest > 100 && ` · slowest ${formatDuration(slowest)}`}
        </span>
      </button>
      {open && (
        <div className="border-t border-zinc-200/80 dark:border-zinc-800/80 px-3.5 py-3">
          <div className="flex flex-wrap gap-1.5">
            {suite.cases.map((c) => (
              <span
                key={c.name}
                title={`${c.name} · ${c.status}${c.ms != null ? ` · ${c.ms.toFixed(1)}ms` : ""}`}
                className={`rounded border px-1.5 py-0.5 font-mono text-[10px] ${
                  c.status === "ok"
                    ? (c.ms != null && c.ms > 150
                        ? "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-300/90"
                        : "border-emerald-500/20 bg-emerald-500/5 text-emerald-700/80 dark:text-emerald-300/80")
                    : c.status === "skipped"
                      ? "border-zinc-300 dark:border-zinc-700 bg-zinc-200/60 dark:bg-zinc-800/40 text-zinc-500 dark:text-zinc-400"
                      : "border-rose-500/30 bg-rose-500/10 text-rose-700 dark:text-rose-300"
                }`}
              >
                {c.name}
                {c.ms != null && c.ms > 150 && (
                  <span className="ml-1 opacity-70" aria-label="slow case">{c.ms.toFixed(0)}ms</span>
                )}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function LiveVerificationCard({
  state,
  onRun,
}: {
  state: VerifyState;
  onRun: () => void;
}) {
  const { result, running, error } = state;

  return (
    <Card
      className={`border transition-colors ${
        running
          ? "border-amber-500/30 bg-gradient-to-r from-amber-950/20 via-white dark:via-zinc-900/60 to-white dark:to-zinc-900/60"
          : result?.ok
            ? "border-emerald-500/30 bg-gradient-to-r from-emerald-950/30 via-white dark:via-zinc-900/60 to-white dark:to-zinc-900/60"
            : result
              ? "border-rose-500/30 bg-rose-950/10"
              : "border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900/60"
      }`}
    >
      <CardContent className="p-5 space-y-4">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="flex items-start gap-3">
            <span
              className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-lg ${
                running
                  ? "bg-amber-500/15 text-amber-600 dark:text-amber-400"
                  : result?.ok
                    ? "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400"
                    : result
                      ? "bg-rose-500/15 text-rose-600 dark:text-rose-400"
                      : "bg-zinc-100 dark:bg-zinc-800 text-zinc-500 dark:text-zinc-400"
              }`}
              aria-hidden="true"
            >
              {running ? (
                <Loader2 className="h-5 w-5 animate-spin" />
              ) : result?.ok ? (
                <TerminalSquare className="h-5 w-5" />
              ) : (
                <Play className="h-5 w-5" />
              )}
            </span>
            <div>
              <h3 className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">
                {running
                  ? "Running live verification…"
                  : result?.ok
                    ? "Verification passed — build is green"
                    : result
                      ? "Verification failed"
                      : "Live verification"}
              </h3>
              <p className="mt-0.5 max-w-xl text-[12px] leading-snug text-zinc-500">
                {running ? (
                  <>py_compile on 9 core files, then <code className="font-mono text-[11px]">tests.test_core + tests.test_e2e</code> — executed against the real audit tree.</>
                ) : result ? (
                  <>{result.summaryLine} · {result.pythonVersion} · {formatDuration(result.durationMs)} total</>
                ) : (
                  <>Actually executes <code className="font-mono text-[11px]">py_compile</code> on the 9 core files and both test suites (45 cases) against <code className="font-mono text-[11px]">audit/Github V6.7</code> — no mocked numbers.</>
                )}
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            {result?.codeHash && !running && (
              <TooltipProvider>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Badge
                      variant="outline"
                      className="hidden border-zinc-300 dark:border-zinc-700 bg-zinc-200/60 dark:bg-zinc-800/40 font-mono text-[10px] text-zinc-500 dark:text-zinc-400 sm:inline-flex"
                    >
                      <Fingerprint className="mr-1 h-3 w-3" /> {shortHash(result.codeHash)}
                    </Badge>
                  </TooltipTrigger>
                  <TooltipContent>
                    <span className="max-w-[280px] break-all font-mono text-xs">
                      code fingerprint (sha256 of the 9 audited files): {result.codeHash}
                    </span>
                  </TooltipContent>
                </Tooltip>
              </TooltipProvider>
            )}
            {result && !running && (
              <span className="hidden font-mono text-[10.5px] text-zinc-500 sm:block">
                {formatTimestamp(result.ranAt)}
              </span>
            )}
            <Button
              size="sm"
              onClick={onRun}
              disabled={running}
              className={
                result?.ok
                  ? "bg-emerald-600 text-emerald-50 hover:bg-emerald-500"
                  : "bg-zinc-900 text-zinc-900 dark:text-zinc-50 hover:bg-zinc-950 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-white"
              }
            >
              {running ? (
                <><Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" /> Running…</>
              ) : result ? (
                <><RefreshCw className="mr-1.5 h-3.5 w-3.5" /> Run again</>
              ) : (
                <><Play className="mr-1.5 h-3.5 w-3.5" /> Run verification</>
              )}
            </Button>
          </div>
        </div>

        {running && (
          <div className="space-y-2" aria-hidden="true">
            <div className="h-1.5 w-full overflow-hidden rounded-full bg-zinc-100 dark:bg-zinc-800">
              <div className="h-full w-1/3 animate-[shimmer_1.4s_ease-in-out_infinite] rounded-full bg-gradient-to-r from-transparent via-amber-400/70 to-transparent" />
            </div>
            <p className="font-mono text-[10.5px] text-zinc-500">
              $ python3 -m py_compile … &amp;&amp; python3 -m unittest tests.test_core tests.test_e2e -v
            </p>
          </div>
        )}

        {error && !running && (
          <div className="rounded-lg border border-rose-500/30 bg-rose-950/20 px-3.5 py-3" role="alert">
            <p className="text-[13px] font-medium text-rose-800 dark:text-rose-200">Verification could not run</p>
            <p className="mt-0.5 font-mono text-[11px] text-rose-700/70 dark:text-rose-300/70">{error}</p>
          </div>
        )}

        {result && !running && (
          <div className="space-y-4">
            {/* summary numbers */}
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              <div className="rounded-lg border border-zinc-200 dark:border-zinc-800 bg-zinc-100/80 dark:bg-zinc-950/60 px-3 py-2.5">
                <p className="font-mono text-lg font-semibold tabular-nums text-zinc-900 dark:text-zinc-100">
                  {result.compile.filter((c) => c.ok).length}/{result.compile.length}
                </p>
                <p className="text-[10.5px] text-zinc-500">files compiled</p>
              </div>
              <div className="rounded-lg border border-zinc-200 dark:border-zinc-800 bg-zinc-100/80 dark:bg-zinc-950/60 px-3 py-2.5">
                <p className={`font-mono text-lg font-semibold tabular-nums ${result.testsPassed === result.testsTotal ? "text-emerald-700 dark:text-emerald-300" : "text-rose-700 dark:text-rose-300"}`}>
                  {result.testsPassed}/{result.testsTotal}
                </p>
                <p className="text-[10.5px] text-zinc-500">tests passed</p>
              </div>
              <div className="rounded-lg border border-zinc-200 dark:border-zinc-800 bg-zinc-100/80 dark:bg-zinc-950/60 px-3 py-2.5">
                <p className="font-mono text-lg font-semibold tabular-nums text-zinc-900 dark:text-zinc-100">{formatDuration(result.durationMs)}</p>
                <p className="text-[10.5px] text-zinc-500">wall clock</p>
              </div>
              <div className="rounded-lg border border-zinc-200 dark:border-zinc-800 bg-zinc-100/80 dark:bg-zinc-950/60 px-3 py-2.5">
                <p className={`font-mono text-lg font-semibold ${result.ok ? "text-emerald-700 dark:text-emerald-300" : "text-rose-700 dark:text-rose-300"}`}>
                  {result.ok ? "GREEN" : "RED"}
                </p>
                <p className="text-[10.5px] text-zinc-500">gate status</p>
              </div>
            </div>

            {/* compile grid */}
            <div>
              <p className="mb-2 text-[11px] font-medium uppercase tracking-wide text-zinc-500">Compile gate — py_compile</p>
              <div className="grid grid-cols-1 gap-1.5 sm:grid-cols-2 lg:grid-cols-3">
                {result.compile.map((c) => (
                  <div
                    key={c.file}
                    title={c.ok ? `${c.file} — ${c.ms}ms` : c.error ?? "compile failed"}
                    className={`flex items-center gap-2 rounded-md border px-2.5 py-1.5 font-mono text-[11px] ${
                      c.ok
                        ? "border-zinc-200 dark:border-zinc-800 bg-zinc-100/80 dark:bg-zinc-950/60 text-zinc-500 dark:text-zinc-400"
                        : "border-rose-500/30 bg-rose-500/10 text-rose-700 dark:text-rose-300"
                    }`}
                  >
                    {c.ok ? (
                      <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-emerald-600/80 dark:text-emerald-500/80" aria-hidden="true" />
                    ) : (
                      <AlertTriangle className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                    )}
                    <span className="min-w-0 truncate">{c.file}</span>
                    <span className="ml-auto shrink-0 text-[10px] text-zinc-500">{c.ms}ms</span>
                  </div>
                ))}
              </div>
            </div>

            {/* suites */}
            <div>
              <p className="mb-2 text-[11px] font-medium uppercase tracking-wide text-zinc-500">Test suites — expand for case list</p>
              <div className="space-y-1.5">
                {result.suites.map((suite) => (
                  <SuiteRow key={suite.suite} suite={suite} />
                ))}
              </div>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

/* ------------------------------------------------------------------ */
/* Tests tab                                                           */
/* ------------------------------------------------------------------ */

function TestsTab({ report, verify, onRunVerify }: { report: ReleaseReport; verify: VerifyState; onRunVerify: () => void }) {
  const pct = Math.round((report.metrics.testsPassed / report.metrics.testsTotal) * 100);
  return (
    <div className="space-y-4">
      <LiveVerificationCard state={verify} onRun={onRunVerify} />

      <Card className="border-emerald-500/25 bg-gradient-to-r from-emerald-950/40 via-white dark:via-zinc-900/60 to-white dark:to-zinc-900/60">
        <CardContent className="p-5">
          <div className="flex flex-wrap items-center justify-between gap-4">
            <div className="flex items-center gap-3">
              <span className="flex h-10 w-10 items-center justify-center rounded-lg bg-emerald-500/15 text-emerald-600 dark:text-emerald-400" aria-hidden="true">
                <FlaskConical className="h-5 w-5" />
              </span>
              <div>
                <p className="font-mono text-xl font-semibold text-emerald-700 dark:text-emerald-300 tabular-nums">
                  {report.metrics.testsPassed}/{report.metrics.testsTotal} passing
                </p>
                <p className="text-xs text-zinc-500">34 unit + 11 end-to-end · 0.22s · no network, no GUI, no Ollama</p>
              </div>
            </div>
            <div className="w-48">
              <Progress value={pct} className="h-2 bg-zinc-100 dark:bg-zinc-800 [&>div]:bg-emerald-400" aria-label={`${pct}% tests passing`} />
              <p className="mt-1.5 text-right text-[11px] text-zinc-500">{pct}% green</p>
            </div>
          </div>
        </CardContent>
      </Card>

      <div className="grid gap-3 md:grid-cols-2">
        {report.testSuites.map((suite: TestSuite) => (
          <Card key={suite.name} className="border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900/60 transition-all duration-200 hover:-translate-y-0.5 hover:border-zinc-300 dark:hover:border-zinc-700">
            <CardHeader className="pb-2">
              <div className="flex items-center justify-between">
                <CardTitle className="font-mono text-sm text-zinc-900 dark:text-zinc-100">{suite.name}</CardTitle>
                <Badge variant="outline" className="border-emerald-500/30 bg-emerald-500/10 font-mono text-emerald-700 dark:text-emerald-300">
                  <CheckCircle2 className="mr-1 h-3 w-3" /> {suite.tests} tests
                </Badge>
              </div>
              <CardDescription className="font-mono text-[11px] text-zinc-500">{suite.module}</CardDescription>
            </CardHeader>
            <CardContent className="space-y-2">
              <p className="text-[12.5px] leading-snug text-zinc-500 dark:text-zinc-400">{suite.covers}</p>
              {suite.caught && (
                <p className="rounded-md border border-amber-500/20 bg-amber-500/5 px-2.5 py-1.5 text-[11.5px] leading-snug text-amber-700/90 dark:text-amber-300/90">
                  <Target className="inline mr-1 h-3 w-3 -translate-y-px" />
                  {suite.caught}
                </p>
              )}
            </CardContent>
          </Card>
        ))}
      </div>

      <Card className="border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900/60">
        <CardHeader className="pb-2">
          <CardTitle className="flex items-center gap-2 text-sm text-zinc-800 dark:text-zinc-200">
            <ServerCog className="h-4 w-4 text-zinc-500" /> Verification checklist
          </CardTitle>
        </CardHeader>
        <CardContent>
          <ul className="space-y-2">
            {report.verification.map((v, i) => (
              <li key={i} className="flex items-start gap-2.5 text-[13px] text-zinc-500 dark:text-zinc-400">
                <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-emerald-600/80 dark:text-emerald-500/80" aria-hidden="true" />
                <span className="leading-snug">{v}</span>
              </li>
            ))}
          </ul>
        </CardContent>
      </Card>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Go-live checklist tab                                               */
/* ------------------------------------------------------------------ */

function ChecklistTab({
  items,
  checked,
  onToggle,
  onReset,
}: {
  items: ChecklistItem[];
  checked: Record<string, boolean>;
  onToggle: (id: string) => void;
  onReset: () => void;
}) {
  const done = items.filter((i) => checked[i.id]).length;
  const pct = Math.round((done / items.length) * 100);
  const p0Items = items.filter((i) => i.priority === "P0");
  const p0Done = p0Items.filter((i) => checked[i.id]).length;
  const allDone = done === items.length;
  const blocked = p0Done < p0Items.length;

  const groups: Priority[] = ["P0", "P1", "P2"];

  return (
    <div className="space-y-4">
      {/* Status header */}
      <Card
        className={`${
          allDone
            ? "border-emerald-500/40 bg-gradient-to-r from-emerald-950/50 via-white dark:via-zinc-900/60 to-white dark:to-zinc-900/60"
            : blocked
              ? "border-rose-500/30 bg-gradient-to-r from-rose-950/20 via-white dark:via-zinc-900/60 to-white dark:to-zinc-900/60"
              : "border-amber-500/30 bg-gradient-to-r from-amber-950/20 via-white dark:via-zinc-900/60 to-white dark:to-zinc-900/60"
        }`}
      >
        <CardContent className="p-5 space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-4">
            <div className="flex items-center gap-3">
              <span
                className={`flex h-10 w-10 items-center justify-center rounded-lg ${
                  allDone
                    ? "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400"
                    : blocked
                      ? "bg-rose-500/15 text-rose-600 dark:text-rose-400"
                      : "bg-amber-500/15 text-amber-600 dark:text-amber-400"
                }`}
                aria-hidden="true"
              >
                {allDone ? <ShieldCheck className="h-5 w-5" /> : <ClipboardCheck className="h-5 w-5" />}
              </span>
              <div>
                <p className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">
                  {allDone ? "Ready to deploy" : blocked ? "Blocked — P0 items outstanding" : "In progress — P0 clear"}
                </p>
                <p className="text-[12px] text-zinc-500">
                  <span className="font-mono">{done}/{items.length}</span> steps complete ·{" "}
                  <span className="font-mono text-rose-700/90 dark:text-rose-300/90">{p0Items.length - p0Done}</span> P0 blockers left
                </p>
              </div>
            </div>
            <Button
              variant="outline" size="sm"
              onClick={onReset}
              className="border-zinc-300 dark:border-zinc-700 bg-transparent text-zinc-500 dark:text-zinc-400 hover:bg-zinc-200 dark:hover:bg-zinc-800 hover:text-zinc-800 dark:hover:text-zinc-200"
            >
              <RotateCcw className="mr-1.5 h-3.5 w-3.5" /> Reset
            </Button>
          </div>
          <div>
            <Progress
              value={pct}
              className={`h-2 bg-zinc-100 dark:bg-zinc-800 ${allDone ? "[&>div]:bg-emerald-400" : "[&>div]:bg-amber-400"}`}
              aria-label={`${pct}% of go-live checklist complete`}
            />
            <p className="mt-1.5 text-right text-[11px] text-zinc-500">{pct}% · progress saved in this browser</p>
          </div>
        </CardContent>
      </Card>

      {/* Item groups */}
      {groups.map((prio) => {
        const groupItems = items.filter((i) => i.priority === prio);
        if (groupItems.length === 0) return null;
        return (
          <div key={prio} className="space-y-2">
            <div className="flex items-center gap-2">
              <Badge variant="outline" className={`font-mono text-[11px] ${PRIORITY_STYLES[prio]}`}>{prio}</Badge>
              <Separator className="flex-1 bg-zinc-100 dark:bg-zinc-800" />
              <span className="font-mono text-[10.5px] text-zinc-500">
                {groupItems.filter((i) => checked[i.id]).length}/{groupItems.length}
              </span>
            </div>
            {groupItems.map((item) => {
              const isDone = !!checked[item.id];
              return (
                <Card
                  key={item.id}
                  className={`border-zinc-800 transition-all duration-200 ${
                    isDone ? "bg-zinc-100/60 dark:bg-zinc-950/40 border-zinc-200/60 dark:border-zinc-800/60" : "bg-white dark:bg-zinc-900/60 hover:border-zinc-300 dark:hover:border-zinc-700 hover:-translate-y-0.5"
                  }`}
                >
                  <CardContent className="flex items-start gap-3.5 p-4">
                    <Checkbox
                      id={`chk-${item.id}`}
                      checked={isDone}
                      onCheckedChange={() => onToggle(item.id)}
                      aria-label={item.label}
                      className="mt-0.5 data-[state=checked]:border-emerald-500 data-[state=checked]:bg-emerald-600"
                    />
                    <div className="min-w-0 flex-1">
                      <label
                        htmlFor={`chk-${item.id}`}
                        className={`cursor-pointer text-[13.5px] font-medium leading-snug transition-colors ${
                          isDone ? "text-zinc-500 line-through decoration-zinc-300 dark:decoration-zinc-700" : "text-zinc-900 dark:text-zinc-100"
                        }`}
                      >
                        {item.label}
                      </label>
                      <p className={`mt-1 text-[12px] leading-relaxed ${isDone ? "text-zinc-400 dark:text-zinc-700" : "text-zinc-500"}`}>
                        {item.detail}
                      </p>
                      <p className="mt-1.5 font-mono text-[10.5px] text-zinc-500">
                        <GitBranch className="inline mr-1 h-3 w-3 -translate-y-px" /> {item.owner}
                      </p>
                    </div>
                    {isDone && (
                      <CheckCircle2 className="h-[18px] w-[18px] shrink-0 text-emerald-600/80 dark:text-emerald-500/80" aria-hidden="true" />
                    )}
                  </CardContent>
                </Card>
              );
            })}
          </div>
        );
      })}

      {allDone && (
        <Card className="border-emerald-500/30 bg-emerald-950/20">
          <CardContent className="flex flex-col items-center gap-2 p-6 text-center">
            <Sparkles className="h-6 w-6 text-emerald-600 dark:text-emerald-400" aria-hidden="true" />
            <p className="text-sm font-semibold text-emerald-800 dark:text-emerald-200">Every step checked — ship it.</p>
            <p className="text-[12px] text-emerald-700/60 dark:text-emerald-300/60">
              Credentials rotated, verification green, smoke test done. The v30.1 build is cleared for deployment.
            </p>
          </CardContent>
        </Card>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Risks tab                                                           */
/* ------------------------------------------------------------------ */

function RisksTab({ risks }: { risks: ReleaseReport["risks"] }) {
  return (
    <div className="space-y-3">
      <p className="text-[13px] text-zinc-500">
        Ordered by priority —{" "}
        <span className="font-medium text-zinc-500 dark:text-zinc-400">P0 blocks deployment</span>, P1 is next-session work.
      </p>
      <motion.ul variants={stagger} initial="hidden" animate="show" className="space-y-3">
        {risks.map((risk) => (
          <motion.li key={risk.title} variants={fadeUp}>
            <Card
              className={`border-zinc-800 bg-white dark:bg-zinc-900/60 transition-all duration-200 hover:-translate-y-0.5 ${
                risk.priority === "P0" ? "border-rose-500/30 bg-rose-950/10 hover:border-rose-500/40" : "hover:border-zinc-300 dark:hover:border-zinc-700"
              }`}
            >
              <CardContent className="p-4">
                <div className="flex flex-wrap items-start gap-3">
                  <Badge variant="outline" className={`font-mono text-[11px] ${PRIORITY_STYLES[risk.priority]}`}>
                    {risk.priority}
                  </Badge>
                  <div className="min-w-0 flex-1">
                    <h3 className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">{risk.title}</h3>
                    <p className="mt-1 text-[12.5px] leading-relaxed text-zinc-500 dark:text-zinc-400">{risk.detail}</p>
                    <p className="mt-2 text-[11px] text-zinc-500">
                      <GitBranch className="inline mr-1 h-3 w-3 -translate-y-px" /> {risk.owner}
                    </p>
                  </div>
                  {risk.priority === "P0" && <Lock className="h-[18px] w-[18px] text-rose-600 dark:text-rose-400" aria-label="Security blocker" />}
                </div>
              </CardContent>
            </Card>
          </motion.li>
        ))}
      </motion.ul>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* History tab (v30.2 — persisted verification runs + code drift)      */
/* ------------------------------------------------------------------ */

interface ChartPoint {
  idx: number;
  ranAt: string;
  durationMs: number;
  ok: boolean;
  testsPassed: number;
  testsTotal: number;
  codeChanged: boolean;
}

function HistoryStatsTiles({ stats }: { stats: NonNullable<HistoryData["stats"]> }) {
  // v0.0.4 — provenance split chips (rendered under the Total runs value).
  const sourceChips = [
    { label: "manual", count: stats.sources.manual, cls: "border-zinc-200 dark:border-zinc-800 bg-zinc-100/80 dark:bg-zinc-900/60 text-zinc-500 dark:text-zinc-400" },
    { label: "startup", count: stats.sources.startup, cls: "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-300" },
    { label: "scheduled", count: stats.sources.scheduled, cls: "border-teal-500/30 bg-teal-500/10 text-teal-700 dark:text-teal-300" },
  ].filter((c) => c.count > 0);
  const tiles: {
    icon: React.ComponentType<{ className?: string }>;
    label: string;
    value: number;
    format?: (v: number) => string;
    sub: string;
    accent: string;
    footer?: React.ReactNode;
  }[] = [
    {
      icon: History, label: "Total runs", value: stats.totalRuns,
      sub: `${stats.okRuns} green · ${stats.distinctCodeVersions} code version${stats.distinctCodeVersions === 1 ? "" : "s"}`,
      accent: "text-emerald-600 dark:text-emerald-400 bg-emerald-500/10",
      footer: sourceChips.length > 0 ? (
        <div className="mt-1.5 flex flex-wrap gap-1" aria-label="Run provenance split">
          {sourceChips.map((c) => (
            <span key={c.label} className={`inline-flex items-center gap-1 rounded border px-1.5 py-0.5 font-mono text-[9.5px] ${c.cls}`}>
              {c.count} {c.label}
            </span>
          ))}
        </div>
      ) : undefined,
    },
    {
      icon: CheckCircle2, label: "Pass rate", value: stats.passRate,
      format: (v) => `${Math.round(v)}%`, sub: "across all persisted runs",
      accent: "text-teal-600 dark:text-teal-400 bg-teal-500/10",
    },
    {
      icon: Zap, label: "Avg duration", value: stats.avgDurationMs,
      format: (v) => formatDuration(v),
      sub: `min ${formatDuration(stats.minDurationMs)} · max ${formatDuration(stats.maxDurationMs)}`,
      accent: "text-amber-600 dark:text-amber-400 bg-amber-500/10",
    },
    {
      icon: TrendingUp, label: "Green streak", value: stats.streak,
      sub: "consecutive passing runs, newest first",
      accent: "text-lime-600 dark:text-lime-400 bg-lime-500/10",
    },
  ];

  return (
    <motion.section
      variants={stagger}
      initial="hidden"
      animate="show"
      className="grid grid-cols-2 gap-3 lg:grid-cols-4"
      aria-label="Verification history statistics"
    >
      {tiles.map((t) => (
        <motion.div key={t.label} variants={fadeUp}>
          <Card className="group border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900/60 transition-all duration-200 hover:-translate-y-0.5 hover:border-zinc-300 dark:hover:border-zinc-700 hover:shadow-[0_8px_30px_-12px_rgba(0,0,0,0.7)]">
            <CardContent className="p-4">
              <div className="flex items-center gap-2.5">
                <span className={`flex h-8 w-8 items-center justify-center rounded-lg ${t.accent}`} aria-hidden="true">
                  <t.icon className="h-4 w-4" />
                </span>
                <p className="text-[12px] font-medium text-zinc-500 dark:text-zinc-400">{t.label}</p>
              </div>
              <p className="mt-3 font-mono text-xl font-semibold tabular-nums text-zinc-900 dark:text-zinc-50">
                <CountUp value={t.value} format={t.format} />
              </p>
              <p className="mt-1 text-[10.5px] leading-snug text-zinc-500">{t.sub}</p>
              {t.footer}
            </CardContent>
          </Card>
        </motion.div>
      ))}
    </motion.section>
  );
}

function DriftBanner({
  history,
  verifying,
  onRun,
}: {
  history: HistoryData;
  verifying: boolean;
  onRun: () => void;
}) {
  const drifted = history.codeDrifted;
  const lastRun = history.runs[0];

  return (
    <Card
      className={
        drifted
          ? "border-amber-500/30 bg-gradient-to-r from-amber-950/25 via-white dark:via-zinc-900/60 to-white dark:to-zinc-900/60"
          : "border-emerald-500/25 bg-gradient-to-r from-emerald-950/30 via-white dark:via-zinc-900/60 to-white dark:to-zinc-900/60"
      }
    >
      <CardContent className="p-5">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="flex items-start gap-3">
            <span
              className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-lg ${
                drifted ? "bg-amber-500/15 text-amber-600 dark:text-amber-400" : "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400"
              }`}
              aria-hidden="true"
            >
              {drifted ? <AlertTriangle className="h-5 w-5" /> : <Fingerprint className="h-5 w-5" />}
            </span>
            <div>
              <h3 className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">
                {drifted
                  ? "Working tree changed since the last verification"
                  : "Verified code matches the working tree"}
              </h3>
              <p className="mt-0.5 max-w-xl text-[12px] leading-snug text-zinc-500">
                {drifted ? (
                  <>
                    Every run fingerprints the 9 audited Python files with sha256. The tree on disk
                    no longer matches {lastRun ? shortHash(lastRun.codeHash) : "the last run"} —
                    re-run the gate so the newest code is the verified code.
                  </>
                ) : (
                  <>
                    The last green run verified exactly the code sitting in{" "}
                    <code className="font-mono text-[11px]">audit/Github V6.7</code> right now —
                    fingerprint {history.currentCodeHash ? shortHash(history.currentCodeHash) : "—"}.
                  </>
                )}
              </p>
            </div>
          </div>
          <Button
            size="sm"
            onClick={onRun}
            disabled={verifying}
            className={
              drifted
                ? "bg-amber-500 text-amber-950 hover:bg-amber-400"
                : "bg-emerald-600 text-emerald-50 hover:bg-emerald-500"
            }
          >
            {verifying ? (
              <><Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" /> Verifying…</>
            ) : (
              <><Play className="mr-1.5 h-3.5 w-3.5" /> {drifted ? "Re-verify now" : "Run verification"}</>
            )}
          </Button>
        </div>
        {lastRun && (
          <div className="mt-4 flex flex-wrap items-center gap-2 text-[11px]">
            <span className="font-mono text-zinc-500">last verified</span>
            <Badge variant="outline" className="border-zinc-300 dark:border-zinc-700 bg-zinc-200/60 dark:bg-zinc-800/40 font-mono text-[10px] text-zinc-500 dark:text-zinc-400">
              <Fingerprint className="mr-1 h-3 w-3" /> {shortHash(lastRun.codeHash)}
            </Badge>
            <span className="font-mono text-zinc-500">· {timeAgo(lastRun.ranAt)}</span>
            {drifted && (
              <>
                <ArrowRight className="h-3 w-3 text-amber-600 dark:text-amber-400" aria-hidden="true" />
                <span className="font-mono text-zinc-500">on disk now</span>
                <Badge variant="outline" className="border-amber-500/30 bg-amber-500/10 font-mono text-[10px] text-amber-700 dark:text-amber-300">
                  <Fingerprint className="mr-1 h-3 w-3" /> {history.currentCodeHash ? shortHash(history.currentCodeHash) : "—"}
                </Badge>
              </>
            )}
          </div>
        )}
        {drifted && history.changedFiles && history.changedFiles.length > 0 && (
          <div className="mt-3 flex flex-wrap items-center gap-1.5" aria-label="Files changed since the last verification">
            <span className="text-[11px] font-medium text-amber-700 dark:text-amber-300">
              {history.changedFiles.length} file{history.changedFiles.length === 1 ? "" : "s"} changed:
            </span>
            {history.changedFiles.map((f) => (
              <Badge
                key={f}
                variant="outline"
                className="border-amber-500/30 bg-amber-500/10 font-mono text-[10px] text-amber-700 dark:text-amber-300"
              >
                <FileCode2 className="mr-1 h-3 w-3" /> {f}
              </Badge>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function ChartTip({ active, payload }: { active?: boolean; payload?: Array<{ payload: ChartPoint }> }) {
  if (!active || !payload || payload.length === 0) return null;
  const p = payload[0].payload;
  if (!p) return null;
  return (
    <div className="rounded-lg border border-zinc-300 dark:border-zinc-700 bg-white/95 dark:bg-zinc-950/95 px-3 py-2 shadow-xl">
      <p className="font-mono text-[10px] text-zinc-500">run #{p.idx} · {formatTimestamp(p.ranAt)}</p>
      <p className="mt-1 font-mono text-[11.5px] text-zinc-800 dark:text-zinc-200">{formatDuration(p.durationMs)} wall clock</p>
      <p className={`font-mono text-[11.5px] ${p.ok ? "text-emerald-700 dark:text-emerald-300" : "text-rose-700 dark:text-rose-300"}`}>
        {p.testsPassed}/{p.testsTotal} tests · {p.ok ? "GREEN" : "RED"}
      </p>
      {p.codeChanged && <p className="mt-0.5 font-mono text-[10px] text-amber-700 dark:text-amber-300">code changed this run</p>}
    </div>
  );
}

function DurationChart({ runs }: { runs: HistoryRun[] }) {
  const { resolvedTheme } = useTheme();
  // Default matches defaultTheme="dark" — recharts SVG colors are plain hex,
  // so the chart re-picks its palette on every theme change.
  const isDark = resolvedTheme !== "light";
  const C = isDark
    ? {
        grid: "#27272a", tick: "#8b8b94", line: "#34d399", lineEdge: "#064e3b",
        fill: "#34d399", ref: "#a1a1aa", cursor: "#52525b",
        fail: "#fb7185", failEdge: "#881337",
      }
    : {
        grid: "#e4e4e7", tick: "#52525b", line: "#059669", lineEdge: "#065f46",
        fill: "#059669", ref: "#71717a", cursor: "#a1a1aa",
        fail: "#e11d48", failEdge: "#9f1239",
      };

  const points: ChartPoint[] = useMemo(
    () =>
      [...runs]
        .reverse()
        .map((r, i) => ({
          idx: i + 1,
          ranAt: r.ranAt,
          durationMs: r.durationMs,
          ok: r.ok,
          testsPassed: r.testsPassed,
          testsTotal: r.testsTotal,
          codeChanged: r.codeChanged,
        })),
    [runs],
  );
  const avg = points.length > 0
    ? Math.round(points.reduce((a, p) => a + p.durationMs, 0) / points.length)
    : 0;

  const renderDot = (p: { cx?: number; cy?: number; payload?: ChartPoint }) => {
    const point = p.payload;
    if (p.cx == null || p.cy == null || !point) return <g />;
    return (
      <circle
        key={point.idx}
        cx={p.cx}
        cy={p.cy}
        r={point.ok ? 3 : 4}
        fill={point.ok ? C.line : C.fail}
        stroke={point.ok ? C.lineEdge : C.failEdge}
        strokeWidth={1.5}
      />
    );
  };

  return (
    <Card className="border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900/60">
      <CardHeader className="pb-2">
        <CardTitle className="flex items-center gap-2 text-sm text-zinc-800 dark:text-zinc-200">
          <Activity className="h-4 w-4 text-emerald-600 dark:text-emerald-400" /> Verification duration trend
        </CardTitle>
        <CardDescription className="text-[12px] text-zinc-500">
          Wall-clock per run, oldest → newest · dashed line = average ({formatDuration(avg)})
        </CardDescription>
      </CardHeader>
      <CardContent>
        {points.length < 2 ? (
          <p className="py-10 text-center text-[13px] text-zinc-500">
            Run verification a few times to build the trend line.
          </p>
        ) : (
          <div className="h-[200px] w-full" role="img" aria-label="Verification duration trend chart">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={points} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
                <defs>
                  <linearGradient id="durFill" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor={C.fill} stopOpacity={0.3} />
                    <stop offset="100%" stopColor={C.fill} stopOpacity={0.02} />
                  </linearGradient>
                </defs>
                <CartesianGrid stroke={C.grid} strokeDasharray="3 3" vertical={false} />
                <XAxis
                  dataKey="idx"
                  tick={{ fill: C.tick, fontSize: 10, fontFamily: "var(--font-geist-mono), monospace" }}
                  tickLine={false}
                  axisLine={{ stroke: C.grid }}
                />
                <YAxis
                  width={46}
                  tickFormatter={(v: number) => `${(v / 1000).toFixed(1)}s`}
                  tick={{ fill: C.tick, fontSize: 10, fontFamily: "var(--font-geist-mono), monospace" }}
                  tickLine={false}
                  axisLine={{ stroke: C.grid }}
                  domain={[(dataMin: number) => Math.max(0, Math.floor(dataMin - 150)), (dataMax: number) => Math.ceil(dataMax + 150)]}
                />
                <RTooltip content={<ChartTip />} cursor={{ stroke: C.cursor, strokeDasharray: "3 3" }} />
                <ReferenceLine y={avg} stroke={C.ref} strokeDasharray="4 4" strokeOpacity={0.6} />
                <Area
                  type="monotone"
                  dataKey="durationMs"
                  stroke={C.line}
                  strokeWidth={2}
                  fill="url(#durFill)"
                  dot={renderDot}
                  activeDot={{ r: 5, fill: C.line, stroke: C.lineEdge, strokeWidth: 2 }}
                />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

/* ------------------------------------------------------------------ */
/* v0.0.4 — run provenance styling                                     */
/* ------------------------------------------------------------------ */

const RUN_SOURCE_STYLES: Record<
  string,
  { icon: React.ComponentType<{ className?: string }>; cls: string; hint: string }
> = {
  manual: {
    icon: MousePointerClick,
    cls: "border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900/60 text-zinc-500 dark:text-zinc-400",
    hint: "Manual run — triggered from the Tests or History tab",
  },
  startup: {
    icon: RefreshCw,
    cls: "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-300",
    hint: "Startup pass — the scheduler's first run, 60s after the server boots",
  },
  scheduled: {
    icon: Clock,
    cls: "border-teal-500/30 bg-teal-500/10 text-teal-700 dark:text-teal-300",
    hint: "Scheduled pass — the 6-hour interval in src/instrumentation.ts",
  },
};

function RunRow({ run, onDelete }: { run: HistoryRun; onDelete: (id: string) => void }) {
  const sourceStyle = RUN_SOURCE_STYLES[run.source] ?? RUN_SOURCE_STYLES.manual;
  const SourceIcon = sourceStyle.icon;
  return (
    <motion.li variants={fadeUp}>
      <div className="group flex flex-wrap items-center gap-x-3 gap-y-1.5 rounded-lg border border-zinc-200 dark:border-zinc-800 bg-zinc-100/80 dark:bg-zinc-950/60 px-3.5 py-2.5 transition-colors hover:border-zinc-300 dark:hover:border-zinc-700 hover:bg-zinc-100 dark:hover:bg-zinc-900/60">
        {run.ok ? (
          <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-600 dark:text-emerald-400" aria-label="passed" />
        ) : (
          <AlertTriangle className="h-4 w-4 shrink-0 text-rose-600 dark:text-rose-400" aria-label="failed" />
        )}

        <TooltipProvider>
          <Tooltip>
            <TooltipTrigger asChild>
              <span className="font-mono text-[12px] text-zinc-700 dark:text-zinc-300 tabular-nums">{timeAgo(run.ranAt)}</span>
            </TooltipTrigger>
            <TooltipContent>
              <span className="font-mono text-xs">{formatTimestamp(run.ranAt)} · {run.summaryLine}</span>
            </TooltipContent>
          </Tooltip>
        </TooltipProvider>

        <Badge
          variant="outline"
          className={`font-mono text-[10px] ${
            run.ok
              ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300"
              : "border-rose-500/30 bg-rose-500/10 text-rose-700 dark:text-rose-300"
          }`}
        >
          {run.ok ? "GREEN" : "RED"}
        </Badge>

        {/* v0.0.4 — provenance: who triggered this run */}
        <TooltipProvider>
          <Tooltip>
            <TooltipTrigger asChild>
              <span
                className={`inline-flex cursor-default items-center gap-1 rounded border px-1.5 py-0.5 font-mono text-[10px] ${sourceStyle.cls}`}
                aria-label={`Run source: ${run.source}`}
              >
                <SourceIcon className="h-3 w-3" aria-hidden="true" />
                {run.source}
              </span>
            </TooltipTrigger>
            <TooltipContent>
              <span className="max-w-[260px] text-xs leading-relaxed">{sourceStyle.hint}</span>
            </TooltipContent>
          </Tooltip>
        </TooltipProvider>

        <span className="font-mono text-[11px] text-zinc-500 dark:text-zinc-400 tabular-nums">
          {run.testsPassed}/{run.testsTotal} <span className="text-zinc-500 dark:text-zinc-600">tests</span>
        </span>
        <span className="font-mono text-[11px] text-zinc-500 dark:text-zinc-400 tabular-nums">
          {run.compileOk}/{run.compileTotal} <span className="text-zinc-500 dark:text-zinc-600">compiles</span>
        </span>
        <span className="font-mono text-[11px] text-zinc-500 dark:text-zinc-400 tabular-nums">{formatDuration(run.durationMs)}</span>

        <span
          className={`inline-flex items-center gap-1 rounded border px-1.5 py-0.5 font-mono text-[10px] ${
            run.codeChanged
              ? "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-300"
              : "border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900/60 text-zinc-500"
          }`}
          title={`code fingerprint: ${run.codeHash}`}
        >
          <Fingerprint className="h-3 w-3" aria-hidden="true" />
          {shortHash(run.codeHash)}
          {run.codeChanged && <span aria-label="code changed since previous run">±</span>}
        </span>

        <span className="hidden font-mono text-[10.5px] text-zinc-500 dark:text-zinc-600 lg:inline">{run.pythonVersion}</span>

        <button
          type="button"
          onClick={() => onDelete(run.id)}
          aria-label={`Delete run from ${formatTimestamp(run.ranAt)}`}
          className="ml-auto rounded-md p-1 text-zinc-400 dark:text-zinc-700 opacity-0 transition-all hover:bg-rose-500/10 hover:text-rose-600 dark:hover:text-rose-400 focus-visible:opacity-100 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-rose-500/50 group-hover:opacity-100"
        >
          <Trash2 className="h-3.5 w-3.5" />
        </button>
      </div>
    </motion.li>
  );
}

function exportHistoryCsv(runs: HistoryRun[]): { ok: boolean; count: number } {
  try {
    const header = "ranAt,source,ok,testsPassed,testsTotal,compileOk,compileTotal,durationMs,pythonVersion,codeHash";
    const lines = runs.map((r) =>
      [
        r.ranAt,
        r.source,
        r.ok ? "GREEN" : "RED",
        r.testsPassed,
        r.testsTotal,
        r.compileOk,
        r.compileTotal,
        r.durationMs,
        `"${r.pythonVersion.replace(/"/g, '""')}"`,
        r.codeHash,
      ].join(","),
    );
    const blob = new Blob([[header, ...lines].join("\n")], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "curator-verification-history.csv";
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    return { ok: true, count: runs.length };
  } catch {
    return { ok: false, count: 0 };
  }
}

function HistoryTab({
  history,
  loading,
  verifying,
  onRunVerify,
  onDeleteRun,
  onClearHistory,
}: {
  history: HistoryData | null;
  loading: boolean;
  verifying: boolean;
  onRunVerify: () => void;
  onDeleteRun: (id: string) => void;
  onClearHistory: () => void;
}) {
  const { toast } = useToast();

  if (loading) {
    return (
      <Card className="border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900/60" aria-busy="true" aria-label="Loading verification history">
        <CardContent className="space-y-4 p-6">
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            {Array.from({ length: 4 }).map((_, i) => (
              <div key={i} className="h-24 rounded-xl bg-zinc-200/70 dark:bg-zinc-800/60 animate-pulse" />
            ))}
          </div>
          <div className="h-[200px] rounded-xl bg-zinc-200/60 dark:bg-zinc-800/40 animate-pulse" />
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="h-11 rounded-lg bg-zinc-200/60 dark:bg-zinc-800/50 animate-pulse" />
          ))}
        </CardContent>
      </Card>
    );
  }

  if (!history || history.runs.length === 0) {
    return (
      <Card className="border-dashed border-zinc-200 dark:border-zinc-800 bg-zinc-50 dark:bg-zinc-900/40">
        <CardContent className="flex flex-col items-center gap-3 p-10 text-center">
          <History className="h-8 w-8 text-zinc-400 dark:text-zinc-700" aria-hidden="true" />
          <p className="text-sm font-medium text-zinc-500 dark:text-zinc-400">No verification runs recorded yet</p>
          <p className="max-w-md text-xs leading-relaxed text-zinc-500">
            Every <code className="font-mono text-[11px]">POST /api/verify</code> run is persisted to SQLite
            (Prisma) with a sha256 fingerprint of the 9 audited files — trends, pass-rate stats and
            code-drift detection live here.
          </p>
          <Button
            size="sm"
            onClick={onRunVerify}
            disabled={verifying}
            className="mt-1 bg-emerald-600 text-emerald-50 hover:bg-emerald-500"
          >
            {verifying ? (
              <><Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" /> Running…</>
            ) : (
              <><Play className="mr-1.5 h-3.5 w-3.5" /> Run the first verification</>
            )}
          </Button>
        </CardContent>
      </Card>
    );
  }

  const onExportCsv = () => {
    const r = exportHistoryCsv(history.runs);
    if (r.ok) {
      toast({ title: "History exported", description: `curator-verification-history.csv — ${r.count} runs` });
    } else {
      toast({ title: "Export failed", variant: "destructive" });
    }
  };

  return (
    <div className="space-y-4">
      <DriftBanner history={history} verifying={verifying} onRun={onRunVerify} />
      {history.stats && <HistoryStatsTiles stats={history.stats} />}
      <DurationChart runs={history.runs} />

      <Card className="border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900/60">
        <CardHeader className="pb-3">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <CardTitle className="flex items-center gap-2 text-sm text-zinc-800 dark:text-zinc-200">
                <History className="h-4 w-4 text-zinc-500" /> Run history
              </CardTitle>
              <CardDescription className="mt-1 text-[12px] text-zinc-500">
                Newest first · persisted in SQLite via Prisma · newest {history.runs.length} runs
              </CardDescription>
            </div>
            <div className="flex items-center gap-2">
              <Button
                variant="outline" size="sm"
                onClick={onExportCsv}
                className="h-7 border-zinc-300 dark:border-zinc-700 bg-transparent text-zinc-500 dark:text-zinc-400 hover:bg-zinc-200 dark:hover:bg-zinc-800 hover:text-zinc-800 dark:hover:text-zinc-200"
              >
                <Download className="mr-1.5 h-3.5 w-3.5" /> <span className="hidden sm:inline">CSV</span>
              </Button>
              <Button
                variant="outline" size="sm"
                onClick={onClearHistory}
                className="h-7 border-zinc-200 dark:border-zinc-800 bg-transparent text-zinc-500 hover:border-rose-500/40 hover:bg-rose-500/10 hover:text-rose-700 dark:hover:text-rose-300"
              >
                <Trash2 className="mr-1.5 h-3.5 w-3.5" /> <span className="hidden sm:inline">Clear</span>
              </Button>
            </div>
          </div>
        </CardHeader>
        <CardContent>
          <ScrollArea className="h-[52vh] max-h-[560px] pr-3 custom-scroll">
            <motion.ul
              variants={stagger}
              initial="hidden"
              animate="show"
              className="space-y-1.5 p-1"
              aria-label="Persisted verification runs"
            >
              {history.runs.map((run) => (
                <RunRow key={run.id} run={run} onDelete={onDeleteRun} />
              ))}
            </motion.ul>
          </ScrollArea>
        </CardContent>
      </Card>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* v30.4 — Timing Insights                                             */
/* ------------------------------------------------------------------ */

/** Hand-rolled SVG sparkline (lighter than pulling recharts into rows). */
function Sparkline({ samples, regressed }: { samples: TimingSample[]; regressed: boolean }) {
  const { resolvedTheme } = useTheme();
  const W = 72;
  const H = 22;
  if (samples.length < 2) {
    return (
      <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} aria-hidden="true" className="shrink-0">
        <line x1="2" y1={H / 2} x2={W - 2} y2={H / 2} strokeDasharray="2 3"
          className="stroke-zinc-300 dark:stroke-zinc-700" strokeWidth="1" />
      </svg>
    );
  }
  const values = samples.map((s) => s.ms);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const stepX = (W - 8) / (samples.length - 1);
  const pts = samples.map((s, i) => ({
    x: 4 + i * stepX,
    y: 4 + (1 - (s.ms - min) / span) * (H - 8),
  }));
  const path = pts.map((p, i) => `${i === 0 ? "M" : "L"}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(" ");
  const stroke = regressed
    ? "#d97706" // amber-600 — a regressed test is always visible
    : resolvedTheme === "light"
      ? "#059669" // emerald-600
      : "#34d399"; // emerald-400
  const last = pts[pts.length - 1];

  return (
    <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} aria-hidden="true" className="shrink-0">
      <path d={path} fill="none" stroke={stroke} strokeWidth="1.5" strokeLinejoin="round" strokeLinecap="round" opacity="0.9" />
      <circle cx={last.x} cy={last.y} r="2.2" fill={stroke} />
    </svg>
  );
}

/** Compact "latest vs previous" percentage chip with direction arrow. */
function DeltaChip({ deltaPct }: { deltaPct: number | null }) {
  if (deltaPct == null) {
    return <span className="font-mono text-[11px] text-zinc-400 dark:text-zinc-600">—</span>;
  }
  const up = deltaPct > 0;
  const flat = Math.abs(deltaPct) <= 5;
  const cls = flat
    ? "text-zinc-500 dark:text-zinc-400"
    : up
      ? "text-amber-600 dark:text-amber-400"
      : "text-emerald-600 dark:text-emerald-400";
  return (
    <span className={`inline-flex items-center gap-0.5 font-mono text-[11px] tabular-nums ${cls}`}>
      {flat ? (
        <span aria-hidden="true">≈</span>
      ) : up ? (
        <ArrowUpRight className="h-3 w-3" aria-hidden="true" />
      ) : (
        <ArrowDownRight className="h-3 w-3" aria-hidden="true" />
      )}
      {deltaPct > 0 ? "+" : ""}{deltaPct}%
    </span>
  );
}

function LeaderboardRow({ insight, rank }: { insight: TimingInsight; rank: number }) {
  return (
    <motion.li variants={fadeUp} className="group">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 rounded-lg border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900/60 px-3 py-2.5 transition-all duration-200 hover:border-zinc-300 dark:hover:border-zinc-700 hover:shadow-[0_6px_24px_-12px_rgba(0,0,0,0.5)]">
        <span
          className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-md font-mono text-[11px] font-semibold tabular-nums ${
            rank === 1
              ? "bg-amber-500/15 text-amber-700 dark:text-amber-300"
              : "bg-zinc-100 dark:bg-zinc-800 text-zinc-500 dark:text-zinc-400"
          }`}
          aria-label={`Rank ${rank} by median duration`}
        >
          {rank}
        </span>
        <div className="min-w-0 flex-1 basis-48">
          <p className="truncate font-mono text-[12px] font-medium text-zinc-800 dark:text-zinc-200" title={insight.id}>
            {insight.name}
          </p>
          <p className="truncate text-[10.5px] text-zinc-500" title={insight.suite}>
            {insight.suite}
          </p>
        </div>
        <Sparkline samples={insight.samples} regressed={insight.regressed} />
        <div className="flex w-24 shrink-0 items-baseline justify-end gap-1.5" title="latest run vs median">
          <span className={`font-mono text-[13px] font-semibold tabular-nums ${
            insight.regressed ? "text-amber-700 dark:text-amber-300" : "text-zinc-800 dark:text-zinc-200"
          }`}>
            {insight.latestMs.toFixed(1)}ms
          </span>
          <span className="font-mono text-[10.5px] text-zinc-400 dark:text-zinc-600">
            ÷{formatDuration(insight.medianMs)}
          </span>
        </div>
        <div className="w-14 shrink-0 text-right">
          <DeltaChip deltaPct={insight.deltaPct} />
        </div>
        <div className="flex w-24 shrink-0 flex-wrap items-end justify-end gap-1">
          {insight.regressed && (
            <Badge variant="outline" className="border-amber-500/40 bg-amber-500/10 px-1.5 text-[9.5px] font-semibold tracking-wide text-amber-700 dark:text-amber-300">
              REGRESSED
            </Badge>
          )}
          {insight.flaky && (
            <Badge variant="outline" className="border-rose-500/40 bg-rose-500/10 px-1.5 text-[9.5px] font-semibold tracking-wide text-rose-700 dark:text-rose-300">
              FLAKY
            </Badge>
          )}
          {!insight.regressed && !insight.flaky && (
            <Badge variant="outline" className="border-emerald-500/30 bg-emerald-500/10 px-1.5 text-[9.5px] font-semibold tracking-wide text-emerald-700 dark:text-emerald-300">
              STABLE
            </Badge>
          )}
        </div>
      </div>
    </motion.li>
  );
}

function ScheduledCard() {
  return (
    <Card className="border-teal-500/25 bg-gradient-to-r from-teal-950/20 via-white dark:via-zinc-900/60 to-white dark:to-zinc-900/60">
      <CardContent className="p-4">
        <div className="flex flex-wrap items-center gap-3">
          <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-teal-500/15 text-teal-600 dark:text-teal-400" aria-hidden="true">
            <Clock className="h-[18px] w-[18px]" />
          </span>
          <div className="min-w-0">
            <h3 className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">
              Scheduled verification — active
            </h3>
            <p className="mt-0.5 text-[12px] leading-snug text-zinc-500">
              A server-side scheduler (<code className="font-mono text-[11px]">src/instrumentation.ts</code>)
              runs the gate 60s after startup and then every 6 hours — drift, test failures and
              compile breaks land in the History tab automatically, without an open browser.
              Triggered runs are indistinguishable from manual ones.
            </p>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

function InsightsTab({
  history,
  loading,
  verifying,
  onRunVerify,
}: {
  history: HistoryData | null;
  loading: boolean;
  verifying: boolean;
  onRunVerify: () => void;
}) {
  const insights = history?.insights ?? null;

  if (loading) {
    return (
      <Card className="border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900/60" aria-busy="true" aria-label="Loading timing insights">
        <CardContent className="space-y-4 p-6">
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            {Array.from({ length: 4 }).map((_, i) => (
              <div key={i} className="h-24 rounded-xl bg-zinc-200/70 dark:bg-zinc-800/60 animate-pulse" />
            ))}
          </div>
          {Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="h-12 rounded-lg bg-zinc-200/60 dark:bg-zinc-800/50 animate-pulse" />
          ))}
        </CardContent>
      </Card>
    );
  }

  if (!insights || insights.caseCount === 0) {
    return (
      <Card className="border-dashed border-zinc-200 dark:border-zinc-800 bg-zinc-50 dark:bg-zinc-900/40">
        <CardContent className="flex flex-col items-center gap-3 p-10 text-center">
          <Gauge className="h-8 w-8 text-zinc-400 dark:text-zinc-700" aria-hidden="true" />
          <p className="text-sm font-medium text-zinc-500 dark:text-zinc-400">No per-case timings recorded yet</p>
          <p className="max-w-md text-xs leading-relaxed text-zinc-500">
            Runs since v30.3 record a wall-clock duration for every one of the 45 test cases. After the
            next <code className="font-mono text-[11px]">POST /api/verify</code>, the slowest-tests
            leaderboard, regression flags and sparkline trends will appear here.
          </p>
          <Button
            size="sm"
            onClick={onRunVerify}
            disabled={verifying}
            className="mt-1 bg-emerald-600 text-emerald-50 hover:bg-emerald-500"
          >
            {verifying ? (
              <><Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" /> Running…</>
            ) : (
              <><Play className="mr-1.5 h-3.5 w-3.5" /> Run verification</>
            )}
          </Button>
        </CardContent>
      </Card>
    );
  }

  const regressedCount = insights.leaderboard.filter((i) => i.regressed).length;
  const flakyCount = insights.leaderboard.filter((i) => i.flaky).length;
  const slowest = insights.leaderboard[0];

  const tiles: {
    icon: React.ComponentType<{ className?: string }>;
    label: string;
    value: number;
    format?: (v: number) => string;
    sub: string;
    accent: string;
  }[] = [
    {
      icon: Gauge, label: "Slowest test", value: slowest.medianMs,
      format: (v) => formatDuration(v),
      sub: slowest.name.replace(/^test_/, "").replace(/_/g, " "),
      accent: "text-amber-600 dark:text-amber-400 bg-amber-500/10",
    },
    {
      icon: FlaskConical, label: "Cases tracked", value: insights.caseCount,
      sub: `across the newest ${insights.windowRuns} run${insights.windowRuns === 1 ? "" : "s"}`,
      accent: "text-emerald-600 dark:text-emerald-400 bg-emerald-500/10",
    },
    {
      icon: TrendingUp, label: "Regressed", value: regressedCount,
      sub: "latest > 1.5× median and > 100ms",
      accent: regressedCount > 0
        ? "text-amber-600 dark:text-amber-400 bg-amber-500/10"
        : "text-zinc-500 dark:text-zinc-400 bg-zinc-500/10",
    },
    {
      icon: AlertTriangle, label: "Flaky", value: flakyCount,
      sub: "any FAIL/ERROR inside the window",
      accent: flakyCount > 0
        ? "text-rose-600 dark:text-rose-400 bg-rose-500/10"
        : "text-teal-600 dark:text-teal-400 bg-teal-500/10",
    },
  ];

  return (
    <div className="space-y-4">
      <ScheduledCard />

      <motion.section
        variants={stagger}
        initial="hidden"
        animate="show"
        className="grid grid-cols-2 gap-3 lg:grid-cols-4"
        aria-label="Timing insight statistics"
      >
        {tiles.map((t) => (
          <motion.div key={t.label} variants={fadeUp}>
            <Card className="group border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900/60 transition-all duration-200 hover:-translate-y-0.5 hover:border-zinc-300 dark:hover:border-zinc-700 hover:shadow-[0_8px_30px_-12px_rgba(0,0,0,0.7)]">
              <CardContent className="p-4">
                <div className="flex items-center gap-2.5">
                  <span className={`flex h-8 w-8 items-center justify-center rounded-lg ${t.accent}`} aria-hidden="true">
                    <t.icon className="h-4 w-4" />
                  </span>
                  <p className="text-[12px] font-medium text-zinc-500 dark:text-zinc-400">{t.label}</p>
                </div>
                <p className="mt-3 font-mono text-xl font-semibold tabular-nums text-zinc-900 dark:text-zinc-50">
                  <CountUp value={t.value} format={t.format} />
                </p>
                <p className="mt-1 truncate text-[10.5px] leading-snug text-zinc-500" title={t.sub}>{t.sub}</p>
              </CardContent>
            </Card>
          </motion.div>
        ))}
      </motion.section>

      <Card className="border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900/60">
        <CardHeader className="pb-3">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <CardTitle className="flex items-center gap-2 text-sm text-zinc-800 dark:text-zinc-200">
                <Gauge className="h-4 w-4 text-zinc-500" /> Slowest tests — median wall-clock
              </CardTitle>
              <CardDescription className="mt-1 text-[12px] text-zinc-500">
                Top {insights.leaderboard.length} of {insights.caseCount} cases · newest {insights.windowRuns} runs ·
                regression = latest &gt; 1.5× median AND &gt; 100ms
              </CardDescription>
            </div>
          </div>
        </CardHeader>
        <CardContent>
          <ScrollArea className="max-h-[52vh] pr-3 custom-scroll">
            <motion.ul
              variants={stagger}
              initial="hidden"
              animate="show"
              className="space-y-1.5 p-1"
              aria-label="Slowest tests leaderboard"
            >
              {insights.leaderboard.map((insight, idx) => (
                <LeaderboardRow key={insight.id} insight={insight} rank={idx + 1} />
              ))}
            </motion.ul>
          </ScrollArea>
        </CardContent>
      </Card>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Releases tab (v0.0.2 — repository & release console)                */
/* ------------------------------------------------------------------ */

function formatSectionText(text: string): string {
  // Strip simple markdown emphasis so changelog bullets read cleanly.
  return text.replace(/\*\*/g, "").replace(/`/g, "");
}

function RepoStatusCard({ repo, ci }: { repo: ReleasesData["repo"]; ci: ReleasesData["ci"] }) {
  const clean = repo.dirtyCount === 0;
  const dirty = (repo.dirtyCount ?? 0) > 0;
  const ciState = ci
    ? ci.ok
      ? { label: `CI passing${ci.runNumber ? ` · #${ci.runNumber}` : ""}`, cls: "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300", icon: CheckCircle2, dot: "bg-emerald-400" }
      : ci.status === "in_progress" || ci.status === "queued"
        ? { label: "CI running", cls: "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-300", icon: Loader2, dot: "bg-amber-400 animate-pulse" }
        : ci.status === "completed"
          ? { label: `CI ${ci.conclusion ?? "failed"}${ci.runNumber ? ` · #${ci.runNumber}` : ""}`, cls: "border-rose-500/30 bg-rose-500/10 text-rose-700 dark:text-rose-300", icon: X, dot: "bg-rose-400" }
          : { label: "CI not run yet", cls: "border-zinc-300 dark:border-zinc-700 bg-zinc-200/60 dark:bg-zinc-800/50 text-zinc-500 dark:text-zinc-400", icon: CircleDot, dot: "bg-zinc-500" }
    : { label: "CI n/a", cls: "border-zinc-300 dark:border-zinc-700 bg-zinc-200/60 dark:bg-zinc-800/50 text-zinc-500 dark:text-zinc-400", icon: CircleDot, dot: "bg-zinc-500" };
  const CiIcon = ciState.icon;
  return (
    <Card className="border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900/60 transition-all duration-200 hover:-translate-y-0.5 hover:border-zinc-300 dark:hover:border-zinc-700 hover:shadow-[0_8px_30px_-12px_rgba(0,0,0,0.7)]">
      <CardHeader className="pb-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <CardTitle className="flex flex-wrap items-center gap-2 text-sm text-zinc-800 dark:text-zinc-200">
              <Github className="h-4 w-4 text-zinc-500" /> assadigit/GitCurator
              <Badge variant="outline" className="border-amber-500/30 bg-amber-500/10 text-[10px] font-normal text-amber-700 dark:text-amber-300">
                <Lock className="mr-1 h-3 w-3" /> private
              </Badge>
              {ci ? (
                <Tooltip>
                  <TooltipTrigger asChild>
                    {ci.htmlUrl ? (
                      <a
                        href={ci.htmlUrl}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="inline-flex"
                        aria-label={`Open CI run ${ci.runNumber ?? ""} on GitHub (new tab)`}
                      >
                        <Badge variant="outline" className={`font-mono text-[10px] font-normal ${ciState.cls}`}>
                          <span className={`mr-1 h-1.5 w-1.5 rounded-full ${ciState.dot}`} aria-hidden="true" />
                          <CiIcon className="mr-1 h-3 w-3" /> {ciState.label}
                        </Badge>
                      </a>
                    ) : (
                      <Badge variant="outline" className={`font-mono text-[10px] font-normal ${ciState.cls}`}>
                        <span className={`mr-1 h-1.5 w-1.5 rounded-full ${ciState.dot}`} aria-hidden="true" />
                        <CiIcon className="mr-1 h-3 w-3" /> {ciState.label}
                      </Badge>
                    )}
                  </TooltipTrigger>
                  <TooltipContent>
                    <span className="max-w-[280px] text-xs leading-relaxed">
                      {ci
                        ? ci.htmlUrl
                          ? `GitHub Actions · ${ci.name ?? "CI"} — ${ci.status}${ci.conclusion ? ` (${ci.conclusion})` : ""}${ci.updatedAt ? ` · updated ${timeAgo(ci.updatedAt)}` : ""} · click to open`
                          : "GitHub Actions has no runs for this repository yet"
                        : "Actions status needs a read-only token (GITCURATOR_GH_TOKEN) — not configured"}
                    </span>
                  </TooltipContent>
                </Tooltip>
              ) : (
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Badge variant="outline" className={`font-mono text-[10px] font-normal ${ciState.cls}`}>
                      <span className={`mr-1 h-1.5 w-1.5 rounded-full ${ciState.dot}`} aria-hidden="true" />
                      <CiIcon className="mr-1 h-3 w-3" /> {ciState.label}
                    </Badge>
                  </TooltipTrigger>
                  <TooltipContent>
                    <span className="max-w-[280px] text-xs leading-relaxed">
                      Actions status needs a read-only token (GITCURATOR_GH_TOKEN) — not configured
                    </span>
                  </TooltipContent>
                </Tooltip>
              )}
            </CardTitle>
            <CardDescription className="mt-1 text-[12px] text-zinc-500">
              The canonical online home for every upcoming version — pushed with one-shot tokens, never stored in git config.
            </CardDescription>
          </div>
          <Button
            variant="outline"
            size="sm"
            asChild
            className="h-7 border-zinc-200 bg-zinc-50 px-2 text-zinc-600 hover:bg-zinc-100 hover:text-zinc-900 dark:border-zinc-700 dark:bg-zinc-800/50 dark:text-zinc-300 dark:hover:bg-zinc-800 dark:hover:text-zinc-100"
          >
            <a
              href={repo.url}
              target="_blank"
              rel="noopener noreferrer"
              aria-label="Open the GitCurator repository on GitHub (new tab)"
            >
              <ExternalLink className="h-3.5 w-3.5" />
              <span className="ml-1.5 hidden text-[12px] sm:inline">Open on GitHub</span>
            </a>
          </Button>
        </div>
      </CardHeader>
      <CardContent>
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4" aria-label="Repository status">
          <div className="rounded-lg border border-zinc-200 bg-zinc-50/60 p-3 dark:border-zinc-800 dark:bg-zinc-900">
            <p className="flex items-center gap-1.5 text-[11px] font-medium text-zinc-500 dark:text-zinc-400">
              <Tag className="h-3 w-3 text-emerald-500" /> VERSION
            </p>
            <p className="mt-1.5 font-mono text-base font-semibold text-zinc-900 dark:text-zinc-50">v{repo.version || "—"}</p>
            <p className="mt-0.5 text-[10.5px] text-zinc-500">repo working tree</p>
          </div>
          <div className="rounded-lg border border-zinc-200 bg-zinc-50/60 p-3 dark:border-zinc-800 dark:bg-zinc-900">
            <p className="flex items-center gap-1.5 text-[11px] font-medium text-zinc-500 dark:text-zinc-400">
              <GitBranch className="h-3 w-3 text-amber-500" /> Last tag
            </p>
            <p className="mt-1.5 font-mono text-base font-semibold text-zinc-900 dark:text-zinc-50">{repo.lastTag ?? "—"}</p>
            <p className="mt-0.5 text-[10.5px] text-zinc-500">on main</p>
          </div>
          <div className="rounded-lg border border-zinc-200 bg-zinc-50/60 p-3 dark:border-zinc-800 dark:bg-zinc-900">
            <p className="flex items-center gap-1.5 text-[11px] font-medium text-zinc-500 dark:text-zinc-400">
              <GitCommitHorizontal className="h-3 w-3 text-teal-500" /> Commits
            </p>
            <p className="mt-1.5 font-mono text-base font-semibold tabular-nums text-zinc-900 dark:text-zinc-50">{repo.commitCount ?? "—"}</p>
            <p className="mt-0.5 truncate text-[10.5px] text-zinc-500" title={repo.headCommit?.subject ?? ""}>
              {repo.headCommit ? `${repo.headCommit.short} · ${repo.headCommit.subject.slice(0, 42)}${repo.headCommit.subject.length > 42 ? "…" : ""}` : "history unavailable"}
            </p>
          </div>
          <div className="rounded-lg border border-zinc-200 bg-zinc-50/60 p-3 dark:border-zinc-800 dark:bg-zinc-900">
            <p className="flex items-center gap-1.5 text-[11px] font-medium text-zinc-500 dark:text-zinc-400">
              <CircleDot className={`h-3 w-3 ${clean ? "text-emerald-500" : dirty ? "text-amber-500" : ""}`} /> Working tree
            </p>
            <p className="mt-1.5 flex items-center gap-1.5">
              <Badge
                variant="outline"
                className={clean
                  ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300"
                  : dirty
                    ? "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-300"
                    : "border-zinc-300 dark:border-zinc-700 bg-zinc-200/60 dark:bg-zinc-800/50 text-zinc-500 dark:text-zinc-400"}
              >
                <span className={`mr-1 h-1.5 w-1.5 rounded-full ${clean ? "bg-emerald-400" : dirty ? "bg-amber-400" : "bg-zinc-500"}`} aria-hidden="true" />
                {repo.dirtyCount === null ? "unknown" : clean ? "clean" : `${repo.dirtyCount} uncommitted`}
              </Badge>
            </p>
            <p className="mt-0.5 text-[10.5px] text-zinc-500">
              {repo.headCommit ? `pushed ${timeAgo(repo.headCommit.date)}` : "—"}
            </p>
          </div>
        </div>
        {dirty && repo.dirtyFiles.length > 0 && (
          <div className="mt-3 flex flex-wrap items-center gap-1.5" aria-label="Uncommitted files">
            <span className="text-[11px] text-zinc-500 dark:text-zinc-400">uncommitted:</span>
            {repo.dirtyFiles.slice(0, 8).map((f) => (
              <span
                key={f}
                title={f}
                className="max-w-[220px] truncate rounded border border-amber-500/30 bg-amber-500/10 px-1.5 py-0.5 font-mono text-[10.5px] text-amber-700 dark:text-amber-300"
              >
                {f}
              </span>
            ))}
            {repo.dirtyFiles.length > 8 && (
              <span className="text-[10.5px] text-zinc-500">+{repo.dirtyFiles.length - 8} more</span>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function BackupsCard({ backups }: { backups: ReleasesData["backups"] }) {
  return (
    <Card className="border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900/60 transition-all duration-200 hover:-translate-y-0.5 hover:border-zinc-300 dark:hover:border-zinc-700 hover:shadow-[0_8px_30px_-12px_rgba(0,0,0,0.7)]">
      <CardHeader className="pb-3">
        <div>
          <CardTitle className="flex items-center gap-2 text-sm text-zinc-800 dark:text-zinc-200">
            <HardDrive className="h-4 w-4 text-zinc-500" /> Local .zip backups
          </CardTitle>
          <CardDescription className="mt-1 text-[12px] text-zinc-500">
            Full snapshots — source tree, .git history (commits + tags) and runtime databases — served from <code className="font-mono">public/</code>.
          </CardDescription>
        </div>
      </CardHeader>
      <CardContent>
        {backups.length === 0 ? (
          <p className="rounded-lg border border-dashed border-zinc-300 dark:border-zinc-700 p-4 text-center text-[12px] text-zinc-500">
            No backup zips found yet — the first release zip lands here with every tagged version.
          </p>
        ) : (
          <ul className="space-y-2" aria-label="Backup archives">
            {backups.map((b) => (
              <li
                key={b.name}
                className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-zinc-200 bg-zinc-50/60 p-2.5 transition-colors hover:border-zinc-300 dark:border-zinc-800 dark:bg-zinc-900 dark:hover:border-zinc-700"
              >
                <div className="flex min-w-0 items-center gap-2.5">
                  <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-teal-500/10 text-teal-600 dark:text-teal-400 ring-1 ring-teal-500/30" aria-hidden="true">
                    <FileArchive className="h-4 w-4" />
                  </span>
                  <div className="min-w-0">
                    <p className="truncate font-mono text-[12.5px] font-medium text-zinc-800 dark:text-zinc-200">{b.name}</p>
                    <p className="text-[10.5px] text-zinc-500">
                      {formatBytes(b.sizeBytes)} · created {timeAgo(b.modifiedAt)}
                      {!b.served && " · not served"}
                    </p>
                  </div>
                </div>
                {b.served ? (
                  <Button
                    variant="outline"
                    size="sm"
                    asChild
                    className="h-7 border-teal-500/30 bg-teal-500/10 px-2 text-teal-700 hover:bg-teal-500/20 hover:text-teal-800 dark:text-teal-300 dark:hover:text-teal-200"
                  >
                    <a href={b.url} download aria-label={`Download ${b.name}`}>
                      <Download className="h-3.5 w-3.5" />
                      <span className="ml-1.5 text-[12px]">Download</span>
                    </a>
                  </Button>
                ) : (
                  <Badge variant="outline" className="border-zinc-300 dark:border-zinc-700 bg-zinc-200/60 dark:bg-zinc-800/50 text-[10.5px] font-normal text-zinc-500 dark:text-zinc-400">
                    archive only
                  </Badge>
                )}
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

const RELEASE_WORKFLOW_STEPS = [
  "Develop & QA in the sandbox tree — verify gate stays green",
  "Mirror changes into the staging repo + bump VERSION / CHANGELOG.md",
  "Commit, tag vMAJOR.MINOR.PATCH, push to GitHub (one-shot token)",
  "Rebuild GitCurator-vX.YY.zip → download/ + public/ (this tab refreshes)",
] as const;

/* ------------------------------------------------------------------ */
/* v0.0.4 — CI history strip (last 10 GitHub Actions runs)             */
/* ------------------------------------------------------------------ */

function ciRunChipCls(run: ReleasesData["ciHistory"] extends (infer R)[] | null ? R : never): string {
  if (run.ok) return "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300 hover:bg-emerald-500/20";
  if (run.status === "in_progress" || run.status === "queued") return "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-300 hover:bg-amber-500/20";
  if (run.status === "completed") return "border-rose-500/30 bg-rose-500/10 text-rose-700 dark:text-rose-300 hover:bg-rose-500/20";
  return "border-zinc-200 dark:border-zinc-800 bg-zinc-100/80 dark:bg-zinc-900/60 text-zinc-500 dark:text-zinc-400";
}

function ciRunDotCls(run: ReleasesData["ciHistory"] extends (infer R)[] | null ? R : never): string {
  if (run.ok) return "bg-emerald-400";
  if (run.status === "in_progress" || run.status === "queued") return "bg-amber-400 animate-pulse";
  if (run.status === "completed") return "bg-rose-400";
  return "bg-zinc-500";
}

function CiHistoryCard({ runs }: { runs: ReleasesData["ciHistory"] }) {
  // runs is newest-first (the Actions API order).
  const completed = runs.filter((r) => r.status === "completed");
  const passing = completed.filter((r) => r.ok).length;
  const lastFailure = completed.find((r) => !r.ok) ?? null;
  const inFlight = runs.find((r) => r.status === "in_progress" || r.status === "queued") ?? null;

  return (
    <Card className="border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900/60 transition-all duration-200 hover:-translate-y-0.5 hover:border-zinc-300 dark:hover:border-zinc-700 hover:shadow-[0_8px_30px_-12px_rgba(0,0,0,0.7)]">
      <CardHeader className="pb-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <CardTitle className="flex items-center gap-2 text-sm text-zinc-800 dark:text-zinc-200">
              <Workflow className="h-4 w-4 text-zinc-500" /> CI run history
              <Badge variant="outline" className="border-zinc-300 dark:border-zinc-700 bg-zinc-200/60 dark:bg-zinc-800/50 font-mono text-[10px] font-normal text-zinc-600 dark:text-zinc-300">
                last {runs.length}
              </Badge>
            </CardTitle>
            <CardDescription className="mt-1 text-[12px] text-zinc-500">
              GitHub Actions, newest first — every push, tag and PR triggers the 45-test gate.
            </CardDescription>
          </div>
          <p className="font-mono text-[11px] text-zinc-500 dark:text-zinc-400 tabular-nums">
            {passing}/{completed.length} passing
            {inFlight && <span className="ml-1.5 text-amber-600 dark:text-amber-400">· 1 running</span>}
          </p>
        </div>
      </CardHeader>
      <CardContent>
        <div
          className="grid grid-cols-5 gap-1.5 sm:grid-cols-10"
          role="list"
          aria-label="Recent GitHub Actions runs, newest first"
        >
          {runs.map((run) => {
            const chip = (
              <span
                className={`flex h-9 items-center justify-center gap-1.5 rounded-lg border font-mono text-[11px] font-semibold tabular-nums transition-colors ${ciRunChipCls(run)}`}
              >
                <span className={`h-1.5 w-1.5 rounded-full ${ciRunDotCls(run)}`} aria-hidden="true" />
                #{run.runNumber ?? "?"}
              </span>
            );
            const label = `CI run #${run.runNumber ?? "?"} — ${
              run.status === "completed" ? run.conclusion ?? "completed" : run.status
            }${run.updatedAt ? `, updated ${timeAgo(run.updatedAt)}` : ""}`;
            return (
              <div role="listitem" key={`${run.runNumber}-${run.createdAt}`}>
                {run.htmlUrl ? (
                  <a
                    href={run.htmlUrl}
                    target="_blank"
                    rel="noopener noreferrer"
                    aria-label={`${label} (opens on GitHub)`}
                    className="inline-block w-full focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-500/50 focus-visible:ring-offset-1 dark:focus-visible:ring-offset-zinc-950 rounded-lg"
                  >
                    {chip}
                  </a>
                ) : (
                  <span title={label}>{chip}</span>
                )}
              </div>
            );
          })}
        </div>
        <p className="mt-3 border-t border-zinc-200 pt-2.5 text-[11px] leading-relaxed text-zinc-500 dark:border-zinc-800 dark:text-zinc-400">
          {lastFailure ? (
            <>
              <AlertTriangle className="mr-1 inline h-3 w-3 -translate-y-px text-rose-500" aria-hidden="true" />
              Last failure: #{lastFailure.runNumber}
              {lastFailure.conclusion ? ` (${lastFailure.conclusion})` : ""}
              {lastFailure.updatedAt ? ` · ${timeAgo(lastFailure.updatedAt)}` : ""} — every other recent run is green.
            </>
          ) : (
            <>
              <CheckCircle2 className="mr-1 inline h-3 w-3 -translate-y-px text-emerald-500" aria-hidden="true" />
              No failures in the last {runs.length} run{runs.length === 1 ? "" : "s"} — the gate has held since run #1.
            </>
          )}
        </p>
      </CardContent>
    </Card>
  );
}

function WorkflowCard() {
  return (
    <Card className="border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900/60 transition-all duration-200 hover:-translate-y-0.5 hover:border-zinc-300 dark:hover:border-zinc-700 hover:shadow-[0_8px_30px_-12px_rgba(0,0,0,0.7)]">
      <CardHeader className="pb-3">
        <div>
          <CardTitle className="flex items-center gap-2 text-sm text-zinc-800 dark:text-zinc-200">
            <Rocket className="h-4 w-4 text-zinc-500" /> How the next version ships
          </CardTitle>
          <CardDescription className="mt-1 text-[12px] text-zinc-500">
            Every release round follows the same five-step pipeline — the dashboard mirrors each one.
          </CardDescription>
        </div>
      </CardHeader>
      <CardContent>
        <ol className="space-y-2.5">
          {RELEASE_WORKFLOW_STEPS.map((step, i) => (
            <li key={i} className="flex items-start gap-3">
              <span
                className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full border border-emerald-500/30 bg-emerald-500/10 font-mono text-[11px] font-semibold text-emerald-700 dark:text-emerald-300"
                aria-hidden="true"
              >
                {i + 1}
              </span>
              <p className="pt-0.5 text-[12.5px] leading-relaxed text-zinc-600 dark:text-zinc-300">{step}</p>
            </li>
          ))}
        </ol>
        <p className="mt-4 border-t border-zinc-200 pt-3 text-[11px] leading-relaxed text-zinc-500 dark:border-zinc-800 dark:text-zinc-400">
          <AlertTriangle className="mr-1 inline h-3 w-3 -translate-y-px text-amber-500" aria-hidden="true" />
          The repo stays <strong>private</strong> until every committed credential is rotated (3 × P0 on the Go-Live tab).
        </p>
      </CardContent>
    </Card>
  );
}

function ReleaseCard({ release, latest }: { release: ReleasesData["releases"][number]; latest: boolean }) {
  return (
    <Card
      className={latest
        ? "border-emerald-500/40 dark:border-emerald-500/30 bg-white dark:bg-zinc-900/60 bg-gradient-to-b from-emerald-500/[0.05] to-transparent dark:from-emerald-500/[0.07] ring-1 ring-emerald-500/20 transition-all duration-200 hover:-translate-y-0.5 hover:border-emerald-500/60 dark:hover:border-emerald-500/40 hover:shadow-[0_8px_30px_-12px_rgba(16,185,129,0.35)]"
        : "border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900/60 transition-all duration-200 hover:-translate-y-0.5 hover:border-zinc-300 dark:hover:border-zinc-700 hover:shadow-[0_8px_30px_-12px_rgba(0,0,0,0.7)]"}
    >
      <CardHeader className="pb-3">
        <div className="flex flex-wrap items-center gap-2">
          <Badge
            variant="outline"
            className={latest
              ? "border-emerald-500/40 bg-emerald-500/10 font-mono text-emerald-700 dark:text-emerald-300"
              : "border-zinc-300 dark:border-zinc-700 bg-zinc-200/60 dark:bg-zinc-800/50 font-mono text-zinc-600 dark:text-zinc-300"}
          >
            <Tag className="mr-1 h-3 w-3" /> v{release.version}
          </Badge>
          <CardTitle className="text-sm text-zinc-800 dark:text-zinc-200">{formatSectionText(release.title)}</CardTitle>
          {latest && (
            <Badge variant="outline" className="border-emerald-500/30 bg-emerald-500/10 text-[10px] font-normal text-emerald-700 dark:text-emerald-300">
              <span className="mr-1 h-1.5 w-1.5 animate-pulse rounded-full bg-emerald-400" aria-hidden="true" />
              current
            </Badge>
          )}
        </div>
        {release.intro && (
          <CardDescription className="mt-1.5 max-w-3xl text-[12px] leading-relaxed text-zinc-500">
            {formatSectionText(release.intro)}
          </CardDescription>
        )}
      </CardHeader>
      {release.sections.length > 0 && (
        <CardContent>
          <div className="grid gap-4 lg:grid-cols-2">
            {release.sections.map((section) => (
              <div key={section.heading} className="rounded-lg border border-zinc-200 bg-zinc-50/60 p-3 dark:border-zinc-800 dark:bg-zinc-900">
                <p className="flex items-center gap-1.5 text-[12px] font-semibold text-zinc-700 dark:text-zinc-200">
                  <Package className="h-3.5 w-3.5 text-zinc-500" aria-hidden="true" />
                  {formatSectionText(section.heading)}
                </p>
                <ul className="mt-2 space-y-1.5">
                  {section.bullets.map((bullet, j) => (
                    <li key={j} className="flex items-start gap-2 text-[11.5px] leading-relaxed text-zinc-600 dark:text-zinc-300">
                      <CheckCircle2 className="mt-0.5 h-3 w-3 shrink-0 text-emerald-500/70" aria-hidden="true" />
                      <span>{formatSectionText(bullet)}</span>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        </CardContent>
      )}
    </Card>
  );
}

function ReleasesTab({ data, loading }: { data: ReleasesData | null; loading: boolean }) {
  if (loading && !data) {
    return (
      <div className="grid gap-4 lg:grid-cols-2" aria-busy="true" aria-label="Loading releases">
        {[0, 1].map((i) => (
          <div key={i} className="h-40 animate-pulse rounded-xl border border-zinc-200 dark:border-zinc-800 bg-zinc-100 dark:bg-zinc-900/60" />
        ))}
      </div>
    );
  }
  if (!data) {
    return (
      <Card className="border-dashed border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-900/60">
        <CardContent className="p-6 text-center">
          <Archive className="mx-auto h-8 w-8 text-zinc-400" aria-hidden="true" />
          <p className="mt-2 text-sm font-medium text-zinc-700 dark:text-zinc-200">Repository status unavailable</p>
          <p className="mt-1 text-[12px] text-zinc-500">
            <code className="font-mono">/api/releases</code> could not be reached — retry from the header.
          </p>
        </CardContent>
      </Card>
    );
  }
  return (
    <div className="space-y-5">
      <RepoStatusCard repo={data.repo} ci={data.ci} />
      {/* v0.0.4 — CI history strip (rendered when the token works and any
          runs exist; the ci chip above already covers the no-token case) */}
      {data.ciHistory && data.ciHistory.length > 0 && <CiHistoryCard runs={data.ciHistory} />}
      <div className="grid gap-4 lg:grid-cols-2">
        <BackupsCard backups={data.backups} />
        <WorkflowCard />
      </div>
      {data.releases.length > 0 && (
        <section aria-label="Version history">
          <h3 className="mb-3 flex items-center gap-2 text-[13px] font-semibold text-zinc-700 dark:text-zinc-200">
            <Layers className="h-4 w-4 text-zinc-500" aria-hidden="true" /> Version history — CHANGELOG.md
          </h3>
          {/* v0.0.4 — timeline rail: a connector line + per-release dots */}
          <ol className="max-h-[62vh] space-y-4 overflow-y-auto pr-1 custom-scroll">
            {data.releases.map((release, i) => (
              <li key={release.version} className="relative pl-5">
                <span
                  className={`absolute left-0 top-[7px] h-2.5 w-2.5 rounded-full border-2 ${
                    i === 0
                      ? "border-emerald-500 bg-emerald-400 shadow-[0_0_0_3px_rgba(16,185,129,0.15)]"
                      : "border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-900"
                  }`}
                  aria-hidden="true"
                />
                {i < data.releases.length - 1 && (
                  <span
                    className="absolute left-[4.5px] top-[22px] bottom-[-16px] w-px bg-zinc-200 dark:bg-zinc-800"
                    aria-hidden="true"
                  />
                )}
                <ReleaseCard release={release} latest={i === 0} />
              </li>
            ))}
          </ol>
        </section>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Page                                                                */
/* ------------------------------------------------------------------ */

export default function Home() {
  const { toast } = useToast();
  const [report, setReport] = useState<ReleaseReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  // Live verification state
  const [verify, setVerify] = useState<VerifyState>({ result: null, running: false, error: null });

  // Verification history state (v30.2 — persisted server-side via Prisma)
  const [history, setHistory] = useState<HistoryData | null>(null);
  const [historyLoading, setHistoryLoading] = useState(true);

  // Go-live checklist state (persisted to localStorage)
  const [checkedItems, setCheckedItems] = useState<Record<string, boolean>>({});

  // Releases & repository state (v0.0.2 — best-effort, like history)
  const [releases, setReleases] = useState<ReleasesData | null>(null);
  const [releasesLoading, setReleasesLoading] = useState(true);

  /* Fetch the report */
  useEffect(() => {
    let cancelled = false;
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 8000);
    fetch("/api/report", { signal: ctrl.signal })
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json() as Promise<ReleaseReport>;
      })
      .then((data) => { if (!cancelled) setReport(data); })
      .catch((e) => { if (!cancelled) setError(e.message ?? "fetch failed"); })
      .finally(() => clearTimeout(timer));
    return () => { cancelled = true; ctrl.abort(); clearTimeout(timer); };
  }, [reloadKey]);

  /* Restore the last verification result (server-cached) — async, no re-run */
  useEffect(() => {
    let cancelled = false;
    fetch("/api/verify")
      .then((r) => r.json() as Promise<{ last: VerifyResult | null }>)
      .then((data) => {
        if (!cancelled && data.last) {
          setVerify((s) => ({ ...s, result: data.last }));
        }
      })
      .catch(() => { /* last-result restore is best-effort */ });
    return () => { cancelled = true; };
  }, []);

  /* Load the persisted verification history (v30.2) */
  const refreshHistory = useCallback(() => {
    return fetch("/api/history")
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json() as Promise<HistoryData>;
      })
      .then((data) => setHistory(data))
      .catch(() => { /* history restore is best-effort */ })
      .finally(() => setHistoryLoading(false));
  }, []);

  useEffect(() => {
    void refreshHistory();
  }, [refreshHistory]);

  /* Load repository & release status (v0.0.2) — best-effort */
  useEffect(() => {
    let cancelled = false;
    fetch("/api/releases")
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json() as Promise<ReleasesData>;
      })
      .then((data) => { if (!cancelled) setReleases(data); })
      .catch(() => { /* releases panel is best-effort */ })
      .finally(() => { if (!cancelled) setReleasesLoading(false); });
    return () => { cancelled = true; };
  }, [reloadKey]);

  /* Load persisted checklist (rAF-deferred — after hydration, no mismatch) */
  useEffect(() => {
    const raf = requestAnimationFrame(() => {
      try {
        const raw = window.localStorage.getItem(CHECKLIST_STORAGE_KEY);
        if (raw) setCheckedItems(JSON.parse(raw) as Record<string, boolean>);
      } catch { /* corrupted storage — start fresh */ }
    });
    return () => cancelAnimationFrame(raf);
  }, []);

  const runVerify = async () => {
    setVerify((s) => ({ ...s, running: true, error: null }));
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 60_000);
    try {
      const r = await fetch("/api/verify", { method: "POST", signal: ctrl.signal });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = (await r.json()) as VerifyResult;
      setVerify({ result: data, running: false, error: null });
      if (data.ok) {
        toast({
          title: "Verification passed",
          description: `${data.testsPassed}/${data.testsTotal} tests · ${data.compile.filter((c) => c.ok).length}/${data.compile.length} compiles · ${formatDuration(data.durationMs)} — run saved to history`,
        });
      } else {
        toast({
          title: "Verification failed",
          description: data.summaryLine,
          variant: "destructive",
        });
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : "verification request failed";
      setVerify({ result: null, running: false, error: msg });
      toast({ title: "Verification error", description: msg, variant: "destructive" });
    } finally {
      clearTimeout(timer);
      // History changed (new run persisted) — refresh the trends/stats.
      void refreshHistory();
    }
  };

  const deleteRun = async (id: string) => {
    try {
      const r = await fetch(`/api/history?id=${encodeURIComponent(id)}`, { method: "DELETE" });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = (await r.json()) as { deleted: number };
      toast({
        title: data.deleted > 0 ? "Run deleted" : "Run not found",
        description: data.deleted > 0 ? "History and stats refreshed." : "It may already be gone.",
      });
      await refreshHistory();
    } catch (e) {
      toast({
        title: "Delete failed",
        description: e instanceof Error ? e.message : "unknown error",
        variant: "destructive",
      });
    }
  };

  const clearHistory = async () => {
    try {
      const r = await fetch("/api/history", { method: "DELETE" });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = (await r.json()) as { deleted: number };
      toast({
        title: "History cleared",
        description: `${data.deleted} run${data.deleted === 1 ? "" : "s"} removed from SQLite.`,
      });
      await refreshHistory();
    } catch (e) {
      toast({
        title: "Clear failed",
        description: e instanceof Error ? e.message : "unknown error",
        variant: "destructive",
      });
    }
  };

  const toggleChecklistItem = (id: string) => {
    setCheckedItems((prev) => {
      const next = { ...prev, [id]: !prev[id] };
      try {
        window.localStorage.setItem(CHECKLIST_STORAGE_KEY, JSON.stringify(next));
      } catch { /* storage full/blocked — state still works in-memory */ }
      return next;
    });
  };

  const resetChecklist = () => {
    setCheckedItems({});
    try {
      window.localStorage.removeItem(CHECKLIST_STORAGE_KEY);
    } catch { /* ignore */ }
    toast({ title: "Checklist reset", description: "All go-live steps unchecked." });
  };

  const exportReport = () => {
    if (!report) return;
    const payload = {
      exportedAt: new Date().toISOString(),
      report,
      liveVerification: verify.result,
      verificationHistory: history,
      releases,
    };
    try {
      const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `github-curator-v${report.version}-report.json`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      toast({
        title: "Report exported",
        description: `github-curator-v${report.version}-report.json${verify.result ? " (includes live verification)" : ""}`,
      });
    } catch {
      toast({ title: "Export failed", variant: "destructive" });
    }
  };

  const generatedAt = useMemo(
    () => new Date().toLocaleString("en-GB", { dateStyle: "medium", timeStyle: "short" }),
    []
  );

  const checklistDone = report
    ? report.deployChecklist.filter((i) => checkedItems[i.id]).length
    : 0;

  // v30.4 — regressed-test count for the Insights tab badge
  const insightsRegressed = useMemo(
    () => history?.insights?.leaderboard.filter((i) => i.regressed).length ?? 0,
    [history],
  );

  // v0.0.2 — newest served backup for the header download button
  const newestBackup = useMemo(
    () => releases?.backups.find((b) => b.served) ?? null,
    [releases],
  );

  const verifyChip = verify.running
    ? { label: "verifying…", cls: "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-300", dot: "bg-amber-400 animate-pulse" }
    : verify.result?.ok
      ? { label: "verified", cls: "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300", dot: "bg-emerald-400" }
      : verify.result
        ? { label: "failing", cls: "border-rose-500/30 bg-rose-500/10 text-rose-700 dark:text-rose-300", dot: "bg-rose-400" }
        : { label: "unverified", cls: "border-zinc-300 dark:border-zinc-700 bg-zinc-200/60 dark:bg-zinc-800/50 text-zinc-500 dark:text-zinc-400", dot: "bg-zinc-500" };

  return (
    <div className="min-h-screen flex flex-col bg-zinc-50 text-zinc-700 selection:bg-emerald-500/30 selection:text-emerald-950 dark:bg-zinc-950 dark:text-zinc-200 dark:selection:text-emerald-50">
      {/* Ambient decoration: grid + radial glows (theme-aware, v30.3) */}
      <div className="pointer-events-none fixed inset-x-0 top-0 h-72 dash-glow" aria-hidden="true" />
      <div className="pointer-events-none fixed inset-0 dash-grid" aria-hidden="true" />

      {/* Header */}
      <header className="sticky top-0 z-40 border-b border-zinc-200 bg-white/80 backdrop-blur supports-[backdrop-filter]:bg-white/60 dark:border-zinc-800/80 dark:bg-zinc-950/80 dark:supports-[backdrop-filter]:bg-zinc-950/60">
        <div className="mx-auto flex w-full max-w-7xl items-center justify-between gap-3 px-4 py-3 sm:px-6 lg:px-8">
          <div className="flex min-w-0 items-center gap-3">
            <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 ring-1 ring-emerald-500/30" aria-hidden="true">
              <Vault className="h-[18px] w-[18px]" />
            </span>
            <div className="min-w-0">
              <h1 className="truncate text-sm font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">
                GitCurator
                <span className="ml-2 font-mono text-xs font-normal text-zinc-500">v{report?.version ?? "0.0.4"}</span>
              </h1>
              <p className="hidden truncate text-[11px] text-zinc-500 sm:block">
                {report?.project ?? "GitCurator — Telegram → Ollama → Obsidian"}
              </p>
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-1.5 sm:gap-2">
            {/* Live verification status chip */}
            <TooltipProvider>
              <Tooltip>
                <TooltipTrigger asChild>
                  <Badge
                    variant="outline"
                    className={`font-mono ${verifyChip.cls}`}
                    aria-live="polite"
                  >
                    <span className={`mr-1.5 h-2 w-2 rounded-full ${verifyChip.dot}`} aria-hidden="true" />
                    <span className="hidden sm:inline">{verifyChip.label}</span>
                  </Badge>
                </TooltipTrigger>
                <TooltipContent>
                  <span className="text-xs font-mono">
                    {verify.result
                      ? `last run ${formatTimestamp(verify.result.ranAt)} — ${verify.result.summaryLine}`
                      : "POST /api/verify — run it from the Tests tab"}
                  </span>
                </TooltipContent>
              </Tooltip>
            </TooltipProvider>
            {/* Theme toggle (v30.3) */}
            <ThemeToggle />
            {/* Backup zip download (v0.0.2) */}
            <TooltipProvider>
              <Tooltip>
                <TooltipTrigger asChild>
                  {newestBackup ? (
                    <Button
                      variant="outline"
                      size="sm"
                      asChild
                      className="h-7 border-teal-500/30 bg-teal-500/10 px-2 text-teal-700 hover:bg-teal-500/20 hover:text-teal-800 dark:text-teal-300 dark:hover:text-teal-200"
                    >
                      <a
                        href={newestBackup.url}
                        download
                        aria-label={`Download the newest backup zip (${newestBackup.name})`}
                        onClick={() =>
                          toast({
                            title: "Backup download started",
                            description: `${newestBackup.name} · ${formatBytes(newestBackup.sizeBytes)}`,
                          })
                        }
                      >
                        <FileArchive className="h-3.5 w-3.5" />
                        <span className="ml-1.5 hidden text-[12px] md:inline">Backup</span>
                      </a>
                    </Button>
                  ) : (
                    <Button
                      variant="outline"
                      size="sm"
                      disabled
                      aria-label="No backup zip available yet"
                      className="h-7 border-zinc-200 bg-zinc-50 px-2 text-zinc-400 dark:border-zinc-700 dark:bg-zinc-800/50 dark:text-zinc-500"
                    >
                      <FileArchive className="h-3.5 w-3.5" />
                      <span className="ml-1.5 hidden text-[12px] md:inline">Backup</span>
                    </Button>
                  )}
                </TooltipTrigger>
                <TooltipContent>
                  <span className="text-xs">
                    {newestBackup
                      ? `${newestBackup.name} — full snapshot: source + .git history + runtime DBs`
                      : "No backup zip served yet — see the Releases tab"}
                  </span>
                </TooltipContent>
              </Tooltip>
            </TooltipProvider>
            {/* Export */}
            <TooltipProvider>
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    variant="outline" size="sm"
                    onClick={exportReport}
                    disabled={!report}
                    aria-label="Export report as JSON"
                    className="h-7 border-zinc-200 bg-zinc-50 px-2 text-zinc-600 hover:bg-zinc-100 hover:text-zinc-900 dark:border-zinc-700 dark:bg-zinc-800/50 dark:text-zinc-300 dark:hover:bg-zinc-800 dark:hover:text-zinc-100"
                  >
                    <Download className="h-3.5 w-3.5" />
                    <span className="ml-1.5 hidden md:inline text-[12px]">Export</span>
                  </Button>
                </TooltipTrigger>
                <TooltipContent>
                  <span className="text-xs">Download the report (+ live verification) as JSON</span>
                </TooltipContent>
              </Tooltip>
            </TooltipProvider>
            {/* Unit test badge */}
            <TooltipProvider>
              <Tooltip>
                <TooltipTrigger asChild>
                  <Badge variant="outline" className="hidden border-zinc-300 dark:border-zinc-700 bg-zinc-200/60 dark:bg-zinc-800/50 font-mono text-zinc-700 dark:text-zinc-300 lg:inline-flex">
                    <Cpu className="mr-1 h-3 w-3" />
                    {report ? `${report.metrics.testsPassed}/${report.metrics.testsTotal}` : "45/45"}
                  </Badge>
                </TooltipTrigger>
                <TooltipContent>
                  <span className="text-xs">Unit + e2e tests passing — tests.test_core, tests.test_e2e</span>
                </TooltipContent>
              </Tooltip>
            </TooltipProvider>
          </div>
        </div>
      </header>

      {/* Main */}
      <main className="mx-auto w-full max-w-7xl flex-1 px-4 py-8 sm:px-6 sm:py-10 lg:px-8">
        {/* Hero */}
        <motion.section
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.5, ease: "easeOut" }}
          className="mb-9"
        >
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant="outline" className="border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300 font-normal">
              Release {report?.version ?? "0.0.4"}
            </Badge>
            <Badge variant="outline" className="border-zinc-300 dark:border-zinc-700 bg-zinc-200/60 dark:bg-zinc-800/40 text-zinc-700 dark:text-zinc-300 font-normal">
              {report?.codename ?? "Provenance & CI History"}
            </Badge>
            <Badge variant="outline" className="border-zinc-300 dark:border-zinc-700 bg-zinc-200/60 dark:bg-zinc-800/40 text-zinc-700 dark:text-zinc-300 font-normal">
              repo lineage <span className="ml-1 font-mono">v30.x</span>
            </Badge>
            <Badge variant="outline" className="border-zinc-300 dark:border-zinc-700 bg-zinc-200/60 dark:bg-zinc-800/40 font-mono text-zinc-500 dark:text-zinc-400 font-normal">
              {report?.releasedAt ?? "2026-09-16"}
            </Badge>
          </div>
          <h2 className="mt-4 max-w-3xl text-balance text-2xl font-bold tracking-tight text-zinc-900 dark:text-zinc-50 sm:text-3xl">
            From an 8.8k-line monolith audit to a{" "}
            <span className="text-emerald-600 dark:text-emerald-400">hardened, tested pipeline</span>.
          </h2>
          <p className="mt-3 max-w-2xl text-pretty text-sm leading-relaxed text-zinc-500 dark:text-zinc-400">
            Full SWOT audit, 13 shipped fixes (including the model-dialog bug you reported), a
            testable 4-module core, and a 45-case regression suite — 34 unit + 11 end-to-end —
            with a live verification gate, persisted run history, code-drift detection and
            per-case timing insights. Now versioned and published: every release ships to the
            private GitCurator repository with a tagged commit and a downloadable .zip backup.
          </p>
          {report && (
            <div className="mt-4 flex flex-wrap gap-1.5">
              {report.stack.map((s) => (
                <span key={s} className="rounded border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900/60 px-2 py-0.5 font-mono text-[10.5px] text-zinc-500">
                  {s}
                </span>
              ))}
            </div>
          )}
        </motion.section>

        {/* Body */}
        {!report && !error && <DashboardSkeleton />}
        {error && <ErrorCard onRetry={() => handleRetry(setReport, setError, setReloadKey)} />}
        {report && (
          <TooltipProvider delayDuration={150}>
            <div className="space-y-10">
              <MetricsRow report={report} />
              <PipelineFlow stages={report.pipeline} />

              <section aria-label="Detailed report">
                <Tabs defaultValue="fixes" className="w-full">
                  <TabsList className="mb-5 h-auto w-full grid grid-cols-4 gap-1 rounded-xl border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900/60 p-1 custom-scroll sm:flex sm:w-fit sm:justify-start sm:overflow-x-auto">
                    <TabsTrigger
                      value="fixes"
                      className="px-1 text-[11.5px] sm:px-2 sm:text-sm data-[state=active]:bg-emerald-500/15 data-[state=active]:text-emerald-700 dark:data-[state=active]:text-emerald-300 data-[state=active]:shadow-none"
                    >
                      <Wrench className="mr-1 h-3.5 w-3.5 sm:mr-1.5" /> Fixes
                      <Badge className="ml-1 h-4 px-1.5 font-mono text-[10px] bg-zinc-100 dark:bg-zinc-800 text-zinc-500 dark:text-zinc-400 border-0">{report.fixes.length}</Badge>
                    </TabsTrigger>
                    <TabsTrigger
                      value="audit"
                      className="px-1 text-[11.5px] sm:px-2 sm:text-sm data-[state=active]:bg-emerald-500/15 data-[state=active]:text-emerald-700 dark:data-[state=active]:text-emerald-300 data-[state=active]:shadow-none"
                    >
                      <ShieldCheck className="mr-1 h-3.5 w-3.5 sm:mr-1.5" /> Audit
                    </TabsTrigger>
                    <TabsTrigger
                      value="tests"
                      className="px-1 text-[11.5px] sm:px-2 sm:text-sm data-[state=active]:bg-emerald-500/15 data-[state=active]:text-emerald-700 dark:data-[state=active]:text-emerald-300 data-[state=active]:shadow-none"
                    >
                      <FlaskConical className="mr-1 h-3.5 w-3.5 sm:mr-1.5" /> Tests
                      <Badge className="ml-1 h-4 px-1.5 font-mono text-[10px] bg-emerald-500/15 text-emerald-700 dark:text-emerald-300 border-0">
                        {report.metrics.testsPassed}/{report.metrics.testsTotal}
                      </Badge>
                    </TabsTrigger>
                    <TabsTrigger
                      value="history"
                      className="px-1 text-[11.5px] sm:px-2 sm:text-sm data-[state=active]:bg-emerald-500/15 data-[state=active]:text-emerald-700 dark:data-[state=active]:text-emerald-300 data-[state=active]:shadow-none"
                    >
                      <History className="mr-1 h-3.5 w-3.5 sm:mr-1.5" /> History
                      <Badge className="ml-1 h-4 min-w-[1.4rem] px-1.5 font-mono text-[10px] tabular-nums bg-zinc-100 dark:bg-zinc-800 text-zinc-500 dark:text-zinc-400 border-0">
                        {history?.stats?.totalRuns ?? 0}
                      </Badge>
                    </TabsTrigger>
                    <TabsTrigger
                      value="insights"
                      className="px-1 text-[11.5px] sm:px-2 sm:text-sm data-[state=active]:bg-emerald-500/15 data-[state=active]:text-emerald-700 dark:data-[state=active]:text-emerald-300 data-[state=active]:shadow-none"
                    >
                      <Gauge className="mr-1 h-3.5 w-3.5 sm:mr-1.5" /> Insights
                      {insightsRegressed > 0 ? (
                        <Badge className="ml-1 h-4 min-w-[1.4rem] px-1.5 font-mono text-[10px] tabular-nums bg-amber-500/15 text-amber-700 dark:text-amber-300 border-0">
                          {insightsRegressed} slowed
                        </Badge>
                      ) : null}
                    </TabsTrigger>
                    <TabsTrigger
                      value="golive"
                      className="px-1 text-[11.5px] sm:px-2 sm:text-sm data-[state=active]:bg-emerald-500/15 data-[state=active]:text-emerald-700 dark:data-[state=active]:text-emerald-300 data-[state=active]:shadow-none"
                    >
                      <ListChecks className="mr-1 h-3.5 w-3.5 sm:mr-1.5" /> Go-Live
                      <Badge className="ml-1 h-4 min-w-[1.4rem] px-1.5 font-mono text-[10px] tabular-nums bg-zinc-100 dark:bg-zinc-800 text-zinc-500 dark:text-zinc-400 border-0">
                        {checklistDone}/{report.deployChecklist.length}
                      </Badge>
                    </TabsTrigger>
                    <TabsTrigger
                      value="releases"
                      className="px-1 text-[11.5px] sm:px-2 sm:text-sm data-[state=active]:bg-emerald-500/15 data-[state=active]:text-emerald-700 dark:data-[state=active]:text-emerald-300 data-[state=active]:shadow-none"
                    >
                      <Rocket className="mr-1 h-3.5 w-3.5 sm:mr-1.5" /> Releases
                      {releases && (
                        <Badge className="ml-1 hidden h-4 px-1.5 font-mono text-[10px] bg-zinc-100 dark:bg-zinc-800 text-zinc-500 dark:text-zinc-400 border-0 sm:inline-flex">
                          v{releases.repo.version || releases.releases[0]?.version || "—"}
                        </Badge>
                      )}
                      {(releases?.repo.dirtyCount ?? 0) > 0 && (
                        <span className="ml-1 h-1.5 w-1.5 rounded-full bg-amber-400" aria-label="uncommitted changes" />
                      )}
                    </TabsTrigger>
                    <TabsTrigger
                      value="risks"
                      className="px-1 text-[11.5px] sm:px-2 sm:text-sm data-[state=active]:bg-emerald-500/15 data-[state=active]:text-emerald-700 dark:data-[state=active]:text-emerald-300 data-[state=active]:shadow-none"
                    >
                      <AlertTriangle className="mr-1 h-3.5 w-3.5 sm:mr-1.5" /> Risks<span className="hidden sm:inline">&nbsp;&amp; Roadmap</span>
                      <Badge className="ml-1 h-4 px-1.5 font-mono text-[10px] bg-rose-500/15 text-rose-700 dark:text-rose-300 border-0">1 P0</Badge>
                    </TabsTrigger>
                  </TabsList>

                  <TabsContent value="fixes" className="mt-0 focus-visible:outline-none">
                    <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.3, ease: "easeOut" }}>
                      <FixesTab fixes={report.fixes} severityBreakdown={report.severityBreakdown} />
                    </motion.div>
                  </TabsContent>
                  <TabsContent value="audit" className="mt-0 focus-visible:outline-none">
                    <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.3, ease: "easeOut" }}>
                      <SwotGrid swot={report.swot} />
                    </motion.div>
                  </TabsContent>
                  <TabsContent value="tests" className="mt-0 focus-visible:outline-none">
                    <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.3, ease: "easeOut" }}>
                      <TestsTab report={report} verify={verify} onRunVerify={runVerify} />
                    </motion.div>
                  </TabsContent>
                  <TabsContent value="history" className="mt-0 focus-visible:outline-none">
                    <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.3, ease: "easeOut" }}>
                      <HistoryTab
                        history={history}
                        loading={historyLoading}
                        verifying={verify.running}
                        onRunVerify={runVerify}
                        onDeleteRun={deleteRun}
                        onClearHistory={clearHistory}
                      />
                    </motion.div>
                  </TabsContent>
                  <TabsContent value="insights" className="mt-0 focus-visible:outline-none">
                    <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.3, ease: "easeOut" }}>
                      <InsightsTab
                        history={history}
                        loading={historyLoading}
                        verifying={verify.running}
                        onRunVerify={runVerify}
                      />
                    </motion.div>
                  </TabsContent>
                  <TabsContent value="golive" className="mt-0 focus-visible:outline-none">
                    <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.3, ease: "easeOut" }}>
                      <ChecklistTab
                        items={report.deployChecklist}
                        checked={checkedItems}
                        onToggle={toggleChecklistItem}
                        onReset={resetChecklist}
                      />
                    </motion.div>
                  </TabsContent>
                  <TabsContent value="releases" className="mt-0 focus-visible:outline-none">
                    <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.3, ease: "easeOut" }}>
                      <ReleasesTab data={releases} loading={releasesLoading} />
                    </motion.div>
                  </TabsContent>
                  <TabsContent value="risks" className="mt-0 focus-visible:outline-none">
                    <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.3, ease: "easeOut" }}>
                      <RisksTab risks={report.risks} />
                    </motion.div>
                  </TabsContent>
                </Tabs>
              </section>
            </div>
          </TooltipProvider>
        )}
      </main>

      {/* Footer (sticky to bottom) */}
      <footer className="mt-auto border-t border-zinc-200/80 dark:border-zinc-800/80 bg-white dark:bg-zinc-950">
        <div className="mx-auto flex w-full max-w-7xl flex-wrap items-center justify-between gap-2 px-4 py-4 text-[11.5px] text-zinc-500 sm:px-6 lg:px-8">
          <p className="flex flex-wrap items-center gap-1.5">
            Dashboard rendered {generatedAt} · data
            <code className="font-mono text-zinc-500">/api/report</code> · live gate
            <code className="font-mono text-zinc-500">/api/verify</code> · history + timing insights
            <code className="font-mono text-zinc-500">/api/history</code> · repo + releases
            <code className="font-mono text-zinc-500">/api/releases</code>
          </p>
          <p className="flex items-center gap-1.5">
            <ArrowRight className="h-3 w-3" aria-hidden="true" />
            Handover &amp; full history: <code className="font-mono text-zinc-500">worklog.md</code>
          </p>
        </div>
      </footer>
    </div>
  );
}
