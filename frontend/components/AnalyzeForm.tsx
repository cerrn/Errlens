"use client";

import { useState } from "react";
import { EXAMPLES } from "@/lib/examples";
import type { AnalyzeRequest } from "@/lib/types";
import { detectLanguage, validateForm, type FormErrors } from "@/lib/validate";
import { Spinner } from "./ui";

const DEMO_REPO = process.env.NEXT_PUBLIC_DEMO_REPO ?? "";

export function AnalyzeForm({
  busy,
  onSubmit,
  onCancel,
}: {
  busy: boolean;
  onSubmit: (req: AnalyzeRequest) => void;
  onCancel?: () => void;
}) {
  const [trace, setTrace] = useState("");
  const [repoUrl, setRepoUrl] = useState("");
  const [ref, setRef] = useState("");
  const [errors, setErrors] = useState<FormErrors>({});
  const [touched, setTouched] = useState(false);

  const lang = trace.trim() ? detectLanguage(trace) : null;

  function submit(e: React.FormEvent) {
    e.preventDefault();
    setTouched(true);
    const errs = validateForm(trace, repoUrl);
    setErrors(errs);
    if (Object.keys(errs).length) return;
    onSubmit({ trace, repo_url: repoUrl.trim(), ref: ref.trim() || null });
  }

  function revalidate(nextTrace = trace, nextRepo = repoUrl) {
    if (touched) setErrors(validateForm(nextTrace, nextRepo));
  }

  function loadExample(id: string) {
    const ex = EXAMPLES.find((x) => x.id === id);
    if (!ex) return;
    setTrace(ex.trace);
    if (DEMO_REPO && !repoUrl) setRepoUrl(DEMO_REPO);
    setErrors({});
  }

  return (
    <form onSubmit={submit} noValidate className="rounded-xl border border-border bg-surface">
      <div className="p-4">
        <div className="mb-1.5 flex items-center justify-between gap-2">
          <label htmlFor="trace" className="text-sm font-medium">
            Stack trace
          </label>
          <div className="flex items-center gap-2 text-xs">
            {lang && (
              <span className="rounded-full bg-surface-2 px-2 py-0.5 text-muted">
                {lang === "python" ? "Python" : "JavaScript"} detected
              </span>
            )}
            <select
              aria-label="Load a demo scenario"
              className="rounded-md border border-border bg-surface px-2 py-1 text-xs text-muted"
              value=""
              onChange={(e) => loadExample(e.target.value)}
            >
              <option value="">Load a demo scenario…</option>
              {EXAMPLES.map((ex) => (
                <option key={ex.id} value={ex.id}>
                  {ex.title}
                </option>
              ))}
            </select>
          </div>
        </div>
        <textarea
          id="trace"
          value={trace}
          onChange={(e) => {
            setTrace(e.target.value);
            revalidate(e.target.value);
          }}
          spellCheck={false}
          placeholder={'Traceback (most recent call last):\n  File "/app/user.py", line 42, in get_profile\n    ...\nKeyError: \'email\''}
          aria-invalid={!!errors.trace}
          aria-describedby={errors.trace ? "trace-error" : undefined}
          className={`h-56 w-full resize-y rounded-lg border bg-code p-3 font-mono text-[12.5px] leading-relaxed outline-none focus:ring-2 focus:ring-accent/30 ${
            errors.trace ? "border-bad" : "border-border"
          }`}
        />
        {errors.trace && (
          <p id="trace-error" className="mt-1.5 text-xs text-bad">
            {errors.trace}
          </p>
        )}

        <div className="mt-4 grid gap-4 sm:grid-cols-[1fr_220px]">
          <div>
            <label htmlFor="repo" className="mb-1.5 block text-sm font-medium">
              GitHub repository
            </label>
            <input
              id="repo"
              value={repoUrl}
              onChange={(e) => {
                setRepoUrl(e.target.value);
                revalidate(trace, e.target.value);
              }}
              placeholder="https://github.com/owner/repo"
              aria-invalid={!!errors.repo_url}
              aria-describedby={errors.repo_url ? "repo-error" : undefined}
              className={`w-full rounded-lg border bg-surface px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-accent/30 ${
                errors.repo_url ? "border-bad" : "border-border"
              }`}
            />
            {errors.repo_url && (
              <p id="repo-error" className="mt-1.5 text-xs text-bad">
                {errors.repo_url}
              </p>
            )}
          </div>
          <div>
            <label htmlFor="ref" className="mb-1.5 block text-sm font-medium">
              Branch / commit <span className="font-normal text-muted">(optional)</span>
            </label>
            <input
              id="ref"
              value={ref}
              onChange={(e) => setRef(e.target.value)}
              placeholder="main or a1b2c3d"
              className="w-full rounded-lg border border-border bg-surface px-3 py-2 font-mono text-sm outline-none focus:ring-2 focus:ring-accent/30"
            />
          </div>
        </div>
        {!ref.trim() && (
          <p className="mt-2 text-xs text-muted">
            Without a ref, the default branch is used. If the trace comes from a different version, line numbers may not match.
          </p>
        )}
      </div>
      <div className="flex items-center justify-end gap-2 border-t border-border px-4 py-3">
        {busy && onCancel && (
          <button type="button" onClick={onCancel} className="rounded-lg px-3 py-2 text-sm text-muted hover:bg-surface-2">
            Cancel
          </button>
        )}
        <button
          type="submit"
          disabled={busy}
          className="inline-flex items-center gap-2 rounded-lg bg-accent px-4 py-2 text-sm font-semibold text-white shadow-sm hover:opacity-90 disabled:opacity-60 dark:text-black"
        >
          {busy && <Spinner />}
          {busy ? "Analyzing…" : "Analyze"}
        </button>
      </div>
    </form>
  );
}
