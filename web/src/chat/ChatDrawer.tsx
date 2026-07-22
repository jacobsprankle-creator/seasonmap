/**
 * AI assistant drawer (Phase 2). POSTs to the worker's /api/chat; the worker
 * runs the Anthropic tool loop server-side and returns
 * {answer_markdown, pins, layer, date} — pins land on the map via App.
 */
import { useRef, useState } from "react";
import { CHAT_API } from "../config";
import type { Pin } from "../map/MapView";

interface Msg {
  role: "user" | "assistant";
  content: string;
}

export interface HighlightSpec {
  layer: string;
  where: { prop: string; equals?: string | number; min?: number; max?: number }[];
}

/** Tiny markdown renderer for chat bubbles: escapes HTML first, then applies
 * bold/italic/code/headers/lists/rules. No dependency, no raw HTML pass-through. */
function renderMd(src: string): string {
  const esc = src.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  return esc
    .split("\n")
    .map((line) => {
      let l = line;
      if (/^\s*(-{3,}|\*{3,})\s*$/.test(l)) return `<div class="md-hr"></div>`;
      const h = l.match(/^\s*#{1,4}\s+(.*)$/);
      if (h) l = `<span class="md-h">${h[1]}</span>`;
      const li = l.match(/^\s*[-*]\s+(.*)$/);
      if (li) l = `<span class="md-li">•</span>${li[1]}`;
      l = l
        .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
        .replace(/\*([^*]+)\*/g, "<i>$1</i>")
        .replace(/`([^`]+)`/g, "<code>$1</code>");
      return l;
    })
    .join("<br/>")
    .replace(/(<br\/>){3,}/g, "<br/><br/>");
}

export interface ChatContext {
  layer: string;
  layer_label: string;
  date: string | null;
  filters: string | null;
  view: { lat: number; lng: number; zoom?: number } | null;
  click: { lat: number; lng: number; feature: Record<string, unknown> | null } | null;
}

interface Props {
  onPins: (pins: Pin[]) => void;
  onHighlights?: (highlights: HighlightSpec[]) => void;
  onSwitchLayer?: (layerId: string) => void;
  context: ChatContext;
}

export function ChatDrawer({ onPins, onHighlights, onSwitchLayer, context }: Props) {
  const [open, setOpen] = useState(false);
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const scroller = useRef<HTMLDivElement>(null);

  const send = async () => {
    const q = input.trim();
    if (!q || busy) return;
    const history = [...messages, { role: "user" as const, content: q }];
    setMessages(history);
    setInput("");
    setBusy(true);
    try {
      const resp = await fetch(CHAT_API, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ messages: history, context }),
      });
      const body = await resp.json();
      if (!resp.ok) throw new Error(body.error ?? `HTTP ${resp.status}`);
      setMessages((m) => [...m, { role: "assistant", content: body.answer_markdown ?? "(no answer)" }]);
      if (Array.isArray(body.pins) && body.pins.length) onPins(body.pins as Pin[]);
      if (Array.isArray(body.highlights) && body.highlights.length) {
        onHighlights?.(body.highlights as HighlightSpec[]);
      } else if (body.layer && body.layer !== context.layer) {
        // The answer came from a different layer — take the map there.
        onSwitchLayer?.(body.layer as string);
      }
    } catch (err) {
      setMessages((m) => [...m, { role: "assistant", content: `Something broke: ${String(err)}` }]);
    } finally {
      setBusy(false);
      requestAnimationFrame(() =>
        scroller.current?.scrollTo({ top: scroller.current.scrollHeight })
      );
    }
  };

  if (!open) {
    return (
      <button className="chat-fab" title="Ask the map" onClick={() => setOpen(true)}>
        💬
      </button>
    );
  }

  return (
    <div className="chat-drawer">
      <div className="chat-head">
        <span>
          Ask the map
          <span className="chat-ctx">
            {" "}
            · {context.layer_label}
            {context.click?.feature && (context.click.feature as any).name
              ? ` · ${(context.click.feature as any).name}`
              : ""}
          </span>
        </span>
        <button onClick={() => setOpen(false)} aria-label="Close">
          ×
        </button>
      </div>
      <div className="chat-msgs" ref={scroller}>
        {messages.length === 0 && (
          <div className="chat-hint">
            Try: “Where within 3 hours of Charlotte will freeze first?” ·
            “When is first frost likely in Asheville?” · “Summarize frost
            progress in the Southeast.”
          </div>
        )}
        {messages.map((m, i) =>
          m.role === "assistant" ? (
            <div
              key={i}
              className="chat-msg chat-assistant"
              dangerouslySetInnerHTML={{ __html: renderMd(m.content) }}
            />
          ) : (
            <div key={i} className="chat-msg chat-user">
              {m.content}
            </div>
          )
        )}
        {busy && (
          <div className="chat-msg chat-assistant chat-typing" aria-label="Thinking">
            <span /><span /><span />
          </div>
        )}
      </div>
      <form
        className="chat-input"
        onSubmit={(e) => {
          e.preventDefault();
          void send();
        }}
      >
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Ask about frost, snow, seasons…"
          disabled={busy}
        />
        <button type="submit" disabled={busy || !input.trim()}>
          →
        </button>
      </form>
    </div>
  );
}
