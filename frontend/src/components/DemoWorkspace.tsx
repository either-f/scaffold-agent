import { useEffect, useReducer, useState } from "react";
import { DEMO_CONNECTIONS, DEMO_ENTITIES, EMPTY_ACTION, KIND_LABELS, STAGES, demoReducer, entityKey, formatDate, isSaved, validSourceUrl } from "../demo";
import type { DemoConnection, DemoOutcome, DemoRule, DemoRun, DemoState } from "../demo";
import type { EntityAction, EntityKind, EntityRef, OpportunityStage, WorkspaceEntity } from "../types";
import { DemoDialog } from "./DemoDialog";
import { DemoAutomation, DemoConnections } from "./DemoSettings";

type DemoRoute = "today" | "discover" | "actions" | "automation" | "connections";
type Experience = "normal" | "loading" | "empty" | "error" | "stale" | "unauthorized";
const NAV: { id: DemoRoute; label: string }[] = [
  { id: "today", label: "今天" }, { id: "discover", label: "发现" }, { id: "actions", label: "我的行动" },
  { id: "automation", label: "自动化" }, { id: "connections", label: "连接设置" },
];
const ACTION_FILTERS = { all: "全部保存", opportunities: "我的机会", favorite: "收藏", watch: "关注", read_later: "稍后阅读", read: "已读", pending: "待处理" };
const EXPERIENCE_LABELS: Record<Experience, string> = { normal: "正常", loading: "加载中", empty: "空集合", error: "加载失败", stale: "数据过期", unauthorized: "无权限" };
function readRoute(): DemoRoute {
  const value = window.location.hash.replace(/^#\/?/, "");
  const aliases: Record<string, DemoRoute> = { home: "today", recruitment: "discover", projects: "discover", "ai-daily": "discover", sources: "connections" };
  return NAV.find((item) => item.id === value)?.id ?? aliases[value] ?? "today";
}
function legacyKind(): EntityKind | null {
  const value = window.location.hash.replace(/^#\/?/, "");
  return value === "projects" ? "project" : value === "ai-daily" ? "news" : value === "recruitment" ? "job" : null;
}
const actionFor = (state: DemoState, entity: EntityRef): EntityAction => state[entityKey(entity)] ?? EMPTY_ACTION;
const pending = (action: EntityAction) => (action.read_later && !action.read) || (!!action.next_step.trim() && !action.next_step_done && action.stage !== "archived");

function DemoCard({ entity, action, onOpen, onUpdate }: {
  entity: WorkspaceEntity; action: EntityAction; onOpen: () => void; onUpdate: (patch: Partial<EntityAction>) => void;
}) {
  return <article className="demo-card">
    <button className="demo-card-main" type="button" onClick={onOpen}>
      <span className={`demo-kind demo-kind-${entity.kind}`}>{KIND_LABELS[entity.kind]}</span>
      <div className="demo-card-copy"><h3>{entity.title}</h3><p className="demo-meta">{entity.organization}{entity.location ? ` · ${entity.location}` : ""} · {formatDate(entity.published_at)}</p><p>{entity.summary}</p><div className="demo-tags">{entity.tags.map((tag) => <span key={tag}>{tag}</span>)}</div></div>
      <span className="demo-origin">{entity.origin === "manual" ? "手动 · 演示" : "演示"}</span>
    </button>
    {action.next_step && <p className="demo-card-step">{action.next_step_done ? "✓ 已完成" : "下一步"}：{action.next_step}</p>}
    <div className="demo-card-actions">
      {entity.kind === "job" ? <button type="button" className={action.stage ? "is-active" : ""} onClick={() => action.stage ? onOpen() : onUpdate({ stage: "saved" })}>{action.stage ? `阶段：${STAGES[action.stage]}` : "加入机会"}</button> : <button type="button" aria-pressed={action.favorite} className={action.favorite ? "is-active" : ""} onClick={() => onUpdate({ favorite: !action.favorite })}>{action.favorite ? "已收藏" : "收藏"}</button>}
      <button type="button" aria-pressed={action.read_later} className={action.read_later ? "is-active" : ""} onClick={() => onUpdate({ read_later: !action.read_later })}>{action.read_later ? "已稍后读" : "稍后读"}</button>
      {(action.read_later || action.read) && <button type="button" onClick={() => onUpdate({ read: !action.read })}>{action.read ? "标为未读" : "完成阅读"}</button>}
      {action.next_step && <button type="button" onClick={() => onUpdate({ next_step_done: !action.next_step_done })}>{action.next_step_done ? "重新打开下一步" : "完成下一步"}</button>}
      {isSaved(action) && <span className="demo-saved-hint">已保留到我的行动</span>}
    </div>
  </article>;
}

function DetailPanel({ entity, action, onClose, onUpdate }: {
  entity: WorkspaceEntity; action: EntityAction; onClose: () => void; onUpdate: (patch: Partial<EntityAction>) => void;
}) {
  const [draft, setDraft] = useState({ note: action.note, next_step: action.next_step, stage: action.stage });
  return <DemoDialog title={entity.title} onClose={onClose}>
    <p>{entity.organization} · 发布时间：{formatDate(entity.published_at)}</p>
    <div className="demo-detail-note"><strong>当前判断</strong><span>{entity.score === null ? "暂无评估，先核对来源和未知项" : `评估 ${entity.score}`}</span></div>
    <p className="demo-summary">{entity.summary}</p>{entity.body.map((paragraph) => <p key={paragraph}>{paragraph}</p>)}
    <div className="demo-detail-meta"><span>抓取：{formatDate(entity.fetched_at)}</span>{entity.source_url && validSourceUrl(entity.source_url) ? <a href={entity.source_url} target="_blank" rel="noopener noreferrer">打开参考来源 ↗</a> : <span>来源：未提供</span>}</div>
    <p>演示参考链接由你选择打开；条目不代表实时招聘或报道。</p>
    <div className="demo-detail-actions">
      <button type="button" aria-pressed={action.favorite} onClick={() => onUpdate({ favorite: !action.favorite })}>{action.favorite ? "取消收藏" : "收藏"}</button>
      <button type="button" aria-pressed={action.watch} onClick={() => onUpdate({ watch: !action.watch })}>{action.watch ? "取消关注" : "关注变化"}</button>
      <button type="button" aria-pressed={action.read_later} onClick={() => onUpdate({ read_later: !action.read_later })}>{action.read_later ? "取消稍后读" : "稍后读"}</button>
      <button type="button" aria-pressed={action.read} onClick={() => onUpdate({ read: !action.read })}>{action.read ? "标为未读" : "完成阅读"}</button>
    </div>
    <p role="status">阅读：{action.read ? "已读" : "未读"} · {action.read_later ? "在稍后阅读中" : "未加入稍后阅读"}。上方快捷动作立即生效。</p>
    <form onSubmit={(event) => {
      event.preventDefault();
      const next = draft.next_step.trim();
      onUpdate({ ...draft, note: draft.note.trim(), next_step: next, next_step_done: next === action.next_step ? action.next_step_done : false });
      onClose();
    }}>
      {entity.kind === "job" && <label className="demo-field">申请阶段<select value={draft.stage ?? ""} onChange={(event) => setDraft({ ...draft, stage: (event.target.value || null) as OpportunityStage | null })}><option value="">未加入机会 / 移出机会</option>{Object.entries(STAGES).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>}
      {entity.kind === "job" && <p>“已记录投递”只表示你手动记录进度，不会向招聘网站投递。</p>}
      <label className="demo-field">我的笔记<textarea rows={4} maxLength={3000} value={draft.note} onChange={(event) => setDraft({ ...draft, note: event.target.value })} placeholder="记录判断、疑问或准备材料" /></label>
      <label className="demo-field">下一步<input maxLength={300} value={draft.next_step} onChange={(event) => setDraft({ ...draft, next_step: event.target.value })} placeholder="例如：核对来源并准备一个项目案例" /></label>
      {action.next_step && <div className="demo-detail-actions"><button type="button" onClick={() => onUpdate({ next_step_done: !action.next_step_done })}>{action.next_step_done ? "重新打开已保存的下一步" : "完成已保存的下一步"}</button></div>}
      <div className="demo-detail-actions"><button type="button" onClick={onClose}>取消编辑</button><button type="submit" className="demo-primary">保存笔记与行动</button></div>
    </form>
  </DemoDialog>;
}

function AddJob({ onClose, onSave }: { onClose: () => void; onSave: (entity: WorkspaceEntity) => void }) {
  const [error, setError] = useState("");
  return <DemoDialog title="添加岗位 · 当前会话" onClose={onClose}><p>手动记录一个机会，不会发布岗位或提交简历。</p><form onSubmit={(event) => {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const title = String(data.get("title") ?? "").trim();
    const organization = String(data.get("organization") ?? "").trim();
    const url = String(data.get("url") ?? "").trim();
    if (!title || !organization) { setError("岗位名称和公司不能为空或只有空格。"); return; }
    if (!validSourceUrl(url)) { setError("来源需为 http/https 链接，且不含账号密码；也可以留空。"); return; }
    onSave({ kind: "job", id: 0, title, organization, location: String(data.get("location") ?? "").trim(), summary: "当前会话手动记录的岗位，详情与来源待核实。", body: ["这是你在演示中创建的本地机会。刷新页面后会重置。"], tags: [], source_id: null, external_id: null, source_url: url || null, published_at: null, fetched_at: null, origin: "manual", score: null });
  }}>
    <label className="demo-field">岗位名称<input name="title" required maxLength={100} /></label><label className="demo-field">公司<input name="organization" required maxLength={100} /></label><label className="demo-field">地点<input name="location" maxLength={100} /></label><label className="demo-field">来源链接（可选）<input name="url" type="url" maxLength={1000} placeholder="https://…" /></label>
    {error && <p className="demo-error" role="alert">{error}</p>}<div className="demo-detail-actions"><button type="button" onClick={onClose}>取消</button><button className="demo-primary" type="submit">添加到我的机会</button></div>
  </form></DemoDialog>;
}

export function DemoWorkspace() {
  const [route, setRoute] = useState<DemoRoute>(readRoute);
  const [kind, setKind] = useState<EntityKind>(() => legacyKind() ?? "job");
  const [queries, setQueries] = useState<Record<DemoRoute, string>>({ today: "", discover: "", actions: "", automation: "", connections: "" });
  const [entities, setEntities] = useState(DEMO_ENTITIES);
  const [state, dispatch] = useReducer(demoReducer, {});
  const [selected, setSelected] = useState<WorkspaceEntity | null>(null);
  const [notice, setNotice] = useState("可以从发现页开始，或手动添加一个岗位。");
  const [experience, setExperience] = useState<Experience>("normal");
  const [actionFilter, setActionFilter] = useState<keyof typeof ACTION_FILTERS>("all");
  const [stage, setStage] = useState("");
  const [board, setBoard] = useState(false);
  const [date, setDate] = useState("all");
  const [sort, setSort] = useState("default");
  const [adding, setAdding] = useState(false);
  const [draftOpen, setDraftOpen] = useState(false);
  const [help, setHelp] = useState(false);
  const [rules, setRules] = useState<DemoRule[]>([]);
  const [runs, setRuns] = useState<DemoRun[]>([]);
  const [connections, setConnections] = useState(DEMO_CONNECTIONS);
  const [validation, setValidation] = useState<{ id: string; outcome: "success" | "failed" } | null>(null);

  useEffect(() => {
    const onHash = () => { setRoute(readRoute()); const legacy = legacyKind(); if (legacy) setKind(legacy); setSelected(null); setAdding(false); setDraftOpen(false); setHelp(false); window.scrollTo(0, 0); };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);
  useEffect(() => {
    const timers = runs.filter((run) => run.status === "running").map((run) => window.setTimeout(() => {
      setRuns((current) => current.map((item) => item.id === run.id ? { ...item, status: item.outcome } : item));
    }, 1200));
    return () => timers.forEach(window.clearTimeout);
  }, [runs]);
  useEffect(() => {
    if (!validation) return;
    const timer = window.setTimeout(() => {
      setConnections((current) => current.map((item) => item.id === validation.id ? { ...item, status: validation.outcome, lastSuccess: validation.outcome === "success" ? `${new Date().toLocaleTimeString("zh-CN")}（模拟）` : item.lastSuccess, error: validation.outcome === "failed" ? "模拟连接超时；配置已保留，可选择结果后重试。" : "" } : item));
      setNotice(`模拟连接验证${validation.outcome === "success" ? "成功" : "失败"}；未执行采集。`);
      setValidation(null);
    }, 1000);
    return () => window.clearTimeout(timer);
  }, [validation]);

  const query = queries[route];
  const data = experience === "empty" ? [] : entities;
  const matches = (entity: WorkspaceEntity) => [entity.title, entity.organization, entity.summary, ...entity.tags].join(" ").toLowerCase().includes(query.trim().toLowerCase());
  const saved = data.filter((entity) => isSaved(actionFor(state, entity)));
  const pendingItems = data.filter((entity) => pending(actionFor(state, entity)));
  const opportunities = data.filter((entity) => entity.kind === "job" && actionFor(state, entity).stage && actionFor(state, entity).stage !== "archived");
  const shownConnections = experience === "empty" ? [] : connections;
  const problemSources = shownConnections.filter((item) => item.status !== "success");
  const visible = data.filter((entity) => entity.kind === kind && matches(entity) && (kind !== "news" || date === "all" || (date === "unknown" ? !entity.published_at : entity.published_at?.startsWith(date))));
  if (sort === "title") visible.sort((a, b) => a.title.localeCompare(b.title, "zh-CN"));
  if (sort === "newest") visible.sort((a, b) => (b.published_at ?? "").localeCompare(a.published_at ?? ""));
  const actionItems = saved.filter((entity) => {
    const action = actionFor(state, entity);
    return matches(entity) && (actionFilter === "all" || (actionFilter === "opportunities" ? entity.kind === "job" && action.stage && (!stage || action.stage === stage) : actionFilter === "pending" ? pending(action) : action[actionFilter]));
  });
  const update = (entity: EntityRef, patch: Partial<EntityAction>) => { dispatch({ ref: entity, patch }); setNotice(`${KIND_LABELS[entity.kind]}状态已更新，仅限当前会话。`); };
  const go = (next: DemoRoute) => { window.location.hash = `/${next}`; };
  const cards = (items: WorkspaceEntity[]) => <div className="demo-list">{items.map((entity) => <DemoCard key={entityKey(entity)} entity={entity} action={actionFor(state, entity)} onOpen={() => setSelected(entity)} onUpdate={(patch) => update(entity, patch)} />)}</div>;
  const clearSearch = () => setQueries((current) => ({ ...current, [route]: "" }));
  const empty = (text: string) => <div className="demo-empty"><strong>{text}</strong><span>清空搜索、调整筛选，或从发现页添加内容。</span><button type="button" onClick={() => { clearSearch(); setDate("all"); setStage(""); setActionFilter("all"); }}>清空搜索与筛选</button></div>;
  function saveConnection(connection: DemoConnection) {
    setConnections((current) => current.some((item) => item.id === connection.id) ? current.map((item) => item.id === connection.id ? connection : item) : [...current, connection]);
    setNotice("演示配置已保存；连接状态未知，请单独模拟验证。");
  }
  function runRule(rule: DemoRule, outcome: DemoOutcome) {
    if (!rule.enabled || runs.some((run) => run.ruleId === rule.id && run.status === "running")) return;
    setRuns((current) => [{ id: crypto.randomUUID(), ruleId: rule.id, name: rule.name, scope: rule.scope, time: new Date().toLocaleTimeString("zh-CN"), outcome, status: "running" }, ...current]);
    setNotice("已开始本地模拟运行，结果见运行记录。");
  }

  function renderToday() {
    const todo = pendingItems.filter(matches);
    const picks = data.filter(matches).slice(0, 3);
    return <><div className="demo-page-head"><div><p className="demo-eyebrow">个人情报与行动工作台</p><h1>今天，先处理真正重要的事</h1><p>发现内容，保留判断，再决定下一步。</p></div><button className="demo-primary" type="button" onClick={() => go("discover")}>去发现</button></div>
      <div className="demo-kpis"><div><span>待处理内容</span><strong>{pendingItems.length}</strong><small>未读的稍后阅读 / 未完成下一步，按内容去重</small></div><div><span>我的机会</span><strong>{opportunities.length}</strong><small>已加入且未归档的岗位</small></div><div><span>待检查来源</span><strong>{problemSources.length}</strong><small>共 {shownConnections.length} 个演示来源，未配置 / 未知 / 失败</small></div></div>
      <section className="demo-section"><div className="demo-section-head"><h2>待处理（{todo.length}）</h2><button type="button" onClick={() => { setActionFilter("pending"); go("actions"); }}>查看我的行动 →</button></div>{todo.length ? cards(todo) : <div className="demo-empty"><strong>{query ? "没有匹配的待处理内容" : "当前没有待处理内容"}</strong><span>单纯收藏不会创建待办；添加下一步或稍后阅读后会出现在这里。</span></div>}</section>
      <section className="demo-section demo-run-section"><div className="demo-section-head"><h2>继续发现</h2><button type="button" onClick={() => go("discover")}>查看全部 →</button></div>{picks.length ? cards(picks) : empty("没有匹配内容")}</section>
      {problemSources.length > 0 && <aside className="demo-notice"><p>待检查：{problemSources.map((item) => item.name).join("、")}（模拟状态）</p><button type="button" onClick={() => go("connections")}>查看连接设置</button></aside>}
    </>;
  }
  function renderDiscover() {
    return <><div className="demo-page-head compact"><div><p className="demo-eyebrow">发现</p><h1>把信息变成可判断的候选</h1><p>内容、来源与人工判断相互独立。未知评分不编造。</p></div><button className="demo-primary" type="button" onClick={() => setAdding(true)}>添加岗位</button></div>
      <div className="demo-tabs">{(["job", "project", "news"] as EntityKind[]).map((value) => <button type="button" aria-pressed={kind === value} className={kind === value ? "active" : ""} key={value} onClick={() => setKind(value)}>{KIND_LABELS[value]}</button>)}</div>
      <div className="demo-toolbar"><span>{visible.length} 条{KIND_LABELS[kind]}</span><label className="demo-field">排序<select value={sort} onChange={(event) => setSort(event.target.value)}><option value="default">默认顺序</option><option value="newest">发布时间</option><option value="title">标题</option></select></label>{kind === "news" && <><label className="demo-field">日报期次<select value={date} onChange={(event) => setDate(event.target.value)}><option value="all">全部期次</option><option value="2026-09-08">9月8日 · 演示</option><option value="2026-09-06">9月6日 · 演示</option><option value="2026-09-07">9月7日 · 空期</option><option value="unknown">日期未提供</option></select></label><button type="button" disabled={!visible.length} onClick={() => setDraftOpen(true)}>预览当前期次草稿</button></>}</div>
      {visible.length ? cards(visible) : empty(kind === "news" ? "本期 / 筛选下没有资讯" : "没有匹配内容")}
    </>;
  }
  function renderActions() {
    return <><div className="demo-page-head compact"><div><p className="demo-eyebrow">我的行动</p><h1>让保存过的内容有下一步</h1><p>阶段只表示本地记录；归档和移出机会不会删除原始内容。</p></div><button className="demo-primary" type="button" onClick={() => setAdding(true)}>添加岗位</button></div>
      <div className="demo-tabs">{Object.entries(ACTION_FILTERS).map(([id, label]) => <button key={id} type="button" aria-pressed={actionFilter === id} className={actionFilter === id ? "active" : ""} onClick={() => setActionFilter(id as keyof typeof ACTION_FILTERS)}>{label}</button>)}</div>
      <div className="demo-toolbar"><span>共 {actionItems.length} 条</span>{actionFilter === "opportunities" && <><label className="demo-field">机会阶段<select value={stage} onChange={(event) => setStage(event.target.value)}><option value="">全部阶段（含归档）</option>{Object.entries(STAGES).map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select></label><button type="button" className="workspace-board-toggle" aria-pressed={board} onClick={() => setBoard(!board)}>{board ? "切换列表" : "切换阶段看板"}</button></>}</div>
      {actionItems.length ? <div className={board && actionFilter === "opportunities" ? "workspace-opportunities is-board" : "workspace-opportunities"}><div className="workspace-list-view">{cards(actionItems)}</div>{board && actionFilter === "opportunities" && <div className="workspace-board">{Object.entries(STAGES).filter(([id]) => !stage || stage === id).map(([id, label]) => <section key={id}><h3>{label}</h3>{cards(actionItems.filter((entity) => actionFor(state, entity).stage === id))}</section>)}</div>}</div> : empty("这里还没有匹配的行动")}
    </>;
  }
  const blocked = experience === "loading" || experience === "error" || experience === "unauthorized";
  return <div className="demo-app">
    <header className="demo-topbar"><a className="demo-brand" href="#/today"><span className="demo-brand-mark">S</span><span>Scaffold Platform</span></a><nav aria-label="演示主导航">{NAV.map((item) => <a key={item.id} href={`#/${item.id}`} aria-current={route === item.id ? "page" : undefined} className={route === item.id ? "active" : ""}>{item.label}</a>)}</nav><div className="demo-top-actions"><span className="demo-mode">体验演示</span><a href="/">返回真实工作台</a></div></header>
    <main className="demo-shell">
      <div className="demo-banner"><span>当前会话演示</span><p>刷新或退出后重置 · 不请求后端，不自动访问外站</p><button type="button" onClick={() => setHelp(true)}>了解</button></div>
      <div className="demo-workspace-tools"><label className="demo-search"><span>⌕</span><input type="search" aria-label={`搜索${NAV.find((item) => item.id === route)?.label}当前视图`} value={query} onChange={(event) => setQueries({ ...queries, [route]: event.target.value })} placeholder={`搜索${NAV.find((item) => item.id === route)?.label}当前视图`} /><button type="button" onClick={clearSearch} disabled={!query}>清空</button></label><label className="demo-field">状态体验（模拟）<select value={experience} onChange={(event) => setExperience(event.target.value as Experience)}>{Object.entries(EXPERIENCE_LABELS).map(([id, label]) => <option value={id} key={id}>{label}</option>)}</select></label></div>
      <p className="demo-live-notice" role="status">{notice}</p>
      {experience === "stale" && <div className="demo-notice" role="status">模拟数据过期：以下为会话快照，可继续查看。<button type="button" onClick={() => setExperience("normal")}>恢复正常体验</button></div>}
      {experience === "empty" && <div className="demo-notice">正在体验空集合，恢复正常后原会话数据仍在。<button type="button" onClick={() => setExperience("normal")}>恢复正常体验</button></div>}
      {blocked ? <div className="demo-empty" role={experience === "error" ? "alert" : "status"} aria-busy={experience === "loading"}><strong>{EXPERIENCE_LABELS[experience]}（模拟）</strong><span>{experience === "unauthorized" ? "演示权限不足，未触发登录或认证请求。" : experience === "loading" ? "加载状态样例，由你结束体验；当前没有网络请求。" : "模拟加载失败；重试仅恢复本地数据。"}</span><button type="button" onClick={() => setExperience("normal")}>{experience === "error" ? "模拟重试" : "恢复正常体验"}</button></div> : route === "today" ? renderToday() : route === "discover" ? renderDiscover() : route === "actions" ? renderActions() : route === "automation" ? <DemoAutomation query={query} rules={experience === "empty" ? [] : rules} runs={experience === "empty" ? [] : runs} onSave={(rule) => { setRules((current) => current.some((item) => item.id === rule.id) ? current.map((item) => item.id === rule.id ? rule : item) : [...current, rule]); setNotice("演示规则已保存；未启动调度或运行。"); }} onToggle={(id) => { setRules((current) => current.map((rule) => rule.id === id ? { ...rule, enabled: !rule.enabled } : rule)); setNotice("演示规则开关已更新，已开始的模拟运行继续完成。"); }} onRun={runRule} /> : <DemoConnections query={query} connections={shownConnections} validating={validation?.id ?? null} onSave={saveConnection} onValidate={(connection, outcome) => setValidation({ id: connection.id, outcome })} />}
    </main>
    {selected && <DetailPanel key={entityKey(selected)} entity={selected} action={actionFor(state, selected)} onClose={() => setSelected(null)} onUpdate={(patch) => update(selected, patch)} />}
    {adding && <AddJob onClose={() => setAdding(false)} onSave={(entity) => { const created = { ...entity, id: Math.max(0, ...entities.filter((item) => item.kind === "job").map((item) => item.id)) + 1 }; setEntities((current) => [...current, created]); update(created, { stage: "saved" }); setAdding(false); setActionFilter("opportunities"); setStage(""); setQueries((current) => ({ ...current, actions: "" })); setExperience("normal"); go("actions"); }} />}
    {draftOpen && <DemoDialog title="日报草稿预览 · 人工样例" onClose={() => setDraftOpen(false)}><p>范围：{date === "all" ? "全部期次" : date} · {visible.length} 条。未调用模型、未发送或推送。</p>{visible.map((entity) => <section key={entityKey(entity)}><h3>{entity.title}</h3><p>{entity.summary}</p>{entity.source_url ? <a href={entity.source_url} target="_blank" rel="noopener noreferrer">参考来源 ↗</a> : <p>来源未提供，需人工补充。</p>}</section>)}</DemoDialog>}
    {help && <DemoDialog title="关于这次体验" onClose={() => setHelp(false)}><p>数据与修改仅保存在当前页面内存中，跨路由共享，刷新或退出即重置。</p><p>配置保存、连接验证、规则启停和模拟运行是不同动作。所有运行结果均为样例，不代表外部采集或通知发送。</p><p>详情的收藏、关注和阅读按钮立即生效；笔记、阶段与下一步需要保存。取消编辑或 Esc 会丢弃这些未保存字段。</p><p>“返回真实工作台”会离开演示；当前演示本身不发出业务或认证请求。</p></DemoDialog>}
  </div>;
}
