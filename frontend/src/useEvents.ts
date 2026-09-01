import { useEffect, useRef } from "react";
import type { SfEvent } from "./types";

const EVENT_TYPES = ["job_status", "gate_open", "render_ready", "chat", "needs_footage", "error"];

/** Подписка на SSE /api/events/stream. Хендлер получает каждое событие. */
export function useEvents(onEvent: (ev: SfEvent) => void) {
  const handler = useRef(onEvent);
  handler.current = onEvent;

  useEffect(() => {
    let es: EventSource | null = null;
    let closed = false;
    let retryTimer: number | undefined;

    const parse = (raw: string) => {
      try {
        const ev = JSON.parse(raw) as SfEvent;
        if (ev && ev.type) handler.current(ev);
      } catch {
        /* не JSON — игнор */
      }
    };

    const connect = () => {
      if (closed) return;
      es = new EventSource("/api/events/stream");
      es.onmessage = (m) => parse(m.data);
      // на случай, если бэк шлёт именованные события
      for (const t of EVENT_TYPES) {
        es.addEventListener(t, (m) => parse((m as MessageEvent).data));
      }
      es.onerror = () => {
        es?.close();
        retryTimer = window.setTimeout(connect, 5000);
      };
    };

    connect();
    return () => {
      closed = true;
      if (retryTimer) window.clearTimeout(retryTimer);
      es?.close();
    };
  }, []);
}
