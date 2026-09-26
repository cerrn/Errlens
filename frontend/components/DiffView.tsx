"use client";

import { useEffect, useState } from "react";
import type { Fix } from "@/lib/types";
import { Card, CodeBlock, CopyButton, RichText } from "./ui";

function useDiffHtml(diff: string, mode: "line-by-line" | "side-by-side") {
  const [html, setHtml] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    import("diff2html")
      .then(({ html: render }) => {
        if (!alive) return;
        setHtml(
          render(diff, {
            drawFileList: false,
            outputFormat: mode,
            matching: "lines",
            // diff2html's auto color scheme follows prefers-color-scheme
            colorScheme: "auto" as never,
          }),
        );
      })
      .catch(() => alive && setHtml(null));
    return () => {
      alive = false;
    };
  }, [diff, mode]);
  return html;
}

export function DiffView({ fix, unavailableReason }: { fix?: Fix | null; unavailableReason?: string | null }) {
  const [mode, setMode] = useState<"line-by-line" | "side-by-side">("line-by-line");
  const html = useDiffHtml(fix?.diff ?? "", mode);

  if (!fix) {
    return (
      <Card title="Proposed fix">
        <p className="text-sm text-muted">
          No applicable fix could be produced for this analysis.
          {unavailableReason && <span className="mt-1 block text-xs">Reason: {unavailableReason}</span>}
        </p>
      </Card>
    );
  }

  function download() {
    const blob = new Blob([fix!.diff], { type: "text/x-diff" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "fix.diff";
    a.click();
    URL.revokeObjectURL(a.href);
  }

  return (
    <Card
      title={
        <span className="flex items-center gap-2">
          Proposed fix
          <span className="font-mono text-xs font-normal text-muted">
            {fix.file}:{fix.start_line}
            {fix.end_line !== fix.start_line ? `-${fix.end_line}` : ""}
          </span>
        </span>
      }
      bodyClassName=""
      right={
        <>
          <div className="hidden rounded-md border border-border p-0.5 text-xs sm:flex">
            {(["line-by-line", "side-by-side"] as const).map((m) => (
              <button
                key={m}
                onClick={() => setMode(m)}
                className={`rounded px-2 py-0.5 ${mode === m ? "bg-surface-2 font-medium" : "text-muted"}`}
              >
                {m === "line-by-line" ? "Unified" : "Split"}
              </button>
            ))}
          </div>
          <button
            onClick={download}
            className="rounded-md border border-border bg-surface px-2.5 py-1 text-xs font-medium hover:bg-surface-2"
          >
            Download
          </button>
          <CopyButton text={fix.diff} label="Copy diff" />
        </>
      }
    >
      {fix.explanation && <p className="border-b border-border px-4 py-3 text-sm leading-relaxed"><RichText text={fix.explanation} /></p>}
      <div className="diff-view overflow-x-auto">
        {html ? (
          <div dangerouslySetInnerHTML={{ __html: html }} />
        ) : (
          <CodeBlock code={fix.diff.trimEnd()} lang="diff" numbers={false} diffLines />
        )}
      </div>
      <div className="flex flex-wrap items-center gap-2 border-t border-border bg-surface-2/50 px-4 py-2 text-xs text-muted">
        <span>To apply:</span>
        <code className="rounded bg-surface px-1.5 py-0.5 font-mono">git apply fix.diff</code>
      </div>
    </Card>
  );
}
