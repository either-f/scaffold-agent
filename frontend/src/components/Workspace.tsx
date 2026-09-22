import { useEffect, useState } from "react";
import { AuthRequiredError, putApi, useApi, useTokenVersion } from "../api";
import { useAuth } from "../auth";
import { KIND_LABELS, STAGES } from "../demo";
import type { EntityKind, EntityRef } from "../types";
import { ENTITY_PATHS, entityBody, entityTitle, personalFlags, type ApplicationRecord, type PersonalFlags, type RemoteEntity } from "../workspace";
import { AuthModal } from "./AuthModal";
import { WorkspaceDetail, WorkspaceJobForm } from "./WorkspaceDetail";
import { Automation } from "../pages/Automation";
import { Sources } from "../pages/Sources";

const NAV = [{ id: "today", label: "今天" }, { id: "discover", label: "发现" }, { id: "actions", label: "我的行动" }, { id: "automation", label: "自动化" }, { id: "connections", label: "连接设置" }];
function readRoute() {
  const hash = window.location.hash.replace(/^#\/?/, "");
  const aliases: Record<string, string> = { home: "today", recruitment: "discover", projects: "discover", "ai-daily": "discover", sources: "connections" };
  return NAV.some((item) => item.id === hash) ? hash : aliases[hash] ?? "today";
}
function readKind(): EntityKind {
  return window.location.hash.endsWith("projects") ? "project" : window.location.hash.endsWith("ai-daily") ? "news" : "job";
}
const FILTERS = { all: "全部保存", opportunities: "我的机会", favorite: "收藏", watch: "关注", read_later: "稍后阅读", read: "已读" };
type Items = { items: RemoteEntity[]; origin?: string };

function UnavailableCard({ item, onChanged }: { item: RemoteEntity; onChanged: () => void }) {
  const { openAuth } = useAuth();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const flags = personalFlags(item);
  const labels: Record<keyof PersonalFlags, string> = { favorite: "取消收藏", watch: "取消关注", read_later: "取消稍后阅读", read: "清除已读记录" };
  async function clear(flag: keyof PersonalFlags) {
    if (busy || !item.kind) return;
    setBusy(true); setError("");
    try { await putApi(`/api/platform/my/items/${item.kind}/${item.id}/actions`, { [flag]: false }); onChanged(); }
    catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); if (cause instanceof AuthRequiredError) openAuth(); }
    finally { setBusy(false); }
  }
  return <article className="demo-setting-card"><h3>{item.kind ? KIND_LABELS[item.kind] : "内容"} #{item.id} · {item.unavailable_reason || "内容已失效"}</h3><p>原始详情、来源与时间未知。仅保留当前账户的保存记录。</p><div className="demo-detail-actions">{(Object.keys(labels) as (keyof PersonalFlags)[]).filter((flag) => flags[flag]).map((flag) => <button key={flag} type="button" disabled={busy} onClick={() => clear(flag)}>{labels[flag]}</button>)}</div>{error && <p role="alert" className="demo-error">{error} · 原记录已保留，可重试。</p>}</article>;
}

function WorkspaceSession() {
  const { user, openAuth, logout } = useAuth();
  const [route, setRoute] = useState(readRoute);
  const [kind, setKind] = useState<EntityKind>(readKind);
  const [queries, setQueries] = useState<Record<string, string>>({});
  const [filter, setFilter] = useState<keyof typeof FILTERS>("all");
  const [stage, setStage] = useState("");
  const [board, setBoard] = useState(false);
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<EntityRef | null>(null);
  const [editing, setEditing] = useState<RemoteEntity | "new" | null>(null);
  const [notice, setNotice] = useState("");
  const [hint, setHint] = useState(false);
  const personalPage = route === "today" || route === "actions";
  const personalFilter = route === "today" ? "read_later" : filter === "all" || filter === "opportunities" ? "" : filter;
  const personal = useApi<Items>(user && personalPage ? `/api/platform/my/items?limit=100&offset=${route === "today" ? 0 : offset}${personalFilter ? `&action=${personalFilter}` : ""}` : null);
  const applications = useApi<{ items: ApplicationRecord[] }>(user && (personalPage || route === "discover") ? "/api/platform/applications" : null);
  const discovery = useApi<Items>(route === "discover" ? `/api/platform/${ENTITY_PATHS[kind]}` : null);
  const query = queries[route] ?? "";
  const saved = personal.data?.items ?? [];
  const opportunities = applications.data?.items ?? [];
  const toKind = (item: RemoteEntity) => item.kind ?? kind;
  const matches = (item: RemoteEntity) => [entityTitle(item), entityBody(item), item.company, item.meta, ...(item.tags ?? [])].join(" ").toLocaleLowerCase().includes(query.trim().toLocaleLowerCase());
  const pending = opportunities.filter((item) => item.next_step?.trim() && !item.next_step_done && item.stage !== "archived");
  const todayItems = new Map<string, RemoteEntity>();
  pending.forEach((record) => todayItems.set(`job:${record.job_id}`, { ...record.job, kind: "job" }));
  saved.filter((item) => personalFlags(item).read_later && !personalFlags(item).read).forEach((item) => todayItems.set(`${item.kind}:${item.id}`, item));

  useEffect(() => {
    const changed = () => { setRoute(readRoute()); if (/recruitment|projects|ai-daily/.test(window.location.hash)) setKind(readKind()); setSelected(null); setEditing(null); setHint(false); window.scrollTo(0, 0); };
    window.addEventListener("hashchange", changed);
    return () => window.removeEventListener("hashchange", changed);
  }, []);
  const refresh = () => { personal.refresh(); applications.refresh(); discovery.refresh(); };
  const open = (item: RemoteEntity, entityKind = toKind(item)) => setSelected({ kind: entityKind, id: item.id });
  function cards(items: RemoteEntity[]) {
    const filtered = items.filter(matches);
    return filtered.length ? <div className="demo-list">{filtered.map((item) => {
      const entityKind = toKind(item);
      if (item.available === false) return <UnavailableCard key={`${entityKind}:${item.id}`} item={item} onChanged={refresh} />;
      const record = entityKind === "job" ? opportunities.find((entry) => entry.job_id === item.id) : undefined;
      const flags = personalFlags(item);
      return <article className="demo-card" key={`${entityKind}:${item.id}`}><button type="button" className="demo-card-main" onClick={() => open(item, entityKind)}><span className={`demo-kind demo-kind-${entityKind}`}>{KIND_LABELS[entityKind]}</span><div className="demo-card-copy"><h3>{entityTitle(item)}</h3><p>{item.company || item.full_name || item.source || item.meta || "来源信息未提供"}</p><p>{entityBody(item)}</p></div><span className="demo-origin">{item.origin === "manual" ? "手动记录" : item.origin === "demo" ? "后端演示" : item.origin === "real" ? "采集内容" : "来源未知"}</span></button>
        {record?.next_step && <p className="demo-card-step">{record.next_step_done ? "已完成" : "下一步"}：{record.next_step}</p>}
        <div className="demo-card-actions"><button type="button" onClick={() => open(item, entityKind)}>查看详情与行动</button>{record && <span>阶段：{STAGES[record.stage]}</span>}{flags.favorite && <span>已收藏</span>}{flags.watch && <span>已关注</span>}{flags.read_later && <span>稍后阅读</span>}{flags.read && <span>已读</span>}</div>
      </article>;
    })}</div> : <div className="demo-empty"><strong>{query ? "没有匹配结果" : "暂无内容"}</strong><span>调整筛选、清空搜索，或添加一个手动岗位。</span>{query && <button type="button" onClick={() => setQueries({ ...queries, [route]: "" })}>清空搜索</button>}</div>;
  }
  function status(data: unknown, error: string | null, retry: () => void) {
    return data === null ? <div className="demo-empty" role={error ? "alert" : "status"}><strong>{error || "加载中…"}</strong>{error && <><button type="button" onClick={retry}>重试读取</button>{error.includes("登录") && <button type="button" onClick={openAuth}>登录</button>}</>}</div> : null;
  }
  function renderPersonal() {
    if (!user) return <div className="demo-empty"><strong>登录后查看个人收藏和机会</strong><button className="demo-primary" type="button" onClick={openAuth}>登录 / 注册</button></div>;
    if (!personal.data || !applications.data) return status(null, personal.error || applications.error, refresh);
    if (route === "today") return <>
      <div className="demo-kpis"><div><span>待处理内容</span><strong>{todayItems.size}</strong><small>机会下一步 + 最近 100 条稍后阅读，按内容去重</small></div><div><span>活跃机会</span><strong>{opportunities.filter((item) => item.stage !== "archived").length}</strong><small>当前账户未归档的机会</small></div><div><span>待完成下一步</span><strong>{pending.length}</strong><small>来自已保存的岗位行动</small></div></div>
      <section className="demo-section"><h2>待处理</h2>{cards([...todayItems.values()])}</section>
    </>;
    return <><div className="demo-tabs">{Object.entries(FILTERS).map(([id, label]) => <button type="button" key={id} aria-pressed={filter === id} className={filter === id ? "active" : ""} onClick={() => { setFilter(id as keyof typeof FILTERS); setOffset(0); }}>{label}</button>)}</div>
      {filter === "opportunities" ? <><div className="demo-toolbar"><label className="demo-field">机会阶段<select value={stage} onChange={(event) => setStage(event.target.value)}><option value="">全部阶段（含归档）</option>{Object.entries(STAGES).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label><button type="button" className="workspace-board-toggle" aria-pressed={board} onClick={() => setBoard(!board)}>{board ? "切换列表" : "切换阶段看板"}</button></div><div className={board ? "workspace-opportunities is-board" : "workspace-opportunities"}><div className="workspace-list-view">{cards(opportunities.filter((item) => !stage || item.stage === stage).map((item) => ({ ...item.job, kind: "job" })))}</div>{board && <div className="workspace-board">{Object.entries(STAGES).filter(([id]) => !stage || id === stage).map(([id, label]) => <section key={id}><h3>{label}</h3>{cards(opportunities.filter((item) => item.stage === id).map((item) => ({ ...item.job, kind: "job" })))}</section>)}</div>}</div></> : <>{cards(saved)}<div className="demo-toolbar"><button type="button" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 100))}>上一页</button><span>第 {offset / 100 + 1} 页 · 本页 {saved.length} 条保存内容</span><button type="button" disabled={saved.length < 100} onClick={() => setOffset(offset + 100)}>下一页</button></div></>}
    </>;
  }
  return <div className="demo-app real-workspace">
    <header className="demo-topbar"><a className="demo-brand" href="?workspace=1#/today"><span className="demo-brand-mark">S</span><span>Scaffold Platform</span></a><nav aria-label="工作台主导航">{NAV.map((item) => <a href={`#/${item.id}`} key={item.id} className={route === item.id ? "active" : ""} aria-current={route === item.id ? "page" : undefined}>{item.label}</a>)}</nav><div className="demo-top-actions"><a href="?demo=1#/today">体验演示</a><a href="?legacy=1">旧版入口</a>{user ? <button type="button" onClick={logout}>{user.nickname || user.username} · 退出</button> : <button type="button" onClick={openAuth}>登录 / 注册</button>}</div></header>
    <main className="demo-shell"><div className="demo-banner"><span>真实工作台</span><p>内容来自服务器 · 个人操作保存到当前账户 · 演示状态不混入</p></div><div className="demo-workspace-tools"><label className="demo-search"><span>⌕</span><input type="search" aria-label="搜索当前视图" placeholder="搜索当前视图" value={query} onChange={(event) => setQueries({ ...queries, [route]: event.target.value })} /><button type="button" disabled={!query} onClick={() => setQueries({ ...queries, [route]: "" })}>清空</button></label>{!["automation", "connections"].includes(route) && <button className="demo-primary" type="button" onClick={refresh}>刷新数据</button>}</div>
      {notice && <p role="status" className="demo-live-notice">{notice}</p>}
      {route === "automation" ? <Automation query={query} onNavigate={(id) => { window.location.hash = `/${id}`; }} onPreview={() => setHint(true)} /> : route === "connections" ? <Sources query={query} onNavigate={(id) => { window.location.hash = `/${id}`; }} onPreview={() => setHint(true)} /> : <>
        <div className="demo-page-head compact"><div><p className="demo-eyebrow">当前账户的工作台</p><h1>{route === "today" ? "今天，继续推进下一步" : route === "discover" ? "发现与判断" : "我的收藏与机会"}</h1><p>详情、阅读与申请进度以服务器返回为准。</p></div><button className="demo-primary" type="button" onClick={() => user ? setEditing("new") : openAuth()}>添加手动岗位</button></div>
        {route === "discover" ? <><div className="demo-tabs">{(["job", "project", "news"] as EntityKind[]).map((value) => <button type="button" key={value} className={kind === value ? "active" : ""} aria-pressed={kind === value} onClick={() => setKind(value)}>{KIND_LABELS[value]}</button>)}</div>{discovery.data ? cards(discovery.data.items.map((item) => ({ ...item, kind }))) : status(discovery.data, discovery.error, discovery.refresh)}</> : renderPersonal()}
      </>}
      {hint && <div className="demo-notice">此配置尚未接入真实保存。<a href={`?demo=1#/${route}`}>进入独立演示体验配置 →</a><button type="button" onClick={() => setHint(false)}>关闭</button></div>}
    </main>
    {selected && <WorkspaceDetail key={`${selected.kind}:${selected.id}`} target={selected} onClose={() => setSelected(null)} onChanged={refresh} onEdit={(item) => { setSelected(null); setEditing(item); }} />}
    {editing && <WorkspaceJobForm item={editing === "new" ? undefined : editing} onClose={() => setEditing(null)} onSaved={(item) => { setEditing(null); setNotice("岗位已保存到服务器，可在详情中加入机会。"); refresh(); setSelected({ kind: "job", id: item.id }); }} />}
  </div>;
}

export function Workspace() {
  const identity = useTokenVersion();
  const { showAuth } = useAuth();
  return <><WorkspaceSession key={identity} />{showAuth && <AuthModal />}</>;
}
