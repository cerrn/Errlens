"use client";

import type { HighlighterCore, ShikiTransformer } from "shiki/core";

type Lang = "python" | "javascript" | "typescript" | "diff";

let highlighterPromise: Promise<HighlighterCore> | null = null;

/** Singleton Shiki highlighter that loads only the languages and themes we need. */
export function getHighlighter(): Promise<HighlighterCore> {
  if (!highlighterPromise) {
    highlighterPromise = (async () => {
      const [{ createHighlighterCore }, { createJavaScriptRegexEngine }] = await Promise.all([
        import("shiki/core"),
        import("shiki/engine/javascript"),
      ]);
      return createHighlighterCore({
        themes: [import("shiki/dist/themes/github-light.mjs"), import("shiki/dist/themes/github-dark.mjs")],
        langs: [
          import("shiki/dist/langs/python.mjs"),
          import("shiki/dist/langs/javascript.mjs"),
          import("shiki/dist/langs/typescript.mjs"),
          import("shiki/dist/langs/diff.mjs"),
        ],
        engine: createJavaScriptRegexEngine(),
      });
    })();
  }
  return highlighterPromise;
}

export interface HighlightOptions {
  lang: Lang;
  /** Lines to highlight (1-based, relative to the block) */
  highlight?: number[];
  diffLines?: boolean;
}

export async function highlight(code: string, opts: HighlightOptions): Promise<string> {
  const hl = await getHighlighter();
  const marks = new Set(opts.highlight ?? []);
  const transformer: ShikiTransformer = {
    line(node, line) {
      if (marks.has(line)) this.addClassToHast(node, "highlighted");
      if (opts.diffLines) {
        const text = code.split("\n")[line - 1] ?? "";
        if (text.startsWith("+")) this.addClassToHast(node, "diff-add");
        else if (text.startsWith("-")) this.addClassToHast(node, "diff-del");
        else if (text.startsWith("@@")) this.addClassToHast(node, "diff-hunk");
      }
    },
  };
  return hl.codeToHtml(code, {
    lang: opts.lang,
    themes: { light: "github-light", dark: "github-dark" },
    defaultColor: "light",
    transformers: [transformer],
  });
}

export function escapeHtml(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}
