import type { GateType, JobStatus } from "./types";

export const STATUS_LABEL: Record<JobStatus, string> = {
  queued: "В очереди",
  scripting: "Сценарий пишется",
  gate_script: "Гейт: сценарий",
  hunting: "Охота за кадрами",
  cutting: "Нарезка клипов",
  gate_clips: "Гейт: клипы",
  voicing: "Озвучка",
  rough_render: "Черновая сборка",
  gate_rough: "Гейт: черновик",
  master_render: "Рендер мастера",
  gate_master: "Гейт: мастер",
  done: "Готово",
  failed: "Ошибка",
};

export const GATE_LABEL: Record<GateType, string> = {
  script: "СЦЕНАРИЙ",
  clips: "КЛИПЫ",
  rough: "ЧЕРНОВИК",
  master: "МАСТЕР",
};

export const STEP_LABEL: Record<string, string> = {
  writer: "Сценарист",
  hunter: "Охотник",
  cutter: "Резчик",
  voicer: "Озвучка",
  rough_mixer: "Черновая сборка",
  master_mixer: "Мастер-сборка",
};

export const ROLE_LABEL: Record<string, string> = {
  hook: "HOOK",
  setup: "SETUP",
  evidence: "EVIDENCE",
  cta: "CTA",
  twist: "TWIST",
  loop: "LOOP",
  punch: "PUNCH",
};

// 6 стадий таймлайна: сценарий → охота → клипы → озвучка → черновик → мастер
export const STAGES = ["Сценарий", "Охота", "Клипы", "Озвучка", "Черновик", "Мастер"] as const;

// индекс текущей стадии для статуса (-1 = ещё не началось)
export function stageIndex(status: JobStatus): number {
  switch (status) {
    case "queued":
      return -1;
    case "scripting":
    case "gate_script":
      return 0;
    case "hunting":
      return 1;
    case "cutting":
    case "gate_clips":
      return 2;
    case "voicing":
      return 3;
    case "rough_render":
    case "gate_rough":
      return 4;
    case "master_render":
    case "gate_master":
      return 5;
    case "done":
      return 6;
    case "failed":
      return -1;
  }
}

export function fmtTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "—";
  return d.toLocaleString("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function fmtDur(sec: number | null | undefined): string {
  if (sec === null || sec === undefined) return "—";
  return `${sec.toFixed(1)} с`;
}
