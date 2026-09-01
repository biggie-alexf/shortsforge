# ShortForge — API-спека (контракт между фронтом и бэком)

База: `/api`. Аутентификация: сессионная кука `sf_session` (HttpOnly). Все ответы JSON.
401 → фронт показывает логин. Ошибки: `{"error": "текст"}` с честным HTTP-кодом.

## Auth
- `POST /api/auth/login` {login, password} → {user:{id,login}} + кука
- `POST /api/auth/logout` → {}
- `GET  /api/auth/me` → {user:{id,login}} | 401

## Batches / Jobs
- `GET  /api/batches` → [{id,title,created_at,jobs:[JobSummary]}] (последние 50)
- `POST /api/batches` {title?, jobs:[{game, idea, format}]} → {id} — создаёт батч + N VideoJob, ставит их в очередь
- `GET  /api/jobs/{id}` → JobDetail (см. ниже)
- `POST /api/jobs/{id}/retry` → {} — перезапуск после FAILED (с текущего шага)

JobSummary: {id, game, idea, format, status, current_version, open_gate: GateType|null, error}
JobDetail: JobSummary + {
  batch_id, gates:[{type,status,approved_by,approved_at}],
  script: {version,title,description,hook_pattern,blocks:[{id,ordinal,role,text_en,frame_desc,search_keys,fx,status,t_start,t_end,
            candidates:[{id,rank,url,duration,motion_score,chosen,manual_note,donor:{yt_video_id,yt_channel,yt_title,is_mock}}]}]},
  voice: {url, duration, is_mock} | null,
  renders: [{id,kind,version,url,preview_url,changelog,qc,created_at}],
  step_runs: [{step,ok,detail,started_at,finished_at}] (последние 20)
}

## Gates
- `POST /api/jobs/{id}/gates/{type}/approve` → {} — апрув; двигает job дальше (type: script|clips|rough|master)
  - для `clips`: 409 если есть блок без выбранного кандидата и не NEEDS_FOOTAGE-решения
- `POST /api/jobs/{id}/blocks/{block_id}/choose` {candidate_id} → {}
- `POST /api/jobs/{id}/blocks/{block_id}/candidates` {query?|yt_url?} → {task:"queued"} — дозаказ кандидатов (новый поиск или конкретное видео)

## Chat (на каждом гейте, per-job)
- `GET  /api/jobs/{id}/chat` → [{id,role,text,extra,created_at,user}]
- `POST /api/jobs/{id}/chat` {text} → {message_id} — агент отвечает асинхронно (события в SSE)
- `POST /api/jobs/{id}/chat/{message_id}/confirm` → {} — выполнить предложенный план
- `POST /api/jobs/{id}/chat/{message_id}/reject` → {}

## Media
- `GET /media/...` — статика из DATA_DIR (сниппеты, wav, mp4, превью). Под сессией.

## Settings
- `GET  /api/settings` → [{key,value_masked,secret,updated_at}] — секреты маскированы (****abcd)
- `PUT  /api/settings/{key}` {value} → {}
- `GET  /api/settings/providers` → {llm:"real|mock", tts:"real|mock", youtube:"real|mock"} — что сейчас активно

## Users (владелец)
- `GET /api/users`, `POST /api/users` {login,password}, `DELETE /api/users/{id}`

## События
- `GET /api/events/stream` — SSE. Каждое событие: {id,type,job_id,batch_id,payload,created_at}
  Типы: job_status, gate_open, render_ready, chat (ответ агента/план), needs_footage, error
- `GET /api/events?after={id}` — поллинг-фолбэк

## Статусная машина (models.JobStatus)
queued → scripting → gate_script → hunting → cutting → gate_clips → voicing →
rough_render → gate_rough → master_render → gate_master → done; failed из любого шага.
Rework на гейте возвращает на соответствующий шаг, не сбрасывая остальное.
Очередь: arq (Redis). Задачи: run_writer(job_id), run_hunter(job_id), run_cutter(job_id),
run_voicer(job_id), run_rough(job_id), run_master(job_id), agent_reply(job_id, message_id),
apply_plan(job_id, message_id), extra_candidates(job_id, block_id, query|yt_url).
Оркестрация: каждая задача в конце сама решает следующий переход (см. pipeline/flow.py).
