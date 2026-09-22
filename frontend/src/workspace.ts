import type { EntityKind, OpportunityStage } from "./types";

export const ENTITY_PATHS: Record<EntityKind, string> = { job: "jobs", project: "projects", news: "news" };
export type PersonalFlags = { favorite: boolean; watch: boolean; read_later: boolean; read: boolean };
export interface RemoteEntity {
  available?: boolean; unavailable_reason?: string;
  id: number; kind?: EntityKind; title?: string; name?: string; company?: string; city?: string;
  salary?: string; full_name?: string; source?: string; meta?: string; description?: string | null;
  desc?: string | null; reason?: string | null; summary?: string | null; tags?: string[];
  source_url?: string | null; source_id?: string | null; origin?: string | null;
  published_at?: string | number | null; fetched_at?: string | number | null;
  created_by?: number | null; favorite?: boolean; favorited?: boolean; watch?: boolean; watched?: boolean;
  read_later?: boolean; read?: boolean; applied?: boolean; actions?: Partial<PersonalFlags> | string[];
}
export interface ApplicationRecord {
  id: number | null; job_id: number; job: RemoteEntity; stage: OpportunityStage;
  next_step: string | null; note: string | null; next_at: string | null; next_step_done: boolean;
  history?: { stage: OpportunityStage; changed_at?: string | number }[];
}
export const entityTitle = (item: RemoteEntity) => item.title || item.name || `条目 #${item.id}`;
export const entityBody = (item: RemoteEntity) => item.description || item.desc || item.reason || item.summary || "内容尚未提供。";
export function personalFlags(item: RemoteEntity): PersonalFlags {
  const actions = !Array.isArray(item.actions) ? item.actions : undefined;
  return { favorite: actions?.favorite ?? item.favorite ?? item.favorited ?? false,
    watch: actions?.watch ?? item.watch ?? item.watched ?? false,
    read_later: actions?.read_later ?? item.read_later ?? false, read: actions?.read ?? item.read ?? false };
}
export function remoteDate(value: string | number | null | undefined) {
  if (!value) return "未提供";
  const date = new Date(typeof value === "number" ? value * 1000 : value);
  return Number.isNaN(date.getTime()) ? "未提供" : date.toLocaleString("zh-CN");
}
export function sourceLink(value: string | null | undefined) {
  if (!value) return null;
  try { const url = new URL(value); return ["http:", "https:"].includes(url.protocol) && !url.username && !url.password ? url.href : null; }
  catch { return null; }
}
