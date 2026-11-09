/**
 * api.js — raw fetch calls to the backend.
 * No DOM access, no state. Returns parsed JSON or throws an Error.
 */

const TIMEOUT_MS = 120000;

async function post(path, body, signal) {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  const data = await res.json();
  if (!res.ok) {
    throw new Error(
      typeof data.detail === "string"
        ? data.detail
        : "Hawk could not respond. Please try again."
    );
  }
  return data;
}

export function makeAbortController() {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  return { controller, clear: () => clearTimeout(timer) };
}

export function chatRequest(messages, signal) {
  return post("/api/chat", { messages }, signal);
}

// Streams a reply from /api/chat/stream, which answers in NDJSON: one JSON object
// per line. Yields each object as it arrives: { token } for each piece of the reply,
// then one { done: true, escalate, escalation_reason, offer_options } at the end.
// The low-confidence fallback sends no tokens; its text is in done's `reply`.
// (app.js also accepts an optional `sources` list there; the server doesn't send one yet.)
export async function* chatStreamRequest(messages, signal) {
  const res = await fetch("/api/chat/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ messages }),
    signal,
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(
      typeof data.detail === "string"
        ? data.detail
        : "Hawk could not respond. Please try again."
    );
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop();
    for (const line of lines) {
      if (line.trim()) yield JSON.parse(line);
    }
  }
}

export function escalateRequest(messages, signal) {
  return post("/api/escalate", { messages }, signal);
}
