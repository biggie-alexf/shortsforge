import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { api } from "../api";
import { errText } from "../App";
import { fmtTime } from "../labels";
import { useToast } from "../toast";
import type { ChatMessage, PlanStatus, SfEvent } from "../types";

const PLAN_STATUS_LABEL: Record<PlanStatus, string> = {
  proposed: "ПРЕДЛОЖЕН",
  confirmed: "ВЫПОЛНЯЕТСЯ",
  executed: "ВЫПОЛНЕН",
  rejected: "ОТКЛОНЁН",
};

interface Props {
  jobId: string;
  eventTick: number;
  lastEvent: SfEvent | null;
}

export default function Chat({ jobId, eventTick, lastEvent }: Props) {
  const [messages, setMessages] = useState<ChatMessage[] | null>(null);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const feedRef = useRef<HTMLDivElement>(null);
  const toast = useToast();

  const load = useCallback(() => {
    api
      .get<ChatMessage[]>(`/api/jobs/${jobId}/chat`)
      .then(setMessages)
      .catch(() => {
        /* грациозно: чат появится, когда API поднимется */
      });
  }, [jobId]);

  useEffect(load, [load]);

  useEffect(() => {
    if (!lastEvent || eventTick === 0) return;
    if (lastEvent.type === "chat" && (!lastEvent.job_id || lastEvent.job_id === jobId)) load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [eventTick]);

  // автоскролл вниз при новых сообщениях
  useEffect(() => {
    const el = feedRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages]);

  const send = async (e: FormEvent) => {
    e.preventDefault();
    const t = text.trim();
    if (!t) return;
    setBusy(true);
    try {
      await api.post<{ message_id: string }>(`/api/jobs/${jobId}/chat`, { text: t });
      setText("");
      load();
    } catch (err) {
      toast.push("error", "ОШИБКА", errText(err));
    } finally {
      setBusy(false);
    }
  };

  const planAction = async (messageId: string, action: "confirm" | "reject") => {
    try {
      await api.post(`/api/jobs/${jobId}/chat/${messageId}/${action}`);
      load();
    } catch (err) {
      toast.push("error", "ОШИБКА", errText(err));
    }
  };

  return (
    <aside className="chat">
      <div className="chat__head">ЧАТ ПРАВОК</div>
      <div className="chat__feed" ref={feedRef}>
        {messages === null && <div className="block-row__frame">Загрузка чата…</div>}
        {messages !== null && messages.length === 0 && (
          <div className="block-row__frame">
            Сообщений пока нет. Опишите правку — агент предложит план.
          </div>
        )}
        {messages?.map((m) => (
          <div key={m.id} className={`msg msg--${m.role}`}>
            <div className="msg__meta">
              {m.role === "user" ? m.user?.login ?? "вы" : m.role === "agent" ? "агент" : "система"} ·{" "}
              {fmtTime(m.created_at)}
            </div>
            {m.text && <div className="msg__bubble">{m.text}</div>}
            {m.role === "agent" && m.extra?.plan && m.extra.plan.length > 0 && (
              <PlanCard
                plan={m.extra.plan}
                status={m.extra.plan_status ?? "proposed"}
                onConfirm={() => planAction(m.id, "confirm")}
                onReject={() => planAction(m.id, "reject")}
              />
            )}
          </div>
        ))}
      </div>
      <form className="chat__form" onSubmit={send}>
        <input
          type="text"
          placeholder="Правка для агента…"
          value={text}
          onChange={(e) => setText(e.target.value)}
        />
        <button className="btn btn--accent" disabled={busy || !text.trim()}>
          {busy ? "…" : "Отправить"}
        </button>
      </form>
    </aside>
  );
}

function PlanCard({
  plan,
  status,
  onConfirm,
  onReject,
}: {
  plan: { tool: string; args: Record<string, unknown>; why: string }[];
  status: PlanStatus;
  onConfirm: () => void;
  onReject: () => void;
}) {
  return (
    <div className="plan">
      <div className="plan__head">
        <span className="plan__title">ПЛАН ПРАВОК</span>
        <span className="badge badge--gate">{PLAN_STATUS_LABEL[status] ?? status}</span>
      </div>
      <ol>
        {plan.map((step, i) => (
          <li key={i}>
            <span className="plan__tool">{step.tool}</span>
            {step.why && <span className="plan__why"> — {step.why}</span>}
          </li>
        ))}
      </ol>
      {status === "proposed" && (
        <div className="plan__actions">
          <button className="btn btn--sm btn--accent" onClick={onConfirm}>
            Выполнить
          </button>
          <button className="btn btn--sm" onClick={onReject}>
            Отклонить
          </button>
        </div>
      )}
    </div>
  );
}
