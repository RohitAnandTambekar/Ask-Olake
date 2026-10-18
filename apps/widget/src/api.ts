import type { ServerEvent } from "./types";

/** Split a Server-Sent Events buffer into complete events; return the unfinished tail. */
export function parseSSE(buffer: string): { events: ServerEvent[]; rest: string } {
  const events: ServerEvent[] = [];
  const blocks = buffer.split("\n\n");
  const rest = blocks.pop() ?? "";
  for (const block of blocks) {
    let name = "";
    const data: string[] = [];
    for (const line of block.split("\n")) {
      if (line.startsWith("event:")) name = line.slice(6).trim();
      else if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
    }
    if (!name || data.length === 0) continue;
    try {
      events.push({ event: name, data: JSON.parse(data.join("\n")) } as ServerEvent);
    } catch {
      // ignore a malformed event rather than breaking the whole answer
    }
  }
  return { events, rest };
}

export class ChatError extends Error {}

const STATUS_MESSAGES: Record<number, string> = {
  429: "You've asked a lot of questions in a short time. Wait a minute, then try again.",
  503: "The assistant is busy or restarting. Try again in a moment.",
  422: "That question couldn't be sent. Shorten it and try again.",
};

/** POST a question and call `onEvent` for each event as it streams in. */
export async function streamChat(
  apiBase: string,
  body: { message: string; history: { role: string; content: string }[] },
  onEvent: (e: ServerEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  let response: Response;
  try {
    response = await fetch(`${apiBase}/api/chat`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
      signal,
    });
  } catch (err) {
    if ((err as Error).name === "AbortError") throw err;
    throw new ChatError("Can't reach the assistant. Check your connection and try again.");
  }
  if (!response.ok || !response.body) {
    throw new ChatError(STATUS_MESSAGES[response.status] ?? `The assistant returned an error (${response.status}).`);
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const { events, rest } = parseSSE(buffer);
    buffer = rest;
    events.forEach(onEvent);
  }
  buffer += decoder.decode();
  parseSSE(buffer + "\n\n").events.forEach(onEvent);
}

export async function sendFeedback(apiBase: string, queryId: string, rating: 1 | -1, comment?: string) {
  await fetch(`${apiBase}/api/feedback`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ query_id: queryId, rating, comment: comment || null }),
  });
}
