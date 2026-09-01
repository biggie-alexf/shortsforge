import { useCallback, useEffect, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { api, NetworkError } from "../api";
import { errText } from "../App";
import { Empty, Skeleton, StatusTimeline } from "../components";
import { fmtTime, GATE_LABEL, STATUS_LABEL } from "../labels";
import type { Batch, JobSummary, VideoFormat } from "../types";

interface NewJobRow {
  game: string;
  idea: string;
  format: VideoFormat;
}

const emptyRow = (): NewJobRow => ({ game: "", idea: "", format: "A" });

export default function BoardPage({ eventTick }: { eventTick: number }) {
  const [batches, setBatches] = useState<Batch[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [offline, setOffline] = useState(false);
  const [showModal, setShowModal] = useState(false);

  const load = useCallback(() => {
    api
      .get<Batch[]>("/api/batches")
      .then((b) => {
        setBatches(b);
        setError(null);
        setOffline(false);
      })
      .catch((e) => {
        if (e instanceof NetworkError) setOffline(true);
        else setError(errText(e));
      });
  }, []);

  useEffect(load, [load, eventTick]);

  return (
    <main className="page">
      <div className="page-head">
        <h1>Доска</h1>
        <div style={{ flex: 1 }} />
        <button className="btn btn--accent" onClick={() => setShowModal(true)}>
          Новый батч
        </button>
      </div>

      {offline && (
        <Empty>
          Нет соединения с сервером. Проверьте, что API запущен, — доска обновится автоматически.
        </Empty>
      )}
      {error && <div className="error-inline">{error}</div>}

      {!offline && batches === null && (
        <>
          <Skeleton height={120} />
          <div style={{ height: 16 }} />
          <Skeleton height={120} />
        </>
      )}

      {batches !== null && batches.length === 0 && (
        <Empty>Пока нет батчей. Нажмите «Новый батч», чтобы запустить первые три видео.</Empty>
      )}

      {batches?.map((b) => (
        <BatchRow key={b.id} batch={b} />
      ))}

      {showModal && (
        <NewBatchModal
          onClose={() => setShowModal(false)}
          onCreated={() => {
            setShowModal(false);
            load();
          }}
        />
      )}
    </main>
  );
}

function BatchRow({ batch }: { batch: Batch }) {
  return (
    <section className="batch">
      <div className="batch__head">
        <span className="batch__title">{batch.title || "Батч"}</span>
        <span className="batch__meta">
          {batch.id} · {fmtTime(batch.created_at)} · {batch.jobs.length} видео
        </span>
      </div>
      <div className="batch__jobs">
        {batch.jobs.map((j) => (
          <JobCard key={j.id} job={j} />
        ))}
      </div>
    </section>
  );
}

function JobCard({ job }: { job: JobSummary }) {
  const navigate = useNavigate();
  return (
    <div className="jobcard" onClick={() => navigate(`/jobs/${job.id}`)}>
      <div className="jobcard__top">
        <span className="jobcard__game">{job.game}</span>
        <span className="badge badge--format">{job.format}</span>
      </div>
      <div className="jobcard__idea">{job.idea}</div>
      <StatusTimeline status={job.status} />
      <div className="jobcard__badges">
        <span className="badge badge--status">{STATUS_LABEL[job.status]}</span>
        {job.open_gate && <span className="badge badge--gate">ГЕЙТ: {GATE_LABEL[job.open_gate]}</span>}
        {job.status === "failed" && (
          <span className="badge badge--error" title={job.error ?? ""}>
            ОШИБКА
          </span>
        )}
      </div>
    </div>
  );
}

function NewBatchModal({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const [rows, setRows] = useState<NewJobRow[]>([emptyRow(), emptyRow(), emptyRow()]);
  const [title, setTitle] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const setRow = (i: number, patch: Partial<NewJobRow>) => {
    setRows((r) => r.map((row, j) => (j === i ? { ...row, ...patch } : row)));
  };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const jobs = rows.filter((r) => r.game.trim() && r.idea.trim());
    if (jobs.length === 0) {
      setError("Заполните хотя бы одну строку: игра и идея.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await api.post<{ id: string }>("/api/batches", {
        title: title.trim() || undefined,
        jobs: jobs.map((r) => ({ game: r.game.trim(), idea: r.idea.trim(), format: r.format })),
      });
      onCreated();
    } catch (err) {
      setError(errText(err));
      setBusy(false);
    }
  };

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <form className="modal" onClick={(e) => e.stopPropagation()} onSubmit={submit}>
        <h2>Новый батч</h2>
        <label className="field">
          <span>Название (необязательно)</span>
          <input type="text" value={title} onChange={(e) => setTitle(e.target.value)} />
        </label>
        {rows.map((row, i) => (
          <div className="modal__row" key={i}>
            <input
              type="text"
              placeholder="Игра"
              value={row.game}
              onChange={(e) => setRow(i, { game: e.target.value })}
            />
            <input
              type="text"
              placeholder="Идея"
              value={row.idea}
              onChange={(e) => setRow(i, { idea: e.target.value })}
            />
            <select
              value={row.format}
              onChange={(e) => setRow(i, { format: e.target.value as VideoFormat })}
            >
              <option value="A">A</option>
              <option value="B">B</option>
            </select>
            <button
              type="button"
              className="btn btn--sm btn--ghost"
              title="Убрать строку"
              disabled={rows.length <= 1}
              onClick={() => setRows((r) => r.filter((_, j) => j !== i))}
            >
              ×
            </button>
          </div>
        ))}
        <button
          type="button"
          className="btn btn--sm"
          disabled={rows.length >= 5}
          onClick={() => setRows((r) => [...r, emptyRow()])}
        >
          + Добавить строку
        </button>
        {error && <div className="error-inline">{error}</div>}
        <div className="modal__actions">
          <button type="button" className="btn" onClick={onClose}>
            Отмена
          </button>
          <button className="btn btn--accent" disabled={busy}>
            {busy ? "Создание…" : "Создать батч"}
          </button>
        </div>
      </form>
    </div>
  );
}
