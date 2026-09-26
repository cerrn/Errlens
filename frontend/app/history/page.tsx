"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ConfidenceMeter } from "@/components/ResultSummary";
import { Badge, Notice, Spinner } from "@/components/ui";
import { formatDateTime, relativeTime } from "@/lib/analysis";
import { getHistory } from "@/lib/api";
import type { HistoryItem } from "@/lib/types";

export default function HistoryPage() {
  const [items, setItems] = useState<HistoryItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  function load() {
    setError(null);
    setItems(null);
    getHistory(100)
      .then(setItems)
      .catch((e: Error) => setError(e.message));
  }

  useEffect(load, []);

  return (
    <div className="space-y-5">
      <div className="flex items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Past analyses</h1>
          <p className="mt-1 text-sm text-muted">Every analysis has a permanent, shareable link.</p>
        </div>
        <button onClick={load} className="rounded-md border border-border bg-surface px-3 py-1.5 text-sm hover:bg-surface-2">
          Refresh
        </button>
      </div>

      {error && (
        <Notice tone="bad" title="Could not load history">
          {error}
        </Notice>
      )}

      {!error && items === null && (
        <div className="flex items-center gap-2 text-sm text-muted">
          <Spinner /> Loading…
        </div>
      )}

      {items && items.length === 0 && (
        <div className="rounded-xl border border-dashed border-border px-6 py-12 text-center">
          <p className="font-medium">No analyses yet</p>
          <p className="mt-1 text-sm text-muted">Your analyses will show up here.</p>
          <Link href="/" className="mt-4 inline-block rounded-lg bg-accent px-4 py-2 text-sm font-semibold text-white dark:text-black">
            Start an analysis
          </Link>
        </div>
      )}

      {items && items.length > 0 && (
        <div className="overflow-hidden rounded-xl border border-border bg-surface">
          <table className="w-full text-sm">
            <thead className="border-b border-border bg-surface-2/60 text-left text-xs text-muted">
              <tr>
                <th className="px-4 py-2 font-medium">Error</th>
                <th className="hidden px-4 py-2 font-medium md:table-cell">Repo</th>
                <th className="px-4 py-2 font-medium">Confidence</th>
                <th className="hidden px-4 py-2 text-right font-medium sm:table-cell">Date</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {items.map((it) => (
                <tr key={it.id} className="group hover:bg-surface-2/50">
                  <td className="max-w-0 px-4 py-2.5">
                    <Link href={`/analysis/${it.id}`} className="block">
                      <div className="flex items-center gap-2">
                        <Badge tone="bad">{it.error_type.split(".").pop()}</Badge>
                      </div>
                      <p className="mt-1 truncate font-mono text-xs text-muted group-hover:text-text">{it.error_message || "—"}</p>
                    </Link>
                  </td>
                  <td className="hidden truncate px-4 py-2.5 font-mono text-xs md:table-cell">{it.repo}</td>
                  <td className="px-4 py-2.5">
                    {it.confidence != null ? <ConfidenceMeter value={it.confidence} size="sm" /> : <span className="text-xs text-muted">no analysis</span>}
                  </td>
                  <td className="hidden px-4 py-2.5 text-right text-xs text-muted sm:table-cell" title={formatDateTime(it.created_at)}>
                    {relativeTime(it.created_at)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
