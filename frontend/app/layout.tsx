import type { Metadata } from "next";
import Link from "next/link";
import "diff2html/bundles/css/diff2html.min.css";
import "./globals.css";

export const metadata: Metadata = {
  title: "ErrLens",
  description: "From stack trace to root cause: explanation, suspect commit, confidence score and an applicable fix.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen">
        <header className="sticky top-0 z-20 border-b border-border bg-bg/85 backdrop-blur">
          <div className="mx-auto flex h-14 max-w-6xl items-center justify-between px-4">
            <Link href="/" className="flex items-center gap-2 font-semibold tracking-tight">
              <Logo />
              <span>ErrLens</span>
            </Link>
            <nav className="flex items-center gap-1 text-sm">
              <Link href="/" className="rounded-md px-3 py-1.5 text-muted hover:bg-surface-2 hover:text-text">
                Analyze
              </Link>
              <Link href="/history" className="rounded-md px-3 py-1.5 text-muted hover:bg-surface-2 hover:text-text">
                History
              </Link>
            </nav>
          </div>
        </header>
        <main className="mx-auto max-w-6xl px-4 py-8">{children}</main>
      </body>
    </html>
  );
}

function Logo() {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <circle cx="10.5" cy="10.5" r="6.5" stroke="var(--accent)" strokeWidth="2.2" />
      <path d="M15.5 15.5L20 20" stroke="var(--accent)" strokeWidth="2.2" strokeLinecap="round" />
      <path d="M8 10.5h5M10.5 8v5" stroke="var(--text)" strokeWidth="1.8" strokeLinecap="round" />
    </svg>
  );
}
