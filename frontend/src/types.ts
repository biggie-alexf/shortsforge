// Типы строго по docs/api-spec.md и backend/shortforge/models.py

export type VideoFormat = "A" | "B" | "C";

export type JobStatus =
  | "queued"
  | "scripting"
  | "gate_script"
  | "hunting"
  | "cutting"
  | "gate_clips"
  | "voicing"
  | "rough_render"
  | "gate_rough"
  | "master_render"
  | "gate_master"
  | "done"
  | "failed";

export type GateType = "script" | "clips" | "rough" | "master";
export type GateStatus = "pending" | "open" | "approved" | "rework";
export type BlockStatus = "ok" | "needs_footage";

export interface User {
  id: string;
  login: string;
}

export interface JobSummary {
  id: string;
  game: string;
  idea: string;
  format: VideoFormat;
  status: JobStatus;
  current_version: number;
  open_gate: GateType | null;
  error: string | null;
}

export interface Batch {
  id: string;
  title: string;
  created_at: string;
  jobs: JobSummary[];
}

export interface Donor {
  yt_video_id: string;
  yt_channel: string;
  yt_title: string;
  is_mock: boolean;
}

export interface Candidate {
  id: string;
  rank: number;
  url: string;
  duration: number;
  motion_score: number;
  chosen: boolean;
  manual_note: string;
  donor: Donor | null;
}

export interface Block {
  id: string;
  ordinal: number;
  role: string;
  text_en: string;
  frame_desc: string;
  search_keys: string[];
  fx: unknown[];
  status: BlockStatus;
  t_start: number | null;
  t_end: number | null;
  candidates: Candidate[];
}

export interface Script {
  version: number;
  title: string;
  description: string;
  hook_pattern: string;
  blocks: Block[];
}

export interface Voice {
  url: string;
  duration: number;
  is_mock: boolean;
}

export interface Render {
  id: string;
  kind: "rough" | "master";
  version: number;
  url: string;
  preview_url: string;
  changelog: string;
  qc: Record<string, number | string>;
  created_at: string;
}

export interface Gate {
  type: GateType;
  status: GateStatus;
  approved_by: string | null;
  approved_at: string | null;
}

export interface StepRun {
  step: string;
  ok: boolean | null;
  detail: string;
  started_at: string;
  finished_at: string | null;
}

export interface JobDetail extends JobSummary {
  batch_id: string;
  gates: Gate[];
  script: Script | null;
  voice: Voice | null;
  renders: Render[];
  step_runs: StepRun[];
}

export type PlanStatus = "proposed" | "confirmed" | "executed" | "rejected";

export interface PlanStep {
  tool: string;
  args: Record<string, unknown>;
  why: string;
}

export interface ChatExtra {
  plan?: PlanStep[];
  plan_status?: PlanStatus;
  [k: string]: unknown;
}

export interface ChatMessage {
  id: string;
  role: "user" | "agent" | "system";
  text: string;
  extra: ChatExtra;
  created_at: string;
  user: User | null;
}

export interface SettingRow {
  key: string;
  value_masked: string;
  secret: boolean;
  updated_at: string | null;
}

export type ProviderMode = "real" | "mock";

export interface Providers {
  llm: ProviderMode;
  tts: ProviderMode;
  youtube: ProviderMode;
}

export interface SfEvent {
  id: number;
  type: "job_status" | "gate_open" | "render_ready" | "chat" | "needs_footage" | "error";
  job_id: string | null;
  batch_id: string | null;
  payload: Record<string, unknown>;
  created_at: string;
}
