import { useCallback, useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { api, NetworkError } from "../api";
import { errText } from "../App";
import { Empty, Skeleton, Spinner, StatusTimeline } from "../components";
import { fmtDur, fmtTime, GATE_LABEL, ROLE_LABEL, STATUS_LABEL, STEP_LABEL } from "../labels";
import { useToast } from "../toast";
import type { Block, JobDetail, Render, SfEvent } from "../types";
import Chat from "./Chat";

interface Props {
  eventTick: number;
  lastEvent: SfEvent | null;
}

export default function JobPage({ eventTick, lastEvent }: Props) {
  const { id } = useParams<{ id: string }>();
  const [job, setJob] = useState<JobDetail | null>(null);
  const [offline, setOffline] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const toast = useToast();

  const load = useCallback(() => {
    if (!id) return;
    api
      .get<JobDetail>(`/api/jobs/${id}`)
      .then((j) => {
        setJob(j);
        setOffline(false);
        setError(null);
      })
      .catch((e) => {
        if (e instanceof NetworkError) setOffline(true);
        else setError(errText(e));
      });
  }, [id]);

  useEffect(load, [load]);

  // live-обновления: рефетч, если событие про этот джоб (или без job_id)
  useEffect(() => {
    if (!lastEvent || eventTick === 0) return;
    if (!lastEvent.job_id || lastEvent.job_id === id) load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [eventTick]);

  const act = async (fn: () => Promise<unknown>, okMsg?: string) => {
    try {
      await fn();
      if (okMsg) toast.push("ok", "ГОТОВО", okMsg);
      load();
    } catch (e) {
      toast.push("error", "ОШИБКА", errText(e));
    }
  };

  if (!id) return null;

  return (
    <main className="page">
      {offline && <Empty>Нет соединения с сервером. Данные появятся, когда API поднимется.</Empty>}
      {error && <div className="error-inline">{error}</div>}
      {!offline && !job && !error && (
        <>
          <Skeleton height={60} />
          <div style={{ height: 16 }} />
          <Skeleton height={400} />
        </>
      )}
      {job && (
        <>
          <div className="job-head">
            <h1>{job.game}</h1>
            <span className="badge badge--format">{job.format}</span>
            <span className="jobcard__idea job-head__idea">{job.idea}</span>
            <span className="badge badge--status">{STATUS_LABEL[job.status]}</span>
            {job.open_gate && (
              <span className="badge badge--gate">ГЕЙТ: {GATE_LABEL[job.open_gate]}</span>
            )}
            <span className="badge">v{job.current_version}</span>
            <div style={{ minWidth: 220 }}>
              <StatusTimeline status={job.status} />
            </div>
          </div>
          <div className="job-layout">
            <div>
              <JobContent job={job} act={act} />
            </div>
            <Chat jobId={id} eventTick={eventTick} lastEvent={lastEvent} />
          </div>
        </>
      )}
    </main>
  );
}

type Act = (fn: () => Promise<unknown>, okMsg?: string) => Promise<void>;

function JobContent({ job, act }: { job: JobDetail; act: Act }) {
  switch (job.status) {
    case "gate_script":
      return <ScriptGate job={job} act={act} />;
    case "gate_clips":
      return <ClipsGate job={job} act={act} />;
    case "gate_rough":
      return <RenderGate job={job} act={act} kind="rough" />;
    case "gate_master":
    case "done":
      return <RenderGate job={job} act={act} kind="master" />;
    case "failed":
      return <FailedView job={job} act={act} />;
    default:
      return <ProgressView job={job} />;
  }
}

/* ---------- G1: сценарий ---------- */

function ScriptGate({ job, act }: { job: JobDetail; act: Act }) {
  const s = job.script;
  if (!s) return <Empty>Сценарий ещё не готов.</Empty>;
  return (
    <section className="panel">
      <div className="panel__head">
        <h2>СЦЕНАРИЙ · v{s.version}</h2>
        <button
          className="btn btn--accent"
          onClick={() => act(() => api.post(`/api/jobs/${job.id}/gates/script/approve`), "Сценарий апрувнут")}
        >
          Апрув сценария
        </button>
      </div>
      <div className="panel__body">
        <div className="script-title">{s.title || "(без заголовка)"}</div>
        {s.hook_pattern && <div className="script-meta">hook: {s.hook_pattern}</div>}
        {s.blocks.map((b) => (
          <div className="block-row" key={b.id}>
            <div className="block-row__num">{b.ordinal}</div>
            <div className="block-row__body">
              <span className="badge badge--role">{ROLE_LABEL[b.role] ?? b.role.toUpperCase()}</span>
              {b.status === "needs_footage" && <span className="badge badge--warn"> NEEDS_FOOTAGE</span>}
              <div className="block-row__text">{b.text_en}</div>
              {b.frame_desc && <div className="block-row__frame">{b.frame_desc}</div>}
              {b.search_keys.length > 0 && (
                <div className="chips">
                  {b.search_keys.map((k, i) => (
                    <span className="chip" key={i}>
                      {k}
                    </span>
                  ))}
                </div>
              )}
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

/* ---------- прогресс между гейтами ---------- */

function ProgressView({ job }: { job: JobDetail }) {
  return (
    <section className="panel">
      <div className="panel__head">
        <h2>ПАЙПЛАЙН РАБОТАЕТ</h2>
      </div>
      <div className="panel__body">
        <div className="progress-hint">
          <Spinner />
          <span>{STATUS_LABEL[job.status]} — экран обновится сам, когда откроется гейт.</span>
        </div>
        <StepRuns job={job} />
      </div>
    </section>
  );
}

function StepRuns({ job }: { job: JobDetail }) {
  if (job.step_runs.length === 0) {
    return <Empty>Шаги ещё не запускались.</Empty>;
  }
  return (
    <div className="steps">
      {job.step_runs.map((s, i) => (
        <div className="steps__row" key={i}>
          <span
            className={`steps__status ${
              s.ok === null ? "steps__status--run" : s.ok ? "steps__status--ok" : "steps__status--fail"
            }`}
          >
            {s.ok === null ? "RUN" : s.ok ? "OK" : "FAIL"}
          </span>
          <span className="steps__name">{STEP_LABEL[s.step] ?? s.step}</span>
          <span className="steps__detail" title={s.detail}>
            {s.detail}
          </span>
          <span className="steps__time">{fmtTime(s.started_at)}</span>
        </div>
      ))}
    </div>
  );
}

function FailedView({ job, act }: { job: JobDetail; act: Act }) {
  return (
    <section className="panel">
      <div className="panel__head">
        <h2 style={{ color: "var(--accent)" }}>ОШИБКА</h2>
        <button
          className="btn btn--accent"
          onClick={() => act(() => api.post(`/api/jobs/${job.id}/retry`), "Перезапуск поставлен в очередь")}
        >
          Перезапустить
        </button>
      </div>
      <div className="panel__body">
        {job.error && <div className="error-inline" style={{ marginBottom: 12 }}>{job.error}</div>}
        <StepRuns job={job} />
      </div>
    </section>
  );
}

/* ---------- G2: раскадровка ---------- */

function ClipsGate({ job, act }: { job: JobDetail; act: Act }) {
  const s = job.script;
  if (!s) return <Empty>Раскадровка ещё не готова.</Empty>;
  return (
    <section className="panel">
      <div className="panel__head">
        <h2>РАСКАДРОВКА · выбор клипов</h2>
        <button
          className="btn btn--accent"
          onClick={() => act(() => api.post(`/api/jobs/${job.id}/gates/clips/approve`), "Клипы апрувнуты")}
        >
          Апрув клипов
        </button>
      </div>
      <div className="panel__body">
        {s.blocks.map((b) => (
          <BlockClips key={b.id} jobId={job.id} block={b} act={act} />
        ))}
      </div>
    </section>
  );
}

function BlockClips({ jobId, block, act }: { jobId: string; block: Block; act: Act }) {
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState(false);
  const toast = useToast();

  const order = async () => {
    const q = query.trim();
    if (!q) return;
    setBusy(true);
    const isUrl = /^https?:\/\//i.test(q) || /youtu\.?be/i.test(q);
    try {
      await api.post(`/api/jobs/${jobId}/blocks/${block.id}/candidates`, isUrl ? { yt_url: q } : { query: q });
      toast.push("ok", "В ОЧЕРЕДИ", "Дозаказ кандидатов запущен");
      setQuery("");
    } catch (e) {
      toast.push("error", "ОШИБКА", errText(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="block-row">
      <div className="block-row__num">{block.ordinal}</div>
      <div className="block-row__body">
        <span className="badge badge--role">{ROLE_LABEL[block.role] ?? block.role.toUpperCase()}</span>
        <div className="block-row__text">{block.text_en}</div>
        {block.candidates.length > 0 && (
          <div className="cands">
            {block.candidates.map((c) => (
              <button
                key={c.id}
                type="button"
                className={`cand${c.chosen ? " cand--chosen" : ""}`}
                title={c.donor ? `${c.donor.yt_channel} — ${c.donor.yt_title}` : c.manual_note}
                onClick={() =>
                  act(() => api.post(`/api/jobs/${jobId}/blocks/${block.id}/choose`, { candidate_id: c.id }))
                }
              >
                <video src={c.url} muted loop autoPlay playsInline />
                <span className="cand__meta">
                  <span>#{c.rank}</span>
                  <span>{fmtDur(c.duration)}</span>
                  <span>m{c.motion_score.toFixed(2)}</span>
                </span>
              </button>
            ))}
          </div>
        )}
        {block.candidates.length === 0 && block.status !== "needs_footage" && (
          <div className="block-row__frame">Кандидатов пока нет.</div>
        )}
        {block.status === "needs_footage" && (
          <div className="needs-footage">
            <div className="needs-footage__title">NEEDS_FOOTAGE — под этот блок не нашлось кадров</div>
            <form
              onSubmit={(e) => {
                e.preventDefault();
                order();
              }}
            >
              <input
                type="text"
                placeholder="Поисковый запрос или ссылка на YouTube"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
              <button className="btn" disabled={busy || !query.trim()}>
                {busy ? "…" : "Дозаказать"}
              </button>
            </form>
          </div>
        )}
      </div>
    </div>
  );
}

/* ---------- G3/G4: плеер + qc + версии ---------- */

function RenderGate({ job, act, kind }: { job: JobDetail; act: Act; kind: "rough" | "master" }) {
  const renders = job.renders
    .filter((r) => r.kind === kind)
    .sort((a, b) => b.version - a.version);
  const [activeId, setActiveId] = useState<string | null>(null);
  const active: Render | undefined = renders.find((r) => r.id === activeId) ?? renders[0];
  const isMaster = kind === "master";
  const done = job.status === "done";

  if (!active) return <Empty>Рендер ещё не готов.</Empty>;

  return (
    <section className="panel">
      <div className="panel__head">
        <h2>
          {isMaster ? "МАСТЕР" : "ЧЕРНОВАЯ СБОРКА"} · v{active.version}
        </h2>
        {!done && (
          <button
            className="btn btn--accent"
            onClick={() =>
              act(
                () => api.post(`/api/jobs/${job.id}/gates/${kind}/approve`),
                isMaster ? "Мастер апрувнут" : "Черновик апрувнут"
              )
            }
          >
            {isMaster ? "Апрув мастера" : "Апрув черновика"}
          </button>
        )}
        {done && <span className="badge badge--ok">ГОТОВО</span>}
      </div>
      <div className="panel__body">
        <div className="render-layout">
          <div className="player">
            <video key={active.id} src={active.url} controls poster={active.preview_url || undefined} playsInline />
          </div>
          <div className="render-side">
            <table className="kv">
              <tbody>
                <tr>
                  <td>Длительность</td>
                  <td>{fmtDur(numQc(active.qc, "duration"))}</td>
                </tr>
                <tr>
                  <td>LUFS</td>
                  <td>{numQc(active.qc, "lufs")?.toFixed(1) ?? "—"}</td>
                </tr>
                {job.voice && (
                  <tr>
                    <td>Озвучка</td>
                    <td>
                      {fmtDur(job.voice.duration)}
                      {job.voice.is_mock ? " · mock" : ""}
                    </td>
                  </tr>
                )}
                <tr>
                  <td>Создан</td>
                  <td>{fmtTime(active.created_at)}</td>
                </tr>
              </tbody>
            </table>

            {isMaster && (
              <>
                <div style={{ marginTop: 16 }}>
                  <a href={active.url} download className="btn btn--accent" style={{ display: "inline-block" }}>
                    Скачать mp4
                  </a>
                </div>

                {renders.length > 1 && (
                  <div style={{ marginTop: 18 }}>
                    <div className="meta-block__label">ВЕРСИИ</div>
                    {renders.map((r) => (
                      <div
                        key={r.id}
                        className={`version-row${r.id === active.id ? " version-row--active" : ""}`}
                        onClick={() => setActiveId(r.id)}
                      >
                        <span className="version-row__v">v{r.version}</span>
                        <span className="version-row__log">{r.changelog || "без changelog"}</span>
                      </div>
                    ))}
                  </div>
                )}

                {job.script && (
                  <div className="meta-block" style={{ marginTop: 18 }}>
                    <MetaField label="TITLE" value={job.script.title} />
                    <MetaField label="DESCRIPTION" value={job.script.description} />
                  </div>
                )}
              </>
            )}
            {!isMaster && active.changelog && (
              <div className="meta-block" style={{ marginTop: 14 }}>
                <MetaField label="CHANGELOG" value={active.changelog} />
              </div>
            )}
          </div>
        </div>
      </div>
    </section>
  );
}

function numQc(qc: Record<string, number | string>, key: string): number | undefined {
  const v = qc?.[key];
  return typeof v === "number" ? v : undefined;
}

function MetaField({ label, value }: { label: string; value: string }) {
  const toast = useToast();
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value);
      toast.push("ok", "СКОПИРОВАНО", label.toLowerCase());
    } catch {
      toast.push("error", "ОШИБКА", "Буфер обмена недоступен");
    }
  };
  return (
    <div className="meta-block__field">
      <div className="meta-block__label">
        <span>{label}</span>
        <button className="btn btn--sm btn--ghost" onClick={copy} disabled={!value}>
          Копировать
        </button>
      </div>
      <div className="meta-block__value">{value || "—"}</div>
    </div>
  );
}
