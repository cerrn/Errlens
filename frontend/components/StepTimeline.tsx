"use client";

import { useEffect, useState } from "react";
import { STEPS, type StepsState, type StepStatus } from "@/lib/analysis";
import { Spinner } from "./ui";

function StatusIcon({ status }: { status: StepStatus }) {
  const base = "flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-xs font-bold";
  switch (status) {
    case "running":
      return (
        <span className={`${base} bg-accent-soft text-accent`}>
          <Spinner />
        </span>
      );
    case "done":
      return (
        <span className={`${base} bg-ok-soft text-ok`} aria-label="done">
          ✓
        </span>
      );
    case "skipped":
      return (
        <span className={`${base} bg-surface-2 text-muted`} aria-label="skipped">
          –
        </span>
      );
    case "error":
      return (
        <span className={`${base} bg-bad-soft text-bad`} aria-label="error">
          !
        </span>
      );
    default:
      return <span className={`${base} border border-dashed border-border`} aria-label="pending" />;
  }
}

function useNow(active: boolean) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    const t = setInterval(() => setNow(Date.now()), 100);
    return () => clearInterval(t);
  }, [active]);
  return now;
}

export function StepTimeline({ steps, finished }: { steps: StepsState; finished?: boolean }) {
  const anyRunning = STEPS.some((s) => steps[s.name].status === "running");
  const now = useNow(anyRunning && !finished);

  return (
    <ol className="relative space-y-0.5" aria-live="polite">
      {STEPS.map((s, i) => {
        const st = steps[s.name];
        const elapsed =
          st.startedAt !== undefined ? ((st.finishedAt ?? (st.status === "running" ? now : st.startedAt)) - st.startedAt) / 1000 : null;
        return (
          <li key={s.name} className="relative flex gap-3 py-1.5">
            {i < STEPS.length - 1 && <span className="absolute left-3 top-8 h-[calc(100%-1.25rem)] w-px bg-border" aria-hidden />}
            <StatusIcon status={st.status} />
            <div className="min-w-0 flex-1 pt-0.5">
              <div className="flex items-baseline justify-between gap-2">
                <span className={`text-sm font-medium ${st.status === "pending" ? "text-muted" : ""}`}>{s.title}</span>
                {elapsed !== null && elapsed >= 0.05 && (
                  <span className="shrink-0 font-mono text-[11px] text-muted">{elapsed.toFixed(1)} s</span>
                )}
              </div>
              {st.label && (
                <p
                  className={`mt-0.5 break-words text-xs ${
                    st.status === "error" ? "text-bad" : st.status === "running" ? "animate-pulse-dot text-muted" : "text-muted"
                  }`}
                >
                  {st.label}
                </p>
              )}
            </div>
          </li>
        );
      })}
    </ol>
  );
}
