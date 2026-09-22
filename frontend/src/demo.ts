import type { EntityAction, EntityKind, EntityRef, OpportunityStage, WorkspaceEntity } from "./types";

export const KIND_LABELS: Record<EntityKind, string> = { job: "岗位", project: "开源项目", news: "AI 资讯" };
export const STAGES: Record<OpportunityStage, string> = {
  saved: "已加入", preparing: "准备中", applied: "已记录投递", interviewing: "面试中",
  offer: "已获 Offer", rejected: "未通过", archived: "已归档",
};
export const STAGE_KEYS = Object.keys(STAGES) as OpportunityStage[];
export const entityKey = ({ kind, id }: EntityRef) => `${kind}:${id}`;

const sample = (kind: EntityKind, id: number, fields: Partial<WorkspaceEntity> & Pick<WorkspaceEntity, "title" | "summary" | "body" | "organization">): WorkspaceEntity => ({
  kind, id, tags: [], source_id: null, external_id: null, source_url: null,
  published_at: null, fetched_at: "2026-09-08T08:30:00+08:00", origin: "demo", score: null, ...fields,
});

// Deliberately reuse numeric IDs across kinds to exercise the public entity contract.
// All descriptions are editorial samples, not verified job listings or live source data.
export const DEMO_ENTITIES: WorkspaceEntity[] = [
  sample("job", 1, {
    title: "后端工程师 · Agent 应用", organization: "纸舟科技（虚构团队）", location: "上海", salary: "25–35K · 演示",
    tags: ["Java", "Agent", "后端"], source_id: "v2ex-jobs", external_id: "demo-job-1", source_url: "https://www.v2ex.com/go/jobs",
    published_at: "2026-09-08T09:00:00+08:00", summary: "把模型能力变成可靠的业务服务，参与工具调用、任务状态与数据接口建设。",
    body: ["演示岗位：负责 Java 服务、模型工具接口与任务运行记录。关注边界清楚的接口设计、失败恢复和可观测性。", "准备材料：挑选一个服务端项目，说明一次接口或数据一致性问题，以及你如何验证修复。", "待核实：招聘有效期、具体职级、薪资结构与工作安排。参考链接指向公开招聘版，不代表该虚构岗位正在招聘。"],
  }),
  sample("job", 2, {
    title: "全栈工程师 · 开发者工具", organization: "松间实验室（虚构团队）", location: "远程", salary: "未提供",
    tags: ["TypeScript", "React", "全栈"], summary: "为开发者构建小而完整的工作流，重视可访问性与可维护的交互。",
    body: ["演示岗位：使用 TypeScript 和 React 设计内容工作台，从信息架构到页面状态完整交付。", "希望看到可运行作品，以及键盘操作、移动端和错误状态的处理说明。", "来源与发布时间尚未提供。先补充来源，再判断是否值得投递。"],
  }),
  sample("job", 3, {
    title: "数据工程师 · 检索与评估", organization: "远山数据（虚构团队）", location: "北京", salary: "20–30K · 演示",
    tags: ["Python", "RAG", "数据"], source_id: "v2ex-jobs", external_id: "demo-job-3", source_url: "https://www.v2ex.com/go/jobs",
    published_at: "2026-09-06T10:00:00+08:00", summary: "整理数据、构建检索评估集，并把质量变化解释给使用者。",
    body: ["演示岗位：构建数据清洗流程、维护检索评估集、分析失败样例。", "准备方向：说明数据来源、基线、评估指标和改进后的实际变化。", "该内容用于体验机会管理；公开招聘版仅作参考，无法核实此岗位。"],
  }),
  sample("project", 1, {
    title: "Scaffold · 个人情报工作台", organization: "本地项目", language: "TypeScript", license: "未核实",
    tags: ["TypeScript", "Agent", "工作流"], summary: "从分散内容到下一步行动：一个用于体验收藏、关注与笔记的项目样例。",
    body: ["这是本地演示项目条目，与你正在使用的工作台共享同一种产品构想。", "适用场景：整合岗位、项目和资讯，并保留人工判断与处理记录。", "没有公开仓库链接，也未核实许可证。不能据此判断可分发范围。"],
  }),
  sample("project", 2, {
    title: "React · 用户界面基础", organization: "facebook/react", language: "JavaScript", license: "请查看仓库许可",
    tags: ["JavaScript", "React", "界面"], source_id: "github", external_id: "facebook/react", source_url: "https://github.com/facebook/react",
    published_at: "2026-09-07T14:00:00+08:00", summary: "用熟悉的组件与状态组合完成交互，不先引入额外状态平台。",
    body: ["演示研究笔记：优先用组件 state/reducer 表达当前会话的用户动作。", "考察问题：状态应放在哪里？多个视图是否读取同一实体？更新后怎样让用户确认结果？", "参考链接指向仓库；本页时间和说明属于演示样例，不代表仓库最新发布情况。"],
  }),
  sample("project", 3, {
    title: "FastAPI · 服务接口", organization: "fastapi/fastapi", language: "Python", license: "请查看仓库许可",
    tags: ["Python", "API", "后端"], source_id: "github", external_id: "fastapi/fastapi", source_url: "https://github.com/fastapi/fastapi",
    published_at: "2026-09-05T12:00:00+08:00", summary: "围绕清晰的数据契约组织接口，记录值得继续研究的边界。",
    body: ["演示选型记录：关注字段验证、错误语义与接口文档。", "下一步可记录：核对项目已有接口，列出来源字段和用户动作的对应关系。", "本条目未测量性能、维护活跃度或兼容性，不生成推荐分。"],
  }),
  sample("news", 1, {
    title: "让 AI 推荐附上可核验的依据", organization: "工作台编辑选读", tags: ["Agent", "评估", "产品"],
    source_id: "reference", external_id: "demo-news-1", source_url: "https://github.com/openai/evals",
    published_at: "2026-09-08T08:00:00+08:00", summary: "比一个高分更有用的，是能看见来源、未知项和下一步验证方法。",
    body: ["这是一篇演示短文，不是实时新闻。推荐卡可以先提供来源、理由、未知项，而不是缺乏依据的精确分数。", "阅读时记录一个具体问题：结论对应哪段证据？如果证据缺失，还能做出什么有限判断？", "参考资料用于继续了解评估实践，不作为本段演示文字的原始报道来源。"],
  }),
  sample("news", 2, {
    title: "从收藏到行动：一次小型工作流实验", organization: "个人笔记", tags: ["工作流", "产品"],
    summary: "收藏、关注和待办解决不同问题；一次点击不应该悄悄创建三种任务。",
    body: ["演示阅读材料：收藏表示值得保留，关注表示希望追踪变化，待办意味着你已经确定下一步。", "把三种状态分开，能够让信息整理更轻，也让行动列表更可信。", "这份笔记没有外部来源或发布时间，可以先记录问题，再补充证据。"],
  }),
  sample("news", 3, {
    title: "检索系统如何记录失败样例", organization: "研究方法选读", tags: ["RAG", "评估", "数据"],
    source_id: "reference", external_id: "demo-news-3", source_url: "https://github.com/openai/evals",
    published_at: "2026-09-06T08:00:00+08:00", summary: "把无法回答的问题留在评估记录中，区分没有证据与生成错误。",
    body: ["演示摘要：先保存问题、检索结果与人工判断，再讨论是否需要新的检索策略。", "不要把一次成功样例当成质量评估。记录评估集范围及未知项，更有助于下一次决策。", "本页不包含真实实验数据；参考链接用于继续阅读。"],
  }),
];

export const EMPTY_ACTION: EntityAction = {
  favorite: false, watch: false, read_later: false, read: false,
  tags: [], note: "", next_step: "", next_step_done: false, stage: null,
};
export type DemoState = Record<string, EntityAction>;
export type DemoUpdate = { ref: EntityRef; patch: Partial<EntityAction> };
export function demoReducer(state: DemoState, { ref, patch }: DemoUpdate): DemoState {
  const key = entityKey(ref);
  return { ...state, [key]: { ...(state[key] ?? EMPTY_ACTION), ...patch,
    ...(patch.read ? { read_later: false } : patch.read_later ? { read: false } : {}),
    ...(ref.kind !== "job" ? { stage: null } : {}) } };
}
export const isSaved = (action: EntityAction) => action.favorite || action.watch || action.read_later || action.read || action.stage !== null || !!action.next_step || !!action.note || action.tags.length > 0;
export function formatDate(value: string | null) {
  return value ? new Intl.DateTimeFormat("zh-CN", { month: "short", day: "numeric", timeZone: "Asia/Shanghai" }).format(new Date(value)) : "未提供";
}

export function formatDateTime(value: string | null | undefined) {
  if (!value) return "未知";
  const parsed = new Date(value);
  // 后端对未运行的来源直接返回"尚未运行"这类文案，原样透传
  if (Number.isNaN(parsed.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", timeZone: "Asia/Shanghai",
  }).format(parsed);
}

export const TEMPLATES = [
  { id: "job-hunter", title: "岗位变化提醒", scope: "Java / Agent / 上海" },
  { id: "github-trending", title: "GitHub 项目更新", scope: "TypeScript / Python" },
  { id: "ai-daily-news", title: "AI 日报整理", scope: "Agent / 评估" },
];
export type DemoOutcome = "success" | "partial" | "failed";
export const OUTCOMES: Record<DemoOutcome, string> = { success: "成功", partial: "部分失败", failed: "失败" };
export interface DemoRule { id: string; module: string; name: string; scope: string; time: string; enabled: boolean }
export interface DemoRun { id: string; ruleId: string; name: string; scope: string; time: string; outcome: DemoOutcome; status: "running" | DemoOutcome }
export type ConnectionStatus = "unconfigured" | "unknown" | "success" | "failed";
export const CONNECTION_LABELS: Record<ConnectionStatus, string> = { unconfigured: "未配置", unknown: "未知 · 未验证", success: "连接可用（模拟）", failed: "连接失败（模拟）" };
export interface DemoConnection { id: string; name: string; category: string; scope: string; status: ConnectionStatus; lastSuccess: string | null; error: string }
export const DEMO_CONNECTIONS: DemoConnection[] = [
  { id: "github", name: "GitHub Trending", category: "公开项目", scope: "TypeScript", status: "success", lastSuccess: "2026-09-08 08:30（样例）", error: "" },
  { id: "v2ex-jobs", name: "V2EX 酷工作", category: "公开招聘", scope: "jobs", status: "failed", lastSuccess: null, error: "模拟超时；可重试验证连接。" },
  { id: "v2ex-hot", name: "V2EX 热门", category: "公开资讯", scope: "", status: "unconfigured", lastSuccess: null, error: "" },
  { id: "local", name: "本地演示来源", category: "手动记录", scope: "当前会话", status: "unknown", lastSuccess: null, error: "" },
];

export function validSourceUrl(value: string) {
  if (!value.trim()) return true;
  try { const url = new URL(value); return ["https:", "http:"].includes(url.protocol) && !url.username && !url.password; }
  catch { return false; }
}
