"use client";

import { confidenceTone } from "@/lib/analysis";
import type { Analysis, ErrorInfo } from "@/lib/types";
import { Badge, Card, RichText } from "./ui";

export function ConfidenceMeter({ value, size = "md" }: { value: number; size?: "sm" | "md" }) {
  const tone = confidenceTone(value);
  const color = tone === "high" ? "var(--ok)" : tone === "mid" ? "var(--warn)" : "var(--bad)";
  const pct = Math.round(value * 100);
  if (size === "sm") {
    return (
      <span className="inline-flex items-center gap-1.5 font-mono text-xs" title={`Confidence ${pct}%`}>
        <span className="h-1.5 w-12 overflow-hidden rounded-full bg-surface-2">
          <span className="block h-full rounded-full" style={{ width: `${pct}%`, background: color }} />
        </span>
        {pct}%
      </span>
    );
  }
  return (
    <div className="flex items-center gap-3">
      <div className="relative h-14 w-14">
        <svg viewBox="0 0 36 36" className="h-14 w-14 -rotate-90">
          <circle cx="18" cy="18" r="15.5" fill="none" stroke="var(--surface-2)" strokeWidth="4" />
          <circle
            cx="18"
            cy="18"
            r="15.5"
            fill="none"
            stroke={color}
            strokeWidth="4"
            strokeLinecap="round"
            strokeDasharray={`${(pct / 100) * 97.4} 97.4`}
          />
        </svg>
        <span className="absolute inset-0 flex items-center justify-center font-mono text-sm font-semibold">{pct}</span>
      </div>
      <div className="text-xs leading-tight text-muted">
        <div className="text-sm font-semibold text-text">
          {tone === "high" ? "High confidence" : tone === "mid" ? "Medium confidence" : "Low confidence"}
        </div>
        confidence score
      </div>
    </div>
  );
}

export function ErrorHeader({ error, causes }: { error: ErrorInfo; causes?: ErrorInfo[] }) {
  return (
    <div className="min-w-0">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone="bad">{error.type}</Badge>
        <Badge>{error.language === "python" ? "Python" : "JavaScript"}</Badge>
      </div>
      <p className="mt-1.5 whitespace-pre-wrap break-words font-mono text-sm">{error.message || "(no message)"}</p>
      {causes && causes.length > 0 && (
        <p className="mt-1 text-xs text-muted">
          Chained: {causes.map((c) => c.type.split(".").pop()).join(" → ")} → {error.type.split(".").pop()}
        </p>
      )}
    </div>
  );
}

export function ResultSummary({
  analysis,
  pendingSummary,
  pendingRootCause,
  error,
  causes,
}: {
  analysis?: Analysis | null;
  pendingSummary?: string;
  pendingRootCause?: string;
  error?: ErrorInfo;
  causes?: ErrorInfo[];
}) {
  const summary = analysis?.summary ?? pendingSummary;
  const rootCause = analysis?.root_cause ?? pendingRootCause;

  return (
    <Card bodyClassName="p-5">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        {error && <ErrorHeader error={error} causes={causes} />}
        {analysis && (
          <div className="shrink-0">
            <ConfidenceMeter value={analysis.confidence} />
          </div>
        )}
      </div>

      {summary ? (
        <div className="mt-5 space-y-4">
          <div>
            <h3 className="text-xs font-semibold uppercase tracking-wide text-muted">What happened?</h3>
            <p className="mt-1 leading-relaxed"><RichText text={summary} /></p>
          </div>
          {rootCause && (
            <div>
              <h3 className="text-xs font-semibold uppercase tracking-wide text-muted">Root cause hypothesis</h3>
              <p className="mt-1 leading-relaxed"><RichText text={rootCause} /></p>
            </div>
          )}
          {analysis && analysis.evidence.length > 0 && (
            <div>
              <h3 className="text-xs font-semibold uppercase tracking-wide text-muted">Evidence</h3>
              <ul className="mt-1.5 space-y-1.5">
                {analysis.evidence.map((ev, i) => (
                  <li key={i} className="flex gap-2 text-sm">
                    <span className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-accent" />
                    <span className="break-words"><RichText text={ev} /></span>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {analysis && (
            <details className="group rounded-lg border border-border bg-surface-2/50">
              <summary className="flex cursor-pointer items-center justify-between px-3 py-2 text-sm font-medium">
                <span>Why {Math.round(analysis.confidence * 100)}% confidence?</span>
                <span className="text-muted transition group-open:rotate-180">▾</span>
              </summary>
              <div className="border-t border-border px-3 py-2.5 text-sm">
                <p className="mb-2 text-xs text-muted">
                  Score = 0.6 × model confidence ({Math.round((analysis.llm_confidence ?? 0) * 100)}%) + 0.4 × average of signals
                </p>
                <ul className="space-y-1.5">
                  {analysis.signals.length > 0
                    ? analysis.signals.map((s) => (
                        <li key={s.name} className="flex gap-2">
                          <span
                            className={`mt-0.5 font-bold ${s.score >= 1 ? "text-ok" : s.score > 0 ? "text-warn" : "text-bad"}`}
                            aria-hidden
                          >
                            {s.score >= 1 ? "✓" : s.score > 0 ? "◐" : "✗"}
                          </span>
                          <span><RichText text={s.reason} /></span>
                        </li>
                      ))
                    : analysis.confidence_reasons.map((r, i) => <li key={i}>{r}</li>)}
                </ul>
              </div>
            </details>
          )}
        </div>
      ) : (
        <div className="mt-5 space-y-2" aria-hidden>
          <div className="h-3 w-2/3 animate-pulse rounded bg-surface-2" />
          <div className="h-3 w-5/6 animate-pulse rounded bg-surface-2" />
          <div className="h-3 w-1/2 animate-pulse rounded bg-surface-2" />
        </div>
      )}
    </Card>
  );
}
