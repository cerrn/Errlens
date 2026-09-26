import type { AnalysisResult, AnalyzeRequest, HistoryItem, HistoryList, StepEvent } from "./types";

export const API_URL = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/+$/, "");

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number = 0,
    public code: string = "error",
  ) {
    super(message);
  }
}

async function errorFrom(resp: Response): Promise<ApiError> {
  let detail = `Server error (${resp.status})`;
  let code = "error";
  try {
    const body = await resp.json();
    if (typeof body.detail === "string") detail = body.detail;
    else if (Array.isArray(body.detail)) {
      // FastAPI validation error
      detail = body.detail.map((d: { msg?: string }) => d.msg ?? "").filter(Boolean).join("; ") || detail;
    }
    if (typeof body.code === "string") code = body.code;
  } catch {
    /* body is not JSON */
  }
  return new ApiError(detail, resp.status, code);
}

function networkError(e: unknown): ApiError {
  if (e instanceof DOMException && e.name === "AbortError") return new ApiError("Request cancelled.", 0, "aborted");
  if (e instanceof ApiError) return e;
  return new ApiError(
    `Could not reach the backend (${API_URL}). Make sure the server is running and NEXT_PUBLIC_API_URL is correct.`,
    0,
    "network",
  );
}

/**
 * Reads the SSE stream. EventSource cannot send POST requests, so fetch + ReadableStream is used.
 * Every `data:` block is a StepEvent.
 */
export async function analyzeStream(
  req: AnalyzeRequest,
  onEvent: (e: StepEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  let resp: Response;
  try {
    resp = await fetch(`${API_URL}/analyze/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
      body: JSON.stringify(req),
      signal,
    });
  } catch (e) {
    throw networkError(e);
  }
  if (!resp.ok || !resp.body) throw await errorFrom(resp);

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, "\n");
      let sep: number;
      while ((sep = buffer.indexOf("\n\n")) !== -1) {
        const block = buffer.slice(0, sep);
        buffer = buffer.slice(sep + 2);
        const data = block
          .split("\n")
          .filter((l) => l.startsWith("data:"))
          .map((l) => l.slice(5).replace(/^ /, ""))
          .join("\n");
        if (!data) continue; // ": ping" comments
        try {
          onEvent(JSON.parse(data) as StepEvent);
        } catch {
          /* malformed block: skip */
        }
      }
    }
  } catch (e) {
    throw networkError(e);
  }
}

export async function analyze(req: AnalyzeRequest, signal?: AbortSignal): Promise<AnalysisResult> {
  let resp: Response;
  try {
    resp = await fetch(`${API_URL}/analyze`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(req),
      signal,
    });
  } catch (e) {
    throw networkError(e);
  }
  if (!resp.ok) throw await errorFrom(resp);
  return resp.json();
}

export async function getHistory(limit = 50): Promise<HistoryItem[]> {
  let resp: Response;
  try {
    resp = await fetch(`${API_URL}/history?limit=${limit}`, { cache: "no-store" });
  } catch (e) {
    throw networkError(e);
  }
  if (!resp.ok) throw await errorFrom(resp);
  const body: HistoryList = await resp.json();
  return body.items;
}

export async function getAnalysis(id: string): Promise<AnalysisResult> {
  let resp: Response;
  try {
    resp = await fetch(`${API_URL}/history/${encodeURIComponent(id)}`, { cache: "no-store" });
  } catch (e) {
    throw networkError(e);
  }
  if (!resp.ok) throw await errorFrom(resp);
  return resp.json();
}
