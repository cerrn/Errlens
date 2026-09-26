"use client";

import { useEffect, useState } from "react";
import { escapeHtml, highlight, type HighlightOptions } from "@/lib/highlight";

export function Card({
  title,
  right,
  children,
  className = "",
  bodyClassName = "p-4",
}: {
  title?: React.ReactNode;
  right?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
  bodyClassName?: string;
}) {
  return (
    <section className={`overflow-hidden rounded-xl border border-border bg-surface ${className}`}>
      {(title || right) && (
        <header className="flex items-center justify-between gap-3 border-b border-border px-4 py-2.5">
          <h2 className="min-w-0 truncate text-sm font-semibold">{title}</h2>
          {right && <div className="flex shrink-0 items-center gap-2">{right}</div>}
        </header>
      )}
      <div className={bodyClassName}>{children}</div>
    </section>
  );
}

type Tone = "neutral" | "ok" | "warn" | "bad" | "accent";

const TONES: Record<Tone, string> = {
  neutral: "bg-surface-2 text-muted",
  ok: "bg-ok-soft text-ok",
  warn: "bg-warn-soft text-warn",
  bad: "bg-bad-soft text-bad",
  accent: "bg-accent-soft text-accent",
};

export function Badge({ tone = "neutral", children, title }: { tone?: Tone; children: React.ReactNode; title?: string }) {
  return (
    <span title={title} className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium ${TONES[tone]}`}>
      {children}
    </span>
  );
}

export function CopyButton({ text, label = "Copy", className = "" }: { text: string; label?: string; className?: string }) {
  const [copied, setCopied] = useState(false);
  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      const ta = document.createElement("textarea");
      ta.value = text;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      ta.remove();
    }
    setCopied(true);
    setTimeout(() => setCopied(false), 1600);
  }
  return (
    <button
      type="button"
      onClick={copy}
      className={`rounded-md border border-border bg-surface px-2.5 py-1 text-xs font-medium hover:bg-surface-2 ${className}`}
    >
      {copied ? "Copied ✓" : label}
    </button>
  );
}

/** Code block highlighted with Shiki. Shows plain text until the highlighter loads. */
export function CodeBlock({
  code,
  startLine = 1,
  numbers = true,
  className = "",
  ...opts
}: HighlightOptions & { code: string; startLine?: number; numbers?: boolean; className?: string }) {
  const [html, setHtml] = useState<string | null>(null);
  const key = `${opts.lang}|${(opts.highlight ?? []).join(",")}|${opts.diffLines}|${code}`;

  useEffect(() => {
    let alive = true;
    highlight(code, opts)
      .then((h) => alive && setHtml(h))
      .catch(() => alive && setHtml(null));
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  const fallback = `<pre><code>${code
    .split("\n")
    .map((l, i) => `<span class="line${(opts.highlight ?? []).includes(i + 1) ? " highlighted" : ""}">${escapeHtml(l)}</span>`)
    .join("\n")}</code></pre>`;

  return (
    <div
      className={`code-block bg-code font-mono ${numbers ? "" : "no-numbers"} ${className}`}
      style={{ ["--start" as string]: String(startLine - 1) }}
      dangerouslySetInnerHTML={{ __html: html ?? fallback }}
    />
  );
}

export function Spinner({ className = "" }: { className?: string }) {
  return (
    <svg className={`animate-spin ${className}`} width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <circle cx="12" cy="12" r="9" stroke="currentColor" strokeOpacity="0.25" strokeWidth="3" />
      <path d="M21 12a9 9 0 0 0-9-9" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
    </svg>
  );
}

export function Notice({ tone = "warn", title, children }: { tone?: "warn" | "bad" | "neutral"; title?: string; children: React.ReactNode }) {
  const cls =
    tone === "bad"
      ? "border-bad/30 bg-bad-soft text-bad"
      : tone === "warn"
        ? "border-warn/30 bg-warn-soft text-warn"
        : "border-border bg-surface-2 text-muted";
  return (
    <div className={`rounded-lg border px-3.5 py-2.5 text-sm ${cls}`} role={tone === "bad" ? "alert" : undefined}>
      {title && <div className="mb-0.5 font-semibold">{title}</div>}
      <div className="text-text/85">{children}</div>
    </div>
  );
}

/** Renders `backticked` parts of model text as inline code. */
export function RichText({ text }: { text: string }) {
  const parts = text.split(/(`[^`\n]+`)/g);
  return (
    <>
      {parts.map((p, i) =>
        p.length > 2 && p.startsWith("`") && p.endsWith("`") ? (
          <code key={i} className="rounded bg-surface-2 px-1 py-0.5 font-mono text-[0.85em]">
            {p.slice(1, -1)}
          </code>
        ) : (
          <span key={i}>{p}</span>
        ),
      )}
    </>
  );
}
