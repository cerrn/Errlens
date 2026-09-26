"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { ResultView, toPartial } from "@/components/ResultView";
import { Notice, Spinner } from "@/components/ui";
import { formatDateTime } from "@/lib/analysis";
import { ApiError, getAnalysis } from "@/lib/api";
import sample from "@/lib/fixture.json";
import type { AnalysisResult } from "@/lib/types";

export default function AnalysisPage() {
  const { id } = useParams<{ id: string }>();
  const [result, setResult] = useState<AnalysisResult | null>(null);
  const [error, setError] = useState<{ message: string; notFound: boolean } | null>(null);

  useEffect(() => {
    if (!id) return;
    if (id === (sample as { id: string }).id) {
      setResult(sample as unknown as AnalysisResult);
      return;
    }
    getAnalysis(id)
      .then(setResult)
      .catch((e: unknown) =>
        setError({
          message: e instanceof Error ? e.message : String(e),
          notFound: e instanceof ApiError && e.status === 404,
        }),
      );
  }, [id]);

  if (error) {
    return (
      <div className="space-y-4">
        <Notice tone="bad" title={error.notFound ? "Analysis not found" : "Could not load the analysis"}>
          {error.notFound ? "This link is invalid or the record was deleted." : error.message}
        </Notice>
        <Link href="/history" className="text-sm text-accent hover:underline">
          ← Back to history
        </Link>
      </div>
    );
  }

  if (!result) {
    return (
      <div className="flex items-center gap-2 text-sm text-muted">
        <Spinner /> Loading analysis…
      </div>
    );
  }

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between gap-3">
        <Link href="/history" className="text-sm text-muted hover:text-text">
          ← History
        </Link>
        <span className="text-xs text-muted">{formatDateTime(result.created_at)}</span>
      </div>
      <ResultView data={toPartial(result)} result={result} />
    </div>
  );
}
