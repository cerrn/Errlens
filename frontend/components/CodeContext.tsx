"use client";

import { useState } from "react";
import { githubBlobUrl, langOf } from "@/lib/analysis";
import type { CodeContext as Ctx, Frame } from "@/lib/types";
import { Card, CodeBlock } from "./ui";

export function CodeContext({
  contexts,
  frames,
  repo,
  sha,
}: {
  contexts: Ctx[];
  frames: Frame[];
  repo?: string;
  sha?: string | null;
}) {
  const [active, setActive] = useState(0);
  if (!contexts.length) return null;
  const ctx = contexts[Math.min(active, contexts.length - 1)];
  const frame = frames[ctx.frame_index];
  const url = repo ? githubBlobUrl(repo, sha, ctx.file, ctx.highlight_line) : null;

  return (
    <Card
      title="Code context"
      bodyClassName=""
      right={
        url && (
          <a href={url} target="_blank" rel="noreferrer" className="text-xs text-muted hover:text-accent">
            Open on GitHub ↗
          </a>
        )
      }
    >
      {contexts.length > 1 && (
        <div className="flex gap-1 overflow-x-auto border-b border-border px-2 pt-2" role="tablist">
          {contexts.map((c, i) => {
            const f = frames[c.frame_index];
            return (
              <button
                key={`${c.file}:${c.highlight_line}`}
                role="tab"
                aria-selected={i === active}
                onClick={() => setActive(i)}
                className={`shrink-0 rounded-t-md border border-b-0 px-3 py-1.5 font-mono text-xs ${
                  i === active ? "border-border bg-code text-text" : "border-transparent text-muted hover:text-text"
                }`}
              >
                {c.file.split("/").pop()}:{c.highlight_line}
                {f?.function && <span className="ml-1.5 text-muted">{f.function}</span>}
              </button>
            );
          })}
        </div>
      )}
      <div className="flex items-center justify-between gap-2 border-b border-border bg-code px-4 py-1.5 font-mono text-xs text-muted">
        <span className="truncate">
          {ctx.file}
          {frame?.function && <> · {frame.function}()</>}
        </span>
        <span className="shrink-0">
          {active === 0 ? "error line" : "caller"} {ctx.highlight_line}
        </span>
      </div>
      <CodeBlock
        code={ctx.snippet}
        lang={langOf(ctx.file)}
        startLine={ctx.start_line}
        highlight={[ctx.highlight_line - ctx.start_line + 1]}
        className="max-h-[420px] overflow-auto"
      />
    </Card>
  );
}

export function StackFrames({ frames }: { frames: Frame[] }) {
  const [showAll, setShowAll] = useState(false);
  const lib = frames.filter((f) => !f.is_user_code).length;
  const visible = showAll ? frames : frames.filter((f, i) => f.is_user_code || i === 0);
  return (
    <Card
      title={`Stack (${frames.length} frame${frames.length === 1 ? "" : "s"})`}
      bodyClassName="p-0"
      right={
        lib > 0 && (
          <button onClick={() => setShowAll((v) => !v)} className="text-xs text-muted hover:text-text">
            {showAll ? "Hide library frames" : `+${lib} library frame${lib === 1 ? "" : "s"}`}
          </button>
        )
      }
    >
      <ol className="divide-y divide-border font-mono text-xs">
        {visible.map((f, i) => (
          <li key={`${f.raw_file}:${f.line}:${i}`} className={`flex items-center gap-2 px-4 py-1.5 ${f.is_user_code ? "" : "text-muted"}`}>
            <span className="w-4 shrink-0 text-right text-muted">{frames.indexOf(f)}</span>
            <span className="min-w-0 flex-1 truncate" title={f.raw_file}>
              {f.function && <span className={f.is_user_code ? "text-accent" : ""}>{f.function} </span>}
              <span>
                {f.file}:{f.line}
              </span>
            </span>
            {f.is_user_code &&
              (f.resolved ? (
                <span className="shrink-0 text-ok" title="Found in the repository">
                  ●
                </span>
              ) : (
                <span className="shrink-0 text-warn" title="Not found in the repository">
                  ○ not found
                </span>
              ))}
          </li>
        ))}
      </ol>
    </Card>
  );
}
