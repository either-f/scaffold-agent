// Workspace entities are additive: existing API page types remain compatible.
export type EntityKind = "job" | "project" | "news";
export interface EntityRef { kind: EntityKind; id: number }
export type OpportunityStage = "saved" | "preparing" | "applied" | "interviewing" | "offer" | "rejected" | "archived";
export interface WorkspaceEntity extends EntityRef {
  title: string;
  summary: string;
  body: string[];
  tags: string[];
  source_id: string | null;
  external_id: string | null;
  source_url: string | null;
  published_at: string | null;
  fetched_at: string | null;
  origin: "real" | "demo" | "manual" | "unknown";
  score: number | null;
  organization: string;
  location?: string;
  salary?: string;
  language?: string;
  license?: string;
}
export interface EntityAction {
  favorite: boolean;
  watch: boolean;
  read_later: boolean;
  read: boolean;
  tags: string[];
  note: string;
  next_step: string;
  next_step_done: boolean;
  stage: OpportunityStage | null;
}

export interface ModuleInfo {
  id: string;
  label: string;
  schedule: string;
  has_approval: boolean;
}

export interface ContentItem {
  id: number;
  module: string;
  source_platform: string;
  external_id: string;
  url: string;
  title: string;
  raw_text: string;
  structured: Record<string, unknown>;
  status: "new" | "reviewed" | "actioned" | "rejected" | "expired";
  score: number;
  created_at: number;
  updated_at: number;
}

export const STATUS_OPTIONS = ["new", "reviewed", "actioned", "rejected", "expired"] as const;

export const STATUS_META: Record<ContentItem["status"], { label: string; en: string }> = {
  new: { label: "待鉴", en: "Incoming" },
  reviewed: { label: "已阅", en: "Reviewed" },
  actioned: { label: "入选", en: "Acquired" },
  rejected: { label: "剔除", en: "Deaccessioned" },
  expired: { label: "撤展", en: "Expired" },
};

// ============================================================
// 平台页面（/api/platform/*，字段与后端 JSON 的 snake_case 对齐）
// ============================================================
export type FeedKind = "job" | "project" | "news" | "referral";

export interface FeedBadge {
  text: string;
  variant: "green" | "gray" | "blue";
}

export interface FeedAction {
  label: string;
  primary?: boolean;
}

export interface FeedItem {
  kind: FeedKind;
  logo: "mi" | "git" | "openai";
  title: string;
  badge?: FeedBadge;
  meta: string;
  tags?: string[];
  time?: string;
  desc?: string;
  reason: string;
  actions: FeedAction[];
}

export interface HomeStat {
  title: string;
  num: string;
  trend: string;
  down: boolean;
  sub: string;
}

export interface RankItem {
  title: string;
  heat: string;
}

export interface TodoItem {
  id: number;
  name: string;
  sub: string;
  time: string;
  has_dot: boolean;
  icon: string;
}

export interface AutoBrief {
  id: number;
  name: string;
  sub: string;
  running: boolean;
}

export interface HomeData {
  stats: HomeStat[];
  feed: FeedItem[];
  ranks: RankItem[];
  todos: TodoItem[];
  automations: AutoBrief[];
}

export interface Pill {
  text: string;
  variant: string;
}

export interface UserInfo {
  id: number;
  username: string;
  nickname: string;
  email: string;
  avatar: string;
  intro: string;
  gender: number;
  roles: string[];
  permissions: string[];
}

export interface JobItem {
  id: number;
  logo: string;
  logo_class: string;
  title: string;
  meta: string;
  tags: string[];
  match: string | null;
  salary: string;
  reason: string;
  referral: boolean;
  actions: string[];
  favorited: boolean;
  applied: boolean;
}

export interface JobsData {
  items: JobItem[];
  follows: { logo: string; logo_class: string; name: string; sub: string; pill: Pill }[];
  status: { label: string; count: number }[];
  interview: { time: string; note: string };
}

export interface ProjectItem {
  id: number;
  logo: string;
  logo_class: string;
  name: string;
  full_name: string;
  meta: string;
  tags: string[];
  desc: string;
  reason: string;
  score: string | null;
  rank: string;
  actions: string[];
  favorited: boolean;
  watched: boolean;
}

export interface ProjectsData {
  items: ProjectItem[];
  trend: { height: number }[];
  topics: string[];
  watches: { name: string; count: string }[];
}

export interface NewsItem {
  id: number;
  logo: string;
  logo_class: string;
  title: string;
  meta: string;
  desc: string;
  score: string | null;
  insight: string;
  actions: string[];
  read_later: boolean;
}

export interface NewsData {
  items: NewsItem[];
  entities: { logo: string; logo_class: string; logo_style?: string | null; name: string; sub: string; star: boolean }[];
  topics: { tag: string; count: number }[];
}

export interface AutomationRule {
  id: number;
  title: string;
  meta: string;
  category: string;
  flow: string[];
  last: string;
  enabled: boolean;
  module_id?: string | null;
  actions: string[];
}

export interface AutomationsData {
  scheduler_enabled?: boolean;
  stats?: { total: number; succeeded: number; failed: number } | null;
  rules: AutomationRule[];
  logs: { id?: string; name: string; sub: string; time: string; error: boolean; status?: string }[];
  pending: { title: string; desc: string }[];
  channels: { icon: string; name: string; pill: Pill }[];
}

export interface SourceItem {
  id: number;
  module_id?: string | null;
  logo: string;
  logo_class: string;
  logo_style?: string | null;
  name: string;
  sub: string;
  category: string;
  status: string;
  status_text: string;
  last: string;
  today: number | null;
  action: string;
}

export interface SourcesData {
  items: SourceItem[];
  metrics: { label: string; value: string; error: boolean }[];
  alerts: { title: string; desc: string }[];
}
