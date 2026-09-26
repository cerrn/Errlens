"use client";

import Link from "next/link";
import type { PartialResult } from "@/lib/analysis";
import type { AnalysisResult } from "@/lib/types";
import { CodeContext, StackFrames } from "./CodeContext";
import { CommitCard } from "./CommitCard";
import { DiffView } from "./DiffView";
import { ResultSummary } from "./ResultSummary";
import { Badge, CopyButton, Notice } from "./ui";

export function toPartial(r: AnalysisResult): PartialResult {
  return {
    error: r.error,
    causes: r.causes,
    frames: r.frames,
    contexts: r.contexts,
    suspect_commit: r.suspect_commit,
    analysis: r.analysis,
    fix: r.fix,
    warnings: r.meta.warnings,
    ref: r.meta.ref,
    sha: r.meta.sha ?? undefined,
  };
}

export function MetaBar({ result, showPermalink = true }: { result: AnalysisResult; showPermalink?: boolean }) {
  const m = result.meta;
  const permalink = typeof window !== "undefined" ? `${window.location.origin}/analysis/${result.id}` : `/analysis/${result.id}`;
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-2 text-xs text-muted">
      <span className="font-mono text-text">{m.repo}</span>
      {m.ref && (
        <span className="font-mono">
          {m.ref}
          {m.sha && ` @ ${m.sha.slice(0, 7)}`}
        </span>
      )}
      {m.cached && <Badge tone="accent">cached</Badge>}
      <span>{(m.duration_ms / 1000).toFixed(1)} s</span>
      {m.model && <span>{m.model}</span>}
      {showPermalink && (
        <span className="ml-auto flex items-center gap-2">
          <Link href={`/analysis/${result.id}`} className="hover:text-accent">
            Permalink
          </Link>
          <CopyButton text={permalink} label="Copy link" />
        </span>
      )}
    </div>
  );
}

export function ResultView({
  data,
  result,
  streaming = false,
}: {
  data: PartialResult;
  result?: AnalysisResult | null;
  streaming?: boolean;
}) {
  const warnings = result?.meta.warnings ?? data.warnings ?? [];
  const repo = result?.meta.repo;
  const sha = result?.meta.sha ?? data.sha;
  const analysisDone = !!result || !!data.analysis;

  return (
    <div className="space-y-4">
      {result && <MetaBar result={result} />}

      {warnings.length > 0 && (
        <Notice title={warnings.length > 1 ? `${warnings.length} warnings` : "Warning"}>
          <ul className="list-disc space-y-0.5 pl-4">
            {warnings.map((w, i) => (
              <li key={i}>{w}</li>
            ))}
          </ul>
        </Notice>
      )}

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_380px]">
        <div className="min-w-0 space-y-4">
          {(data.error || data.summary || data.analysis) && (
            <ResultSummary
              analysis={data.analysis}
              pendingSummary={data.summary}
              pendingRootCause={data.root_cause}
              error={data.error}
              causes={data.causes}
            />
          )}
          {(analysisDone || data.fix) && !streaming ? (
            <DiffView fix={data.fix} unavailableReason={result?.meta.fix_unavailable_reason} />
          ) : (
            data.fix && <DiffView fix={data.fix} />
          )}
          {data.contexts && data.contexts.length > 0 && (
            <CodeContext contexts={data.contexts} frames={data.frames ?? []} repo={repo} sha={sha} />
          )}
        </div>
        <div className="min-w-0 space-y-4">
          {data.suspect_commit && <CommitCard commit={data.suspect_commit} />}
          {data.frames && data.frames.length > 0 && <StackFrames frames={data.frames} />}
        </div>
      </div>
    </div>
  );
}
