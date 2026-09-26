"use client";

import { useRef, useState } from "react";
import { AnalyzeForm } from "@/components/AnalyzeForm";
import { ResultView, toPartial } from "@/components/ResultView";
import { StepTimeline } from "@/components/StepTimeline";
import { Card, Notice } from "@/components/ui";
import { applyEvent, initialSteps, stepsFromResult, type PartialResult, type StepsState } from "@/lib/analysis";
import { analyzeStream, ApiError } from "@/lib/api";
import sample from "@/lib/fixture.json";
import type { AnalysisResult, AnalyzeRequest } from "@/lib/types";

type Phase = "idle" | "streaming" | "done" | "error";

export default function AnalyzePage() {
  const [phase, setPhase] = useState<Phase>("idle");
  const [steps, setSteps] = useState<StepsState>(initialSteps);
  const [partial, setPartial] = useState<PartialResult>({});
  const [result, setResult] = useState<AnalysisResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const stateRef = useRef<{ steps: StepsState; partial: PartialResult }>({ steps: initialSteps(), partial: {} });

  async function run(req: AnalyzeRequest) {
    abortRef.current?.abort();
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    stateRef.current = { steps: initialSteps(), partial: {} };
    setSteps(stateRef.current.steps);
    setPartial({});
    setResult(null);
    setError(null);
    setPhase("streaming");

    let finished = false;
    try {
      await analyzeStream(
        req,
        (e) => {
          const [s, p] = applyEvent(stateRef.current.steps, stateRef.current.partial, e);
          stateRef.current = { steps: s, partial: p };
          setSteps(s);
          setPartial(p);
          if (e.step === "done") {
            finished = true;
            if (e.status === "done" && e.data.result) {
              const r = e.data.result as AnalysisResult;
              setResult(r);
              setPartial(toPartial(r));
              setPhase("done");
            } else {
              setError(e.label);
              setPhase("error");
            }
          }
        },
        ctrl.signal,
      );
      if (!finished) {
        setError("The connection closed before the analysis finished. Please try again.");
        setPhase("error");
      }
    } catch (e) {
      if (e instanceof ApiError && e.code === "aborted") {
        setPhase("idle");
        return;
      }
      setError(e instanceof Error ? e.message : String(e));
      setPhase("error");
    }
  }

  function cancel() {
    abortRef.current?.abort();
  }

  function showSample() {
    const r = sample as unknown as AnalysisResult;
    setResult(r);
    setPartial(toPartial(r));
    setSteps(stepsFromResult(r));
    setError(null);
    setPhase("done");
  }

  const started = phase !== "idle";

  return (
    <div className="space-y-6">
      {!started && (
        <div className="max-w-2xl">
          <h1 className="text-2xl font-semibold tracking-tight">From stack trace to root cause</h1>
          <p className="mt-1.5 text-muted">
            Paste a stack trace and a repository link. ErrLens pulls the related code and recent changes from GitHub, explains
            the error in plain language, finds the suspect commit and proposes a fix you can apply with{" "}
            <code className="font-mono text-sm">git apply</code>.
          </p>
        </div>
      )}

      <div className={`grid gap-6 ${started ? "lg:grid-cols-[minmax(0,1fr)_300px]" : ""}`}>
        <AnalyzeForm busy={phase === "streaming"} onSubmit={run} onCancel={cancel} />
        {started && (
          <Card title="Analysis steps" className="h-fit lg:sticky lg:top-20">
            <StepTimeline steps={steps} finished={phase !== "streaming"} />
          </Card>
        )}
      </div>

      {!started && (
        <p className="text-sm text-muted">
          To see the interface without a backend,{" "}
          <button onClick={showSample} className="font-medium text-accent hover:underline">
            show a sample result
          </button>
          .
        </p>
      )}

      {phase === "error" && error && (
        <Notice tone="bad" title="The analysis could not be completed">
          {error}
        </Notice>
      )}

      {(phase === "streaming" || phase === "done" || (phase === "error" && partial.error)) && (
        <ResultView data={partial} result={result} streaming={phase === "streaming"} />
      )}
    </div>
  );
}
