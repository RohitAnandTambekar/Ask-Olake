import { useEffect, useMemo, useRef, useState } from "preact/hooks";

import { ChatError, sendFeedback, streamChat } from "./api";
import { renderAnswer, sourceLabel } from "./markdown";
import { loadMessages, saveMessages } from "./storage";
import type { Message, ServerEvent, Source } from "./types";

const SUGGESTIONS = [
  "How do I set up CDC for a Postgres source?",
  "Which Iceberg catalogs can OLake write to?",
  "How does OLake Fusion schedule compaction?",
];
const HISTORY_TURNS = 6;
const SLACK = "https://olake.io/slack/";

const uid = () => Math.random().toString(36).slice(2, 10);

function WaveIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
      <path d="M3 9c2 0 2-2 4.5-2S10 9 12 9s2-2 4.5-2S19 9 21 9" stroke-linecap="round" />
      <path d="M3 15c2 0 2-2 4.5-2S10 15 12 15s2-2 4.5-2 2.5 2 4.5 2" stroke-linecap="round" />
    </svg>
  );
}

function SourceRail({ sources, citations }: { sources: Source[]; citations: number[] }) {
  const cited = citations.map((n) => sources[n - 1]).filter(Boolean);
  if (!cited.length) return null;
  return (
    <ol class="rail" aria-label="Sources for this answer">
      {cited.map((s) => {
        const { path, kind } = sourceLabel(s);
        return (
          <li key={s.n}>
            <span class="num">{s.n}</span>
            <a href={s.url} target="_blank" rel="noopener noreferrer" title={s.title}>
              <span class="path">{path}</span>
            </a>
            <span class="kind">{kind}</span>
          </li>
        );
      })}
    </ol>
  );
}

function Answer({ message, apiBase, onFeedback }: {
  message: Message;
  apiBase: string;
  onFeedback: (id: string, rating: 1 | -1) => void;
}) {
  const html = useMemo(
    () => renderAnswer(message.text, message.sources ?? []),
    [message.text, message.sources],
  );

  const copy = (e: MouseEvent) => {
    const button = (e.target as HTMLElement).closest("button[data-copy]") as HTMLButtonElement | null;
    if (!button) return;
    const code = button.parentElement?.querySelector("pre")?.textContent ?? "";
    navigator.clipboard?.writeText(code).then(
      () => {
        button.textContent = "Copied";
        setTimeout(() => (button.textContent = "Copy"), 1500);
      },
      () => (button.textContent = "Copy failed"),
    );
  };

  if (message.pending && !message.text) {
    return <p class="thinking">Searching the docs and code…</p>;
  }
  if (message.status === "error") {
    return <div class="note warn" role="alert">{message.text}</div>;
  }

  const rate = (rating: 1 | -1) => {
    if (!message.queryId || message.feedback) return;
    onFeedback(message.id, rating);
    sendFeedback(apiBase, message.queryId, rating).catch(() => undefined);
  };

  return (
    <div>
      {message.notices?.map((n) => (
        <div class="note warn" key={n}>{n}</div>
      ))}
      {message.status === "abstained" ? (
        <div class="note calm">
          <strong>Not in the docs</strong>
          <div class="answer" dangerouslySetInnerHTML={{ __html: html }} />
        </div>
      ) : (
        <div class="answer" onClick={copy} dangerouslySetInnerHTML={{ __html: html }} />
      )}
      {!message.pending && message.status !== "abstained" && (
        <SourceRail sources={message.sources ?? []} citations={message.citations ?? []} />
      )}
      {message.warnings?.map((w) => (
        <div class="note warn" key={w} dangerouslySetInnerHTML={{ __html: renderAnswer(w, []) }} />
      ))}
      {!message.pending && message.queryId && (
        <div class="feedback">
          {message.feedback ? (
            <span>Thanks for the feedback.</span>
          ) : (
            <>
              <span>Was this helpful?</span>
              <button type="button" aria-pressed={message.feedback === 1} onClick={() => rate(1)}>
                Yes
              </button>
              <button type="button" aria-pressed={message.feedback === -1} onClick={() => rate(-1)}>
                No
              </button>
            </>
          )}
        </div>
      )}
    </div>
  );
}

export function App({ apiBase, startOpen }: { apiBase: string; startOpen: boolean }) {
  const [open, setOpen] = useState(startOpen);
  const [messages, setMessages] = useState<Message[]>(loadMessages);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const logRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => saveMessages(messages.filter((m) => !m.pending)), [messages]);
  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight });
  }, [messages, open]);
  useEffect(() => {
    if (open) inputRef.current?.focus();
  }, [open]);

  const update = (id: string, patch: Partial<Message> | ((m: Message) => Partial<Message>)) =>
    setMessages((all) =>
      all.map((m) => (m.id === id ? { ...m, ...(typeof patch === "function" ? patch(m) : patch) } : m)),
    );

  async function ask(question: string) {
    const text = question.trim();
    if (!text || busy) return;
    const history = messages
      .filter((m) => !m.pending && m.status !== "error" && m.text)
      .slice(-HISTORY_TURNS)
      .map((m) => ({ role: m.role, content: m.text }));
    const answerId = uid();
    setMessages((all) => [
      ...all,
      { id: uid(), role: "user", text },
      { id: answerId, role: "assistant", text: "", pending: true },
    ]);
    setDraft("");
    setBusy(true);
    const controller = new AbortController();
    abortRef.current = controller;

    const onEvent = (e: ServerEvent) => {
      switch (e.event) {
        case "meta":
          update(answerId, { queryId: e.data.query_id, notices: e.data.notices });
          break;
        case "sources":
          update(answerId, { sources: e.data.sources });
          break;
        case "delta":
          update(answerId, (m) => ({ text: m.text + e.data.text }));
          break;
        case "final":
          update(answerId, {
            text: e.data.answer,
            status: e.data.status,
            citations: e.data.citations,
            warnings: e.data.warnings,
            pending: false,
          });
          break;
        case "error":
          update(answerId, { text: e.data.message, status: "error", pending: false });
          break;
      }
    };

    try {
      await streamChat(apiBase, { message: text, history }, onEvent, controller.signal);
      update(answerId, (m) =>
        m.pending ? { pending: false, status: "error", text: "The answer was cut off. Try asking again." } : {},
      );
    } catch (err) {
      if ((err as Error).name !== "AbortError") {
        const msg = err instanceof ChatError ? err.message : "Something went wrong. Try asking again.";
        update(answerId, { pending: false, status: "error", text: msg });
      }
    } finally {
      setBusy(false);
      abortRef.current = null;
    }
  }

  const startOver = () => {
    abortRef.current?.abort();
    setMessages([]);
    setBusy(false);
    inputRef.current?.focus();
  };

  if (!open) {
    return (
      <div class="root">
        <button class="launcher" type="button" aria-expanded="false" onClick={() => setOpen(true)}>
          <WaveIcon />
          Ask OLake
        </button>
      </div>
    );
  }

  return (
    <div class="root">
      <section
        class="panel"
        role="dialog"
        aria-label="Ask OLake"
        onKeyDown={(e) => e.key === "Escape" && setOpen(false)}
      >
        <header>
          <div class="titles">
            <h2>Ask OLake</h2>
            <p>Answers from the OLake docs and source code, with sources.</p>
          </div>
          {messages.length > 0 && (
            <button class="icon-button" type="button" onClick={startOver}>
              Start over
            </button>
          )}
          <button class="icon-button" type="button" aria-label="Close" onClick={() => setOpen(false)}>
            ✕
          </button>
        </header>

        <div class="log" ref={logRef} aria-live="polite">
          {messages.length === 0 ? (
            <div class="empty">
              <h3>What are you setting up?</h3>
              <p>Ask about sources, sync modes, catalogs, Fusion maintenance, deployment or an error you're seeing.</p>
              <div class="suggestions">
                {SUGGESTIONS.map((s) => (
                  <button class="suggestion" type="button" key={s} onClick={() => ask(s)}>
                    {s}
                  </button>
                ))}
              </div>
            </div>
          ) : (
            messages.map((m) => (
              <div class="turn" key={m.id}>
                {m.role === "user" ? (
                  <div class="user">{m.text}</div>
                ) : (
                  <Answer
                    message={m}
                    apiBase={apiBase}
                    onFeedback={(id, rating) => update(id, { feedback: rating })}
                  />
                )}
              </div>
            ))
          )}
        </div>

        <form
          onSubmit={(e) => {
            e.preventDefault();
            ask(draft);
          }}
        >
          <div class="composer">
            <textarea
              ref={inputRef}
              rows={1}
              value={draft}
              maxLength={2000}
              placeholder="Ask a question or paste an error"
              aria-label="Your question"
              onInput={(e) => setDraft((e.target as HTMLTextAreaElement).value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  ask(draft);
                }
              }}
            />
            <button class="send" type="submit" disabled={busy || !draft.trim()}>
              Ask
            </button>
          </div>
          <p class="fineprint">
            Answers can be wrong; check the linked sources. Don't paste passwords or keys. Need a person?{" "}
            <a href={SLACK} target="_blank" rel="noopener noreferrer">
              Ask on Slack
            </a>
            .
          </p>
        </form>
      </section>
    </div>
  );
}
