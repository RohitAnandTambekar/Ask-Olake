import type { Message } from "./types";

// The conversation survives page navigation within the docs, but not the browser session.
// Storage can be unavailable (private mode, blocked cookies): fall back to memory.
const KEY = "ask-olake:conversation:v1";
const MAX_MESSAGES = 40;

export function loadMessages(): Message[] {
  try {
    const raw = sessionStorage.getItem(KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? (parsed as Message[]).filter((m) => !m.pending) : [];
  } catch {
    return [];
  }
}

export function saveMessages(messages: Message[]): void {
  try {
    sessionStorage.setItem(KEY, JSON.stringify(messages.slice(-MAX_MESSAGES)));
  } catch {
    // quota exceeded or storage blocked: the chat still works, it just won't persist
  }
}
