import type {
  Analysis,
  AnalysisResult,
  CodeContext,
  ErrorInfo,
  Fix,
  Frame,
  StepEvent,
  SuspectCommit,
} from "./types";

export type StepName = Exclude<StepEvent["step"], "done">;
export type StepStatus = StepEvent["status"] | "pending";

export const STEPS: { name: StepName; title: string }[] = [
  { name: "parse", title: "Parse trace" },
  { name: "resolve_paths", title: "Resolve paths" },
  { name: "fetch_context", title: "Fetch context" },
  { name: "blame", title: "Blame" },
  { name: "analyze", title: "LLM analysis" },
  { name: "build_fix", title: "Fix & confidence" },
];

export interface StepState {
  status: StepStatus;
  label: string;
  startedAt?: number;
  finishedAt?: number;
}

export type StepsState = Record<StepName, StepState>;

export function initialSteps(): StepsState {
  return Object.fromEntries(STEPS.map((s) => [s.name, { status: "pending", label: "" }])) as StepsState;
}

/** Result that fills in gradually while the stream runs. */
export interface PartialResult {
  error?: ErrorInfo;
  causes?: ErrorInfo[];
  frames?: Frame[];
  contexts?: CodeContext[];
  suspect_commit?: SuspectCommit | null;
  summary?: string;
  root_cause?: string;
  analysis?: Analysis | null;
  fix?: Fix | null;
  warnings?: string[];
  ref?: string;
  sha?: string;
}

export function applyEvent(steps: StepsState, partial: PartialResult, e: StepEvent): [StepsState, PartialResult] {
  const next = { ...partial };
  let nextSteps = steps;
  if (e.step !== "done") {
    const prev = steps[e.step];
    const now = Date.now();
    nextSteps = {
      ...steps,
      [e.step]: {
        status: e.status,
        label: e.label,
        startedAt: prev.startedAt ?? now,
        finishedAt: e.status === "running" ? undefined : now,
      },
    };
  }
  const d = e.data as Record<string, unknown>;
  if (d.error) next.error = d.error as ErrorInfo;
  if (d.causes) next.causes = d.causes as ErrorInfo[];
  if (d.frames) next.frames = d.frames as Frame[];
  if (d.warnings) next.warnings = d.warnings as string[];
  if (d.contexts) next.contexts = d.contexts as CodeContext[];
  if (d.suspect_commit) next.suspect_commit = d.suspect_commit as SuspectCommit;
  if (typeof d.summary === "string") next.summary = d.summary;
  if (typeof d.root_cause === "string") next.root_cause = d.root_cause;
  if (d.analysis) next.analysis = d.analysis as Analysis;
  if (d.fix) next.fix = d.fix as Fix;
  if (typeof d.ref === "string") next.ref = d.ref;
  if (typeof d.sha === "string") next.sha = d.sha;
  return [nextSteps, next];
}

/** Marks every step as finished for a complete result (cached or fixture). */
export function stepsFromResult(result: AnalysisResult): StepsState {
  const s = initialSteps();
  for (const step of STEPS) s[step.name] = { status: "done", label: "" };
  if (!result.fix) s.build_fix = { status: "skipped", label: result.meta.fix_unavailable_reason ?? "" };
  return s;
}

export function githubBlobUrl(repo: string, sha: string | null | undefined, file: string, line?: number): string | null {
  if (!sha || !/^[\w.-]+\/[\w.-]+$/.test(repo) || repo.startsWith("local/")) return null;
  return `https://github.com/${repo}/blob/${sha}/${file}${line ? `#L${line}` : ""}`;
}

function plural(n: number, unit: string): string {
  return `${n} ${unit}${n === 1 ? "" : "s"} ago`;
}

export function relativeTime(iso: string): string {
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return iso;
  const secs = (Date.now() - t) / 1000;
  if (secs < 60) return "just now";
  if (secs < 3600) return plural(Math.floor(secs / 60), "minute");
  if (secs < 86400) return plural(Math.floor(secs / 3600), "hour");
  const days = Math.floor(secs / 86400);
  if (days < 60) return plural(days, "day");
  return new Date(t).toLocaleDateString("en-US", { year: "numeric", month: "short", day: "numeric" });
}

export function formatDateTime(iso: string): string {
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return iso;
  return new Date(t).toLocaleString("en-US", { dateStyle: "medium", timeStyle: "short" });
}

export function confidenceTone(c: number): "high" | "mid" | "low" {
  if (c >= 0.75) return "high";
  if (c >= 0.5) return "mid";
  return "low";
}

export function langOf(file: string): "python" | "javascript" | "typescript" {
  if (file.endsWith(".py")) return "python";
  if (/\.(ts|tsx|mts|cts)$/.test(file)) return "typescript";
  return "javascript";
}
