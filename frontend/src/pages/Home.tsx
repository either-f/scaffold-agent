import { useState } from "react";
import { postApi, useApi } from "../api";
import { Icon, SourceLogo, type IconName } from "../components/Icons";
import type { PreviewRequest } from "../components/DesignPreview";
import { matchesQuery, PageStatus } from "../components/ui";
import type { AutoBrief, FeedItem, HomeData, TodoItem } from "../types";

const TABS = [
  { key: "recommend", label: "推荐" },
  { key: "job", label: "招聘" },
  { key: "project", label: "项目" },
  { key: "news", label: "AI动态" },
  { key: "referral", label: "内推" },
];

const STAT_ICONS: IconName[] = ["briefcase", "folder", "fire", "task"];

function badgeClass(variant: string) {
  if (variant === "blue") return "badge-soft blue";
  if (variant === "gray") return "badge-soft gray";
  return "badge-soft";
}

function FeedCard({ item, onAction }: { item: FeedItem; onAction: (label: string, item: FeedItem) => void }) {
  return (
    <article className={`item ${item.kind === "project" ? "project" : item.kind === "news" ? "news" : ""}`}>
      <div className="item-top">
        <SourceLogo type={item.logo} />
        <div>
          <div className="title-row">
            <div className="title">{item.title}</div>
            {item.badge && <span className={badgeClass(item.badge.variant)}>{item.badge.text}</span>}
          </div>
          <div className="meta">{item.meta}</div>
          {item.tags && <div className="tags">{item.tags.map((t) => <span className="tag" key={t}>{t}</span>)}</div>}
        </div>
        <div className="side-actions">
          <div className="side-icons">
            <Icon name="bookmark" />
            <Icon name="more" style={{ fill: "#516072" }} />
          </div>
          {item.time && <div className="item-time">{item.time}</div>}
        </div>
      </div>
      {item.desc && <div className="desc">{item.desc}</div>}
      {item.reason && <><div className="reason-label">推荐理由</div><div className="reason">{item.reason}</div></>}
      <div className="actions">
        {item.actions.map((a) => (
          <button key={a.label} className={`btn${a.primary ? " primary" : ""}`} onClick={() => onAction(a.label, item)}>{a.label}</button>
        ))}
      </div>
    </article>
  );
}

export function Home({ query, onPreview }: { query: string; onPreview: (request: PreviewRequest) => void }) {
  const [tab, setTab] = useState("recommend");
  const { data, error, setData } = useApi<HomeData>("/api/platform/home");

  async function toggleAutomation(auto: AutoBrief) {
    const res = await postApi<{ enabled: boolean }>(`/api/platform/automations/${auto.id}/toggle`);
    setData((d) => ({
      ...d,
      automations: d.automations.map((a) => (a.id === auto.id ? { ...a, running: res.enabled } : a)),
    }));
  }

  async function doneTodo(todo: TodoItem) {
    const res = await postApi<{ done: boolean }>(`/api/platform/todos/${todo.id}/done`);
    if (!res.done) return;
    setData((d) => ({ ...d, todos: d.todos.filter((t) => t.id !== todo.id) }));
  }

  if (data === null) {
    return <main className="page"><PageStatus data={data} error={error} /></main>;
  }

  const feed = data.feed
    .filter((i) => tab === "recommend" ? i.kind !== "referral" : i.kind === tab)
    .filter((i) => matchesQuery(query, i.title, i.meta, i.desc, i.reason, ...(i.tags ?? [])));

  function handleFeedAction(label: string, item: FeedItem) {
    if (label === "查看项目" && item.kind === "project") {
      onPreview({ kind: "project-detail", title: item.title, subtitle: item.meta, score: null });
      return;
    }
    if (label === "查看详情" && item.kind === "job") {
      onPreview({ kind: "job-detail", title: item.title, subtitle: item.meta, score: null });
      return;
    }
    if (item.kind === "project") {
      onPreview({ kind: "project-detail", title: item.title, subtitle: item.meta, score: null });
      return;
    }
    onPreview({ kind: "news-detail", title: item.title, subtitle: item.meta, score: null });
  }

  return (
    <main className="page">
      <section className="stats">
        {data.stats.map((s, i) => (
          <div className="stat" key={s.title}>
            <div className="stat-ico"><Icon name={STAT_ICONS[i]} /></div>
            <div>
              <div className="stat-title">{s.title}</div>
              <div className="stat-line">
                <span className="stat-num">{s.num}</span>
                <span className={`trend${s.down ? " down" : ""}`}>{s.trend}</span>
              </div>
              <div className="stat-sub">{s.sub}</div>
            </div>
          </div>
        ))}
      </section>

      <section className="grid">
        <div className="panel">
          <div className="tabs">
            {TABS.map((t) => (
              <button key={t.key} className={`tab${tab === t.key ? " active" : ""}`} onClick={() => setTab(t.key)}>
                {t.label}
              </button>
            ))}
            <div className="tab-tools">
              <button type="button" onClick={() => document.querySelector<HTMLInputElement>(".search-input")?.focus()}><Icon name="filter" />筛选</button>
              <button type="button" aria-label="刷新" onClick={() => window.location.reload()}><Icon name="refresh" /></button>
            </div>
          </div>

          <div className="feed">
            {feed.map((item, i) => <FeedCard item={item} onAction={handleFeedAction} key={`${item.kind}-${i}`} />)}
            <div className="loaded">{feed.length ? <><Icon name="check" />已加载全部</> : "没有匹配的内容"}</div>
          </div>
        </div>

        <aside className="sidebar">
          <section className="side-card hot">
            <div className="side-head"><div className="side-title">今日热榜</div><button type="button" className="more more-button" onClick={() => onPreview({ kind: "news-detail", title: "今日热榜" })}>更多 ›</button></div>
            <div className="rank-list">
              {data.ranks.length === 0 && <div className="side-empty">暂无热榜数据，热度排行尚未接入真实来源。</div>}
              {data.ranks.map((r, i) => (
                <div className="rank" key={r.title}>
                  <span className="no">{i + 1}</span>
                  <span>{r.title}</span>
                  <span className="heat"><Icon name="fire" />{r.heat}</span>
                </div>
              ))}
            </div>
          </section>

          <section className="side-card todo">
            <div className="side-head"><div className="side-title">待处理</div><button type="button" className="more more-button" onClick={() => onPreview({ kind: "notifications" })}>查看全部（{data.todos.length}）</button></div>
            <div className="todo-list">
              {data.todos.length === 0 && <div className="side-empty">暂无待处理事项。</div>}
              {data.todos.map((t) => (
                <button className="todo-row" key={t.id} type="button" onClick={() => doneTodo(t)}>
                  <div className={`todo-icon${t.icon === "git" ? " git" : t.icon === "link" ? " ref" : ""}`}>
                    {t.icon === "git" ? (
                      <svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="8" fill="currentColor" /></svg>
                    ) : (
                      <Icon name={t.icon as IconName} />
                    )}
                  </div>
                  <div><div className="todo-name">{t.name}</div><div className="todo-sub">{t.sub}</div></div>
                  <div className="todo-time">{t.time}{t.has_dot && <span className="red-dot" />}</div>
                </button>
              ))}
            </div>
          </section>

          <section className="side-card auto">
            <div className="side-head"><div className="side-title">自动化状态</div><button type="button" className="more more-button" onClick={() => onPreview({ kind: "automation-logs" })}>管理 ›</button></div>
            <div className="auto-list">
              {data.automations.map((a, i) => (
                <div className="auto-row" key={a.id}>
                  <div className="auto-icon"><Icon name={i === 0 ? "refresh" : i === 1 ? "radar" : "clock"} /></div>
                  <div><div className="auto-name">{a.name}</div><div className="auto-sub">{a.sub}</div></div>
                  <button
                    type="button"
                    className={`toggle${a.running ? "" : " off"}`}
                    aria-label={`${a.name}${a.running ? "：关闭" : "：开启"}`}
                    aria-pressed={a.running}
                    onClick={() => toggleAutomation(a)}
                  />
                </div>
              ))}
            </div>
          </section>
        </aside>
      </section>
    </main>
  );
}
