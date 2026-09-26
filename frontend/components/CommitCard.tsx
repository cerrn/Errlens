"use client";

import { useState } from "react";
import { formatDateTime, relativeTime } from "@/lib/analysis";
import type { SuspectCommit } from "@/lib/types";
import { Badge, Card, CodeBlock } from "./ui";

export function CommitCard({ commit }: { commit: SuspectCommit }) {
  const [open, setOpen] = useState(true);
  const [subject, ...body] = commit.message.trim().split("\n");
  const bodyText = body.join("\n").trim();

  return (
    <Card
      title="Suspect commit"
      right={
        <Badge tone={commit.selection === "related" ? "accent" : "neutral"} title={commit.selection_reason}>
          {commit.selection === "related" ? "related change" : "blame"}
        </Badge>
      }
      bodyClassName=""
    >
      <div className="space-y-2 p-4">
        <div className="flex items-start gap-3">
          <code className="shrink-0 rounded bg-surface-2 px-1.5 py-0.5 font-mono text-xs">{commit.sha.slice(0, 7)}</code>
          <div className="min-w-0">
            <p className="break-words font-medium leading-snug">{subject}</p>
            {bodyText && <p className="mt-1 whitespace-pre-wrap break-words text-sm text-muted">{bodyText}</p>}
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted">
          <span>{commit.author || "unknown author"}</span>
          <span title={formatDateTime(commit.date)}>{relativeTime(commit.date)}</span>
          {commit.file && <span className="font-mono">{commit.file}</span>}
          {commit.url && (
            <a href={commit.url} target="_blank" rel="noreferrer" className="hover:text-accent">
              Open on GitHub ↗
            </a>
          )}
        </div>
        {commit.selection_reason && (
          <p className="rounded-md bg-surface-2 px-3 py-2 text-xs text-muted">{commit.selection_reason}</p>
        )}
      </div>
      {commit.patch && (
        <div className="border-t border-border">
          <button
            onClick={() => setOpen((v) => !v)}
            className="flex w-full items-center justify-between px-4 py-2 text-xs font-medium text-muted hover:text-text"
          >
            <span>Patch preview</span>
            <span>{open ? "▴" : "▾"}</span>
          </button>
          {open && <CodeBlock code={commit.patch} lang="diff" numbers={false} diffLines className="max-h-80 overflow-auto border-t border-border" />}
        </div>
      )}
    </Card>
  );
}
